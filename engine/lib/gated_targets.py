from __future__ import annotations

from typing import NamedTuple

try:
    from .archive_members import ArchiveRead
except ImportError:
    from archive_members import ArchiveRead

class GitLookup(NamedTuple):
    base: str
    pathspecs: tuple[str, ...]
    tree: str | None
    strict: bool


class GatedTargets(NamedTuple):
    paths: tuple[str, ...]
    unresolved: bool
    lookup: GitLookup | None = None
    indexable: bool = False
    archive: ArchiveRead | None = None
    worktree: str | None = None


NO_TARGETS = GatedTargets((), False)
UNRESOLVED = GatedTargets((), True)


def _read_targets(read: ArchiveRead | None) -> GatedTargets:
    return UNRESOLVED if read is None else GatedTargets((), False, archive=read)
