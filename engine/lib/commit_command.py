from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import takewhile
from pathlib import Path

from .commit_functions import Function, extract_functions, with_positional
from .commit_inputs import (
    PLAIN_COMMIT,
    SCRIPT_READERS,
    Command,
    Input,
    InputScope,
    UnreadableCommit,
    is_dynamic,
    normalized,
    shell_input,
)
from .commit_processes import MAX_ROUNDS, UNSETTLED, Touched, WalkRun
from .commit_segments import (
    alias_definitions as _alias_definitions,
)
from .commit_segments import (
    bind_input as _bind_input,
)
from .commit_segments import (
    command_words as _command_words,
)
from .commit_segments import (
    is_assignment as _is_assignment,
)
from .commit_segments import (
    segments as _segments,
)
from .commit_writes import (
    changes_files as _changes_files,
    config_command,
    config_path,
    git_environment,
    operand_writes,
    read_only_git_metadata,
    reads_stdin_operands,
    write_keys,
)
from .path_policy import anchored
from .script_files import shebang_shell
from .shell_operators import SEPARATORS
from .shell_syntax import SHELL_C_INTERPRETERS, _basename
from .shell_text import split_redirects
from .temp_scope import sealed_temp_tail

try:
    from .commit_command_words import (
        COMMAND_RUNNERS, DECLARERS, DYNAMIC_PROGRAM, FILE_NEUTRAL_VERBS, HIDDEN_COMMIT, POSITIONAL_RE, VARIABLE_RE,
        _assignments, _cached_index, _expanded, _expanded_words, _file_neutral, _payload_command_index,
        _physical_mode, _refuse_hidden_commit,
    )
    from .commit_git_resolution import (
        BUILTIN_VERBS, CHDIR_FLAGS, CONFIG_ENV_PREFIXES, DIRECTORY_MOVE, DYNAMIC_DIRECTORY, DYNAMIC_INDEX, GIT_LOCATION_ENV_VARS,
        GIT_TIMEOUT_SECONDS, GIT_VALUE_FLAGS, LOCATION_OVERRIDE, MAX_ALIAS_DEPTH, PATH_FLAGS, SHELL_ALIAS, GitCall,
        _GitContext, _alias, _alias_env, _alias_words, _exec_value, _flag_cwd, _git_call, _git_payloads,
        _located_verb, _location_override, _moves_directory, _refuse_location_override, _resolved, _shell_alias,
        _skip_git_flag,
    )
    from .commit_walk_keys import (
        _command_key, _process_key, _function_key, _left_state, _variable_changes,
    )
except ImportError:
    from commit_command_words import (
        COMMAND_RUNNERS, DECLARERS, DYNAMIC_PROGRAM, FILE_NEUTRAL_VERBS, HIDDEN_COMMIT, POSITIONAL_RE, VARIABLE_RE,
        _assignments, _cached_index, _expanded, _expanded_words, _file_neutral, _payload_command_index,
        _physical_mode, _refuse_hidden_commit,
    )
    from commit_git_resolution import (
        BUILTIN_VERBS, CHDIR_FLAGS, CONFIG_ENV_PREFIXES, DIRECTORY_MOVE, DYNAMIC_DIRECTORY, DYNAMIC_INDEX, GIT_LOCATION_ENV_VARS,
        GIT_TIMEOUT_SECONDS, GIT_VALUE_FLAGS, LOCATION_OVERRIDE, MAX_ALIAS_DEPTH, PATH_FLAGS, SHELL_ALIAS, GitCall,
        _GitContext, _alias, _alias_env, _alias_words, _exec_value, _flag_cwd, _git_call, _git_payloads,
        _located_verb, _location_override, _moves_directory, _refuse_location_override, _resolved, _shell_alias,
        _skip_git_flag,
    )
    from commit_walk_keys import (
        _command_key, _process_key, _function_key, _left_state, _variable_changes,
    )

__all__ = ["SHELL_ALIAS", "GitCall", "UnreadableCommit", "git_calls"]

MAX_NESTING = 10
MAX_FUNCTION_DEPTH = 32
PIPES = frozenset({"|", "|&"})
DIRECTORY_VERBS = frozenset({"cd", "pushd", "popd"})
OUTPUT_OPERATORS = frozenset({">", ">>", ">|", "<>"})

TOO_DEEP = f"This command nests shells or scripts deeper than the commit gate reads. {PLAIN_COMMIT}"
PHYSICAL_CONTEXT = "set -P"
UNSET = "\0unset"
AMBIGUOUS_FUNCTION = f"This function has different readable definitions, so the commit gate cannot resolve its call. {PLAIN_COMMIT}"


def git_calls(command: str | list[str], cwd: Path) -> list[GitCall]:
    key = (command if isinstance(command, str) else tuple(command), str(cwd))
    if key not in _RESULTS:
        try:
            _RESULTS[key] = tuple(_walked_calls(command, cwd))
        except UnreadableCommit as exc:
            _RESULTS[key] = exc
    result = _RESULTS[key]
    if isinstance(result, UnreadableCommit):
        raise result
    return list(result)


def _walked_calls(command: str | list[str], cwd: Path) -> list[GitCall]:
    if isinstance(command, str):
        command = sealed_temp_tail(command, cwd)
    run, touched, unseen_writes = WalkRun(), frozenset(), False
    for _round in range(MAX_ROUNDS):
        run.new_round()
        walk = _Walk(cwd, 0, Touched(touched, run.reads), run=run, unseen_writes=unseen_writes)
        calls = _calls(command, walk)
        settled = frozenset(walk.touched), walk.unseen_writes
        if not run.skipped or settled == (touched, unseen_writes):
            return calls
        touched, unseen_writes = settled
    raise UnreadableCommit(UNSETTLED)


_RESULTS: dict[tuple[object, str], tuple[GitCall, ...] | UnreadableCommit] = {}


def _calls(command: str | list[str], walk: _Walk) -> list[GitCall]:
    if walk.depth > MAX_NESTING:
        raise UnreadableCommit(TOO_DEEP)
    if isinstance(command, str):
        segments, walk.declarations = _prepared(command)
    else:
        segments = tuple(tuple(segment) for segment in _segments(command))
    for segment in segments:
        walk.step(list(segment))
    return walk.calls


@lru_cache(maxsize=4096)
def _prepared(command: str) -> tuple[tuple[tuple[str, ...], ...], dict[str, Function]]:
    text, declarations = extract_functions(command)
    return tuple(tuple(segment) for segment in _segments(text)), declarations


@lru_cache(maxsize=4096)
def _bound(body: str, args: tuple[str, ...]) -> str:
    return with_positional(body, list(args), known=True)


@dataclass(slots=True)
class _Walk:
    cwd: Path
    depth: int
    touched: Touched
    groups: list[_Walk] = field(default_factory=list)
    pushed: list[Path] = field(default_factory=list)
    calls: list[GitCall] = field(default_factory=list)
    previous: Command | None = None
    feed: Command | None = None
    fds: dict[int, Input] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    functions: dict[str, Function] = field(default_factory=dict)
    exported_functions: set[str] = field(default_factory=set)
    alternates: dict[str, tuple[Function, ...]] = field(default_factory=dict)
    declarations: dict[str, Function] = field(default_factory=dict)
    function_stack: tuple[str, ...] = ()
    local_variables: set[str] = field(default_factory=set)
    relocated: str | None = None
    commit_variable: bool = False
    inspected_command: bool = False
    unseen_writes: bool = False
    shell: str = ""
    physical: bool = False
    fds_replaced: bool = False
    run: WalkRun = field(default_factory=WalkRun)

    def step(self, segment: list[str]) -> None:
        if segment in (["("], [")"]):
            self._group(segment[0])
            return
        if len(segment) == 1 and segment[0] in SEPARATORS:
            self.feed = self.previous if segment[0] in PIPES else None
            return
        if not segment:
            return
        self.run.tick()
        prior_writes = self.unseen_writes or bool(self.touched)
        self.inspected_command = False
        command, declaration, nonwriting = self._read(segment)
        if declaration is not None:
            self._define(declaration)
        else:
            self._command(command, nonwriting)
        skip_operands = nonwriting or declaration is not None or reads_stdin_operands(segment, self.cwd)
        self._note_writes(command, skip_operands, prior_writes)
        self.previous, self.feed = command, None

    def _group(self, token: str) -> None:
        if token == "(":
            snapshot = self.nested()
            snapshot.local_variables = set(self.local_variables)
            snapshot.fds = dict(self.fds)
            self.groups.append(snapshot)
            return
        if not self.groups:
            return
        snapshot = self.groups.pop()
        self.cwd, self.variables = snapshot.cwd, snapshot.variables
        self.functions, self.aliases = snapshot.functions, snapshot.aliases
        self.exported_functions = snapshot.exported_functions
        self.alternates = snapshot.alternates
        self.local_variables, self.fds = snapshot.local_variables, snapshot.fds
        self.relocated, self.commit_variable = snapshot.relocated, snapshot.commit_variable
        self.physical = snapshot.physical

    def _define(self, declaration: Function) -> None:
        name = declaration.name
        known = self.alternates.get(name) or ((self.functions[name],) if name in self.functions else ())
        if known and all(function.body != declaration.body for function in known):
            self.alternates[name] = (*known, declaration)
        self.functions[name] = declaration

    def _read(self, segment: list[str]) -> tuple[Command, Function | None, bool]:
        words, redirects = split_redirects(_command_words(segment))
        index = _payload_command_index(words)
        declaration = self.declarations.get(words[index]) if index < len(words) else None
        inputs = dict(self.fds)
        for redirect in redirects:
            _bind_input(inputs, redirect._replace(target=_expanded(redirect.target, self.variables)))
        outputs = [_expanded(redirect.target, self.variables) for redirect in redirects if redirect.operator in OUTPUT_OPERATORS]
        nonwriting = (
            not redirects and index < len(words) and words[index] in {"shift", "break", "continue", "getopts", "wait"}
            and all(_is_assignment(word) for word in words[:index])
        )
        if not redirects and not (self.unseen_writes or self.touched or self.run.git_configured):
            nonwriting = nonwriting or read_only_git_metadata(words, self.variables)
        return Command(_expanded_words(words, self.variables), inputs, outputs), declaration, nonwriting

    def _command(self, command: Command, nonwriting: bool) -> None:
        words = command.words
        index = _payload_command_index(words)
        verb = words[index] if index < len(words) else ""
        if verb in self.aliases:
            self._expand_alias(verb, words[index + 1:])
            self.inspected_command = True
            return
        if verb in self.functions and all(_is_assignment(word) for word in words[:index]):
            self._invoke_function(verb, command)
            self.inspected_command = True
            return
        if verb in DIRECTORY_VERBS:
            self._change_directory(verb, words[index + 1:])
            return
        if verb == "alias":
            self.aliases.update(_alias_definitions(words[index + 1:]))
            return
        if words == ["exec"]:
            self.fds, self.fds_replaced = command.inputs, True
            return
        self._run(command, nonwriting)

    def _run(self, command: Command, nonwriting: bool) -> None:
        self._export_functions(command.words)
        self.physical = _physical_mode(command.words, self.physical)
        assigned = _assignments(command.words)
        self.variables.update(assigned)
        if command.words[:1] == ["local"]:
            self.local_variables.update(word.partition("=")[0] for word in command.words[1:])
        if any(name in GIT_LOCATION_ENV_VARS for name, _value in assigned):
            self.relocated = LOCATION_OVERRIDE
        elif any(name == "GIT_INDEX_FILE" for name, _value in assigned):
            self.relocated = self.relocated or DYNAMIC_INDEX
        self.commit_variable = self.commit_variable or any("commit" in value for _name, value in assigned)
        found = _segment_calls(command, self)
        self.unseen_writes = self.unseen_writes or not (self.inspected_command or nonwriting or _file_neutral(command.words))
        if self.relocated and any(call.verb == "commit" for call in found):
            raise UnreadableCommit(self.relocated)
        self.calls.extend(found)

    def _note_writes(self, command: Command, skip_operands: bool, prior_writes: bool) -> None:
        if command.words[:1] == ["export"]:
            for name in command.words[1:]:
                if "=" not in name and git_environment(name):
                    self.variables.setdefault(name, f"${name}")
        self.run.git_configured |= config_command(command.words)
        written = [*command.outputs]
        if not self.inspected_command and not skip_operands and _changes_files(command.words):
            written.extend(operand_writes(command.words, self.cwd, self.variables, self.run.git_configured, prior_writes))
        keys = [key for word in written if word for key in write_keys(self.cwd, word)]
        self.run.git_configured |= any(config_path(key) for key in keys)
        self.touched.update(keys)

    def nested(self, shell_depth: int = 0) -> _Walk:
        return _Walk(
            self.cwd, self.depth + shell_depth, self.touched,
            variables=dict(self.variables), aliases=dict(self.aliases), functions=dict(self.functions),
            exported_functions=set(self.exported_functions), alternates=dict(self.alternates),
            function_stack=self.function_stack, relocated=self.relocated, commit_variable=self.commit_variable,
            unseen_writes=self.unseen_writes, shell=self.shell, physical=self.physical, run=self.run,
        )

    def absorb(self, child: _Walk, function: bool = False) -> None:
        self.cwd = child.cwd
        self.functions = child.functions
        self.aliases = child.aliases
        self.exported_functions = child.exported_functions
        self.alternates = child.alternates
        self.variables.update((name, value) for name, value in child.variables.items()
                              if not function or name not in child.local_variables)
        if not function:
            self.local_variables.update(child.local_variables)
        self.relocated = self.relocated or child.relocated
        self.commit_variable = self.commit_variable or child.commit_variable
        self.unseen_writes = self.unseen_writes or child.unseen_writes
        self.physical = child.physical
        if child.fds_replaced:
            self.fds, self.fds_replaced = child.fds, True

    def shell_child(self, shared: bool) -> _Walk:
        child = self.nested(shell_depth=1)
        if not shared:
            child.variables = {}
            child.aliases = {}
            child.functions = {name: function for name, function in self.functions.items() if name in self.exported_functions}
            child.alternates = {name: bodies for name, bodies in self.alternates.items() if name in self.exported_functions}
            child.function_stack = ()
        return child

    def _export_functions(self, words: list[str]) -> None:
        if not words or words[0] not in {"export", "declare", "typeset"}:
            return
        flags = list(takewhile(lambda word: word.startswith("-"), words[1:]))
        exporting = words[0] == "export" or any("x" in flag for flag in flags)
        if not exporting or not any("f" in flag for flag in flags):
            return
        names = words[len(flags) + 1:]
        if any("n" in flag for flag in flags):
            self.exported_functions.difference_update(names)
            return
        self.exported_functions.update(name for name in names if name in self.functions)

    def _invoke_function(self, verb: str, command: Command) -> None:
        if verb in self.function_stack or len(self.function_stack) >= MAX_FUNCTION_DEPTH:
            raise UnreadableCommit(TOO_DEEP)
        index = _payload_command_index(command.words)
        definitions = self.alternates.get(verb, (self.functions[verb],))
        children = [self._function_walk(function, command, command.words[index + 1:]) for function in definitions]
        if any(_left_state(child) != _left_state(children[0]) for child in children[1:]):
            raise UnreadableCommit(AMBIGUOUS_FUNCTION)
        changes = [_variable_changes(child, self.variables) for child in children]
        unknown = {name for change in changes for name in change if any(other.get(name, UNSET) != change[name] for other in changes)}
        found = [call for child in children for call in child.calls]
        if any(call.verb == "commit" for call in found):
            _refuse_location_override(command.words[:index])
        self.calls.extend(found)
        self.unseen_writes = self.unseen_writes or any(child.unseen_writes for child in children)
        self.absorb(children[-1], function=True)
        self.commit_variable = self.commit_variable or any(child.commit_variable for child in children)
        for name in unknown:
            self.variables.pop(name, None)

    def _function_walk(self, function: Function, command: Command, args: list[str]) -> _Walk:
        key = _function_key(self, function, command.inputs, args)
        cached = self.run.function_walks.get(key)
        if cached is not None:
            self.touched.update(cached[1])
            return _copied(cached[0])
        child = self.nested()
        child.function_stack = (*self.function_stack, function.name)
        child.fds = command.inputs
        reads, visits, writes = len(self.run.reads), self.run.visits, len(self.touched.writes)
        _calls(_bound(function.body, tuple(args)), child)
        if len(self.run.reads) == reads and self.run.visits == visits:
            self.run.function_walks[key] = (_copied(child), frozenset(self.touched.writes[writes:]))
        return child

    def _expand_alias(self, verb: str, args: list[str]) -> None:
        value = self.aliases.pop(verb)
        try:
            for segment in _segments(f"{value} {shlex.join(args)}"):
                self.step(segment)
        finally:
            self.aliases[verb] = value

    def _change_directory(self, verb: str, args: list[str]) -> None:
        if verb == "popd":
            self.cwd = self.pushed.pop() if self.pushed else self.cwd
            return
        if verb == "pushd":
            self.pushed.append(self.cwd)
        self.cwd = anchored(args[-1], self.cwd) if args else self.cwd


def _segment_calls(command: Command, walk: _Walk) -> list[GitCall]:
    words = command.words
    payload = _shell_input(command, _scope(walk, walk.feed), walk.run)
    walk.run.check_clock()
    if payload is not None:
        return _shell_calls(payload, command, walk)
    call = _git_call(words, walk.cwd)
    if call is None:
        _refuse_hidden_commit(words, walk.commit_variable)
        _refuse_runner_shell_commit(command, walk)
        return []
    nested = (found for payload in _git_payloads(call) for found in _calls(payload, _process_child(walk, "sh", shared=True)))
    if any(found.verb == "commit" for found in nested):
        raise UnreadableCommit(HIDDEN_COMMIT)
    if call.index_file is not None and (walk.unseen_writes or walk.touched or command.outputs):
        raise UnreadableCommit("Run the index write separately so the commit gate can scan the final index.")
    source = command.inputs.get(0)
    return [call._replace(stdin=source.text if source is not None else None)]


def _refuse_runner_shell_commit(command: Command, walk: _Walk) -> None:
    index = _payload_command_index(command.words)
    words = command.words[index:]
    if not words or _basename(words[0]) not in COMMAND_RUNNERS:
        return
    for offset, word in enumerate(words[1:], start=1):
        if _basename(word) not in SHELL_C_INTERPRETERS:
            continue
        payload = _shell_input(command._replace(words=words[offset:]), _scope(walk, None), walk.run)
        walk.run.check_clock()
        if payload is None:
            continue
        child = _process_child(walk, _basename(word))
        found = _process_calls(payload, child, command.words[:index])
        walk.unseen_writes = walk.unseen_writes or child.unseen_writes
        if any(call.verb == "commit" for call in found):
            raise UnreadableCommit(HIDDEN_COMMIT)


def _shell_calls(payload: str, command: Command, walk: _Walk) -> list[GitCall]:
    walk.inspected_command = True
    if walk.commit_variable and is_dynamic(next(iter(payload.split()), "")):
        raise UnreadableCommit(DYNAMIC_PROGRAM)
    words = command.words
    index = _payload_command_index(words)
    shared = index < len(words) and words[index] in {*SCRIPT_READERS, "eval", "trap"}
    child = walk.shell_child(shared) if shared else _process_child(walk, _process_shell(words[index], walk.cwd))
    found = _calls(payload, child) if shared else _process_calls(payload, child, words[:index])
    walk.unseen_writes = walk.unseen_writes or child.unseen_writes
    if shared and words[index] != "trap":
        walk.absorb(child)
    commits = any(call.verb == "commit" for call in found)
    if commits and words[index] == "trap":
        raise UnreadableCommit(HIDDEN_COMMIT)
    if commits:
        _refuse_location_override(words[:index])
    return found


def _process_child(walk: _Walk, shell: str, shared: bool = False) -> _Walk:
    child = walk.shell_child(shared)
    child.shell = shell
    child.physical = child.physical and shared
    return child


def _shell_input(command: Command, scope: InputScope, run: WalkRun) -> str | None:
    key = (_command_key(command), _command_key(scope.feed), scope.cwd, scope.script_kind, scope.unseen_writes, scope.shell, scope.context)
    text, _ = run.payload(key, scope.touched, lambda: shell_input(command, scope))
    return text


def _scope(walk: _Walk, feed: Command | None) -> InputScope:
    context = PHYSICAL_CONTEXT if walk.physical else ""
    return InputScope(walk.cwd, feed, walk.touched, unseen_writes=walk.unseen_writes, shell=walk.shell, context=context)


def _process_shell(word: str, cwd: Path) -> str:
    name = _basename(word)
    if name in SHELL_C_INTERPRETERS:
        return name
    return shebang_shell(anchored(word, cwd)) if "/" in word else ""


def _process_calls(payload: str, child: _Walk, prefix: list[str]) -> list[GitCall]:
    visited = child.run.visit(
        _process_key(payload, child), child.touched, lambda: (_calls(payload, child), child.unseen_writes),
        _location_override(prefix) is None,
    )
    child.unseen_writes = visited.unseen_writes
    return list(visited.calls)


def _copied(walk: _Walk) -> _Walk:
    copy = walk.nested()
    copy.local_variables = set(walk.local_variables)
    copy.fds, copy.fds_replaced = dict(walk.fds), walk.fds_replaced
    copy.calls = list(walk.calls)
    return copy
