from __future__ import annotations

import math
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from . import imported_baseline, path_policy, scan_input
from .baseline import (
    OutOfTime, baseline_mode, fits_deadline, remaining, split_fallback_with_snapshot, split_with_snapshot,
)
from .config import resolve_outcome
from .imported_baseline import Snapshot
from .scanner import read_scannable, scan_all
from .temp_scope import cwd_forms, outside_project_temp, temp_roots
from .tests_policy import project_root

STOP_SCAN_CAP = 6.0
FULL_TURN = STOP_SCAN_CAP / 2
MAX_NAMED_SKIPPED = 3
INHERITED_FIELD = "inherited"
Split = Callable[[dict], tuple[list[dict], list[dict]]]


@dataclass(slots=True)
class Rescan:
    findings: list[dict] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    clean: list[str] = field(default_factory=list)
    cut: str = ""
    cut_left: float = 0.0
    stamps: dict[str, str] = field(default_factory=dict)
    paused: set[str] = field(default_factory=set)


def deadline(started_at: float | None = None, *, clock: Callable[[], float] = time.monotonic) -> float:
    return (clock() if started_at is None else started_at) + STOP_SCAN_CAP


def _tagged(split: Split, cfg: dict) -> list[dict]:
    if baseline_mode(cfg) != "none":
        return split(cfg)[0]
    owned, inherited = split({**cfg, "baseline": "report"})
    return [*owned, *({**row, INHERITED_FIELD: True} for row in inherited)]


def _path_rows(path: Path, cfg: dict, snapshot: Snapshot | None, until: float) -> list[dict]:
    text = read_scannable(path, cfg)
    remaining(until)
    if text is None:
        stamp = path.stat()
        found = scan_input.fallback_findings(path)
        remaining(until)
        rows = _tagged(lambda mode: split_fallback_with_snapshot(path, found, mode, snapshot, until=until), cfg)
        digest = f"{stamp.st_size}:{stamp.st_mtime_ns}"
    else:
        fits_deadline(text, lambda: remaining(until))
        found = scan_all(str(path), text, cfg)
        remaining(until)
        rows = _tagged(lambda mode: split_with_snapshot(path, found, mode, snapshot, text, until=until), cfg)
        digest = sha256(text.encode("utf-8")).hexdigest()
    return [{**row, "content_hash": digest, "path": str(path)} for row in rows if resolve_outcome(row, cfg) == "block"]


def _owned_rows(path: Path, cfg: dict, until: float) -> list[dict]:
    with scan_alarm(until):
        snapshot = imported_baseline.lookup(cfg, path)
        return _path_rows(path, cfg, snapshot, until)


def _expire(_signum: int, _frame: object) -> None:
    raise OutOfTime


def _alarm_free() -> bool:
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        return False
    return signal.getitimer(signal.ITIMER_REAL)[0] == 0


@contextmanager
def scan_alarm(until: float) -> Iterator[None]:
    left = remaining(until)
    if math.isinf(left) or not _alarm_free():
        yield
        return
    previous = signal.signal(signal.SIGALRM, _expire)
    try:
        signal.setitimer(signal.ITIMER_REAL, left)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@dataclass(frozen=True, slots=True)
class ScanPaths:
    directory: Path
    project: str
    roots: tuple[Path, ...]
    project_forms: tuple[Path, ...] | None = None

    @classmethod
    def from_cwd(cls, cwd: str) -> ScanPaths:
        directory = Path(cwd or ".").absolute()
        project = project_root(str(directory))
        forms = cwd_forms(project) if project and Path(project).is_absolute() else None
        return cls(directory, project, temp_roots(), forms)

    def candidate(self, raw_path: object) -> Path | None:
        if not isinstance(raw_path, str) or not raw_path:
            return None
        path = path_policy.anchored(raw_path, self.directory)
        if outside_project_temp(path, self.project, roots=self.roots, project=self.project_forms) or not path.is_file():
            return None
        return path


def _scan_candidates(paths: list[str], scan_paths: ScanPaths, excluded: list[str]) -> Iterator[tuple[str, Path]]:
    for raw_path in paths:
        if (path := scan_paths.candidate(raw_path)) is not None:
            yield raw_path, path
        else:
            excluded.append(raw_path)


def rescan(paths: list[str], scan_paths: ScanPaths, cfg: dict, until: float,
           clock: Callable[[], float] = time.monotonic, pause: frozenset[str] = frozenset()) -> Rescan:
    result = Rescan()
    candidates = _scan_candidates(paths, scan_paths, result.clean)
    for raw_path, path in candidates:
        if raw_path in pause:
            result.skipped.append(str(path))
            result.paused.add(raw_path)
            continue
        left = until - clock()
        try:
            if left <= 0:
                raise OutOfTime
            rows = _owned_rows(path, cfg, until)
        except OutOfTime:
            result.cut_left = left
            result.skipped.extend([str(path), *(str(unchecked) for _, unchecked in candidates)])
            result.cut = raw_path
            break
        result.existing.append(str(path))
        result.findings.extend(rows)
        if not rows:
            result.clean.append(raw_path)
    return result


def skipped_reason(skipped: list[str]) -> str:
    names = sorted(Path(path).name for path in skipped)[:MAX_NAMED_SKIPPED]
    return _skip_notice(len(skipped), names)


def queued_summary(paths: list[object], scan_paths: ScanPaths) -> str:
    names = []
    count = 0
    for raw in paths:
        if (path := scan_paths.candidate(raw)) is None:
            continue
        count += 1
        if len(names) < MAX_NAMED_SKIPPED:
            names.append(path.name)
    if not count:
        return ""
    noun = "file" if count == 1 else "files"
    preview = " (" + ", ".join(sorted(names)) + ")"
    return f"{count} {noun}{preview}"


def _skip_notice(count: int, names: list[str]) -> str:
    noun = "file" if count == 1 else "files"
    named = ": " + ", ".join(names) if names else ""
    more = f" and {count - len(names)} more" if names and count > len(names) else ""
    tracked = "It stays" if count == 1 else "They stay"
    return (
        f"agent-discipline-watcher: Stop ran out of time. {count} {noun} not scanned{named}{more}. "
        f"{tracked} tracked for the next Stop."
    )
