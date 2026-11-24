from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field, replace
from functools import lru_cache
from itertools import takewhile
from pathlib import Path
from typing import NamedTuple

from .commit_functions import Function, extract_functions, with_positional
from .commit_inputs import (
    DYNAMIC_SCRIPT,
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
from .commit_writes import changes_files as _changes_files
from .path_policy import anchored
from .script_files import shebang_shell
from .shell_operators import SEPARATORS
from .shell_syntax import SHELL_C_INTERPRETERS, WRAPPER_COMMANDS, _basename
from .shell_syntax import _payload_command_index as _command_index
from .shell_text import heredoc_value, split_redirects

__all__ = ["SHELL_ALIAS", "GitCall", "UnreadableCommit", "git_calls"]

GIT_TIMEOUT_SECONDS = 30
MAX_NESTING = 10
MAX_ALIAS_DEPTH = 8
MAX_FUNCTION_DEPTH = 32
SHELL_ALIAS = "!"
PIPES = frozenset({"|", "|&"})
DIRECTORY_VERBS = frozenset({"cd", "pushd", "popd"})
FILE_NEUTRAL_VERBS = frozenset({
    "[", "[[", "test", "true", "false", ":", "echo", "printf", "pwd", "ls", "cat", "grep", "rg", "head", "tail", "wc",
    "type", "which", "export", "local", "readonly", "declare", "typeset", "set", "shopt", "unset", "exit", "return",
    "read", "sleep", "date", "uname", "id", "whoami", "fi", "done", "esac", "}", "then", "else",
})
OUTPUT_OPERATORS = frozenset({">", ">>", ">|", "<>"})
GIT_VALUE_FLAGS = frozenset({"-c", "--namespace", "--config-env"})
PATH_FLAGS = frozenset({"-C", "--work-tree", "--git-dir"})
GIT_LOCATION_ENV_VARS = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"})
DECLARERS = frozenset({"export", "declare", "typeset", "local", "readonly"})
CONFIG_ENV_PREFIXES = ("GIT_", "HOME", "XDG_CONFIG_HOME")
CHDIR_FLAGS = {"env": ("-C", "--chdir"), "sudo": ("-D", "--chdir")}
COMMAND_RUNNERS = frozenset({
    "caffeinate", "doas", "find", "flock", "ionice", "parallel", "setsid", "stdbuf", "unbuffer", "watch", "xargs",
})
BUILTIN_VERBS = frozenset(["add", "am", "annotate", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file", "check-attr", "check-ignore", "checkout", "cherry", "cherry-pick", "clean", "clone", "commit", "commit-tree", "config", "count-objects", "describe", "diff", "diff-files", "diff-index", "diff-tree", "difftool", "fetch", "for-each-ref", "format-patch", "fsck", "gc", "grep", "hash-object", "help", "init", "log", "ls-files", "ls-remote", "ls-tree", "maintenance", "merge", "merge-base", "mv", "notes", "prune", "pull", "push", "range-diff", "read-tree", "rebase", "reflog", "remote", "repack", "replace", "rerere", "reset", "restore", "rev-list", "rev-parse", "revert", "rm", "shortlog", "show", "show-ref", "sparse-checkout", "stage", "stash", "status", "submodule", "switch", "symbolic-ref", "tag", "update-index", "update-ref", "var", "version", "whatchanged", "worktree", "write-tree"])
POSITIONAL_RE = re.compile(r"\$(?:[@*]|\d|\{(?:[@*]|\d+)[^}]*\})")
VARIABLE_RE = re.compile(r"\$(?:\{(\w+)\}|(\w+))")

TOO_DEEP = f"This command nests shells or scripts deeper than the commit gate reads. {PLAIN_COMMIT}"
HIDDEN_COMMIT = f"This command runs git commit through another program, so the commit gate cannot scan it. {PLAIN_COMMIT}"
DYNAMIC_PROGRAM = f"This command takes its program or git subcommand from a variable or substitution, so the commit gate cannot see whether it commits. {PLAIN_COMMIT}"
LOCATION_OVERRIDE = (
    "GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, or another git location variable moves the commit's repository or index, "
    "so the commit gate cannot tell which index it records. Commit without them."
)
DIRECTORY_MOVE = "env -C or sudo -D moves the commit into another directory. Run cd in its own step, then commit."
DYNAMIC_DIRECTORY = f"This command moves into a folder named by a variable or substitution, so the commit gate cannot tell which repository commits. {PLAIN_COMMIT}"
PHYSICAL_CONTEXT = "set -P"
UNSET = "\0unset"
AMBIGUOUS_FUNCTION = f"This function has different readable definitions, so the commit gate cannot resolve its call. {PLAIN_COMMIT}"


class GitCall(NamedTuple):
    cwd: Path
    verb: str
    args: list[str]
    stdin: str | None = None


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


def _payload_command_index(words: list[str]) -> int:
    return _cached_index(tuple(words))


@lru_cache(maxsize=65536)
def _cached_index(words: tuple[str, ...]) -> int:
    return _command_index(list(words))


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
    relocated: bool = False
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
        self.inspected_command = False
        command, declaration = self._read(segment)
        if declaration is not None:
            self._define(declaration)
        else:
            self._command(command)
        self._note_writes(command, declaration is not None)
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

    def _read(self, segment: list[str]) -> tuple[Command, Function | None]:
        words, redirects = split_redirects(_command_words(segment))
        index = _payload_command_index(words)
        declaration = self.declarations.get(words[index]) if index < len(words) else None
        inputs = dict(self.fds)
        for redirect in redirects:
            _bind_input(inputs, redirect._replace(target=_expanded(redirect.target, self.variables)))
        outputs = [_expanded(redirect.target, self.variables) for redirect in redirects if redirect.operator in OUTPUT_OPERATORS]
        return Command(_expanded_words(words, self.variables), inputs, outputs), declaration

    def _command(self, command: Command) -> None:
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
        self._run(command)

    def _run(self, command: Command) -> None:
        self._export_functions(command.words)
        self.physical = _physical_mode(command.words, self.physical)
        assigned = _assignments(command.words)
        self.variables.update(assigned)
        if command.words[:1] == ["local"]:
            self.local_variables.update(word.partition("=")[0] for word in command.words[1:])
        self.relocated = self.relocated or any(name in GIT_LOCATION_ENV_VARS for name, _value in assigned)
        self.commit_variable = self.commit_variable or any("commit" in value for _name, value in assigned)
        found = _segment_calls(command, self)
        self.unseen_writes = self.unseen_writes or not (self.inspected_command or _file_neutral(command.words))
        if self.relocated and any(call.verb == "commit" for call in found):
            raise UnreadableCommit(LOCATION_OVERRIDE)
        self.calls.extend(found)

    def _note_writes(self, command: Command, declaration: bool = False) -> None:
        written = [*command.outputs]
        if not self.inspected_command and not declaration and _changes_files(command.words):
            arguments = command.words[_payload_command_index(command.words) + 1:]
            written.extend(word for word in arguments if not word.startswith("-"))
        self.touched.update(normalized(self.cwd, word) for word in written if word)

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
    text, reads = run.payload(key, scope.touched, lambda: shell_input(command, scope))
    if any(is_dynamic(read) for read in reads):
        raise UnreadableCommit(DYNAMIC_SCRIPT)
    return text


def _command_key(command: Command | None) -> tuple[object, ...] | None:
    if command is None:
        return None
    return tuple(command.words), frozenset(command.inputs.items()), tuple(command.outputs)


def _scope(walk: _Walk, feed: Command | None) -> InputScope:
    context = PHYSICAL_CONTEXT if walk.physical else ""
    return InputScope(walk.cwd, feed, walk.touched, unseen_writes=walk.unseen_writes, shell=walk.shell, context=context)


def _physical_mode(words: list[str], physical: bool) -> bool:
    if words[:1] != ["set"]:
        return physical
    for index, word in enumerate(words[1:], start=1):
        option = words[index + 1] if index + 1 < len(words) else ""
        if word in {"-o", "+o"} and option == "physical":
            physical = word == "-o"
        elif word[:1] in {"-", "+"} and not word.startswith(("--", "++")) and "P" in word:
            physical = word.startswith("-")
    return physical


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


def _process_key(payload: str, child: _Walk) -> tuple[object, ...]:
    functions = tuple(sorted((name, function.body) for name, function in child.functions.items()))
    alternates = tuple(sorted((name, tuple(function.body for function in bodies)) for name, bodies in child.alternates.items()))
    return (
        payload, str(child.cwd), child.shell, functions, alternates, tuple(sorted(child.exported_functions)),
        child.relocated, child.commit_variable, child.unseen_writes,
    )


def _function_key(walk: _Walk, function: Function, inputs: dict[int, Input], args: list[str]) -> tuple[object, ...]:
    return (
        function, tuple(args), walk.cwd, frozenset(walk.variables.items()), frozenset(walk.functions.items()),
        frozenset(walk.aliases.items()), frozenset(walk.exported_functions), frozenset(walk.alternates.items()),
        walk.function_stack, walk.depth, walk.relocated, walk.commit_variable, walk.unseen_writes, walk.physical,
        walk.shell, frozenset(inputs.items()),
    )


def _copied(walk: _Walk) -> _Walk:
    copy = walk.nested()
    copy.local_variables = set(walk.local_variables)
    copy.fds, copy.fds_replaced = dict(walk.fds), walk.fds_replaced
    copy.calls = list(walk.calls)
    return copy


def _left_state(walk: _Walk) -> tuple[object, ...]:
    return (
        walk.cwd, walk.functions, walk.aliases, walk.exported_functions, walk.alternates,
        walk.relocated, walk.physical, walk.fds if walk.fds_replaced else None,
    )


def _variable_changes(walk: _Walk, entry: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in walk.variables.items()
            if name not in walk.local_variables and entry.get(name) != value}


def _file_neutral(words: list[str]) -> bool:
    index = _payload_command_index(words)
    return index >= len(words) or words[index] in FILE_NEUTRAL_VERBS


def _expanded(word: str, variables: dict[str, str]) -> str:
    heredoc = heredoc_value(word)
    if heredoc is not None:
        return heredoc
    return VARIABLE_RE.sub(lambda match: variables.get(match.group(1) or match.group(2), match.group()), word)


def _expanded_words(words: list[str], variables: dict[str, str]) -> list[str]:
    expanded = [_expanded(word, variables) for word in words]
    index = _payload_command_index(expanded)
    if index < len(expanded) and expanded[index] != words[index] and " " in expanded[index]:
        return [*expanded[:index], *expanded[index].split(), *expanded[index + 1:]]
    return expanded


def _git_payloads(call: GitCall) -> list[str]:
    if call.verb == "rebase":
        return [value for index, arg in enumerate(call.args) if (value := _exec_value(call.args, index, arg)) is not None]
    runner = {"submodule": "foreach", "bisect": "run"}.get(call.verb)
    if runner not in call.args:
        return []
    return [" ".join(arg for arg in call.args[call.args.index(runner) + 1:] if not arg.startswith("-"))]


def _exec_value(args: list[str], index: int, arg: str) -> str | None:
    name, separator, inline = arg.partition("=")
    if separator and len(name) > 3 and "--exec".startswith(name):
        return inline
    if arg == "-x" or (len(arg) > 3 and "--exec".startswith(arg)):
        return args[index + 1] if index + 1 < len(args) else ""
    return arg[2:] if arg.startswith("-x") and len(arg) > 2 else None


@dataclass(frozen=True, slots=True)
class _GitContext:
    cwd: Path
    prefix: list[str]
    depth: int = 0


def _git_call(words: list[str], cwd: Path) -> GitCall | None:
    index = _payload_command_index(words)
    if index >= len(words) or _basename(words[index]) != "git":
        return None
    prefix = words[:index]
    call = _resolved(words[index:], _GitContext(cwd, prefix))
    if call is not None and call.verb == "commit":
        _refuse_location_override(prefix)
        if is_dynamic(str(call.cwd)):
            raise UnreadableCommit(DYNAMIC_DIRECTORY)
    return call


def _resolved(git_words: list[str], context: _GitContext) -> GitCall | None:
    located = _located_verb(git_words, context.cwd)
    if located is None:
        return None
    location, cursor = located
    verb, args, flags = git_words[cursor], git_words[cursor + 1:], git_words[1:cursor]
    if is_dynamic(verb):
        raise UnreadableCommit(DYNAMIC_PROGRAM)
    alias = None if verb in BUILTIN_VERBS else _alias(verb, flags, context)
    if alias is None:
        return GitCall(location, verb, args)
    if alias.startswith("!"):
        return GitCall(location, _shell_alias(verb, alias), args)
    if context.depth >= MAX_ALIAS_DEPTH:
        raise UnreadableCommit(f"git {verb} expands through more aliases than the commit gate follows. {PLAIN_COMMIT}")
    return _resolved(["git", *flags, *_alias_words(verb, alias), *args], replace(context, depth=context.depth + 1))


def _located_verb(git_words: list[str], cwd: Path) -> tuple[Path, int] | None:
    current, pinned, cursor = cwd, False, 1
    while cursor < len(git_words) and git_words[cursor].startswith("-"):
        step = _skip_git_flag(git_words, cursor, current)
        if step is None:
            return None
        cursor, candidate, pins = step
        current = candidate if pins or not pinned else current
        pinned = pinned or pins
    return (current, cursor) if cursor < len(git_words) else None


def _skip_git_flag(words: list[str], cursor: int, current: Path) -> tuple[int, Path, bool] | None:
    token = words[cursor]
    if token.startswith("-C") and len(token) > 2:
        return cursor + 1, anchored(token[2:], current), False
    name, separator, inline = token.partition("=")
    if separator and name in PATH_FLAGS:
        return cursor + 1, _flag_cwd(name, current, inline), name == "--work-tree"
    if token in PATH_FLAGS:
        if cursor + 1 >= len(words):
            return None
        return cursor + 2, _flag_cwd(token, current, words[cursor + 1]), token == "--work-tree"
    if token in GIT_VALUE_FLAGS:
        return cursor + 2, current, False
    return cursor + 1, current, False


def _flag_cwd(flag: str, current: Path, value: str) -> Path:
    resolved = anchored(value, current)
    if flag == "--git-dir" and resolved.name == ".git":
        return resolved.parent
    return resolved


def _alias(verb: str, flags: list[str], context: _GitContext) -> str | None:
    try:
        done = subprocess.run(
            ["git", *flags, "config", "--get", f"alias.{verb}"], cwd=context.cwd, env=_alias_env(verb, context.prefix),
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UnreadableCommit(f"The commit gate could not check whether git {verb} is an alias: {exc}. {PLAIN_COMMIT}") from exc
    return done.stdout.strip() if done.returncode == 0 else None


def _alias_env(verb: str, prefix: list[str]) -> dict[str, str]:
    assigned = dict(token.split("=", 1) for token in prefix if _is_assignment(token))
    if any(name.startswith(CONFIG_ENV_PREFIXES) and is_dynamic(value) for name, value in assigned.items()):
        raise UnreadableCommit(f"This command sets git configuration from a shell variable, so the commit gate cannot resolve git {verb}. {PLAIN_COMMIT}")
    return {**os.environ, **assigned}


def _shell_alias(verb: str, alias: str) -> str:
    if "commit" in alias:
        raise UnreadableCommit(f"git {verb} is a shell alias that runs commit, so the commit gate cannot read it. {PLAIN_COMMIT}")
    return SHELL_ALIAS


def _alias_words(verb: str, alias: str) -> list[str]:
    try:
        return shlex.split(alias)
    except ValueError as exc:
        raise UnreadableCommit(f"The commit gate cannot read the quoting of the git {verb} alias. {PLAIN_COMMIT}") from exc


def _refuse_location_override(prefix: list[str]) -> None:
    reason = _location_override(prefix)
    if reason is not None:
        raise UnreadableCommit(reason)


def _location_override(prefix: list[str]) -> str | None:
    if any(token.partition("=")[0] in GIT_LOCATION_ENV_VARS for token in prefix if _is_assignment(token)):
        return LOCATION_OVERRIDE
    return DIRECTORY_MOVE if _moves_directory(prefix) else None


def _moves_directory(prefix: list[str]) -> bool:
    wrapper = ""
    for token in prefix:
        wrapper = _basename(token) if _basename(token) in WRAPPER_COMMANDS else wrapper
        if token.startswith(CHDIR_FLAGS.get(wrapper, ())):
            return True
    return False


def _assignments(words: list[str]) -> list[tuple[str, str]]:
    if words[:1] and words[0] in DECLARERS:
        declared = words[1:]
    elif words and all(_is_assignment(word) for word in words):
        declared = words
    else:
        return []
    return [(name, value) for name, separator, value in (word.partition("=") for word in declared) if separator]


def _refuse_hidden_commit(words: list[str], commit_variable: bool) -> None:
    names = [_basename(word) for word in words[_payload_command_index(words):]]
    if not names:
        return
    if POSITIONAL_RE.search(words[_payload_command_index(words)]):
        raise UnreadableCommit(DYNAMIC_PROGRAM)
    if is_dynamic(names[0]) and ("commit" in names or commit_variable):
        raise UnreadableCommit(DYNAMIC_PROGRAM)
    programs = [index for index, name in enumerate(names) if name == "git" or is_dynamic(name)]
    if names[0] in COMMAND_RUNNERS and programs and "commit" in names[programs[0]:]:
        raise UnreadableCommit(HIDDEN_COMMIT)
