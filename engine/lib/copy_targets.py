from __future__ import annotations

import os
import re
from pathlib import PurePosixPath
from typing import NamedTuple

try:
    from .shell_expansion import Expansions
    from .shell_parse import _bare, _expand_home
    from .gated_targets import GatedTargets, UNRESOLVED
except ImportError:
    from shell_expansion import Expansions
    from shell_parse import _bare, _expand_home
    from gated_targets import GatedTargets, UNRESOLVED

DESTINATION_LAST_ARG_VERBS = frozenset({"cp", "mv", "ln", "sed", "install", "rsync"})
DESTINATION_ALL_ARGS_VERBS = frozenset({"rm", "unlink", "shred", "truncate", "chmod", "chown", "touch", "sponge", "mkfifo"})
ARGUMENT_VERBS = DESTINATION_LAST_ARG_VERBS | DESTINATION_ALL_ARGS_VERBS | {"dd"}
TARGET_DIR_VERBS = frozenset({"cp", "mv", "ln", "install"})
TARGET_DIR_FLAGS = frozenset({"-t", "--target-directory"})
TARGET_DIR_PREFIX = "--target-directory="
DD_OUTPUT_PREFIX = "of="
COPY_VERBS = frozenset({"cp", "mv", "ln", "install", "rsync"})


class Slot(NamedTuple):
    position: int
    prefix: str


def _destination_slots(verb: str, words: list[str]) -> list[Slot]:
    positional = [position for position, word in enumerate(words) if not word.startswith("-")]
    if verb == "chmod" and positional:
        mode = positional[0]
        if all(word in {"-R", "--"} for word in words[:mode]) and re.fullmatch(r"[ugoa]*[+=-][rwxXstugo]+|[0-7]{3,4}", words[mode]):
            return [Slot(position, "") for position in range(mode + 1, len(words))]
    if verb == "dd":
        return [Slot(position, DD_OUTPUT_PREFIX) for position in positional if words[position].startswith(DD_OUTPUT_PREFIX)]
    if verb in DESTINATION_ALL_ARGS_VERBS:
        return [Slot(position, "") for position in positional]
    target = _target_slot(words) if verb in TARGET_DIR_VERBS else None
    if target is not None:
        return [target]
    return [Slot(positional[-1], "")] if verb in DESTINATION_LAST_ARG_VERBS and positional else []


def _target_slot(words: list[str]) -> Slot | None:
    for position, word in enumerate(words):
        if word.startswith(TARGET_DIR_PREFIX):
            return Slot(position, TARGET_DIR_PREFIX)
        if word in TARGET_DIR_FLAGS and position + 1 < len(words):
            return Slot(position + 1, "")
    return None


def _target_directory(args: list[str]) -> str | None:
    for position, token in enumerate(args):
        bare = _bare(token)
        if bare.startswith(TARGET_DIR_PREFIX):
            return bare[len(TARGET_DIR_PREFIX):]
        if bare in TARGET_DIR_FLAGS and position + 1 < len(args):
            return _bare(args[position + 1])
    return None


def _drop_target_dir(args: list[str]) -> list[str]:
    kept = []
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        bare = _bare(token)
        if bare.startswith(TARGET_DIR_PREFIX):
            continue
        if bare in TARGET_DIR_FLAGS:
            skip_next = True
            continue
        kept.append(token)
    return kept


def _copy_gate(verb: str, args: list[str], expansions: Expansions) -> GatedTargets:
    words = [expansions.pattern(token) or "" for token in args]
    slot = _target_slot(words) if verb != "rsync" else None
    positional = [position for position, word in enumerate(words) if not word.startswith("-")]
    destination = slot[0] if slot else (positional[-1] if len(positional) > 1 else None)
    if destination is not None and expansions.path(args[destination]) is None:
        return UNRESOLVED
    return GatedTargets(tuple(_expand_home(path) for path in _copy_targets(verb, words, slot, expansions.cwd)), False)


def _copy_targets(verb: str, words: list[str], slot: Slot | None, cwd: str | None = None) -> list[str]:
    taken = {slot[0]} if slot else set()
    sources = [word for position, word in enumerate(words) if not word.startswith("-") and position not in taken]
    if slot is None:
        if len(sources) < 2:
            return sources
        target_dir, sources = sources[-1], sources[:-1]
    else:
        target_dir = words[slot[0]][len(slot[1]):]
    joined = [os.path.join(target_dir, PurePosixPath(source).name) for source in sources]
    dest = os.path.join(cwd or ".", _expand_home(target_dir))
    same = len(sources) == 1 and PurePosixPath(target_dir).name == PurePosixPath(sources[0]).name
    if not os.path.isdir(dest) and (same or os.path.isfile(dest)):
        joined = []
    return list(dict.fromkeys([target_dir, *joined, *(sources if verb == "mv" else [])]))
