from __future__ import annotations

from pathlib import Path

try:
    from . import git_port
    from .path_policy import PROJECT_MARKERS
except ImportError:
    import git_port
    from path_policy import PROJECT_MARKERS


def project_location(cwd: Path, *, until: float | None = None,
                     markers: frozenset[str] = PROJECT_MARKERS) -> tuple[Path, bool] | None:
    home = Path.home().resolve()
    if cwd == home:
        return None
    git_root = git_port.view().toplevel(cwd, until=until)
    if git_root is not None:
        return (git_root.resolve(), True) if git_root.resolve() != home else None
    for directory in (cwd, *cwd.parents):
        if directory == home or directory == directory.parent:
            break
        if (directory / ".git").exists():
            raise OSError("Git project root unavailable")
        if any((directory / marker).is_file() for marker in markers):
            return directory, False
    return None
