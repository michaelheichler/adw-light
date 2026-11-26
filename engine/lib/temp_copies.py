from __future__ import annotations

import fnmatch
import glob
import os
from pathlib import Path
from typing import NamedTuple

from lib.shell_syntax import _bare, _basename, _command_word_index, _segments
from lib.path_policy import anchored, anchored_identity
from lib.scan_input import in_dot_folder
from lib.shell_parse import line_segments
from lib.temp_scope import _within, outside_project_temp
from lib.write_targets import _drop_target_dir, _expand_home, _target_directory

COPY_VERBS = frozenset({"cp", "mv", "install", "rsync"})
GLOB_CHARS = frozenset("*?[")
Cwd = str | os.PathLike[str] | None


class Landing(NamedTuple):
    source: str
    destination: str


class _Copy(NamedTuple):
    verb: str
    source: str
    destination: Path
    cwd: Cwd
    authored: frozenset[str] | None


def source_key(path: str | os.PathLike[str], cwd: Cwd = None) -> str:
    return str(anchored_identity(path, Path(cwd or ".").absolute()))


def temp_copy_destinations(command: str, cwd: Cwd) -> list[str]:
    return list(dict.fromkeys(landing.destination for landing in _landings(command, cwd, None)))


def copy_landings(command: str, cwd: Cwd, authored: frozenset[str]) -> tuple[list[str], list[str]]:
    owned: dict[str, None] = {}
    imported: dict[str, None] = {}
    for landing in _landings(command, cwd, authored):
        (owned if landing.source in authored else imported)[landing.destination] = None
    return list(owned), [path for path in imported if path not in owned]


def _landings(command: str, cwd: Cwd, authored: frozenset[str] | None) -> list[Landing]:
    found: list[Landing] = []
    for segment in _segments(command):
        index = _command_word_index(segment)
        if index < len(segment) and _basename(segment[index]) in COPY_VERBS:
            verb = _basename(segment[index])
            found.extend(_segment_landings(verb, segment[index + 1:], cwd, authored))
    return found


def _copy_operands(args: list[str]) -> tuple[list[str], str] | None:
    target_dir = _target_directory(args)
    paths = [_expand_home(_bare(token)) for token in _drop_target_dir(args) if not _bare(token).startswith("-")]
    if target_dir is not None:
        return paths, _expand_home(target_dir)
    return (paths[:-1], paths[-1]) if len(paths) >= 2 else None


def _segment_landings(verb: str, args: list[str], cwd: Cwd, authored: frozenset[str] | None) -> list[Landing]:
    operands = _copy_operands(args)
    if operands is None:
        return []
    sources, target = operands
    destination = anchored(target, Path(cwd or ".").absolute())
    return [
        landing
        for source in sources if outside_project_temp(source, cwd)
        for landing in _source_landings(_Copy(verb, source, destination, cwd, authored))
    ]


def dot_copy_destinations(command: str, cwd: Cwd) -> list[str]:
    base = Path(cwd or ".").absolute()
    found: list[str] = []
    for segment in line_segments(command):
        index = _command_word_index(segment)
        if index >= len(segment) or _basename(segment[index]) not in COPY_VERBS:
            continue
        operands = _copy_operands(segment[index + 1:])
        if operands is None:
            continue
        sources, target = operands
        found.extend(
            landing.destination
            for source in sources if in_dot_folder(anchored(source, base) / "entry")
            for landing in _source_landings(_Copy(_basename(segment[index]), source, anchored(target, base), cwd, None))
        )
    return list(dict.fromkeys(found))


def _source_landings(copy: _Copy) -> list[Landing]:
    if any(char in copy.source for char in GLOB_CHARS):
        matches = sorted(glob.glob(str(anchored(copy.source, Path(copy.cwd or ".").absolute()))))
        if matches:
            return [landing for match in matches for landing in _tree_landings(Path(match), copy.destination, False)]
        return _moved_glob_landings(copy)
    source = anchored(copy.source, Path(copy.cwd or ".").absolute())
    if source.exists():
        contents = copy.source.endswith("/.") or (copy.verb == "rsync" and copy.source.endswith("/"))
        return _tree_landings(source, copy.destination, contents)
    return _moved_landings(copy, source) if copy.verb == "mv" else []


def _tree_landings(source: Path, destination: Path, contents: bool) -> list[Landing]:
    if source.is_file():
        landed = destination / source.name if destination.is_dir() else destination
        return [Landing(source_key(source), str(landed))] if landed.is_file() else []
    if not source.is_dir():
        return []
    named = destination / source.name
    root = destination if contents or not named.is_dir() else named
    pairs = ((item, root / item.relative_to(source)) for item in sorted(source.rglob("*")) if item.is_file())
    return [Landing(source_key(item), str(landed)) for item, landed in pairs if landed.is_file()]


def _moved_landings(copy: _Copy, source: Path) -> list[Landing]:
    named = copy.destination / source.name
    root = named if named.exists() else copy.destination
    if _within(Path(copy.cwd or ".").absolute(), (root,)):
        return []
    if root.is_file():
        landings = [Landing(source_key(source), str(root))]
    else:
        base = source_key(source)
        landings = [
            Landing(os.path.join(base, str(item.relative_to(root))), str(item))
            for item in sorted(root.rglob("*")) if item.is_file()
        ]
    return [landing for landing in landings if copy.authored is None or landing.source in copy.authored]


def _moved_glob_landings(copy: _Copy) -> list[Landing]:
    if not copy.authored or not copy.destination.is_dir():
        return []
    pattern = source_key(copy.source, copy.cwd)
    return [
        Landing(key, str(copy.destination / Path(key).name))
        for key in sorted(copy.authored)
        if fnmatch.fnmatchcase(key, pattern) and (copy.destination / Path(key).name).is_file()
    ]
