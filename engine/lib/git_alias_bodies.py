from __future__ import annotations

import shlex
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path

try:
    from .git_targets import _git_base
    from .shell_expansion import _repo_root
    from .shell_parse import line_segments
    from .shell_syntax import _bare, _basename, _command_word_index, _decoded_payload
except ImportError:
    from git_targets import _git_base
    from shell_expansion import _repo_root
    from shell_parse import line_segments
    from shell_syntax import _bare, _basename, _command_word_index, _decoded_payload


CHECKED_INLINE_ALIAS: ContextVar[str | None] = ContextVar("checked_inline_alias", default=None)


def inline_alias(command: str, cwd: str) -> str | None:
    if "alias." not in command.lower():
        return None
    segments = line_segments(command)
    if len(segments) != 1:
        return None
    segment = segments[0]
    if len(segment) < 4 or segment[:2] != ["git", "-c"] or command.strip() != " ".join(segment):
        return None
    assignment = _decoded_payload(segment[2])
    if not assignment:
        return None
    key, separator, body = assignment.partition("=")
    if not separator or key.lower() != "alias." + _bare(segment[3]).lower():
        return None
    if body.startswith("!") and _repo_root(cwd) != str(Path(cwd).resolve()):
        return None
    return _expanded(body, [], segment[4:])


def alias_commands(command: str) -> Iterator[str | None]:
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
            yield _expanded(body, words[:position], rest)


def _expanded(body: str | None, options: list[str], rest: list[str]) -> str | None:
    if not body:
        return None
    if body.startswith("!"):
        return None if options else " ".join([body[1:], *rest])
    try:
        words = shlex.split(body)
    except ValueError:
        return None
    return " ".join(["git", *options, shlex.join(words), *rest]) if words else None
