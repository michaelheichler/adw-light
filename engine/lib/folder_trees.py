from __future__ import annotations

import os

try:
    from . import config_roots as config_paths
except ImportError:
    import config_roots as config_paths
import time
from typing import NamedTuple

from .find_matches import MAX_ENTRIES, MAX_SECONDS
from .protected import CONFIG_SEAL_BASENAME, WIRING_FILES
from .shell_expansion import Expansions, Tree
from .shell_syntax import _bare, _basename, _command_word_index, _without_redirects
from .temp_scope import Directories, joined_paths, segment_directories

TREE_VERBS = frozenset({"rm", "mv", "rsync"})
RECURSIVE_FLAGS = frozenset({"-r", "-R", "--recursive"})
TARGET_DIR_FLAGS = frozenset({"-t", "--target-directory"})
RSYNC_VALUE_FLAGS = frozenset({
    "-e", "--rsh", "--exclude", "--include", "--filter", "-f", "--exclude-from", "--include-from",
    "--files-from", "--rsync-path", "--log-file", "--backup-dir", "--suffix", "--chmod", "--chown",
})
END_OF_OPTIONS = "--"
STATE_FOLDERS = (".adw", ".agent-discipline")
STATE_PART = ".adw"
WATCHER_PART = "agent-discipline"
AGENT_FOLDER = "agents"
AGENT_FILE_SUFFIXES = frozenset({".md", ".toml"})
WIRING_NAMES = frozenset(parts[-1] for parts in WIRING_FILES)


class _Scope(NamedTuple):
    expansions: Expansions
    cwd: str


class _Walk(NamedTuple):
    deadline: float
    pending: list[str]
    files: list[str]
    seen: list[int]


def folder_trees(segments: list[list[str]], expansions: Expansions, cwd: str) -> tuple[Tree, ...]:
    scope = _Scope(expansions, cwd)
    places = segment_directories(segments, (".",), expansions.path)
    return tuple(_tree(segment, folders, scope) for segment, folders in zip(segments, places) if recursive_operands(segment))


def recursive_operands(segment: list[str]) -> list[tuple[str, bool]]:
    index = _command_word_index(segment)
    verb = _basename(segment[index]) if index < len(segment) else ""
    if verb not in TREE_VERBS:
        return []
    flags, operands = _split_operands(verb, _without_redirects(segment[index + 1:]))
    if verb == "rm":
        return [(raw, raw.endswith("/")) for raw in operands] if _recursive(flags) else []
    if verb == "rsync":
        return _rsync_operands(flags, operands)
    sources = operands if any(flag in TARGET_DIR_FLAGS or flag.startswith("--target-directory=") for flag in flags) else operands[:-1]
    return [(raw, raw.endswith("/")) for raw in sources]


def _split_operands(verb: str, args: list[str]) -> tuple[list[str], list[str]]:
    flags: list[str] = []
    operands: list[str] = []
    position = 0
    while position < len(args):
        word = _bare(args[position])
        if word == END_OF_OPTIONS:
            return flags, operands + args[position + 1:]
        (flags if word.startswith("-") else operands).append(word if word.startswith("-") else args[position])
        valued = word in TARGET_DIR_FLAGS or (verb == "rsync" and word in RSYNC_VALUE_FLAGS)
        position += 2 if valued else 1
    return flags, operands


def _recursive(flags: list[str]) -> bool:
    clusters = [flag[1:] for flag in flags if flag.startswith("-") and not flag.startswith("--")]
    return any(flag in RECURSIVE_FLAGS for flag in flags) or any("r" in cluster or "R" in cluster for cluster in clusters)


def _rsync_operands(flags: list[str], operands: list[str]) -> list[tuple[str, bool]]:
    found = [(operands[-1], True)] if operands and any(flag.startswith("--delete") for flag in flags) else []
    return found + ([(raw, True) for raw in operands[:-1]] if "--remove-source-files" in flags else [])


def _tree(segment: list[str], folders: Directories, scope: _Scope) -> Tree:
    parts = [_operand(operand, folders, scope) for operand in recursive_operands(segment)]
    return Tree(
        tuple(segment), tuple(path for part in parts for path in part.named),
        tuple(path for part in parts for path in part.files), tuple(word for part in parts for word in part.unknown),
    )


def _operand(operand: tuple[str, bool], folders: Directories, scope: _Scope) -> Tree:
    raw, follow = operand
    path = scope.expansions.path(raw)
    roots = [] if path is None else joined_paths([path], folders)
    if path is not None and not roots:
        return Tree((), (), (), (raw,))
    located = [os.path.join(scope.cwd, os.path.expanduser(root)) for root in roots]
    walked = [_walked(root, follow) for root in located]
    if None in walked:
        return Tree((), (), (), (f"{raw} (folder too large to check)",))
    files = [path for found in walked if found is not None for path in found]
    named = [root for root in _protected_roots(scope.cwd) for place in located if _holds(place, root, follow)]
    return Tree((), tuple(named + [path for path in files if _protected_name(path) and not os.path.islink(path)]), tuple(files), ())


def _walked(root: str, follow: bool) -> list[str] | None:
    if not follow and os.path.islink(root):
        return []
    target = os.path.realpath(root) if follow else root
    return descendants(target) if os.path.isdir(target) else []


def descendants(root: str) -> list[str] | None:
    walk = _Walk(time.monotonic() + MAX_SECONDS, [root], [], [0])
    while walk.pending:
        if not _scan(walk, walk.pending.pop()):
            return None
    return walk.files


def _scan(walk: _Walk, folder: str) -> bool:
    try:
        with os.scandir(folder) as entries:
            children = list(entries)
    except OSError:
        return False
    walk.seen[0] += len(children)
    if walk.seen[0] > MAX_ENTRIES or time.monotonic() > walk.deadline:
        return False
    walk.pending.extend(child.path for child in children if child.is_dir(follow_symlinks=False))
    walk.files.extend(child.path for child in children if not child.is_dir(follow_symlinks=False))
    return True


def _protected_roots(cwd: str) -> list[str]:
    home = str(config_paths.home_root())
    start = os.path.abspath(cwd)
    configs = [os.path.join(folder, CONFIG_SEAL_BASENAME) for folder in (start, *_parents(start))]
    governing = next((path for path in configs if os.path.lexists(path)), None)
    return [*([governing] if governing else []), *(os.path.join(home, name) for name in STATE_FOLDERS)]


def _parents(path: str) -> list[str]:
    found = []
    while os.path.dirname(path) != path:
        path = os.path.dirname(path)
        found.append(path)
    return found


def _holds(place: str, protected: str, follow: bool) -> bool:
    if not follow and os.path.islink(place):
        return False
    folder = os.path.realpath(place)
    target = os.path.realpath(protected)
    return target == folder or target.startswith(folder.rstrip(os.sep) + os.sep)


def _protected_name(path: str) -> bool:
    parts = [part.lower() for part in path.split(os.sep)]
    name = parts[-1] if parts else ""
    in_agents = len(parts) > 1 and parts[-2] == AGENT_FOLDER and os.path.splitext(name)[1] in AGENT_FILE_SUFFIXES
    watcher = any(STATE_PART in part or WATCHER_PART in part for part in parts)
    return name == CONFIG_SEAL_BASENAME or name in WIRING_NAMES or in_agents or watcher
