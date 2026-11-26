from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

try:
    from . import git_port
    from .commit_command_words import DYNAMIC_PROGRAM, _payload_command_index
    from .commit_inputs import PLAIN_COMMIT, UnreadableCommit, is_dynamic
    from .commit_segments import is_assignment as _is_assignment
    from .path_policy import anchored
    from .shell_syntax import WRAPPER_COMMANDS, _basename
except ImportError:
    import git_port
    from commit_command_words import DYNAMIC_PROGRAM, _payload_command_index
    from commit_inputs import PLAIN_COMMIT, UnreadableCommit, is_dynamic
    from commit_segments import is_assignment as _is_assignment
    from path_policy import anchored
    from shell_syntax import WRAPPER_COMMANDS, _basename

GIT_TIMEOUT_SECONDS = 30
MAX_ALIAS_DEPTH = 8
SHELL_ALIAS = "!"
GIT_VALUE_FLAGS = frozenset({"-c", "--namespace", "--config-env"})
PATH_FLAGS = frozenset({"-C", "--work-tree", "--git-dir"})
GIT_LOCATION_ENV_VARS = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"})
CONFIG_ENV_PREFIXES = ("GIT_", "HOME", "XDG_CONFIG_HOME")
CHDIR_FLAGS = {"env": ("-C", "--chdir"), "sudo": ("-D", "--chdir")}
BUILTIN_VERBS = frozenset(["add", "am", "annotate", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file", "check-attr", "check-ignore", "checkout", "cherry", "cherry-pick", "clean", "clone", "commit", "commit-tree", "config", "count-objects", "describe", "diff", "diff-files", "diff-index", "diff-tree", "difftool", "fetch", "for-each-ref", "format-patch", "fsck", "gc", "grep", "hash-object", "help", "init", "log", "ls-files", "ls-remote", "ls-tree", "maintenance", "merge", "merge-base", "mv", "notes", "prune", "pull", "push", "range-diff", "read-tree", "rebase", "reflog", "remote", "repack", "replace", "rerere", "reset", "restore", "rev-list", "rev-parse", "revert", "rm", "shortlog", "show", "show-ref", "sparse-checkout", "stage", "stash", "status", "submodule", "switch", "symbolic-ref", "tag", "update-index", "update-ref", "var", "version", "whatchanged", "worktree", "write-tree"])
LOCATION_OVERRIDE = (
    "GIT_DIR, GIT_WORK_TREE, GIT_COMMON_DIR, or GIT_OBJECT_DIRECTORY changes the repository, "
    "so the commit gate cannot tell which index it records. Commit without them."
)
DYNAMIC_INDEX = "The commit gate cannot establish which GIT_INDEX_FILE this command uses. Set a literal index path directly on git commit."
DIRECTORY_MOVE = "env -C or sudo -D moves the commit into another directory. Run cd in its own step, then commit."
DYNAMIC_DIRECTORY = f"This command moves into a folder named by a variable or substitution, so the commit gate cannot tell which repository commits. {PLAIN_COMMIT}"


class GitCall(NamedTuple):
    cwd: Path
    verb: str
    args: list[str]
    stdin: str | None = None
    index_file: Path | None = None


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
        indexes = [word.partition("=")[2] for word in prefix if word.startswith("GIT_INDEX_FILE=")]
        _refuse_location_override([word for word in prefix if not word.startswith("GIT_INDEX_FILE=")])
        if indexes:
            value = indexes[-1]
            literal = value and not any(char in value for char in "$`~{}*?[]()\\")
            direct = all(_is_assignment(word) or word == "env" for word in prefix)
            relocated = any(word.partition("=")[0] in {"--git-dir", "--work-tree"} for word in words[index + 1:])
            if not literal or not direct or relocated or len(indexes) != 1:
                raise UnreadableCommit(DYNAMIC_INDEX)
            call = call._replace(index_file=call.cwd / value)
    return call


def _resolved(git_words: list[str], context: _GitContext) -> GitCall | None:
    located = _located_verb(git_words, context.cwd)
    if located is None:
        return None
    location, cursor = located
    verb, args, flags = git_words[cursor], git_words[cursor + 1:], git_words[1:cursor]
    if is_dynamic(verb):
        raise UnreadableCommit(DYNAMIC_PROGRAM)
    if verb == "commit" or verb not in BUILTIN_VERBS:
        _require_folder(location)
        if verb not in BUILTIN_VERBS and location != context.cwd:
            _require_folder(context.cwd)
    alias = None if verb in BUILTIN_VERBS else _alias(verb, flags, context)
    if alias is None:
        return GitCall(location, verb, args)
    if alias.startswith("!"):
        return GitCall(location, _shell_alias(verb, alias), args)
    if context.depth >= MAX_ALIAS_DEPTH:
        raise UnreadableCommit(f"git {verb} expands through more aliases than the commit gate follows. {PLAIN_COMMIT}")
    return _resolved(["git", *flags, *_alias_words(verb, alias), *args], replace(context, depth=context.depth + 1))


def _require_folder(folder: Path) -> None:
    if is_dynamic(str(folder)):
        raise UnreadableCommit(DYNAMIC_DIRECTORY)
    if not folder.is_dir():
        if folder.exists():
            raise UnreadableCommit(f"The path {folder} is not a folder.")
        raise UnreadableCommit(f"The commit folder {folder} does not exist. Create it before running the commit.")


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
        return git_port.view().config_value(context.cwd, f"alias.{verb}", flags=flags, env=_alias_env(verb, context.prefix))
    except (OSError, subprocess.SubprocessError) as exc:
        raise UnreadableCommit(f"The commit gate could not check whether git {verb} is an alias: {exc}. {PLAIN_COMMIT}") from exc


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
    if any(word.startswith("GIT_INDEX_FILE=") for word in prefix):
        return DYNAMIC_INDEX
    return DIRECTORY_MOVE if _moves_directory(prefix) else None


def _moves_directory(prefix: list[str]) -> bool:
    wrapper = ""
    for token in prefix:
        wrapper = _basename(token) if _basename(token) in WRAPPER_COMMANDS else wrapper
        if token.startswith(CHDIR_FLAGS.get(wrapper, ())):
            return True
    return False
