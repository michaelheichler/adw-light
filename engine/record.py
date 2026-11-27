from __future__ import annotations

import os
import stat
import sys
import time
from dataclasses import dataclass, field
from collections.abc import Callable
from pathlib import Path
from typing import cast

from failure import normalize_payload, record_success
from lib import blocker_state, captured_output, imported_baseline, journal, payloads, scan_input, temp_authorship, wording_debt
from lib.mcp_health import config_roots
from lib.config import contract_strings, effective_config
from lib.findings import Finding, VerdictKind
from lib.hookio import advise, claude_feedback_response, config_failure, join_notices, read_payload, stop_block, write_payload
from lib.payloads import RecordPayload, exact_string_dict, record_payload
from lib.baseline import split_committed, split_fallback
from lib.import_advice import imported_advice
from lib.reporting import (
    append_row,
    inherited_advice,
    now_iso,
    record_findings,
    run_with_ledger,
    verdict_message,
)
from lib.scanner import scan_all
from lib.temp_scope import outside_project_temp

UNDECIDABLE_KEY = blocker_state.RECORD_ERROR_KEY
SHELL_TOOL = "bash"
MAX_IMPORTED_SCANS = 200


@dataclass(frozen=True, slots=True)
class _RecordVerdict:
    kind: VerdictKind
    reason: str


@dataclass(frozen=True, slots=True)
class _EditJournal:
    payload: RecordPayload
    paths: list[str]
    root: str | Path | None
    state_root: str | Path | None
    config: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _RecordGateContext:
    journal: _EditJournal
    tracked_paths: list[str]
    scan_paths: list[str]
    imported_paths: list[str]
    output_paths: set[str]
    cwd: Path
    config: dict
    blocker_scope: blocker_state.BlockerScope
    shell: bool = False


def edited_paths(payload: object) -> list[str]:
    return list(payloads.edited_paths(payload))


def _edit_row_template(edits: _EditJournal, turn_id: str) -> dict:
    return {
        "ts": now_iso(),
        "session_id": edits.payload["session_id"],
        "hook": "record",
        "event": "edit",
        "family": "",
        "rule": "",
        "path": "",
        "tool": edits.payload["tool_name"],
        "tool_use_id": edits.payload["tool_use_id"],
        "turn_id": turn_id,
        "outcome": "",
    }


def _journal_candidate(edits: _EditJournal, path: str, turn_id: str) -> None:
    session_id = edits.payload["session_id"]
    try:
        target = payloads.resolved_path(path, Path(edits.payload["cwd"] or "."))
        journal.record_edit(
            session_id, turn_id, edits.payload["tool_use_id"], target,
            state_root=edits.state_root, project=edits.payload["cwd"] or None,
        )
    except Exception as exc:
        sys.stderr.write(f"agent-discipline-watcher: candidate journal append failed: {exc}\n")
        return


def _journal_edits(edits: _EditJournal, turn_id: str = "") -> None:
    template = _edit_row_template(edits, turn_id)
    session_id = edits.payload["session_id"]
    cwd = edits.payload["cwd"] or None
    for path in edits.paths:
        append_row({**template, "path": path}, edits.root)
        if not session_id:
            continue
        if not outside_project_temp(path, cwd):
            _journal_candidate(edits, path, turn_id)
    if temp_authorship.note_authored_temp(session_id, edits.paths, cwd, edits.state_root):
        append_row({**template, "event": "scratch_provenance_full", "outcome": "observe"}, edits.root)


def _stamped(findings: list[dict], path: Path, content_hash: str | None = None) -> list[dict]:
    stamped = []
    for finding in findings:
        item = Finding.from_dict(finding).with_path(str(path))
        if content_hash:
            item = item.with_content_hash(content_hash)
        stamped.append(item.to_dict())
    return stamped




_MAX_OPEN_SCAN_BYTES = 1_000_000

def _approved_path(raw_path: object, cwd: Path) -> tuple[Path, tuple[int, int]] | None:
    if not isinstance(raw_path, str):
        return None
    candidate_text = raw_path.strip()
    if not candidate_text or any(ord(char) < 0x20 or ord(char) == 0x7F for char in candidate_text):
        return None
    try:
        path = payloads.resolved_path(candidate_text, cwd).resolve(strict=True)
        metadata = path.stat()
    except (OSError, RuntimeError, ValueError):
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    return path, (metadata.st_dev, metadata.st_ino)

def _held_fallback(descriptor: int, path: Path) -> list[dict]:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            if scan_input.is_data_text(stream, path):
                return []
            result = scan_input._count_open_file_lines(stream)
    except (OSError, ValueError):
        result = None
    if result is None:
        return scan_input.fallback_findings_from_count(path, 0, capped=False)
    count, capped = result
    return scan_input.fallback_findings_from_count(path, count, capped)

def _close_quietly(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass

def _scan_descriptor(descriptor: int, path: Path, cfg: dict) -> tuple[list[dict], list[dict]]:
    limit = min(
        _MAX_OPEN_SCAN_BYTES,
        max(0, scan_input.int_setting(cfg, "max_scan_bytes", "ADW_MAX_SCAN_BYTES", _MAX_OPEN_SCAN_BYTES)),
    )
    raw = os.read(descriptor, max(scan_input.CONTENT_SAMPLE_BYTES, limit + 1))
    if raw.startswith(b"PK\x03\x04"):
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            text = scan_input.office_text.extract(handle, limit)
        return ([], []) if text is None else split_committed(path, scan_all(str(path), text, cfg), cfg, text)
    if scan_input.is_binary_content(path, raw):
        return [], []
    if len(raw) > limit or b"\0" in raw[:scan_input.CONTENT_SAMPLE_BYTES]:
        return split_fallback(path, _held_fallback(descriptor, path), cfg)
    text = raw.decode("utf-8", errors="replace")
    rows = scan_all(str(path), text, cfg)
    return split_committed(path, rows, cfg, text)

def _scan_open_file(
    path: Path, identity: tuple[int, int], cfg: dict
) -> tuple[list[dict], list[dict]] | None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != identity or not stat.S_ISREG(metadata.st_mode):
            return None
        return _scan_descriptor(descriptor, path, cfg)
    except (OSError, ValueError):
        return None
    finally:
        _close_quietly(descriptor)


def scanned_rewrite(path: Path, cfg: dict) -> list[dict]:
    try:
        metadata = path.lstat()
    except OSError:
        return []
    if not stat.S_ISREG(metadata.st_mode):
        return []
    scanned = _scan_open_file(path, (metadata.st_dev, metadata.st_ino), cfg)
    return scanned[0] if scanned is not None else []


def _scan_paths(paths: list[str], cwd: Path, cfg: dict, *, allow_absent: bool = False) -> tuple[list[dict], list[dict]]:
    owned_rows: list[dict] = []
    inherited_rows: list[dict] = []
    for raw_path in paths:
        if outside_project_temp(raw_path, cwd):
            continue
        approved = _approved_path(raw_path, cwd)
        if approved is None and (scan_input.sink_device(raw_path) or (allow_absent and scan_input.nothing_landed(raw_path, cwd))):
            continue
        if approved is None:
            owned_rows.extend(_stamped(scan_input.fallback_findings_from_count(Path(raw_path), 0, capped=False), Path(raw_path)))
            continue
        path, identity = approved
        scanned = _scan_open_file(path, identity, cfg)
        if scanned is None:
            owned_rows.extend(_stamped(scan_input.fallback_findings_from_count(path, 0, capped=False), path))
            continue
        owned, inherited = scanned
        owned_rows.extend(_stamped(owned, path))
        inherited_rows.extend(_stamped(inherited, path))
    return owned_rows, inherited_rows


def _projected_payload(payload: dict) -> RecordPayload:
    projected = record_payload(payload)
    identity = normalize_payload(payload)
    for field in ("session_id", "cwd", "tool_name", "tool_use_id"):
        projected[field] = identity[field]
    return projected


def _scan_config(trusted_config: dict) -> dict:
    scan_config = dict(trusted_config)
    scan_config.pop("state_root", None)
    scan_config.pop("ledger_root", None)
    return scan_config


def _note_success(projected: RecordPayload, trusted_config: dict) -> None:
    record_success(
        {
            "session_id": projected["session_id"],
            "cwd": projected["cwd"],
            "tool_name": projected["tool_name"],
            "tool_use_id": projected["tool_use_id"],
            "tool_input": {"file_path": projected["file_path"]},
        },
        trusted_config,
    )


def _response(
    verdict: _RecordVerdict, inherited: list[dict], cfg: dict, imported: str = ""
) -> dict:
    notice = inherited_advice(inherited, cfg)
    content = "\n".join(part for part in (verdict.reason, notice, imported) if part)
    if verdict.kind == "block":
        return {"decision": "block", "reason": content}
    return advise(content, "PostToolUse") if content else {}


def _clear_blocker_state(
    scope: blocker_state.BlockerScope,
    tracked_paths: list[str],
    verdict: _RecordVerdict, decisions: list,
) -> None:
    blocked = {os.path.realpath(row["path"]) for row, outcome in decisions if outcome == "block" and row.get("path")}
    pending = {path: verdict.reason if os.path.realpath(path) in blocked else None for path in tracked_paths}
    blocker_state.apply_paths(scope, tracked_paths, pending)


def _imported_notice(context: _RecordGateContext, report_cfg: dict) -> str:
    notes = captured_output.structured_output_notes(context.output_paths)
    if not context.imported_paths:
        return imported_advice(notes, report_cfg)
    session_id = context.journal.payload["session_id"]
    fresh = context.imported_paths
    if session_id:
        fresh = imported_baseline.snapshot_many(session_id, context.imported_paths, context.journal.state_root)
    owned, inherited = _scan_paths(fresh[:MAX_IMPORTED_SCANS], context.cwd, context.config)
    return imported_advice(owned + inherited + notes, report_cfg)


def _ledger_decisions(context: _RecordGateContext, owned: list[dict], turn_id: str, started: float) -> list:
    projected = context.journal.payload
    return record_findings(
        session_id=projected["session_id"], hook="record",
        event="PostToolUse", findings=owned, turn_id=turn_id,
        tool_use_id=projected["tool_use_id"],
        duration_ms=int((time.monotonic() - started) * 1000),
        root=context.journal.root, config=context.config,
    )


def _gate_for(context: _RecordGateContext) -> Callable[[str], dict]:
    def gate(turn_id: str) -> dict:
        started = time.monotonic()
        projected = context.journal.payload
        if projected["session_id"]:
            _journal_edits(context.journal, turn_id)
        owned, inherited = _scan_paths(context.scan_paths, context.cwd, context.config, allow_absent=context.shell)
        decisions = _ledger_decisions(context, owned, turn_id, started)
        report_cfg = {**context.config, "session_id": projected["session_id"], "turn_id": turn_id}
        kind, reason = verdict_message(decisions, report_cfg)
        verdict = _RecordVerdict(VerdictKind(kind), reason)
        damaged = temp_authorship.take_malformed_notice(projected["session_id"], context.journal.state_root)
        notices = "\n".join(part for part in (damaged, _imported_notice(context, report_cfg)) if part)
        response = _response(verdict, inherited, report_cfg, notices)
        if projected["session_id"]:
            _clear_blocker_state(context.blocker_scope, context.tracked_paths, verdict, decisions)
            project = [path for path in context.tracked_paths if not outside_project_temp(path, context.cwd)]
            imported_baseline.note_written(projected["session_id"], project, context.journal.state_root)
            wording_debt.note_written(
                projected["session_id"], context.tracked_paths, context.journal.state_root,
                cwd=str(context.cwd), contracts=contract_strings(context.config),
            )
        return response

    return gate


def _run_record(payload: dict, config: dict | None) -> dict:
    name = payload.get("tool_name")
    if isinstance(name, str) and name and name.lower() not in payloads.WRITE_TOOL_NAMES:
        return {}
    projected = _projected_payload(payload)
    trusted_config = exact_string_dict(config)
    roots = config_roots(trusted_config)
    state_root = cast(str | Path | None, roots.state)
    ledger_root = cast(str | Path | None, roots.ledger)
    cwd_text = projected["cwd"]
    cfg = effective_config(_scan_config(trusted_config), cwd_text or None)
    cfg["state_root"] = state_root
    if projected["session_id"]:
        cfg["session_id"] = projected["session_id"]
        _note_success(projected, trusted_config)
    gate_context = _gate_context_for(payload, projected, cfg, state_root, ledger_root)
    return run_with_ledger(
        hook="record",
        payload={**projected, "turn_id": payloads.turn_id(payload)},
        gate=_gate_for(gate_context),
        ledger_root=ledger_root,
        state_root=state_root,
    )


def _missing_removed_paths(payload: dict, cwd: Path) -> set[str]:
    missing = set()
    for raw_path in payloads.removed_paths(payload):
        try:
            payloads.resolved_path(raw_path, cwd).lstat()
        except FileNotFoundError:
            missing.add(raw_path)
    return missing


def _gate_context_for(
    payload: dict,
    projected: RecordPayload,
    cfg: dict,
    state_root: str | Path | None,
    ledger_root: str | Path | None,
)-> _RecordGateContext:
    if not projected["cwd"]:
        raise ValueError("PostToolUse payload requires a trusted cwd")
    cwd = Path(projected["cwd"])
    authored = temp_authorship.authored_temp(projected["session_id"], state_root)
    written, imported = payloads.split_edited_paths(payload, authored)
    paths = list(written)
    removed = _missing_removed_paths(payload, cwd)
    return _RecordGateContext(
        journal=_EditJournal(projected, paths, ledger_root, state_root, cfg),
        tracked_paths=[str(payloads.resolved_path(raw, cwd)) for raw in paths],
        scan_paths=[raw for raw in paths if raw not in removed],
        imported_paths=list(imported),
        output_paths=captured_output.output_destinations(projected["edit_text"], cwd) - authored if projected["tool_name"].lower() == SHELL_TOOL else set(),
        cwd=cwd,
        config=cfg,
        blocker_scope=blocker_state.BlockerScope(
            projected["session_id"], payloads.agent_id(payload), state_root
        ),
        shell=projected["tool_name"].lower() == SHELL_TOOL,
    )


def _merge_after_run(record: dict, after: dict) -> dict:
    if not after or not record:
        return after or record
    responses = (record, after)
    messages = [str(item.get("hookSpecificOutput", {}).get("additionalContext") or item.get("systemMessage", ""))
                for item in responses]
    reasons = [item["reason"] for item in responses if item.get("decision") == "block"]
    notice = join_notices(*dict.fromkeys(messages))
    if reasons:
        return {**after, **stop_block(join_notices(*dict.fromkeys(reasons), notice))}
    return {**record, **after, **advise(notice, "PostToolUse")}


def run(payload: dict, config: dict | None = None, *, until: float | None = None) -> dict:
    try:
        result = _run_record(payload, config)
        from lib import after_run

        name = payloads.tool_name(payload)
        if name == "Bash":
            return _merge_after_run(result, after_run.post(payload, config, until=until))
        if name in after_run.DIRECT_WRITERS:
            after_run.note_direct_write(payload, config, until=until)
        return result
    except Exception as exc:
        reason = config_failure("edit", exc, payloads.cwd(payload) or None)
        blocker_state.hold_undecidable(payload, config, UNDECIDABLE_KEY, reason)
        return stop_block(reason)


if __name__ == "__main__":
    write_payload(claude_feedback_response(run(read_payload()), "PostToolUse"))
