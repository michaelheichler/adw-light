from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NamedTuple

from lib.archive_commands import STDIN
from lib.archive_members import ArchiveRead, landed_paths
from lib.folder_trees import recursive_operands, scratch_tree_directory
from lib.git_globs import removed_paths, tree_paths
from lib.incoming_paths import missing_stash
from lib.payloads import resolved_path
from lib.shell_expansion import Expansions, Tree
from lib.shell_parse import (
    _bare,
    _basename,
    _command_word_index,
    command_expansions,
    line_segments,
)
from lib.shell_syntax import _without_redirects
from lib.temp_scope import (
    Directories,
    _changes_directory,
    joined_paths,
    landing_directories,
    segment_directories,
)
from lib.test_units import language_of
from lib.tests_policy import (
    is_test_path,
    outside_project,
    project_root,
    temp_scratch_repo,
    untracked_scratch,
)
from lib.write_targets import (
    GatedTargets,
    GitLookup,
    _git_base,
    gated_test_targets,
    mutation_targets,
    unknown_targets,
)

OUTER_START: tuple[str, ...] = (".",)
MAX_JUDGED_FILES = 2_000
REMOVE_VERBS = frozenset({"rm", "unlink"})
GIT_KEEPING_FLAGS = frozenset({"--cached", "-n", "--dry-run"})
RM_LONG_OPTIONS = ("cached", "dry-run", "force", "quiet", "ignore-unmatch", "sparse", "pathspec-from-file", "pathspec-file-nul")
RM_SHORT_LETTERS = frozenset("fnqr")
RM_KEEPING = ("cached", "dry-run")
PATHSPEC_FILE_FLAG = "--pathspec-from-file"


class Scope(NamedTuple):
    cwd: Path
    directories: Directories
    outer: bool
    created: frozenset[Path] = frozenset()


def reachable_directories(command: str, cwd: Path) -> Directories:
    found = segment_directories(line_segments(command), OUTER_START, command_expansions(command, cwd).path)
    if None in found:
        return None
    return tuple(sorted({folder for folders in found if folders for folder in folders}))


def gated_paths(
    command: str, scope: Scope, *, landed: bool = False, staged: set[str] | None = None,
) -> list[str]:
    expansions = command_expansions(command, scope.cwd)
    segments = line_segments(command)
    places = (landing_directories(segments, scope.cwd, expansions.path, created=scope.created) if landed
              else segment_directories(segments, scope.directories, expansions.path))
    paths: list[str] = []
    for position, (segment, folders) in enumerate(zip(segments, places)):
        gated = gated_test_targets(segment, expansions)
        lookups = scope.outer and all(map(_changes_directory, segments[:position]))
        indexed = gated._replace(indexable=gated.indexable and lookups)
        named = _segment_paths(segment, expansions, gated, folders, scope.cwd)
        named.extend(_lookup_paths(indexed, folders, scope.cwd) or ())
        if staged is not None and _index_only_git(segment):
            staged.update(named)
        else:
            paths.extend(named)
        if not _scratch_tree(segment, expansions, folders, scope.cwd):
            paths.extend(_tree_tests(expansions.tree(segment)) or ())
        paths.extend(_archive_paths(gated.archive, folders, scope.cwd) or ())
    return list(dict.fromkeys(paths))


def _segment_paths(segment, expansions, gated, folders: Directories, cwd: Path) -> list[str]:
    if not folders or folders == OUTER_START:
        return joined_paths([*gated.paths, *mutation_targets(segment, expansions)], folders)
    paths = []
    for folder in folders:
        local = expansions._replace(cwd=str(cwd / folder))
        paths.extend(joined_paths([*gated_test_targets(segment, local).paths, *mutation_targets(segment, local)], (folder,)))
    return paths


def _scratch_directory(directory: str, cwd: Path) -> bool:
    return outside_project(directory, str(cwd)) or temp_scratch_repo(directory, str(cwd))


def _archive_paths(read: ArchiveRead | None, folders: Directories, cwd: Path) -> list[str] | None:
    if read is None:
        return []
    if folders is None:
        return None
    found: list[str] = []
    for folder in folders:
        landed = None if read.source == STDIN else landed_paths(read, cwd / folder)
        destination = str(cwd / folder / read.directory)
        if landed is None and (_scratch_directory(destination, cwd) or untracked_scratch(destination, project_root(str(cwd)))):
            continue
        if landed is None:
            named = os.path.normpath(os.path.join(folder, read.directory))
            if is_test_path(named, str(cwd)):
                found.append(named)
                continue
            return found or None
        found.extend(os.path.normpath(os.path.join(folder, path)) for path in landed)
    return found


def _scratch_tree(segment: list[str], expansions: Expansions, folders: Directories, cwd: Path) -> bool:
    paths = [expansions.path(raw) for raw, _follow in recursive_operands(segment)]
    if not paths or folders is None or any(path is None for path in paths):
        return False
    return all(scratch_tree_directory(cwd / folder / str(path), cwd) for path in paths for folder in folders)


def _tree_tests(tree: Tree | None) -> list[str] | None:
    candidates = [] if tree is None else [path for path in tree.files if is_test_path(path) or language_of(path)]
    return candidates if len(candidates) <= MAX_JUDGED_FILES else None


def _lookup_paths(gated: GatedTargets, folders: Directories, cwd: Path) -> list[str] | None:
    lookup = gated.lookup
    if lookup is None:
        return []
    if folders is None or not gated.indexable:
        literal = [os.path.join(lookup.base, path) for path in lookup.pathspecs]
        named_files = folders is not None and all(_names_a_file(path, folders, cwd) for path in literal)
        return [] if not lookup.strict or named_files else None
    found: list[str] = []
    for folder in folders:
        listed = _listed(lookup, cwd / folder / lookup.base)
        if listed is None:
            return None if lookup.strict else found
        found.extend(os.path.normpath(os.path.join(folder, lookup.base, path)) for path in listed)
    return found


def _listed(lookup: GitLookup, directory: Path) -> list[str] | None:
    if lookup.tree is None:
        return removed_paths(lookup.pathspecs, directory)
    sourced = tree_paths(lookup.tree, lookup.pathspecs, directory)
    if lookup.tree != "stash@{0}":
        return sourced
    current = removed_paths(lookup.pathspecs, directory)
    if sourced is None and missing_stash(directory):
        return current
    return None if sourced is None or current is None else [*sourced, *current]


def _names_a_file(path: str, folders: tuple[str, ...], cwd: Path) -> bool:
    candidates = [cwd / folder / path for folder in folders]
    return any(map(Path.is_file, candidates)) and not any(map(Path.is_dir, candidates))


def removal_targets(command: str, cwd: Path) -> frozenset[Path]:
    expansions = command_expansions(command, cwd)
    named: list[str] = []
    for segment in line_segments(command):
        paths = _removal_paths(segment, expansions)
        if not paths:
            return frozenset()
        named.extend(paths)
    return frozenset(resolved_path(path, cwd) for path in named)


def only_files(removed: frozenset[Path]) -> frozenset[Path]:
    return removed if removed and all(map(Path.is_file, removed)) else frozenset()


def _removal_paths(segment: list[str], expansions: Expansions) -> list[str] | None:
    if _without_redirects(segment) != segment or expansions.calls(segment) is not None:
        return None
    index = _command_word_index(segment)
    verb = _basename(segment[index]) if index < len(segment) else ""
    if verb in REMOVE_VERBS:
        return None if unknown_targets(segment, expansions) else mutation_targets(segment, expansions)
    words = segment[index + 1:]
    _, position = _git_base(words)
    if verb != "git" or words[position:position + 1] != ["rm"] or _keeps_files(words[position + 1:]):
        return None
    gated = gated_test_targets(segment, expansions)
    return None if gated.unresolved else list(gated.paths)


def _index_only_git(segment: list[str]) -> bool:
    index = _command_word_index(segment)
    if _without_redirects(segment) != segment or index >= len(segment) or _basename(segment[index]) != "git":
        return False
    words = [_bare(word) for word in segment[index + 1:]]
    _, position = _git_base(words)
    rest = words[position + 1:]
    options = [word for word in (rest[:rest.index("--")] if "--" in rest else rest) if word.startswith("-") and word != "-"]
    verb = words[position] if position < len(words) else ""
    if verb == "read-tree":
        return not any("u" in word[1:] for word in options if not word.startswith("--"))
    if verb == "write-tree":
        return True
    state: dict[str, bool] = dict.fromkeys(RM_KEEPING, False)
    return verb == "rm" and all(_rm_option(word, state) for word in options) and any(state.values())


def _rm_option(word: str, state: dict[str, bool]) -> bool:
    if not word.startswith("--"):
        state["dry-run"] = state["dry-run"] or "n" in word
        return set(word[1:]) <= RM_SHORT_LETTERS
    name = word[2:].partition("=")[0]
    stem = name.removeprefix("no-")
    exact = [option for option in RM_LONG_OPTIONS if option == stem]
    matches = exact or [option for option in RM_LONG_OPTIONS if stem and option.startswith(stem)]
    if len(matches) != 1:
        return False
    if matches[0] in state:
        state[matches[0]] = stem == name
    return True


def _keeps_files(words: list[str]) -> bool:
    return any(word in GIT_KEEPING_FLAGS or word.startswith(PATHSPEC_FILE_FLAG) for word in words)
