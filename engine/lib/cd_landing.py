from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

try:
    from .shell_expansion import Expansions
    from .shell_parse import line_segments
    from .shell_syntax import RESERVED_PREFIXES, _basename, _bare, _command_word_index, _tokens, _without_redirects
    from .shell_text import REDIRECT_PARTS_RE, shell_tokens
    from .temp_scope import (DIRECTORY_VERBS, EXPANDING_RE, MAX_DIRECTORY_CANDIDATES, _changes_directory,
                             _searchable, mkdir_parents, unmade_parent)
    from .tests_policy import is_test_path, project_root
except ImportError:
    from shell_expansion import Expansions
    from shell_parse import line_segments
    from shell_syntax import RESERVED_PREFIXES, _basename, _bare, _command_word_index, _tokens, _without_redirects
    from shell_text import REDIRECT_PARTS_RE, shell_tokens
    from temp_scope import (DIRECTORY_VERBS, EXPANDING_RE, MAX_DIRECTORY_CANDIDATES, _changes_directory,
                            _searchable, mkdir_parents, unmade_parent)
    from tests_policy import is_test_path, project_root

CD_MODES = {"-L": False, "-P": True}
ERREXIT_MODES = {("set", "-e"): True, ("set", "-o", "errexit"): True,
                 ("set", "+e"): False, ("set", "+o", "errexit"): False}
AND_OR = {"&&": True, "||": False}
LIST_ENDS = frozenset({";", "\n", "&", ""})
PIPE_ENDS = frozenset({"|", "|&"})
WRITE_OPERATORS = frozenset({">", ">>", ">|", "<>"})
SEARCH_STARTS = frozenset({".", ".."})
COMPOUND_HEADS = (RESERVED_PREFIXES - {"!"}) | {"case", "esac", "fi", "for", "select", "done", "function", "{", "}"}
CHAIN_LIMIT = 256
UNFOLLOWED_SEPARATORS = frozenset({";;", ";&", "&>"})
WORD_PART_RE = re.compile(r"""'([^']*)'|"((?:\\.|[^"\\])*)"|\\(.)|([^'"\\]+)""", re.DOTALL)
DOUBLE_PART_RE = re.compile(r'\\([$`"\\\n])|([$`])|(\\|[^\\$`]+)', re.DOTALL)
UNQUOTED_EXPANSION_RE = re.compile(r"[*?\[{$`]")


class ShellScope(NamedTuple):
    text: str
    directory: Path | None
    exported_cdpath: str | None
    separator: str = ""
    created: frozenset[Path] = frozenset()


class LandedCommand(NamedTuple):
    text: str
    scopes: tuple[ShellScope, ...]


class Cd(NamedTuple):
    operand: str
    physical: bool


class State(NamedTuple):
    directory: Path
    cdpath: str | None
    exported_cdpath: str | None
    succeeded: bool
    scopes: tuple[ShellScope, ...] = ()
    stack: tuple[Path, ...] = ()
    created: frozenset[Path] = frozenset()
    errexit: bool = False
    deferred: bool = False
    halted: bool = False


class Stage(NamedTuple):
    text: str
    words: list[str]
    separator: str
    missing_test_guard: bool = False
    body: tuple[Stage, ...] | None = None


def landed_commands(
    command: str, cwd: Path, *, start: Path | None = None, cdpath: str | None = "",
) -> list[LandedCommand] | None:
    directory = (start or cwd).absolute()
    inherited = os.environ.get("CDPATH", "") if start is None else cdpath
    if not any(_tracks_landing(segment) for segment in line_segments(command)):
        scopes = (ShellScope(command, directory, inherited),)
        if start is None:
            return [LandedCommand(command, scopes)]
        rendered = _render(scopes, cwd)
        return None if rendered is None else [LandedCommand(rendered, scopes)]
    stages = _stages(command)
    if stages is None:
        return None
    states = _walk(stages, State(directory, inherited, inherited, True), cwd)
    if states is None:
        return None
    commands: dict[LandedCommand, None] = {}
    for state in states:
        rendered = _render(state.scopes, cwd)
        if rendered is None:
            return None
        commands[LandedCommand(rendered, state.scopes)] = None
    return list(commands)


def _stages(command: str) -> list[Stage] | None:
    frames: list[list[Stage]] = [[]]
    group: tuple[Stage, ...] | None = None
    start = count = 0
    loop_start = None
    for left, right, separator in _separators(command):
        text = command[start:left] + (command[left:right] if right > left + len(separator) else "")
        words = _tokens(text)
        opens_loop = words[:1] == ["for"]
        if opens_loop:
            if loop_start is not None:
                return None
            loop_start = start
        start = right
        loop_ended = loop_start is not None and words == ["done"]
        if loop_start is not None:
            if loop_ended:
                text = command[loop_start:left]
                loop_start = None
            elif separator in {"(", ")", ""} or not opens_loop and _tracks_landing(words):
                return None
            else:
                continue
        if separator in UNFOLLOWED_SEPARATORS or separator == "(" and (words or group is not None):
            return None
        if separator == "(":
            frames.append([])
            continue
        stage_end = "" if separator == ")" else separator
        stage = Stage(text, [], stage_end) if loop_ended else _stage(text, words, stage_end, group)
        group = None
        if stage is False:
            return None
        if stage is not None:
            frames[-1].append(stage)
            count += 1
        if count > CHAIN_LIMIT:
            return None
        if separator == ")":
            if len(frames) == 1 or not frames[-1]:
                return None
            group = tuple(frames.pop())
    if len(frames) != 1 or loop_start is not None:
        return None
    stages = frames[0]
    if stages and stages[0].separator == "&&" and sum(_changes_directory(stage.words) for stage in stages) == 1:
        stages[0] = stages[0]._replace(missing_test_guard=True)
    return stages


def _stage(text: str, words: list[str], separator: str, group: tuple[Stage, ...] | None) -> Stage | None | bool:
    if group is not None:
        return False if _without_redirects(words) else Stage(text, [], separator, body=group)
    if not words:
        return None
    return False if words[0] in COMPOUND_HEADS else Stage(text, words, separator)


def _tracks_landing(words: list[str]) -> bool:
    return _directory_verb(words)[1] in DIRECTORY_VERBS or any(word.startswith("CDPATH=") for word in words)


def _directory_verb(words: list[str]) -> tuple[int, str]:
    index = _command_word_index(words)
    verb = _bare(words[index]) if index < len(words) else ""
    if verb == "builtin" and index + 1 < len(words) and words[index + 1] in DIRECTORY_VERBS:
        return index + 1, words[index + 1]
    return index, verb


def _separators(command: str) -> Iterator[tuple[int, int, str]]:
    end = 0
    for token in shell_tokens(command):
        match = token.match
        if match is None or match.lastgroup != "separator" or match.start() < end:
            continue
        start = match.start()
        pair = command[start:start + 2]
        separator = pair if pair in AND_OR or pair in {"|&", ";;", ";&", "&>"} else match.group()
        end = max(token.end, start + len(separator))
        yield start, end, separator
    yield len(command), len(command), ""


def _walk(stages: list[Stage], initial: State, cwd: Path) -> set[State] | None:
    states = {initial}
    start = 0
    for end, stage in enumerate(stages):
        if stage.separator not in LIST_ENDS:
            continue
        chain = stages[start:end + 1]
        updated: set[State] = set()
        for state in states:
            result = {state} if state.halted else _and_or(chain, state, cwd)
            if result is None:
                return None
            if stage.separator == "&" and not state.halted:
                result = {state._replace(scopes=item.scopes, succeeded=True) for item in result}
            updated.update(result)
            if len(updated) > MAX_DIRECTORY_CANDIDATES:
                return None
        states = updated
        start = end + 1
    return states if start == len(stages) else None


def _and_or(chain: list[Stage], initial: State, cwd: Path) -> set[State] | None:
    states = {initial}
    start = 0
    required = None
    for end, stage in enumerate(chain):
        if stage.separator in PIPE_ENDS:
            continue
        reached: set[State] = set()
        for state in states:
            if required is not None and state.succeeded != required:
                reached.add(state)
                continue
            result = _pipeline(chain[start:end + 1], state, cwd)
            if result is None:
                return None
            reached.update(result)
        if len(reached) > MAX_DIRECTORY_CANDIDATES:
            return None
        states = reached
        start = end + 1
        required = AND_OR.get(stage.separator)
    return states


def _pipeline(stages: list[Stage], initial: State, cwd: Path) -> set[State] | None:
    states = {initial}
    inverted = stages[0].words[:1] == ["!"]
    for stage in stages:
        reached: set[State] = set()
        for state in states:
            result = _execute(stage, state, cwd)
            if result is None:
                return None
            if len(stages) > 1:
                result = {_isolated_stage(stage, state, item, initial) for item in result}
            reached.update(result)
        states = reached
    return {state._replace(succeeded=state.succeeded != inverted) for state in states}


def _isolated_stage(stage: Stage, before: State, after: State, initial: State) -> State:
    if before.scopes == after.scopes:
        after = _record(after._replace(directory=initial.directory), ":")
    last = after.scopes[-1]._replace(separator=stage.separator if stage.separator in PIPE_ENDS else "")
    return initial._replace(succeeded=after.succeeded, scopes=(*after.scopes[:-1], last))


def _execute(stage: Stage, state: State, cwd: Path) -> set[State] | None:
    if stage.body is not None:
        return _subshell(stage, state, cwd)
    words = _without_redirects(stage.words)
    index, verb = _directory_verb(words)
    prefix = [word for word in words[:index] if word not in {"!", "builtin"}]
    if verb in {"cd", "pushd"}:
        return _move(stage, state, words[index + 1:], prefix, cwd, verb)
    if verb == "popd":
        return _pop(stage, state, words[index + 1:], prefix)
    if _changes_directory(stage.words):
        return None
    return _execute_regular(stage, state, words, index, verb)


def _execute_regular(stage: Stage, state: State, words: list[str], index: int, verb: str) -> set[State]:
    state = _record(_cdpath_state(state, words, verb), stage.text)
    mode = ERREXIT_MODES.get(tuple(words)) if index == 0 and words == stage.words else None
    if mode is not None:
        return {state._replace(errexit=mode, succeeded=True)}
    directory = None if state.deferred else state.directory
    created = mkdir_parents(words[1:], directory, _literal_word) if index == 0 and verb == "mkdir" else frozenset()
    if verb in {"true", "false", ":"} and words == stage.words:
        return {state._replace(succeeded=verb != "false")}
    return {state._replace(succeeded=True, created=state.created | created), state._replace(succeeded=False)}


def _cdpath_state(state: State, words: list[str], verb: str) -> State:
    if verb == "unset" and "CDPATH" in words[1:]:
        return state._replace(cdpath="", exported_cdpath="")
    if verb and verb != "export":
        return state
    values = [word.partition("=")[2] for word in words if word.startswith("CDPATH=")]
    value = Expansions({}).assigned(values[-1]) if values else state.cdpath
    exported = verb == "export" and (values or "CDPATH" in words[1:])
    inherited = value if exported else state.exported_cdpath
    return state._replace(cdpath=value, exported_cdpath=inherited)


def _subshell(stage: Stage, state: State, cwd: Path) -> set[State] | None:
    reached = _walk(list(stage.body or ()), state, cwd)
    if reached is None:
        return None
    redirects = _cd_redirects(stage.text)
    outer = {
        "directory": state.directory, "cdpath": state.cdpath, "exported_cdpath": state.exported_cdpath,
        "stack": state.stack, "errexit": state.errexit, "deferred": state.deferred, "halted": state.halted,
    }
    left = {inner._replace(**outer) for inner in reached}
    return {_record(item, f": {redirects}") for item in left} if redirects else left


def _pop(stage: Stage, state: State, operands: list[str], prefix: list[str]) -> set[State] | None:
    if operands or not state.stack or any("=" not in word and word != "command" for word in prefix):
        return None
    redirects = _cd_redirects(stage.text)
    if redirects:
        state = _record(state, f": {redirects}")
    popped = state._replace(directory=state.stack[0], stack=state.stack[1:], succeeded=True, deferred=False)
    return {popped, state._replace(succeeded=False)} if redirects else {popped}


def _move(stage: Stage, state: State, operands: list[str], prefix: list[str], cwd: Path, verb: str) -> set[State] | None:
    if any("=" not in word and word != "command" for word in prefix):
        return None
    cd = _cd(operands, verb)
    if cd is None:
        return None
    stack = (state.directory, *state.stack) if verb == "pushd" else state.stack
    assignments = [word.partition("=")[2] for word in prefix if word.startswith("CDPATH=")]
    search = Expansions({}).assigned(assignments[-1]) if assignments else state.cdpath
    if search is None and _uses_cdpath(cd.operand):
        return None
    redirects = _cd_redirects(stage.text)
    if redirects:
        state = _record(state, f": {redirects}")
    return _move_outcomes(stage, state, cd, cwd, stack, search or "", bool(redirects))


def _move_outcomes(
    stage: Stage, state: State, cd: Cd, cwd: Path, stack: tuple[Path, ...], search: str, redirects: bool,
) -> set[State]:
    candidates = _candidates(cd.operand, state.directory, search)
    bases = {base for candidate in candidates if (base := _base(candidate, cd.physical, state.created)) is not None}
    outcomes = {state._replace(directory=base, succeeded=True, stack=stack, deferred=False) for base in bases}
    if not bases or redirects:
        halt = state.errexit and stage.separator in LIST_ENDS - {"&"} and stage.words[:1] != ["!"]
        outcomes.add(state._replace(succeeded=False, deferred=True, halted=halt))
    if not bases and stage.missing_test_guard:
        missing = _missing_test_bases(candidates, cwd)
        outcomes.update(state._replace(directory=base, succeeded=True, stack=stack, deferred=False) for base in missing)
    return outcomes


def _cd(words: list[str], verb: str = "cd") -> Cd | None:
    physical = False
    while verb == "cd" and words and words[0] in CD_MODES:
        physical = CD_MODES[words[0]]
        words = words[1:]
    if words[:1] == ["--"]:
        words = words[1:]
    if not words:
        return Cd("~", physical) if verb == "cd" else None
    operand = _unquoted(words[0])
    flagged = operand is None or operand.startswith(("-", "+") if verb == "pushd" else "-")
    return None if flagged or len(words) != 1 else Cd(operand, physical)


def _unquoted(word: str) -> str | None:
    pieces: list[str] = []
    cursor = 0
    for match in WORD_PART_RE.finditer(word):
        single, double, escaped, plain = match.groups()
        if match.start() != cursor or plain is not None and UNQUOTED_EXPANSION_RE.search(plain):
            return None
        piece = _double_quoted(double) if double is not None else single if single is not None else plain
        if escaped is not None:
            piece = "" if escaped == "\n" else escaped
        if piece is None:
            return None
        pieces.append(piece)
        cursor = match.end()
    text = "".join(pieces)
    quoted_tilde = text.startswith("~") and not word.startswith("~")
    return None if cursor != len(word) or not text or quoted_tilde else text


def _double_quoted(body: str) -> str | None:
    pieces: list[str] = []
    for match in DOUBLE_PART_RE.finditer(body):
        escaped, expanding, plain = match.groups()
        if expanding is not None:
            return None
        pieces.append(plain if escaped is None else "" if escaped == "\n" else escaped)
    return "".join(pieces)


def _cd_redirects(text: str) -> str:
    return " ".join(
        token.match.group() for token in shell_tokens(text)
        if token.match is not None and token.match.lastgroup in {"redirect", "dup"}
    )


def _missing_test_bases(candidates: list[Path], cwd: Path) -> set[Path]:
    root = project_root(str(cwd))
    return {
        Path(os.path.normpath(path)) for path in candidates
        if not path.exists() and is_test_path(str(_placed(path, cwd) / "adw-placeholder"), root)
    }


def _uses_cdpath(operand: str) -> bool:
    return not Path(os.path.expanduser(operand)).is_absolute() and operand.partition("/")[0] not in SEARCH_STARTS


def _candidates(operand: str, cwd: Path, cdpath: str) -> list[Path]:
    path = Path(os.path.expanduser(operand))
    local = cwd / path
    if not _uses_cdpath(operand):
        return [local]
    return [local, *(cwd / os.path.expanduser(entry) / path for entry in cdpath.split(os.pathsep) if entry)]


def _base(candidate: Path, physical: bool, created: frozenset[Path]) -> Path | None:
    logical = Path(os.path.normpath(candidate))
    if not physical and _searchable(logical):
        return logical
    real = Path(os.path.realpath(candidate))
    if real in created:
        return real if physical else logical
    return real if _searchable(candidate) else None


def _record(state: State, text: str) -> State:
    directory = None if state.deferred else state.directory
    scope = ShellScope(text, directory, state.exported_cdpath, created=state.created)
    return state._replace(scopes=(*state.scopes, scope))


def _render(scopes: tuple[ShellScope, ...], cwd: Path) -> str | None:
    pieces: list[str] = []
    previous = cwd.absolute()
    for scope in scopes:
        if scope.directory is None:
            continue
        directory = _placed(scope.directory, cwd)
        if directory != previous:
            quoted = _quoted(directory)
            if quoted is None:
                return None
            pieces.append(f"cd {quoted} && ")
            previous = directory
        text = _placed_redirects(scope.text, directory, cwd, scope.created)
        if text is None:
            return None
        pieces.extend([text, f" {scope.separator} " if scope.separator else "\n"])
    return "".join(pieces)


def _placed(path: Path, cwd: Path) -> Path:
    real = Path(os.path.realpath(path))
    for anchor in (cwd, Path(project_root(str(cwd)))):
        root = Path(os.path.realpath(anchor))
        if real == root or root in real.parents:
            return anchor / real.relative_to(root)
    return real


def _placed_redirects(rest: str, base: Path, cwd: Path, created: frozenset[Path]) -> str | None:
    pieces: list[str] = []
    cursor = 0
    for token in shell_tokens(rest):
        match = token.match
        parts = REDIRECT_PARTS_RE.match(match.group()) if match is not None and match.lastgroup == "redirect" else None
        word = _literal_word(parts[3]) if parts is not None and parts[2] in WRITE_OPERATORS else None
        if word is None or word.startswith(("/", "~")):
            continue
        destination = _placed(base / word, cwd)
        pieces.append(rest[cursor:match.start()])
        if not unmade_parent(destination, created):
            quoted = _quoted(destination)
            if quoted is None:
                return None
            pieces.extend([parts[1], parts[2], quoted])
        cursor = match.end()
    return "".join([*pieces, rest[cursor:]])


def _literal_word(word: str) -> str | None:
    bare = _unquoted(word)
    return None if bare is None or EXPANDING_RE.search(bare) else bare


def _quoted(path: Path) -> str | None:
    text = str(path)
    return None if "'" in text or "\n" in text else shlex.quote(text)
