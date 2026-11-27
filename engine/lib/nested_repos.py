from __future__ import annotations

import os
import time
from pathlib import Path

try:
    from . import shell_snapshot
except ImportError:
    import shell_snapshot

CACHE = "nested_repos"
MAX_CACHED_ROOTS = 8
MAX_WATCHED_DIRS = 1024
SEARCH_SECONDS = 0.3


def _directory_stamp(directory: Path) -> int:
    try:
        return directory.stat().st_mtime_ns
    except OSError:
        return -1


def _is_fresh(root: Path, record: dict, until: float) -> bool:
    if not record.get("complete"):
        return False
    return all(time.monotonic() < until and _directory_stamp(root / raw) == stamp
               for raw, stamp in record["watched"].items())


def listing(root: Path, known: dict, until: float, *, refresh: bool = False) -> dict:
    until = min(until, time.monotonic() + SEARCH_SECONDS)
    if not refresh and _is_fresh(root, known, until):
        return known
    repos, watched, skipped = set(), {}, set()
    try:
        _walk(root, until, repos, watched, skipped)
        complete = True
    except (OSError, shell_snapshot.WalkOverflow):
        repos.update(known.get("repos", ()))
        complete = False
    return {"repos": sorted(repos), "watched": watched, "skipped": sorted(skipped), "complete": complete}


def _walk(root: Path, until: float, repos: set[str], watched: dict, skipped: set[str]) -> None:
    pending = [root]
    while pending:
        shell_snapshot.check_deadline(until)
        directory = pending.pop()
        raw = directory.relative_to(root).as_posix()
        watched[raw] = _directory_stamp(directory)
        if len(watched) > MAX_WATCHED_DIRS:
            raise shell_snapshot.WalkOverflow
        with os.scandir(directory) as children:
            for child in children:
                shell_snapshot.check_deadline(until)
                if child.name == ".git":
                    if directory != root and not child.is_symlink():
                        repos.add(raw)
                elif child.is_dir(follow_symlinks=False):
                    path = Path(child.path)
                    relative = path.relative_to(root).as_posix()
                    if child.name in shell_snapshot.SKIP_FOLDERS:
                        skipped.add(relative)
                    else:
                        pending.append(path)


def remember(state: dict, records: dict[str, dict]) -> dict:
    known = {root: record for root, record in state.get(CACHE, {}).items() if root not in records}
    return {**state, CACHE: dict(list((known | records).items())[-MAX_CACHED_ROOTS:])}
