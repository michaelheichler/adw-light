from __future__ import annotations

import os
from pathlib import Path


def real_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.realpath(os.path.expanduser(os.fspath(path))))


def lexical_absolute(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def anchored(path: str | os.PathLike[str], cwd: Path) -> Path:
    candidate = Path(os.path.expanduser(os.fspath(path)))
    return candidate if candidate.is_absolute() else cwd / candidate


def anchored_identity(path: str | os.PathLike[str], cwd: Path) -> Path:
    return Path(os.path.realpath(anchored(path, cwd)))
