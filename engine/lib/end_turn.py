from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from . import blocker_state, imported_baseline, payloads, stop_scan
from .config import resolve_outcome
from .reporting import compact_block
from .stop_stall import BlockingSet, ReasonBuilder, StopCall, StopOutcome, guarded

DUPLICATES_UNCHECKED = (
    "agent-discipline-watcher: Stop ran out of time before the duplicate content check. "
    "The batch block stays for the next Stop."
)


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


def _batch_findings(request: BatchScanRequest, pending: dict[str, str]) -> list[dict] | None:
    if blocker_state.BATCH_KEY not in pending or len(request.paths) < 2:
        return []
    import batch
    try:
        with stop_scan.scan_alarm(request.deadline):
            paths = _agent_written(request.paths, request.config)
            rows = batch.duplicate_findings_for_paths(request.cwd, paths, request.config)
    except stop_scan.OutOfTime:
        return None
    return [row for row in rows if resolve_outcome(row, request.config) == "block"]


def _remaining_reason(session_id: str, agent_id: str, root) -> str:
    pending, _paths, _revision = blocker_state.details(session_id, agent_id, root)
    return "\n".join(dict.fromkeys(pending.values()))


def _residual_reasons(pending: dict[str, str], paths: list[str]) -> list[str]:
    resolved = set(paths) | {blocker_state.BATCH_KEY}
    return [value for key, value in pending.items() if key not in resolved]


def _finding_reason(blocking: BlockingSet, cfg: dict) -> ReasonBuilder:
    def build(report: str | None) -> tuple[str, str]:
        if not blocking.findings:
            return "\n".join(dict.fromkeys(blocking.residual)), ""
        reason, written = compact_block(blocking.findings, cfg, report_path=report)
        return "\n".join(dict.fromkeys([reason, *blocking.residual])), written

    return build


def _blocking_set(scanned: stop_scan.Rescan, duplicates: list[dict] | None, residual: list[str]) -> BlockingSet | None:
    findings = scanned.findings + (duplicates or [])
    unchecked = [*scanned.skipped, *(scanned.existing if duplicates is None else [])]
    if not findings and not unchecked:
        return None
    if scanned.skipped:
        residual.append(stop_scan.skipped_reason(scanned.skipped))
    if duplicates is None:
        residual.append(DUPLICATES_UNCHECKED)
    owned = any(not row.get(stop_scan.INHERITED_FIELD) for row in findings)
    return BlockingSet(findings, residual, stop_scan.stamps(unchecked), owned)


def _scope_outcome(payload: dict, call: StopCall, until: float) -> StopOutcome:
    scope, cfg = call.scope, call.cfg
    pending, paths, revision = blocker_state.details(scope.session_id, scope.agent_id, scope.root)
    if paths and time.monotonic() >= until:
        return StopOutcome(stop_scan.skipped_reason(paths), "")
    cwd = payloads.cwd(payload)
    scanned = stop_scan.rescan(paths, Path(cwd or "."), cfg, until)
    request = BatchScanRequest(scope.session_id, cwd, tuple(scanned.existing), cfg, until)
    blocking = _blocking_set(scanned, _batch_findings(request, pending), _residual_reasons(pending, paths))
    if scanned.skipped and blocker_state.BATCH_KEY not in pending:
        blocker_state.settle_partial(scope, revision, blocker_state.PartialScan(scanned.clean, scanned.cut))
    if blocking is not None:
        return guarded(call, blocking, _finding_reason(blocking, cfg))
    path_keys = set(paths)
    cleared = [key for key in pending if key in path_keys]
    if paths:
        cleared.extend([blocker_state.BATCH_KEY, *blocker_state.RESCAN_RELEASED_KEYS])
    blocker_state.reconcile(scope.session_id, scope.agent_id, revision, cleared, paths, scope.root)
    remaining = _remaining_reason(scope.session_id, scope.agent_id, scope.root)
    if not remaining:
        return StopOutcome("", "")
    return guarded(call, BlockingSet([], remaining.split("\n")), lambda _report: (remaining, ""))


def stop_outcome(payload: dict, cfg: dict, *, hook: str = "stop", turn_id: str = "", until: float | None = None) -> StopOutcome:
    session_id = payloads.session_id(payload)
    if not session_id:
        return StopOutcome("", "")
    scope = blocker_state.BlockerScope(session_id, payloads.agent_id(payload), cfg.get("state_root"))
    event = "SubagentStop" if scope.agent_id else "Stop"
    call = StopCall(scope, {**cfg, "session_id": session_id}, hook, event, turn_id)
    outcome = _scope_outcome(payload, call, stop_scan.deadline() if until is None else until)
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


def foreign_outcome(payload: dict, cfg: dict, turn_id: str, *, until: float) -> StopOutcome:
    session_id = payloads.session_id(payload)
    reasons = []
    notices = []
    for scope in blocker_state.scope_ids(session_id, cfg.get("state_root")):
        if scope:
            outcome = stop_outcome({**payload, "agent_id": scope}, cfg, turn_id=turn_id, until=until)
            if outcome.reason:
                reasons.append(f"{scope}: {outcome.reason}")
            if outcome.notice:
                notices.append(outcome.notice)
    return StopOutcome("\n".join(reasons), "\n".join(notices))
