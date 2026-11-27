from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

from .commit_git_resolution import SHELL_ALIAS, GitCall
from .commit_inputs import UnreadableCommit
from .git_port import view
from .regular_file import open_regular

INDEX_WRITERS = frozenset({
    "add", "am", "apply", "checkout", "cherry-pick", "merge", "mv", "pull", "read-tree",
    "rebase", "reset", "restore", "revert", "rm", "stage", "stash", "update-index",
    SHELL_ALIAS,
})
WORKTREE_LONG_OPTIONS = frozenset({
    "--all", "--include", "--interactive", "--only", "--patch", "--pathspec-from-file",
})
VALUE_LONG_OPTIONS = frozenset({
    "--author", "--cleanup", "--date", "--file", "--fixup", "--message", "--reedit-message",
    "--reuse-message", "--squash", "--template", "--trailer",
})
WORKTREE_SHORT_FLAGS = frozenset("aiop")
VALUE_SHORT_FLAGS = frozenset("CFcmt")
ATTACHED_VALUE_SHORT_FLAGS = frozenset("Su")
NEUTRAL_LONG_OPTIONS = frozenset({
    "--no-verify", "--verify", "--no-edit", "--edit", "--quiet", "--verbose", "--signoff", "--no-signoff", "--amend",
    "--allow-empty", "--allow-empty-message", "--no-gpg-sign", "--gpg-sign", "--no-post-rewrite", "--status",
    "--no-status", "--reset-author", "--no-verbose", "--untracked-files",
})
NEUTRAL_VALUE_OPTIONS = frozenset({
    "--file", "--message", "--author", "--date", "--cleanup", "--trailer", "--template", "--reuse-message",
    "--reedit-message",
})
NEUTRAL_SHORT_FLAGS = frozenset("aiosqvne")


def commit_reads_worktree(args: list[str]) -> bool:
    cursor = 0
    while cursor < len(args):
        token = args[cursor]
        if token == "--":
            return cursor + 1 < len(args)
        if not token.startswith("-") or token == "-":
            return True
        reads, takes_next = _long_option(token) if token.startswith("--") else _short_bundle(token[1:])
        if reads:
            return True
        cursor += 2 if takes_next else 1
    return False


def _long_option(token: str) -> tuple[bool, bool]:
    name, separator, _value = token.partition("=")
    if any(option.startswith(name) for option in WORKTREE_LONG_OPTIONS):
        return True, False
    return False, not separator and any(option.startswith(name) for option in VALUE_LONG_OPTIONS)


def _short_bundle(flags: str) -> tuple[bool, bool]:
    for index, flag in enumerate(flags):
        if flag in WORKTREE_SHORT_FLAGS:
            return True, False
        if flag in ATTACHED_VALUE_SHORT_FLAGS:
            return False, False
        if flag in VALUE_SHORT_FLAGS:
            return False, index == len(flags) - 1
    return False, False


class CommitSources(NamedTuple):
    include_index: bool
    paths: list[str]


def commit_sources(args: list[str]) -> CommitSources:
    paths = []
    modes = set()
    cursor = 0
    while cursor < len(args):
        token = args[cursor]
        cursor += 1
        if token == "--":
            paths.extend(args[cursor:])
            break
        if token in {"--all", "--include", "--only"}:
            modes.add({"--all": "a", "--include": "i", "--only": "o"}[token])
        elif token.startswith("--"):
            name, separator, _ = token.partition("=")
            if name in NEUTRAL_LONG_OPTIONS and (not separator or name in {"--gpg-sign", "--untracked-files"}):
                continue
            if name not in NEUTRAL_VALUE_OPTIONS:
                raise UnreadableCommit("Cannot resolve the worktree commit options.")
            cursor += int(not separator)
        elif token.startswith("-"):
            for pos, flag in enumerate(token[1:], 2):
                if flag in "mFtcC":
                    cursor += int(pos == len(token))
                    break
                if flag in ATTACHED_VALUE_SHORT_FLAGS:
                    break
                if flag not in NEUTRAL_SHORT_FLAGS:
                    raise UnreadableCommit("Cannot resolve the worktree commit flags.")
                if flag in "aio":
                    modes.add(flag)
        else:
            paths.append(token)
    if len(modes) > 1 or (paths and "a" in modes) or (not paths and modes & {"i", "o"}):
        raise UnreadableCommit("Cannot resolve the worktree commit paths.")
    return CommitSources(not paths or "i" in modes, paths)


def index_blobs(repo: Path, call: GitCall, refusal: str):
    yield from ((path, view().index_bytes(repo, path, call.index_file)) for path in _staged(repo, call.index_file, refusal))


def commit_blobs(repo: Path, call: GitCall, refusal: str, additions: set[str]):
    sources = commit_sources(call.args)
    amended = _amended_paths(repo, call, refusal)
    if not additions and not amended and not commit_reads_worktree(call.args):
        yield from index_blobs(repo, call, refusal)
        return
    selected, changes = _selected_worktree(repo, call, sources, refusal)
    if sources.include_index:
        selected.update(additions)
        staged = set(_staged(repo, call.index_file, refusal)) | {p for p in amended if view().is_tracked(repo, p)}
        yield from ((p, view().index_bytes(repo, p, call.index_file)) for p in staged - selected)
    else:
        for path in amended - selected:
            raw = view().head_bytes(repo, path)
            if raw is None:
                raise UnreadableCommit(refusal)
            yield path, raw
    for path in sorted(selected):
        if changes.get(path) == "D" or (path in additions and not os.path.lexists(repo / path)):
            continue
        target = repo / path
        try:
            if target.is_symlink():
                yield path, os.fsencode(os.readlink(target))
            else:
                with open_regular(target) as handle:
                    yield path, handle.read()
        except OSError as exc:
            raise UnreadableCommit(refusal) from exc


def _selected_worktree(repo, call, sources, refusal):
    status = view().porcelain_status(repo)
    if status is None or any(len(row) < 4 or "U" in row[:2] for row in status):
        raise UnreadableCommit(refusal)
    changes = {row[3:]: row[1] for row in status if row[:2] != "??"}
    selected = {path for path, state in changes.items() if state != " "}
    if not sources.paths and not commit_reads_worktree(call.args):
        selected.clear()
    if sources.paths:
        selected = _named_paths(call, repo, sources.paths, refusal)
        deleted = {row[3:] for row in status if row[0] == "D"}
        for path in deleted & selected:
            if not os.path.lexists(repo / path):
                changes[path] = "D"
    return selected, changes


def _amended_paths(repo: Path, call: GitCall, refusal: str) -> set[str]:
    if "--amend" not in call.args:
        return set()
    paths = view().commit_paths(repo, "HEAD")
    if paths is None:
        raise UnreadableCommit(refusal)
    return set(paths)


def _named_paths(call: GitCall, repo: Path, specs: list[str], refusal: str) -> set[str]:
    paths = set()
    directory = call.cwd.resolve()
    for spec in specs:
        if not spec or spec.startswith(":") or any(char in spec for char in "$`~{}*?[]()\\"):
            raise UnreadableCommit(refusal)
        names = view().tracked_paths(directory, [spec])
        if names is None:
            raise UnreadableCommit(refusal)
        if view().resolve_commit(repo, "HEAD"):
            old = view().tree_paths(directory, "HEAD", [spec])
            if old is None:
                raise UnreadableCommit(refusal)
            names = [*names, *old]
        if not names:
            raise UnreadableCommit(refusal)
        paths.update(os.path.relpath(directory / name, repo) for name in names)
    return paths


def _staged(repo: Path, index: Path | None, refusal: str) -> list[str]:
    paths = view().staged_paths(repo, index)
    if paths is None:
        raise UnreadableCommit(refusal)
    return paths


def staged_additions(repo: Path, call: GitCall, previous: list[GitCall], refusal: str) -> set[str]:
    paths = set()
    for earlier in previous:
        if earlier.verb not in INDEX_WRITERS or earlier.repo() != repo:
            continue
        if earlier.verb not in {"add", "stage"} or earlier.index_file != call.index_file:
            raise UnreadableCommit(refusal)
        args = earlier.args[1:] if earlier.args[:1] == ["--"] else earlier.args
        if not args:
            raise UnreadableCommit(refusal)
        for spec in args:
            if not spec or spec.startswith(("-", ":")) or any(c in spec for c in "$`~{}*?[]()\\"):
                raise UnreadableCommit(refusal)
            name = os.path.relpath(earlier.cwd.resolve() / spec, repo)
            if name == ".." or name.startswith("../"):
                raise UnreadableCommit(refusal)
            paths.update(_added_paths(repo, name, refusal))
    return paths


def _added_paths(repo: Path, name: str, refusal: str) -> set[str]:
    if not (repo / name).is_dir() or (repo / name).is_symlink():
        return {name}
    status = view().porcelain_status(repo)
    if status is None or any(len(row) < 4 or "U" in row[:2] for row in status):
        raise UnreadableCommit(refusal)
    return {row[3:] for row in status if row[1] != " " and Path(row[3:]).is_relative_to(name)}
