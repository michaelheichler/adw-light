from __future__ import annotations

import re
import time
from pathlib import Path
from typing import NamedTuple

from lib import fixture_prose, imported_baseline, payloads, tests_policy, wording_debt
from lib.baseline import LENGTH_RULES, baseline_mode, colon_ownership, committed_text, partition, split_committed, split_pending_file
from lib.config import payload_hook_config
from lib.hookio import (
    PARSE_FAILURE, advise, allow, claude_pretool_response, deny, fail_closed, read_payload, write_payload,
)
from lib.protected import path_findings
from lib.provenance import compare
from lib.regular_file import read_regular_text, special_file
from lib.patch_content import projected_content
from lib.reporting import (
    inherited_advice, record_findings, run_with_ledger, verdict_message,
)
from lib.scanner import file_length_findings, scan_all
from lib.temp_scope import outside_project_temp
from lib.write_shape import WriteConfig, grouped_write_rows, write_config

PATCH_FILE = re.compile(r"^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s+(.+)$", re.MULTILINE)
SPECIAL_TARGET = (
    "Write target {path} is a named pipe, device, or socket, so the text reaches a reader that no scan sees. "
    "Write to a regular file instead."
)


def _tool_input(payload: dict) -> dict:
    value = payload.get("tool_input") or payload.get("toolInput") or payload.get("input") or {}
    return value if isinstance(value, dict) else {}


class PendingWrite(NamedTuple):
    tool_input: dict
    path: str
    is_edit: bool
    edits: list[dict]
    notebook_source: str | None


def _decode(tool_input: dict) -> PendingWrite:
    path = str(
        tool_input.get("file_path") or tool_input.get("path")
        or tool_input.get("notebook_path") or "<pending>"
    )
    is_edit = (
        "new_string" in tool_input or "new_source" in tool_input
        or isinstance(tool_input.get("edits"), list)
    )
    edits: list[dict] = []
    if "new_string" in tool_input:
        edits = [tool_input]
    elif isinstance(tool_input.get("edits"), list):
        edits = [edit for edit in tool_input["edits"] if isinstance(edit, dict)]
    notebook_source = str(tool_input.get("new_source") or "") if "new_source" in tool_input else None
    return PendingWrite(tool_input, path, is_edit, edits, notebook_source)


def pending_writes(decoded: PendingWrite) -> list[tuple[str, str]]:
    tool_input = decoded.tool_input
    if "content" in tool_input:
        return [(decoded.path, str(tool_input.get("content") or ""))]
    if decoded.edits:
        return [(decoded.path, "\n".join(str(edit.get("new_string") or "") for edit in decoded.edits))]
    if decoded.notebook_source is not None:
        return [(decoded.path, decoded.notebook_source)]
    patch = tool_input.get("patch") or tool_input.get("command") or tool_input.get("input") or ""
    if isinstance(patch, list):
        patch = "\n".join(str(part) for part in patch)
    if isinstance(patch, str) and patch:
        split = split_patch(patch)
        return split if split else [(decoded.path, patch)]
    return []


def _completed_patch_rows(path: str, lines: list[str]) -> list[tuple[str, str]]:
    return [(path, "\n".join(lines))] if path else []


def split_patch(patch: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    current = ""
    lines: list[str] = []
    for line in patch.splitlines():
        match = PATCH_FILE.match(line)
        if match:
            rows.extend(_completed_patch_rows(current, lines))
            current = match.group(1).strip().strip('"')
            lines = []
            continue
        if current and line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    rows.extend(_completed_patch_rows(current, lines))
    return rows


def run(payload: dict, config: dict | None = None) -> dict:
    return fail_closed("write", lambda: _checked_run(payload, config), payloads.cwd(payload))


def _checked_run(payload: dict, config: dict | None) -> dict:
    if payload is PARSE_FAILURE:
        raise ValueError("unreadable hook payload")
    return _run(payload, config)


def _run(payload: dict, config: dict | None) -> dict:
    cfg = payload_hook_config(config, payload)
    return run_with_ledger(
        hook="pre_write",
        payload=payload,
        gate=lambda turn_id: _gate(payload, cfg, write_config(config, payload, cfg), turn_id),
        ledger_root=cfg.get("ledger_root"),
        state_root=cfg.get("state_root"),
    )


def _unique_findings(findings: list[dict]) -> list[dict]:
    seen: set[tuple[object, ...]] = set()
    result: list[dict] = []
    for finding in findings:
        key = (finding.get("rule"), finding.get("path"), finding.get("line"))
        if key not in seen:
            seen.add(key)
            result.append(finding)
    return result


def _record(payload: dict, cfg: dict, turn_id: str, findings: list[dict], started: float) -> list[tuple[dict[str, object], str]]:
    return record_findings(
        session_id=str(payload.get("session_id") or ""), hook="pre_write",
        event="PreToolUse", findings=findings, turn_id=turn_id,
        tool_use_id=str(payload.get("tool_use_id") or ""),
        duration_ms=int((time.monotonic() - started) * 1000),
        root=cfg.get("ledger_root"), config=cfg,
    )


def _note_first_seen(payload: dict, cwd: Path, cfg: dict) -> None:
    paths = [_resolved_path(raw, cwd) for raw in payloads.edited_paths(payload)]
    project = [str(path) for path in paths if not outside_project_temp(path, cwd)]
    imported_baseline.note_first_seen(
        payloads.session_id(payload), project, cfg.get("state_root"),
        lambda path: committed_text(path) is not None,
    )


def _special_target_refusal(payload: dict, cwd: Path) -> str:
    special = [str(path) for path in (_resolved_path(raw, cwd) for raw in payloads.edited_paths(payload)) if special_file(path)]
    return SPECIAL_TARGET.format(path=special[0]) if special else ""


def _gate(payload: dict, cfg: dict, settings_for: WriteConfig, turn_id: str) -> dict:
    started = time.monotonic()
    decoded = _decode(_tool_input(payload))
    cwd = Path(payload.get("cwd") or ".")
    refusal = _special_target_refusal(payload, cwd)
    if refusal:
        return deny(refusal)
    _note_first_seen(payload, cwd, cfg)
    findings, inherited = _pending_findings(decoded, cwd, cfg, settings_for)
    gate = tests_policy.Gate(payload, cfg, time.time())
    findings += wording_debt.gate_findings(gate, _test_changes(decoded, cwd), settings_for=settings_for)
    decisions = [decision for settings, rows in grouped_write_rows(_unique_findings(findings), settings_for, cfg)
                 for decision in _record(payload, settings, turn_id, rows, started)]
    kind, message = verdict_message(decisions, cfg)
    if kind == "block":
        return deny(message)
    notice = inherited_advice(inherited, cfg)
    if kind == "observe":
        return advise("\n".join(part for part in (message, notice) if part), "PreToolUse")
    return advise(notice, "PreToolUse") if notice else allow()


def _stamped(findings: list[dict], path: str) -> list[dict]:
    return [{**finding, "path": path} for finding in findings]


def _label_pending_text(findings: list[dict]) -> list[dict]:
    return [
        {**finding, "detail": finding["detail"] + " (line " + str(finding["line"]) + " of pending edit text)"}
        for finding in findings
    ]


def _pending_edit_text(decoded: PendingWrite) -> str:
    if decoded.edits:
        return "\n".join(str(edit.get("new_string") or "") for edit in decoded.edits)
    if decoded.notebook_source is not None:
        return decoded.notebook_source
    return ""


def _apply_edits(decoded: PendingWrite, path: str) -> tuple[str, str] | None:
    if not decoded.edits:
        return None
    try:
        before = read_regular_text(path)
    except (OSError, UnicodeError):
        return None
    after = before
    for edit in decoded.edits:
        old = str(edit.get("old_string") or "")
        new = str(edit.get("new_string") or "")
        if not old or old not in after:
            return None
        count = -1 if edit.get("replace_all") else 1
        after = after.replace(old, new, count)
    return before, after


def _length_split(rows: list[dict], resolved_path: Path, cfg: dict, after: str) -> tuple[list[dict], list[dict]]:
    length = [row for row in rows if row.get("rule") in LENGTH_RULES]
    if not length:
        return rows, []
    owned, inherited = split_committed(resolved_path, length, cfg, after)
    kept = {id(row) for row in owned}
    return [row for row in rows if row.get("rule") not in LENGTH_RULES or id(row) in kept], inherited


def _edit_findings(decoded: PendingWrite, resolved_path: Path, cfg: dict, root: str) -> tuple[list[dict], list[dict]]:
    scan_path = str(resolved_path)
    scan_cfg = fixture_prose.scan_config(cfg, resolved_path, root)
    applied = _apply_edits(decoded, scan_path)
    if applied is None:
        pending = _pending_edit_text(decoded)
        return _label_pending_text(_stamped(scan_all(decoded.path, pending, scan_cfg), decoded.path)), []
    before, after = applied
    ordinary, added, carried = colon_ownership(scan_path, scan_all(scan_path, after, scan_cfg), [before], scan_cfg)
    owned, _ = partition(ordinary, compare(scan_path, before, after))
    owned.extend(added)
    kept, inherited = _length_split(owned, resolved_path, cfg, after)
    if baseline_mode(cfg) == "report":
        inherited.extend(carried)
    return _stamped(kept, decoded.path), _stamped(inherited, decoded.path)


def _protected_edit_text(decoded: PendingWrite, resolved_path: Path) -> str:
    applied = _apply_edits(decoded, str(resolved_path))
    return applied[1] if applied is not None else _pending_edit_text(decoded)


def _edit_shape_findings(decoded: PendingWrite, cwd: Path, cfg: dict, root: str) -> tuple[list[dict], list[dict]]:
    resolved_path = _resolved_path(decoded.path, cwd)
    protected = _stamped(
        path_findings(str(resolved_path), cfg, cwd=cwd, content=_protected_edit_text(decoded, resolved_path)),
        decoded.path,
    )
    if tests_policy.scratch_write(resolved_path, cwd):
        return protected, []
    owned, inherited = _edit_findings(decoded, resolved_path, cfg, root)
    return protected + owned, inherited


class _ShapeScope(NamedTuple):
    cwd: Path
    cfg: dict
    projected: dict
    settings_for: WriteConfig


def _patch_text(tool_input: dict) -> object:
    patch = tool_input.get("patch") or tool_input.get("command") or tool_input.get("input") or ""
    return "\n".join(str(part) for part in patch) if isinstance(patch, list) else patch


def _projected_rows(scope: _ShapeScope) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    inherited: list[dict] = []
    for path, content in scope.projected.items():
        resolved_path = _resolved_path(path, scope.cwd)
        rows.extend(_stamped(path_findings(str(resolved_path), scope.cfg, cwd=scope.cwd, content=content), path))
        if content is not None and not tests_policy.scratch_write(resolved_path, scope.cwd):
            settings = scope.settings_for(path)
            owned, relieved = _length_split(file_length_findings(path, content), resolved_path, settings, content)
            rows.extend(_stamped(owned, path))
            inherited.extend(_stamped(relieved, path))
    return rows, inherited


def _scanned_pending(path: str, text: str, scope: _ShapeScope) -> list[dict]:
    target = _resolved_path(path, scope.cwd)
    root = tests_policy.target_root(Path(path), scope.cwd)
    scan_cfg = fixture_prose.scan_config(scope.settings_for(path), target, root)
    scanned = _stamped(scan_all(path, text, scan_cfg), path)
    if scope.projected.get(path) is None:
        return scanned
    return [row for row in scanned if row["rule"] not in {
        "file_length_warning", "file_length_critical", "file_too_long",
    }]


def _write_shape_findings(decoded: PendingWrite, cwd: Path, cfg: dict, settings_for: WriteConfig) -> tuple[list[dict], list[dict]]:
    whole_file = "content" in decoded.tool_input
    patch = _patch_text(decoded.tool_input)
    scope = _ShapeScope(cwd, cfg, projected_content(patch, cwd) if isinstance(patch, str) else {}, settings_for)
    owned_rows, inherited_rows = _projected_rows(scope)
    for path, text in pending_writes(decoded):
        resolved_path = _resolved_path(path, cwd)
        if path not in scope.projected:
            content = text if whole_file else None
            owned_rows.extend(_stamped(path_findings(str(resolved_path), cfg, cwd=cwd, content=content), path))
        if tests_policy.scratch_write(resolved_path, cwd):
            continue
        scanned = _scanned_pending(path, text, scope)
        if not whole_file:
            owned_rows.extend(_label_pending_text(scanned))
            continue
        owned, inherited = split_pending_file(resolved_path, scanned, settings_for(path), text)
        owned_rows.extend(owned)
        inherited_rows.extend(inherited)
    return owned_rows, inherited_rows


def _test_changes(decoded: PendingWrite, cwd: Path) -> list[tests_policy.PendingChange]:
    if decoded.is_edit:
        resolved_path = _resolved_path(decoded.path, cwd)
        after = _protected_edit_text(decoded, resolved_path)
        return [tests_policy.PendingChange(decoded.path, tests_policy.read_before(resolved_path), after)]
    rows: dict[str, str | None] = dict(pending_writes(decoded))
    patch = _patch_text(decoded.tool_input)
    if "content" not in decoded.tool_input and isinstance(patch, str):
        for path, content in projected_content(patch, cwd).items():
            rows[path] = content if content is not None else rows.get(path)
    return [
        tests_policy.PendingChange(path, tests_policy.read_before(_resolved_path(path, cwd)), after)
        for path, after in rows.items()
    ]


def _pending_findings(
    decoded: PendingWrite, cwd: Path, cfg: dict, settings_for: WriteConfig,
) -> tuple[list[dict], list[dict]]:
    if decoded.is_edit:
        root = tests_policy.target_root(Path(decoded.path), cwd)
        return _edit_shape_findings(decoded, cwd, settings_for(decoded.path), root)
    return _write_shape_findings(decoded, cwd, cfg, settings_for)


def _resolved_path(path: str, cwd: Path) -> Path:
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else cwd / candidate


if __name__ == "__main__":
    write_payload(claude_pretool_response(run(read_payload())))
