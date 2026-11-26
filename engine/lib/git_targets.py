from __future__ import annotations

import os
import re
from typing import NamedTuple

try:
    from .git_history_targets import filter_targets, submodule_targets
    from .archive_commands import git_apply_read
    from .archive_members import ArchiveRead
    from .git_writes import WORKTREE_COMMANDS, cleans_files, worktree_rewrite, written_paths
    from .shell_expansion import Expansions
    from .shell_parse import _bare, _expand_home
    from .gated_targets import GitLookup, GatedTargets, NO_TARGETS, UNRESOLVED, _read_targets
    from .copy_targets import _copy_targets
except ImportError:
    from git_history_targets import filter_targets, submodule_targets
    from archive_commands import git_apply_read
    from archive_members import ArchiveRead
    from git_writes import WORKTREE_COMMANDS, cleans_files, worktree_rewrite, written_paths
    from shell_expansion import Expansions
    from shell_parse import _bare, _expand_home
    from gated_targets import GitLookup, GatedTargets, NO_TARGETS, UNRESOLVED, _read_targets
    from copy_targets import _copy_targets

GIT_PATH_COMMANDS = frozenset({"checkout", "restore", "rm"})
GIT_CHANGING_COMMANDS = GIT_PATH_COMMANDS | {"mv", "apply", "stash"} | WORKTREE_COMMANDS | {
    "reset", "switch", "clean", "worktree", "clone", "filter-branch", "submodule", "read-tree", "sparse-checkout",
}
GIT_VALUE_FLAGS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})
GIT_BASE_FLAGS = frozenset({"-C", "--work-tree"})
WORK_TREE_PREFIX = "--work-tree="
GLOB_CHARS = frozenset("*?[")
PATHSPEC_FILE_FLAG = "--pathspec-from-file"
GIT_MERGE_FILE = "merge-file"
MERGE_PRINT_FLAGS = frozenset({"-p", "--stdout", "--object-id"})
MERGE_VALUE_FLAGS = frozenset({"-L"})
GIT_DRY_APPLY_FLAGS =frozenset({"--check", "--stat", "--numstat", "--summary"})
RESTORE_STAGED = frozenset({"--staged", "-S"})
RESTORE_WORKTREE = frozenset({"--worktree", "-W", "-SW", "-WS"})
RESTORE_PLAIN_FLAGS = frozenset({"--staged", "-S", "-q", "--quiet"})


class _GitSource(NamedTuple):
    plain_call: bool
    stdin: str | None
    cwd: str | None


class _GitCall(NamedTuple):
    command: str
    base: str
    rest: list[str]
    indexable: bool


def _merged_file(args: list[str]) -> tuple[str, str] | None:
    bare = [_bare(token) for token in args]
    base, position = _git_base(bare)
    if position >= len(bare) or bare[position] != GIT_MERGE_FILE or MERGE_PRINT_FLAGS.intersection(bare[position + 1:]):
        return None
    operands = _merge_operands(args[position + 1:])
    return (base, operands[0]) if operands else None


def _merge_operands(rest: list[str]) -> list[str]:
    operands: list[str] = []
    position = 0
    while position < len(rest):
        word = _bare(rest[position])
        if word == "--":
            return operands + rest[position + 1:]
        operands += [] if word.startswith("-") else [rest[position]]
        position += 2 if word in MERGE_VALUE_FLAGS else 1
    return operands


def _git_changes_paths(args: list[str], expansions: Expansions) -> bool:
    _, position = _git_base([_bare(token) for token in args])
    if position >= len(args):
        return False
    if args[:1] == ["-C"] and position == 2 and args[2:] == ["worktree", "list"]:
        if re.fullmatch(r'"\$(?:[A-Za-z_]\w*|\{[A-Za-z_]\w*\})"', args[1], re.ASCII):
            return False
    subcommand = expansions.pattern(args[position])
    return subcommand is None or subcommand in GIT_CHANGING_COMMANDS


def _git_base(words: list[str]) -> tuple[str, int]:
    position = 0
    base = ""
    while position < len(words) and words[position].startswith("-"):
        flag = words[position]
        base = os.path.join(base, _base_value(flag, words[position + 1:position + 2]))
        position += 2 if flag in GIT_VALUE_FLAGS else 1
    return base, position


def _base_value(flag: str, following: list[str]) -> str:
    if flag.startswith(WORK_TREE_PREFIX):
        return flag[len(WORK_TREE_PREFIX):]
    return following[0] if flag in GIT_BASE_FLAGS and following else ""


def _git_targets(words: list[str], source: _GitSource) -> GatedTargets:
    base, position = _git_base(words)
    command, rest = (words[position], words[position + 1:]) if position < len(words) else ("", [])
    found = _git_command_targets(words, source)
    if cleans_files(command, rest):
        return UNRESOLVED
    written = tuple(_expand_home(os.path.join(base, path)) for path in written_paths(command, rest))
    return found._replace(paths=found.paths + written) if written else found


def _git_command_targets(words: list[str], source: _GitSource) -> GatedTargets:
    base, position = _git_base(words)
    command, rest = (words[position], words[position + 1:]) if position < len(words) else ("", [])
    if command == "filter-branch":
        return filter_targets(rest, base, source.cwd)
    if command == "submodule":
        return submodule_targets(rest, base, source.cwd)
    rewrites = worktree_rewrite(command, rest, os.path.join(source.cwd or ".", base))
    if command in GIT_PATH_COMMANDS:
        found = _pathspec_targets(_GitCall(command, base, rest, source.plain_call and _directory_flags_only(words[:position])))
        return found._replace(worktree=base) if rewrites else found
    if command == "mv":
        return GatedTargets(tuple(_expand_home(os.path.join(base, path)) for path in _copy_targets("mv", rest, None, os.path.join(source.cwd or ".", base))), False)
    dry_apply = bool(GIT_DRY_APPLY_FLAGS.intersection(rest)) and "--apply" not in rest
    if command == "apply" and not dry_apply:
        return _read_targets(_based(git_apply_read(rest, source.stdin), base))
    if command == "stash" and rest[:1] in (["pop"], ["apply"]):
        return UNRESOLVED
    return GatedTargets((), False, worktree=base) if rewrites else NO_TARGETS


def _based(read: ArchiveRead | None, base: str) -> ArchiveRead | None:
    if read is None or not base:
        return read
    return read._replace(source=os.path.join(base, read.source), directory=os.path.join(base, read.directory))


def _directory_flags_only(flags: list[str]) -> bool:
    return len(flags) % 2 == 0 and all(flag == "-C" for flag in flags[::2])


def _pathspec_targets(call: _GitCall) -> GatedTargets:
    options = call.rest[:call.rest.index("--")] if "--" in call.rest else call.rest
    if _unstages_only(call, options):
        return NO_TARGETS
    paths = call.rest[call.rest.index("--") + 1:] if "--" in call.rest else [word for word in call.rest if not word.startswith("-")]
    tree = _source_tree(call, options)
    wide = [path for path in paths if GLOB_CHARS.intersection(path) or _is_directory_pathspec(path)]
    globbed = any(GLOB_CHARS.intersection(path) for path in paths)
    closed = (call.command == "checkout" and "--" not in call.rest) or not call.indexable or (tree is not None and globbed)
    unreadable = any(option.startswith(PATHSPEC_FILE_FLAG) for option in options) or any(path.startswith(":") for path in paths)
    if unreadable or (wide and closed):
        return UNRESOLVED
    named = tuple(_expand_home(os.path.join(call.base, path)) for path in paths if path not in wide)
    lookup = GitLookup(call.base, tuple(paths), tree, call.command == "rm" or bool(wide))
    return GatedTargets(named, False, lookup, call.indexable)


def _unstages_only(call: _GitCall, options: list[str]) -> bool:
    if call.command != "restore" or not RESTORE_STAGED.intersection(options):
        return False
    worktree = RESTORE_WORKTREE.intersection(options)
    sourced = _option_value(options, ("-s", "--source")) is not None
    return not worktree and not sourced and all(option in RESTORE_PLAIN_FLAGS for option in options if option.startswith("-"))


def _source_tree(call: _GitCall, options: list[str]) -> str | None:
    if call.command == "restore":
        source = _option_value(options, ("-s", "--source"))
        return source if source is not None else ("HEAD" if "--staged" in options or "-S" in options else None)
    trees = [word for word in options if not word.startswith("-")] if call.command == "checkout" else []
    return trees[0] if trees else None


def _option_value(options: list[str], flags: tuple[str, str]) -> str | None:
    for position, word in enumerate(options):
        if word.startswith(flags[1] + "="):
            return word[len(flags[1]) + 1:]
        if word in flags and position + 1 < len(options):
            return options[position + 1]
    return None


def _is_directory_pathspec(path: str) -> bool:
    return path in {"", ".", ".."} or path.endswith(("/", "/.", "/..")) or path.startswith(":")
