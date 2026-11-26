from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

try:
    from . import git_port
except ImportError:
    import git_port


def removed_paths(pathspecs: Sequence[str], directory: Path) -> list[str] | None:
    return git_port.view().matched_paths(directory, pathspecs)


def tree_paths(tree: str, pathspecs: Sequence[str], directory: Path) -> list[str] | None:
    return git_port.view().tree_paths(directory, tree, pathspecs)


def tracked_paths(directory: Path) -> list[str] | None:
    return git_port.view().tracked_paths(directory)


def outside_work_trees(path: Path) -> bool:
    directory = path
    while not directory.is_dir() and directory != directory.parent:
        directory = directory.parent
    return not git_port.view().has_work_tree(directory)
