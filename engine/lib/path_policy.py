from __future__ import annotations

import os
from pathlib import Path

PROJECT_MARKERS = frozenset({
    ".gitignore", "pyproject.toml", "setup.py", "setup.cfg", "package.json", "Cargo.toml",
    "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "Makefile", "CMakeLists.txt",
    "Gemfile", "composer.json", "project.toml", "AGENTS.md", "CLAUDE.md", ".agent-discipline.json",
})


def real_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.realpath(os.path.expanduser(os.fspath(path))))


def lexical_absolute(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def anchored(path: str | os.PathLike[str], cwd: Path) -> Path:
    candidate = Path(os.path.expanduser(os.fspath(path)))
    return candidate if candidate.is_absolute() else cwd / candidate


def anchored_identity(path: str | os.PathLike[str], cwd: Path) -> Path:
    return Path(os.path.realpath(anchored(path, cwd)))


def marked_project(directory: Path, *, all_ancestors: bool = False) -> Path | None:
    home = None if all_ancestors else Path.home().resolve()
    for parent in (directory, *directory.parents):
        if not all_ancestors and (parent == home or parent == parent.parent):
            break
        if (parent / ".git").exists() or any((parent / name).is_file() for name in PROJECT_MARKERS):
            return parent
    return None


def exited_project(cwd: Path) -> Path | None:
    resolved = cwd.resolve()
    if cwd == resolved:
        return None
    for parent in cwd.parents:
        root = parent.resolve()
        if not resolved.is_relative_to(root) and marked_project(root) == root:
            return root
    return None
