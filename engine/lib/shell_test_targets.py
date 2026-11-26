from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import NamedTuple

from lib.archive_commands import STDIN
from lib.archive_members import ArchiveRead, landed_paths
from lib.find_matches import MAX_ENTRIES
from lib.folder_trees import recursive_operands, scratch_tree_directory
from lib.git_alias_bodies import CHECKED_INLINE_ALIAS
from lib.git_globs import removed_paths, tracked_paths, tree_paths
from lib.git_writes import aliased, relocated
from lib.incoming_paths import MERGE, Incoming, changed_paths, incoming
from lib.payloads import resolved_path
from lib.shell_expansion import Expansions, Tree
from lib.shell_parse import (
    _bare,
    _basename,
    _command_word_index,
    command_expansions,
    line_segments,
)
from lib.shell_syntax import _tokens, _without_redirects
from lib.temp_scope import (
    Directories,
    _changes_directory,
    joined_paths,
    logical_directory,
    outside_project_temp,
    segment_directories,
    trusted_directory,
)
from lib.test_units import language_of
from lib.tests_policy import (
    foreign_scratch_repo,
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


def reachable_directories(command: str, cwd: Path) -> Directories:
    found = segment_directories(line_segments(command), OUTER_START, command_expansions(command, cwd).path)
    if None in found:
        return None
    return tuple(sorted({folder for folders in found if folders for folder in folders}))


def gated_paths(command: str, scope: Scope) -> tuple[list[str], list[str]]:
    expansions = command_expansions(command, scope.cwd)
    segments = line_segments(command)
    paths: list[str] = []
    unresolved: list[str] = []
    for position, (segment, folders) in enumerate(zip(segments, segment_directories(segments, scope.directories, expansions.path))):
        gated = gated_test_targets(segment, expansions)
        lookups = scope.outer and all(map(_changes_directory, segments[:position]))
        named = _segment_paths(segment, expansions, gated, folders, scope.cwd)
        looked_up = _lookup_paths(gated._replace(indexable=gated.indexable and lookups), folders, scope.cwd)
        inside = _tree_tests(expansions.tree(segment)) if not _scratch_tree(segment, expansions, folders, scope.cwd) else []
        landed = _archive_paths(gated.archive, folders, scope.cwd)
        paths.extend([*(named or []), *(looked_up or []), *(inside or []), *(landed or [])])
        coming = incoming(segment) if lookups else None
        rewrites = gated.worktree is not None and _rewrite_tests(command, segment, scope, folders, gated.worktree, coming)
        if gated.unresolved or rewrites or _unknown_repo(segment, command, gated) or named is None or looked_up is None or inside is None or landed is None:
            unresolved.append(" ".join(segment))
    return list(dict.fromkeys(paths)), unresolved


def _unknown_repo(segment: list[str], command: str, gated: GatedTargets) -> bool:
    index = _command_word_index(segment)
    if index >= len(segment) or _basename(segment[index]) != "git":
        return False
    words = [_bare(word) for word in segment[index + 1:]]
    options = words[:_git_base(words)[1]]
    changes = bool(gated.paths or gated.lookup or gated.archive) or gated.worktree is not None
    if aliased(options) and command != CHECKED_INLINE_ALIAS.get():
        return True
    return changes and relocated(command, options)


def _segment_paths(segment, expansions, gated, folders: Directories, cwd: Path) -> list[str] | None:
    if _uncertain_delete(segment, expansions, folders, cwd):
        return None
    mutations = mutation_targets(segment, expansions)
    if not folders or folders == OUTER_START:
        return _in_folders([*gated.paths, *mutations], folders)
    paths = []
    for folder in folders:
        local = expansions._replace(cwd=str(cwd / folder))
        paths.extend(joined_paths([*gated_test_targets(segment, local).paths, *mutations], (folder,)))
    return paths


def _scratch_worktree_add(command, segment, scope) -> bool:
    if not scope.outer or segment[:3] != ["git", "worktree", "add"]:
        return False
    directory = trusted_directory(command, scope.cwd)
    if directory and os.path.isabs(directory) and _tokens(command)[3:] == segment and len(segment) == 6:
        destination, flag, branch = segment[3:]
        if flag == "-b" and re.fullmatch(r"\.\./[\w-]+", destination) and re.fullmatch(r"[\w-]+", branch):
            project = project_root(str(scope.cwd))
            base = logical_directory(directory, scope.cwd)
            if base is None:
                return False
            target = base / destination
            return temp_scratch_repo(directory, project) and outside_project_temp(target, project) and outside_project(str(target), project)
    if line_segments(command)[0] != segment:
        return False
    operands = segment[3:]
    while operands and operands[0] in {"--detach", "-d", "--quiet", "-q", "--force", "-f"}:
        operands = operands[1:]
    if operands[:1] == ["--"]:
        operands = operands[1:]
    if len(operands) not in {1, 2} or (len(operands) == 2 and not re.fullmatch(r"[\w/][\w./~-]*", operands[1])):
        return False
    raw = operands[0]
    if not raw.startswith(("/", "'/", '"/', '"$TMPDIR"/', '"$TMPDIR/', "$TMPDIR/")):
        return False
    path = command_expansions(command, scope.cwd).path(raw)
    if not path or not re.fullmatch(r"/[\w./-]+", path) or ".." in Path(path).parts:
        return False
    return outside_project_temp(path, project_root(str(scope.cwd)))


def _rewrite_tests(command, segment, scope, folders, base, coming) -> bool:
    if _scratch_worktree_add(command, segment, scope):
        return False
    if coming and coming.verb == MERGE and folders and scope.outer:
        directory = trusted_directory(command, scope.cwd)
        tokens = _tokens(command)
        if directory in folders and tokens[3:] == segment and not directory.startswith("~"):
            listed = changed_paths(coming, scope.cwd / directory / base)
            return listed is None or any(map(is_test_path, listed))
    return _tracks_tests(base, folders, scope.cwd, coming)


def _uncertain_delete(segment, expansions, folders: Directories, cwd: Path) -> bool:
    index = _command_word_index(segment)
    if index >= len(segment) or _basename(segment[index]) not in REMOVE_VERBS:
        return False
    for raw in _without_redirects(segment[index + 1:]):
        if expansions.path(raw) is not None:
            continue
        pattern = expansions.pattern(raw)
        if not pattern or not folders:
            return True
        if any(_delete_glob_needs_gate(pattern, cwd / folder, cwd) for folder in folders):
            return True
    return False


def _delete_glob_needs_gate(pattern: str, folder: Path, cwd: Path) -> bool:
    parent, name = os.path.split(os.path.expanduser(pattern))
    if any(char in parent for char in "*?[") or "[" in name or not name:
        return True
    project = Path(project_root(str(cwd))).resolve()
    try:
        with os.scandir(folder / parent) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_ENTRIES:
                    return True
                if fnmatch.fnmatchcase(entry.name.casefold(), name.casefold()):
                    target = Path(entry.path).resolve()
                    if project.is_relative_to(target) or not outside_project(str(target), str(project)):
                        return True
    except (OSError, RuntimeError):
        return True
    return False


def _tracks_tests(base: str, folders: Directories, cwd: Path, coming: Incoming | None) -> bool:
    if folders is None:
        return True
    for folder in folders:
        directory = cwd / folder / base
        if _scratch_directory(str(directory), cwd) or foreign_scratch_repo(str(directory), project_root(str(cwd))):
            continue
        listed = tracked_paths(directory) if coming is None else changed_paths(coming, directory)
        if listed is None or any(map(is_test_path, listed)):
            return True
    return False


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
            return None
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


def _in_folders(paths: list[str], folders: Directories) -> list[str] | None:
    joined = joined_paths(paths, folders)
    return None if folders is None and len(joined) < len(paths) else joined


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
    return tree_paths(lookup.tree, lookup.pathspecs, directory)


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


def staged_only(command: str) -> bool:
    segments = line_segments(command)
    return bool(segments) and all(map(_index_only_rm, segments))


def _index_only_rm(segment: list[str]) -> bool:
    index = _command_word_index(segment)
    if _without_redirects(segment) != segment or index >= len(segment) or _basename(segment[index]) != "git":
        return False
    words = [_bare(word) for word in segment[index + 1:]]
    _, position = _git_base(words)
    rest = words[position + 1:]
    options = [word for word in (rest[:rest.index("--")] if "--" in rest else rest) if word.startswith("-") and word != "-"]
    if words[position:position + 1] != ["rm"]:
        return False
    state: dict[str, bool] = dict.fromkeys(RM_KEEPING, False)
    return all(_rm_option(word, state) for word in options) and any(state.values())


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
