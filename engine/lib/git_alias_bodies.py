from __future__ import annotations

import shlex
import subprocess
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path

try:
    from . import git_port
    from .commit_git_resolution import BUILTIN_VERBS
    from .git_targets import GIT_VALUE_FLAGS, _git_base
    from .path_policy import anchored
    from .shell_expansion import _repo_root
    from .shell_parse import line_segments
    from .shell_syntax import _bare, _basename, _command_word_index, _decoded_payload
except ImportError:
    import git_port
    from commit_git_resolution import BUILTIN_VERBS
    from git_targets import GIT_VALUE_FLAGS, _git_base
    from path_policy import anchored
    from shell_expansion import _repo_root
    from shell_parse import line_segments
    from shell_syntax import _bare, _basename, _command_word_index, _decoded_payload


CHECKED_INLINE_ALIAS: ContextVar[str | None] = ContextVar("checked_inline_alias", default=None)


def inline_alias(command: str, cwd: str) -> tuple[str, str] | None:
    segments = line_segments(command)
    if len(segments) != 1 or segments[0][:1] != ["git"]:
        return None
    words = segments[0][1:]
    _, position = _git_base(list(map(_bare, words)))
    if position >= len(words):
        return None
    options, verb, rest = words[:position], _bare(words[position]), words[position + 1:]
    key = "alias." + verb.lower()
    assignments = [raw for flag, raw in _git_options(options) if flag == "-c" and _bare(raw).lower().startswith(key + "=")]
    if not assignments:
        return _directory_alias(options, verb, rest, cwd)
    assignment = _decoded_payload(assignments[-1])
    if assignment is None:
        has_directory = any(flag.startswith("-C") for flag, _ in _git_options(options))
        return None if has_directory and _alias_folder(options, cwd) is None else ("", cwd)
    return _expanded(assignment.partition("=")[2], options, rest, cwd, require_repo=True)


def _directory_alias(options: list[str], verb: str, rest: list[str], cwd: str) -> tuple[str, str] | None:
    if verb in BUILTIN_VERBS or not options or any(not flag.startswith("-C") for flag, _ in _git_options(options)):
        return None
    folder = _alias_folder(options, cwd)
    if folder is None:
        return None
    try:
        body = git_port.view().config_value(Path(folder), "alias." + verb.lower())
    except (OSError, subprocess.SubprocessError):
        return None
    return (" ".join([body[1:], *rest]), folder) if body and body.startswith("!") else None


def _git_options(options: list[str]) -> Iterator[tuple[str, str]]:
    tokens = iter(options)
    for raw in tokens:
        flag = _bare(raw)
        yield flag, next(tokens, "") if flag in GIT_VALUE_FLAGS else ""


def alias_commands(command: str, cwd: str = ".") -> Iterator[tuple[str, str] | None]:
    if "alias." not in command.lower():
        return
    definitions: dict[str, set[str | None]] = {}
    for segment in line_segments(command):
        index = _command_word_index(segment)
        if index >= len(segment) or _basename(segment[index]) != "git":
            continue
        words = segment[index + 1:]
        _, position = _git_base(list(map(_bare, words)))
        if position >= len(words):
            continue
        verb = _bare(words[position])
        rest = words[position + 1:]
        if verb == "config":
            for key, raw in zip(rest, rest[1:]):
                name = _bare(key).lower()
                if name.startswith("alias."):
                    definitions.setdefault(name[6:], set()).add(_decoded_payload(raw))
        for body in definitions.get(verb.lower(), ()):
            yield _expanded(body, words[:position], rest, cwd)


def _expanded(
    body: str | None, options: list[str], rest: list[str], cwd: str, *, require_repo: bool = False,
) -> tuple[str, str] | None:
    if not body:
        return None
    if body.startswith("!"):
        folder = _alias_folder(options, cwd, require_repo=require_repo)
        return None if folder is None else (" ".join([body[1:], *rest]), folder)
    try:
        words = shlex.split(body)
    except ValueError:
        return None
    return (" ".join(["git", *options, shlex.join(words), *rest]), cwd) if words else None


def _alias_folder(options: list[str], cwd: str, *, require_repo: bool = True) -> str | None:
    folder = Path(cwd)
    for flag, raw in _git_options(options):
        if flag.partition("=")[0] in {"--git-dir", "--work-tree"}:
            return None
        if flag.startswith("-C"):
            value = _decoded_payload(raw) if flag == "-C" else flag[2:]
            if not value or any(char in value for char in "$`"):
                return None
            folder = anchored(value, folder)
    return _repo_root(folder) or (None if require_repo else str(folder))
