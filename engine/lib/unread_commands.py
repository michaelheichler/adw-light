from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NamedTuple

try:
    from .shell_parse import _bare, line_segments
except ImportError:
    from shell_parse import _bare, line_segments

READ_COMMANDS = frozenset({
    "cat", "head", "tail", "ls", "pwd", "wc", "grep", "egrep", "fgrep", "rg", "find", "stat", "diff", "cmp",
    "shasum", "sha256sum", "md5", "md5sum", "cksum", "echo", "printf", "which", "type", "basename", "dirname",
    "realpath", "readlink", "cut", "tr", "nl", "true", "false", "test", "[", "whoami", "id",
})
GIT_READS = frozenset({"status", "diff", "log", "show", "ls-files", "ls-tree", "rev-parse", "blame", "cat-file", "describe", "shortlog", "grep"})
UNSAFE_FLAGS = {
    "find": ("-exec", "-ok", "-delete", "-fprint", "-fls"),
    "rg": ("--pre", "--hostname-bin"),
    "git": ("--output", "-O", "--open-files-in-pager", "--ext-diff", "--textconv", "--filters"),
}
RESTORE_FLAGS = frozenset({"-W", "--worktree", "-S", "--staged", "-q", "--quiet"})
REMOVE_FLAGS = frozenset({"-f"})
MODE_RE = re.compile(r"^(?:[0-7]{3,4}|[ugoa]*[-+=][rwx]+)$")
BY_GIT = "git"
BY_RM = "rm"
BY_CHMOD = "chmod"
QUIET_REDIRECT_RE = re.compile(r"(?:\d|&)?>>?\s*/dev/null|\d>&\d")
UNSAFE_TEXT_RE = re.compile(r"[<>`$\\&]")
GLOB_CHARS = frozenset("*?[~")


class Restore(NamedTuple):
    path: str
    kind: str


class Reading(NamedTuple):
    known: bool
    restores: tuple[Restore, ...]


def classify(command: str, cwd: Path) -> Reading:
    text = QUIET_REDIRECT_RE.sub(" ", command).replace("&&", ";")
    if UNSAFE_TEXT_RE.search(text):
        return Reading(False, ())
    restores: list[Restore] = []
    for segment in line_segments(text):
        words = [_bare(word) for word in segment]
        if not words:
            continue
        found = _restores(words, cwd)
        if found is None and not _reads(words):
            return Reading(False, ())
        restores.extend(found or ())
    return Reading(True, tuple(restores))


def _reads(words: list[str]) -> bool:
    name = words[0]
    if name == "git":
        return len(words) > 1 and words[1] in GIT_READS and not _unsafe(words, name)
    return name in READ_COMMANDS and not _unsafe(words, name)


def _unsafe(words: list[str], name: str) -> bool:
    return any(word.startswith(flag) for word in words[1:] for flag in UNSAFE_FLAGS.get(name, ()))


def _restores(words: list[str], cwd: Path) -> list[Restore] | None:
    if words[:2] == ["git", "restore"]:
        return _named(words[2:], cwd, RESTORE_FLAGS, kind=BY_GIT)
    if words[:3] == ["git", "checkout", "--"]:
        return _named(words[3:], cwd, frozenset(), kind=BY_GIT, ended=True)
    if words[0] == "rm":
        return _named(words[1:], cwd, REMOVE_FLAGS, kind=BY_RM)
    if words[0] == "chmod" and len(words) > 2 and MODE_RE.match(words[1]):
        return _named(words[2:], cwd, frozenset(), kind=BY_CHMOD, ended=True)
    return None


def _named(args: list[str], cwd: Path, flags: frozenset[str], *, kind: str, ended: bool = False) -> list[Restore] | None:
    paths: list[Restore] = []
    for word in args:
        if not ended and word == "--":
            ended = True
            continue
        if not ended and word.startswith("-"):
            if word not in flags:
                return None
            continue
        if not word or GLOB_CHARS & set(word):
            return None
        paths.append(Restore(os.path.realpath(cwd / word), kind))
    return paths or None
