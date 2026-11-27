from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    from . import git_port
    from .parallel_calls import gathered
except ImportError:
    import git_port
    from parallel_calls import gathered

CONFIG_NAME = ".agent-discipline.json"
IGNORED_MARK = "!!"
REFRESH_SECONDS = 30.0
CHEAP_SECONDS = 0.03
SEARCH_SECONDS = 1.0
START_SECONDS = 2.0
MAX_WATCHED = 512
MAX_ROOTS = 40
IGNORE_FILES = (".gitignore", ".git/info/exclude")


@dataclass(frozen=True, slots=True)
class Listing:
    paths: tuple[str, ...]
    complete: bool
    record: dict | None = None


def _stamp(path: Path) -> list[int]:
    try:
        info = path.lstat()
    except OSError:
        return []
    return [info.st_mode, info.st_size, info.st_mtime_ns, info.st_ino]


def _stamps(root: Path, raws) -> dict[str, list[int]]:
    return {raw: _stamp(root / raw if raw else root) for raw in raws}


def _fresh(root: Path, record: dict, *, trust_cheap: bool = False) -> bool:
    age = time.time() - record.get("at", 0)
    cheap = record.get("cost", 0) < CHEAP_SECONDS and not (trust_cheap and record.get("complete") is True)
    if not 0 <= age < REFRESH_SECONDS or cheap:
        return False
    watched = record.get("watched")
    return isinstance(watched, dict) and _stamps(root, watched) == watched


def stored(record: object) -> Listing | None:
    if not isinstance(record, dict) or not isinstance(record.get("paths"), list):
        return None
    return Listing(tuple(raw for raw in record["paths"] if isinstance(raw, str)), record.get("complete") is True)


def _bounded(call: Callable[[float], list[str] | None], until: float) -> list[str] | None:
    left = until - time.monotonic()
    return call(left) if left > 0 else None


def _git_search(root: Path, until: float) -> tuple[list[str], list[str]] | None:
    view = git_port.view()
    names, folders = gathered([
        lambda: _bounded(lambda left: view.ignored_files(root, config_only=True, timeout=left), until),
        lambda: _bounded(lambda left: view.ignored_files(root, directory=True, timeout=left), until),
    ])
    if names is None or folders is None:
        return None
    return names, sorted((raw.rstrip("/") for raw in folders if raw.endswith("/")), key=lambda raw: raw.count("/"))


def _walk_search(root: Path, until: float, skip: frozenset[str]) -> tuple[list[str], list[str]] | None:
    found, pending = [], [str(root)]
    while pending:
        try:
            with os.scandir(pending.pop()) as children:
                for child in children:
                    if time.monotonic() >= until:
                        return None
                    if child.name.casefold() == CONFIG_NAME:
                        found.append(Path(os.path.relpath(child.path, root)).as_posix())
                    elif child.name != ".git" and child.is_dir(follow_symlinks=False):
                        pending.append(child.path)
        except OSError:
            return None
    return found, sorted(name for name in skip if (root / name).is_dir())


def listing(root: Path, in_git: bool, record: object, until: float, *, skip: frozenset[str] = frozenset(),
            search: bool = True, refresh: bool = False, trust_cheap: bool = False) -> Listing:
    known = stored(record)
    if known is not None and not refresh and (not search or _fresh(root, record, trust_cheap=trust_cheap)):
        return known
    started = time.monotonic()
    found = _git_search(root, until) if in_git else _walk_search(root, until, skip)
    cost = time.monotonic() - started
    if found is None:
        paths = known.paths if known else ()
        return Listing(paths, False, {"at": time.time(), "paths": list(paths), "watched": {}, "complete": False,
                                      "cost": cost})
    names, folders = found
    watched = dict.fromkeys(["", *IGNORE_FILES, *(os.path.dirname(raw) for raw in names), *folders])
    record = {"at": time.time(), "paths": sorted(names), "watched": _stamps(root, list(watched)[:MAX_WATCHED]),
              "complete": True, "cost": cost}
    return Listing(tuple(record["paths"]), True, record)


def made_during(mark: object, windows: object) -> bool:
    if not isinstance(mark, list) or len(mark) != 2 or mark[0] != IGNORED_MARK or len(mark[1]) < 4:
        return False
    changed_at = mark[1][3]
    return isinstance(windows, list) and any(
        isinstance(window, list) and len(window) == 2 and window[0] <= changed_at <= window[1] for window in windows
    )


def remember(data: dict, root: str, record: dict | None) -> dict:
    if record is None:
        return data
    kept = {key: value for key, value in data.get("ignored", {}).items() if key != root}
    return {**data, "ignored": dict(list({**kept, root: record}.items())[-MAX_ROOTS:])}
