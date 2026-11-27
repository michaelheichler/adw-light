from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from . import blocker_state, imported_baseline, payloads, scan_cache, session_state, stop_scan
from .config import resolve_outcome
from .reporting import compact_block
from .stop_stall import BlockingSet, ReasonBuilder, StopCall, StopOutcome, guarded, guarded_all

QUEUED_HEADER = "agent-discipline-watcher: Stop ran out of time. Work stays tracked for the next Stop."
DUPLICATES_UNCHECKED = (
    "agent-discipline-watcher: Stop ran out of time before the duplicate content check. "
    "The batch block stays for the next Stop."
)
ROTATION_KEY = "stop_rotation"


@dataclass(frozen=True, slots=True)
class BatchScanRequest:
    session_id: str
    cwd: str
    paths: tuple[str, ...]
    config: dict
    deadline: float = float("inf")


def _agent_written(paths: tuple[str, ...], cfg: dict) -> list[str]:
    kept = []
    for path in paths:
        snapshot = imported_baseline.lookup(cfg, path)
        if snapshot is None or imported_baseline.file_digest(path) != snapshot.digest:
            kept.append(path)
    return kept


def _batch_findings(
    request: BatchScanRequest, pending: dict[str, str], roots: tuple[Path, ...],
) -> list[dict] | None:
    if blocker_state.BATCH_KEY not in pending or len(request.paths) < 2:
        return []
    import batch
    try:
        with stop_scan.scan_alarm(request.deadline):
            paths = _agent_written(request.paths, request.config)
            rows = batch.duplicate_findings_for_paths(request.cwd, paths, request.config, roots=roots)
    except stop_scan.OutOfTime:
        return None
    return [row for row in rows if resolve_outcome(row, request.config) == "block"]


def _remaining_reason(session_id: str, agent_id: str, root) -> str:
    pending, _paths, _revision = blocker_state.details(session_id, agent_id, root)
    return "\n".join(dict.fromkeys(reason for key, reason in pending.items() if key != blocker_state.AFTER_RUN_KEY))


def _residual_reasons(pending: dict[str, str], paths: list[str]) -> list[str]:
    resolved = set(paths) | {blocker_state.BATCH_KEY, blocker_state.AFTER_RUN_KEY}
    return [value for key, value in pending.items() if key not in resolved]


def _finding_reason(blocking: BlockingSet, cfg: dict, notices: list[str]) -> ReasonBuilder:
    def build(report: str | None) -> tuple[str, str]:
        if not blocking.findings:
            return "\n".join(dict.fromkeys([*blocking.residual, *notices])), ""
        reason, written = compact_block(blocking.findings, cfg, report_path=report)
        return "\n".join(dict.fromkeys([reason, *blocking.residual, *notices])), written

    return build


def _blocking_set(scanned: stop_scan.Rescan, duplicates: list[dict] | None, residual: list[str]) -> BlockingSet | None:
    findings = scanned.findings + (duplicates or [])
    if not findings and not scanned.skipped and duplicates is not None:
        return None
    owned = any(not row.get(stop_scan.INHERITED_FIELD) for row in findings) or any(v.startswith("1") for v in scanned.stamps.values())
    return BlockingSet(findings, residual, tuple(path + stamp for path, stamp in scanned.stamps.items()), owned)


def _scan_scope(call: StopCall, state: blocker_state.ScanState, scan_paths: stop_scan.ScanPaths,
                until: float, clock: Callable[[], float]) -> tuple[stop_scan.Rescan, blocker_state.PartialScan | None]:
    scanned = stop_scan.rescan(state.paths, scan_paths, call.cfg, until, clock, frozenset(state.pause))
    skipped = set(scanned.skipped)
    scanned.stamps = {path: stamp for path, stamp in state.stamps.items() if path in skipped}
    scanned.stamps.update(scan_cache.identities(scanned.findings))
    scanned.findings.extend(row for row in state.findings if row["path"] in skipped)
    if not scanned.skipped and scanned.stamps == state.stamps:
        return scanned, None
    cursor = scanned.cut
    pause = set(state.pause).difference(scanned.paused, scanned.clean)
    if cursor and not scanned.existing and scanned.cut_left >= stop_scan.FULL_TURN:
        pause.add(cursor)
        gone = set(scanned.clean)
        cursor = next((path for path in state.paths[state.paths.index(cursor) + 1:] if path not in gone), cursor)
    clean = scanned.clean if skipped and blocker_state.BATCH_KEY not in state.pending else []
    return scanned, blocker_state.PartialScan(clean, cursor, scanned.findings, scanned.stamps, sorted(pause), str(scan_paths.directory))


def _scope_outcome(cwd: str, call: StopCall, until: float, scan_paths: stop_scan.ScanPaths,
                   clock: Callable[[], float]) -> StopOutcome:
    scope, cfg = call.scope, call.cfg
    state = blocker_state.scan_state(scope)
    pending, paths, revision = state.pending, state.paths, state.revision
    scanned, partial = _scan_scope(call, state, scan_paths, until, clock)
    request = BatchScanRequest(scope.session_id, cwd, tuple(scanned.existing), cfg, until)
    batch_cut = scanned.skipped and blocker_state.BATCH_KEY in pending
    duplicates = None if batch_cut else _batch_findings(request, pending, scan_paths.roots)
    residual = _residual_reasons(pending, paths)
    blocking = _blocking_set(scanned, duplicates, residual)
    if blocking is not None:
        if partial is not None:
            blocker_state.settle_partial(scope, revision, partial)
        notices = [stop_scan.skipped_reason(scanned.skipped)] if scanned.skipped else []
        if duplicates is None:
            notices.append(DUPLICATES_UNCHECKED)
        return guarded(call, blocking, _finding_reason(blocking, cfg, notices))
    path_keys = set(paths)
    cleared = [key for key in pending if key in path_keys]
    if paths:
        cleared.extend([blocker_state.BATCH_KEY, *blocker_state.RESCAN_RELEASED_KEYS])
    blocker_state.reconcile(scope.session_id, scope.agent_id, revision, cleared, paths, scope.root)
    remaining = _remaining_reason(scope.session_id, scope.agent_id, scope.root)
    if not remaining:
        return StopOutcome("", "")
    return guarded(call, BlockingSet([], remaining.split("\n")), lambda _report: (remaining, ""))


def _queued_summary(queued: blocker_state.QueuedScope | None, scan_paths: stop_scan.ScanPaths) -> str:
    if queued is None:
        return ""
    return stop_scan.queued_summary(queued.paths, scan_paths) or ("pending checks" if queued.has_pending else "")


def _stop_call(payload: dict, cfg: dict, hook: str, turn_id: str) -> StopCall:
    session_id = payloads.session_id(payload)
    scope = blocker_state.BlockerScope(session_id, payloads.agent_id(payload), cfg.get("state_root"))
    event = "SubagentStop" if scope.agent_id else "Stop"
    return StopCall(scope, {**cfg, "session_id": session_id}, hook, event, turn_id)


def _fixed(reason: str) -> ReasonBuilder:
    return lambda _report: (reason, "")


def _deferred_call(call: StopCall, summary: str, reason: str) -> tuple[StopCall, BlockingSet, ReasonBuilder]:
    return replace(call, stall_key=blocker_state.DEFERRED_STALL_KEY), BlockingSet([], [summary]), _fixed(reason)


def _deferred(call: StopCall, summary: str, reason: str) -> StopOutcome:
    return guarded(*_deferred_call(call, summary, reason)) if summary else StopOutcome("", "")


def stop_outcome(
    payload: dict,
    cfg: dict,
    *,
    hook: str = "stop",
    turn_id: str = "",
    until: float | None = None,
    scan_paths: stop_scan.ScanPaths | None = None,
    clock: Callable[[], float] | None = None,
) -> StopOutcome:
    clock = time.monotonic if clock is None else clock
    if not payloads.session_id(payload):
        return StopOutcome("", "")
    until = stop_scan.deadline(clock=clock) if until is None else until
    cwd = payloads.cwd(payload)
    if scan_paths is None:
        scan_paths = stop_scan.ScanPaths.from_cwd(cwd)
    call = _stop_call(payload, cfg, hook, turn_id)
    scope = call.scope
    if clock() >= until:
        queued = blocker_state.queued_scopes(scope.session_id, scope.root).get(scope.agent_id)
        summary = _queued_summary(queued, scan_paths)
        return _deferred(call, summary, f"{QUEUED_HEADER}\n{summary}")
    return _with_dropped_notice(scope, _scope_outcome(cwd, call, until, scan_paths, clock))


def _with_dropped_notice(scope: blocker_state.BlockerScope, outcome: StopOutcome) -> StopOutcome:
    dropped = blocker_state.take_dropped(scope)
    if not dropped:
        return outcome
    capped = (
        f"agent-discipline-watcher: {dropped} older edited files without a pending finding left the Stop rescan, "
        f"because one scope tracks at most {blocker_state.MAX_SCOPE_PATHS} files."
    )
    return StopOutcome(outcome.reason, "\n".join(part for part in (outcome.notice, capped) if part))


def unresolved_reason(payload: dict, cfg: dict) -> str:
    return stop_outcome(payload, cfg).reason


@dataclass(frozen=True, slots=True)
class _Rotation:
    payload: dict
    cfg: dict
    turn_id: str
    until: float
    scan_paths: stop_scan.ScanPaths
    clock: Callable[[], float]
    own: str | None


class _ScopeRun(NamedTuple):
    agent_id: str
    outcome: StopOutcome
    deferred: bool


class TurnOutcome(NamedTuple):
    own: StopOutcome
    foreign: StopOutcome


def _rotated(agent_ids: list[str], start: object) -> list[str]:
    if start not in agent_ids:
        return agent_ids
    at = agent_ids.index(start)
    return [*agent_ids[at:], *agent_ids[:at]]


def _next_start(order: list[str], index: int, started: bool) -> str:
    return order[(index + 1) % len(order)] if started and index == 0 else order[index]


def _scanned(rotation: _Rotation, agent_id: str) -> _ScopeRun:
    outcome = stop_outcome(
        {**rotation.payload, "agent_id": agent_id}, rotation.cfg, turn_id=rotation.turn_id, until=rotation.until,
        scan_paths=rotation.scan_paths, clock=rotation.clock,
    )
    return _ScopeRun(agent_id, outcome, False)


def _deferred_runs(rotation: _Rotation, waiting: dict[str, str]) -> list[_ScopeRun]:
    calls = [
        _deferred_call(_stop_call({**rotation.payload, "agent_id": agent_id}, rotation.cfg, "stop", rotation.turn_id), summary, summary)
        for agent_id, summary in waiting.items()
    ]
    return [_ScopeRun(agent_id, outcome, True) for agent_id, outcome in zip(waiting, guarded_all(calls))]


def _store_start(rotation: _Rotation, order: list[str], start: str | None) -> None:
    if start is not None and start != order[0]:
        session_state.update_state_strict(
            payloads.session_id(rotation.payload), lambda state: {**state, ROTATION_KEY: start}, rotation.cfg.get("state_root"),
        )


def _scope_runs(rotation: _Rotation, agent_ids: list[str], queued: dict[str, blocker_state.QueuedScope]) -> list[_ScopeRun]:
    stored = session_state.read_state_strict(payloads.session_id(rotation.payload), rotation.cfg.get("state_root"))
    order = _rotated(agent_ids, stored.get(ROTATION_KEY))
    runs: list[_ScopeRun] = []
    waiting: dict[str, str] = {}
    ran_out: str | None = None
    for index, agent_id in enumerate(order):
        started = rotation.clock() < rotation.until
        if started or agent_id == rotation.own:
            runs.append(_scanned(rotation, agent_id))
        elif summary := _queued_summary(queued.get(agent_id), rotation.scan_paths):
            waiting[agent_id] = summary
        if ran_out is None and (not started or rotation.clock() >= rotation.until):
            ran_out = _next_start(order, index, started)
    _store_start(rotation, order, ran_out)
    return [*runs, *_deferred_runs(rotation, waiting)]


def _foreign(runs: list[_ScopeRun]) -> StopOutcome:
    scanned = [f"{run.agent_id}: {run.outcome.reason}" for run in runs if run.outcome.reason and not run.deferred]
    deferred = [f"{run.agent_id}: {run.outcome.reason}" for run in runs if run.outcome.reason and run.deferred]
    reasons = [*scanned, *([QUEUED_HEADER, *deferred] if deferred else [])]
    return StopOutcome("\n".join(reasons), "\n".join(run.outcome.notice for run in runs if run.outcome.notice))


def foreign_outcome(
    payload: dict,
    cfg: dict,
    turn_id: str,
    *,
    until: float,
    scan_paths: stop_scan.ScanPaths | None = None,
    clock: Callable[[], float] | None = None,
) -> StopOutcome:
    scan_paths = stop_scan.ScanPaths.from_cwd(payloads.cwd(payload)) if scan_paths is None else scan_paths
    rotation = _Rotation(payload, cfg, turn_id, until, scan_paths, time.monotonic if clock is None else clock, None)
    queued = blocker_state.queued_scopes(payloads.session_id(payload), cfg.get("state_root"))
    return _foreign(_scope_runs(rotation, [agent_id for agent_id in queued if agent_id], queued))


def turn_outcome(
    payload: dict,
    cfg: dict,
    turn_id: str,
    *,
    until: float,
    scan_paths: stop_scan.ScanPaths | None = None,
    clock: Callable[[], float] | None = None,
) -> TurnOutcome:
    own = payloads.agent_id(payload)
    scan_paths = stop_scan.ScanPaths.from_cwd(payloads.cwd(payload)) if scan_paths is None else scan_paths
    rotation = _Rotation(payload, cfg, turn_id, until, scan_paths, time.monotonic if clock is None else clock, own)
    queued = blocker_state.queued_scopes(payloads.session_id(payload), cfg.get("state_root"))
    runs = _scope_runs(rotation, [own, *(agent_id for agent_id in queued if agent_id and agent_id != own)], queued)
    mine = next(run.outcome for run in runs if run.agent_id == own)
    return TurnOutcome(mine, _foreign([run for run in runs if run.agent_id != own]))
