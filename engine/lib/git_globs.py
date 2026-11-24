from __future__ import annotations

import functools
import glob
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

GIT_TIMEOUT_SECONDS = 2
LITERAL_MAGIC = ":(literal)"
NO_REPOSITORY = "not a git repository"
NO_REPOSITORY_EXIT = 128


def removed_paths(pathspecs: Sequence[str], directory: Path) -> list[str] | None:
    expanded = [match for pattern in pathspecs for match in _shell_matches(pattern, directory)]
    listed = _git_list(["ls-files", "-z", "--", *pathspecs, *(LITERAL_MAGIC + match for match in expanded)], directory)
    return None if listed is None else sorted({*expanded, *listed})


def tree_paths(tree: str, pathspecs: Sequence[str], directory: Path) -> list[str] | None:
    if tree.startswith("-"):
        return None
    return _git_list(["ls-tree", "-r", "-z", "--name-only", tree, "--", *pathspecs], directory)


def tracked_paths(directory: Path) -> list[str] | None:
    return _git_list(["ls-files", "-z"], directory)


def outside_work_trees(path: Path) -> bool:
    directory = path
    while not directory.is_dir() and directory != directory.parent:
        directory = directory.parent
    return _no_work_tree(str(directory))


@functools.lru_cache(maxsize=512)
def _no_work_tree(directory: str) -> bool:
    clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        found = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, errors="surrogateescape", timeout=GIT_TIMEOUT_SECONDS, check=False, env=clean,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return found.returncode == NO_REPOSITORY_EXIT and NO_REPOSITORY in found.stderr


def _git_list(arguments: list[str], directory: Path) -> list[str] | None:
    try:
        listed = subprocess.run(
            ["git", "-C", str(directory), *arguments],
            capture_output=True, text=True, errors="surrogateescape", timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [path for path in listed.stdout.split("\0") if path] if listed.returncode == 0 else None


def _shell_matches(pattern: str, directory: Path) -> list[str]:
    return glob.glob(pattern, root_dir=directory, recursive=True, include_hidden=True)
