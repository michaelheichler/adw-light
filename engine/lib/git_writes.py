from __future__ import annotations

import os
import re
from itertools import pairwise

WORKTREE_COMMANDS = frozenset({"checkout-index", "merge", "pull", "cherry-pick", "revert", "am", "rebase", "bisect"})
RESET_WORKTREE_FLAGS = frozenset({"--hard", "--merge", "--keep"})
STASH_REWRITES = frozenset({"push", "save", "branch"})
BRANCH_COMMANDS = frozenset({"checkout", "switch"})
BRANCH_VALUE_FLAGS = frozenset({"-b", "-B", "--orphan", "-c", "-C", "--create", "--force-create"})
FORCE_FLAGS = frozenset({"-f", "--force"})
SHORT_OUTPUT_COMMANDS = frozenset({"format-patch", "archive"})
OUTPUT_FLAGS = ("--output", "--output-directory")
DRY_RUN_FLAGS = frozenset({"-n", "--dry-run"})
CLONE_SUFFIX = ".git"
LOCATION_RE = re.compile(
    r"\bGIT_(?:DIR|WORK_TREE|COMMON_DIR|CONFIG\w*|CEILING_DIRECTORIES|DISCOVERY_ACROSS_FILESYSTEM"
    r"|OBJECT_DIRECTORY|ALTERNATE_OBJECT_DIRECTORIES)\b"
)
RELOCATING_FLAGS = ("--git-dir", "--config-env", "--bare")
RELOCATING_KEYS = ("core.worktree", "core.bare", "include")
ALIAS_KEY = "alias."


def worktree_rewrite(command: str, rest: list[str], directory: str) -> bool:
    if command in WORKTREE_COMMANDS:
        return True
    if command == "reset":
        return bool(RESET_WORKTREE_FLAGS.intersection(rest))
    if command == "read-tree":
        return "-u" in rest
    if command == "stash":
        return not rest or rest[0].startswith("-") or rest[0] in STASH_REWRITES
    if command == "sparse-checkout":
        return rest[:1] != ["list"]
    return command in BRANCH_COMMANDS and _moves_branch(command, rest, directory)


def _moves_branch(command: str, rest: list[str], directory: str) -> bool:
    if "--" in rest:
        return False
    if FORCE_FLAGS.intersection(rest):
        return True
    positional: list[str] = []
    named_value = False
    for word in rest:
        if not named_value and (word == "-" or not word.startswith("-")):
            positional.append(word)
        named_value = word in BRANCH_VALUE_FLAGS
    if command == "switch":
        return bool(positional) or "--orphan" in rest
    return any(not os.path.lexists(os.path.join(directory, word)) for word in positional)


def cleans_files(command: str, rest: list[str]) -> bool:
    if command != "clean":
        return False
    options = rest[:rest.index("--")] if "--" in rest else rest
    undone = any(word.startswith("--no-d") for word in options)
    clusters = [word for word in options if word.startswith("-") and not word.startswith("--") and "n" in word]
    return undone or not (DRY_RUN_FLAGS.intersection(options) or clusters)


def written_paths(command: str, rest: list[str]) -> list[str]:
    found = clone_paths(rest) if command == "clone" else []
    for position, word in enumerate(rest):
        following = rest[position + 1] if position + 1 < len(rest) else ""
        name, separator, value = word.partition("=")
        if name in OUTPUT_FLAGS:
            found.append(value if separator else following)
        elif word == "-o" and command in SHORT_OUTPUT_COMMANDS:
            found.append(following)
    return [path for path in found if path]


def clone_paths(rest: list[str]) -> list[str]:
    values = {"--depth", "--branch", "-b", "--origin", "-o", "--reference", "--reference-if-able",
              "--shallow-since", "--shallow-exclude", "--filter", "--jobs", "-j", "--config", "-c",
              "--upload-pack", "-u", "--separate-git-dir", "--template", "--revision"}
    positional: list[str] = []
    locations: list[str] = []
    operands = iter(rest)
    for word in operands:
        if word == "--":
            positional.extend(operands)
            break
        name, separator, value = word.partition("=")
        if name in values:
            location = value if separator else next(operands, "")
            if name == "--separate-git-dir" and location:
                locations.append(location)
        elif not word.startswith("-"):
            positional.append(word)
    if len(positional) == 1:
        name = os.path.basename(positional[0].rstrip("/")).rsplit(":", 1)[-1]
        locations.append(name.removesuffix(CLONE_SUFFIX))
    return locations + positional[1:]


def relocated(command: str, options: list[str]) -> bool:
    if LOCATION_RE.search(command):
        return True
    keyed = [following.lower() for word, following in pairwise(options) if word == "-c"]
    return any(word.startswith(RELOCATING_FLAGS) for word in options) or any(key.startswith(RELOCATING_KEYS) for key in keyed)


def aliased(options: list[str], verb: str) -> bool:
    return any(word == "-c" and following.partition("=")[0].lower() == ALIAS_KEY + verb.lower() for word, following in pairwise(options))
