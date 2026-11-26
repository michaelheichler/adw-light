from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

try:
    from . import git_port
    from .shell_parse import _bare, _basename, _command_word_index
    from .write_targets import _git_base
except ImportError:
    from lib import git_port
    from lib.shell_parse import _bare, _basename, _command_word_index
    from lib.write_targets import _git_base

MERGE = "merge"
CHERRY_PICK = "cherry-pick"
MERGE_FLAGS = frozenset({
    "--no-ff", "--ff", "--ff-only", "--no-edit", "--edit", "-e", "-q", "--quiet", "--no-commit", "--commit", "--squash",
    "--no-squash", "--no-verify", "--stat", "--no-stat", "-n", "--log", "--no-log", "-v", "--verbose", "--progress",
    "--no-progress", "--signoff", "--no-signoff",
})
PICK_FLAGS = frozenset({
    "-x", "--ff", "-n", "--no-commit", "-e", "--edit", "--no-edit", "-s", "--signoff", "--allow-empty",
    "--keep-redundant-commits",
})
FLAGS_OF = {MERGE: MERGE_FLAGS, CHERRY_PICK: PICK_FLAGS}
MESSAGE_FLAG = "-m"
MESSAGE_PREFIX = "--message="
UNREADABLE_REFS = frozenset({"FETCH_HEAD", "MERGE_HEAD", "ORIG_HEAD", "CHERRY_PICK_HEAD", "-"})
UNREADABLE_MARKS = ("..", "^", "@{", ":", "$", "`", "*", "?", "[")


class Incoming(NamedTuple):
    verb: str
    refs: tuple[str, ...]


def incoming(segment: list[str]) -> Incoming | None:
    index = _command_word_index(segment)
    if index >= len(segment) or _basename(segment[index]) != "git":
        return None
    words = [_bare(word) for word in segment[index + 1:]]
    position = _git_base(words)[1]
    if position >= len(words) or words[position] not in FLAGS_OF:
        return None
    verb = words[position]
    refs = _refs(verb, words[position + 1:])
    return Incoming(verb, refs) if refs else None


def _refs(verb: str, rest: list[str]) -> tuple[str, ...]:
    refs: list[str] = []
    message_value = False
    for word in rest:
        if message_value:
            message_value = False
        elif verb == MERGE and word == MESSAGE_FLAG:
            message_value = True
        elif word in FLAGS_OF[verb] or (verb == MERGE and word.startswith(MESSAGE_PREFIX)):
            continue
        elif word.startswith("-") or word in UNREADABLE_REFS or any(mark in word for mark in UNREADABLE_MARKS):
            return ()
        else:
            refs.append(word)
    return () if message_value else tuple(refs)


def changed_paths(coming: Incoming, directory: Path) -> list[str] | None:
    found: list[str] = []
    for ref in coming.refs:
        if coming.verb == CHERRY_PICK and not _single_parent(ref, directory):
            return None
        span = [f"HEAD...{ref}"] if coming.verb == MERGE else [f"{ref}^", ref]
        listed = git_port.view().diff_paths(directory, span)
        if listed is None:
            return None
        found.extend(listed)
    return found


def _single_parent(ref: str, directory: Path) -> bool:
    parents = git_port.view().commit_parents(directory, ref)
    return parents is not None and len(parents) == 1
