from __future__ import annotations

import operator
import os
import re
import time
from collections import Counter
from pathlib import Path

from lib import imported_baseline, payloads, reporting, temp_authorship, tests_policy, unread_snapshot, wording_debt
from lib.baseline import committed_text
from lib.config import payload_hook_config
from lib.git_alias_bodies import CHECKED_INLINE_ALIAS, alias_commands, inline_alias
from lib.hookio import (
    PARSE_FAILURE,
    advise,
    allow,
    claude_pretool_response,
    deny,
    fail_closed,
    read_payload,
    write_payload,
)
from lib.reporting import compact_block, record_findings, run_with_ledger
from lib.shell_expansion import literal_array_removal
from lib.shell_parse import _bare, command_expansions, expansion_memo, line_segments, literal_writes
from lib.incoming_content import incoming_test_content
from lib.shell_payload_scan import (
    MAX_SHELL_PAYLOAD_DEPTH,
    SHELL_SCRIPTS,
    _scan_targets,
    opaque_write_findings,
    _opaque_findings,
    _script_findings,
    _script_rows,
    _stdin_recurse,
    _shell_payload_findings,
    _recursed_findings,
    _literal_shell_c_payloads,
    _substitution_bodies,
    _literal_shell_stdin_payloads,
    _pipe_stdin_payload,
    _literal_shell_pipe_payloads,
)
from lib.shell_self_protection import (
    INSTALLER_NAMES,
    CAP_VARS,
    NO_VERIFY_FLAGS,
    STATE_DELETE_VERBS,
    STATE_TARGET_RE,
    WRITE_OR_EDIT_ACTION,
    SAVE_THEN_RUN_ACTION,
    RULES,
    command_findings,
    _protected_target_hits,
    _segment_views,
    _command_segments,
    _segment_cwds,
    _session_path,
    _sets_home,
    _overrides_cap,
    _runs_installer,
    _skips_commit_gate,
    _deletes_state,
    _mutates_state,
    _targets_state,
    _is_mutating,
    _mutates_install_surface,
    _finding,
    _finding_factory,
)
from lib.shell_scripts import changed_script_inputs
from lib.opaque_write import _run_findings
from lib.shell_test_targets import (
    OUTER_START,
    Scope,
    gated_paths,
    only_files,
    reachable_directories,
    removal_targets,
    staged_only,
)
from lib.shell_write_paths import (
    BASH_WRITE_CAP,
    OVERSIZE_WRITE,
    target_findings,
    _shell_targets,
    dynamic_target_findings,
    unresolved_write_destinations,
    _lost_destinations,
    _largest_oversize,
    oversize_write,
    write_paths,
    _destinations,
    _mutation_paths,
    write_findings,
)
from lib.temp_scope import Directories, joined_paths, logical_directory, outside_project_temp, segment_directories, trusted_directory
from lib.write_shape import shaped_write_findings

REDIRECT_SPLIT_RE = re.compile(r"[<>&|]+")


def run(payload: dict, config: dict | None = None) -> dict:
    return fail_closed("command", lambda: _checked_run(payload, config))


def _checked_run(payload: dict, config: dict | None) -> dict:
    if payload is PARSE_FAILURE:
        raise ValueError("unreadable hook payload")
    with expansion_memo():
        return _run(payload, config)


def _run(payload: dict, config: dict | None) -> dict:
    cfg = payload_hook_config(config, payload)
    return run_with_ledger(
        hook="pre_bash",
        payload=payload,
        gate=lambda turn_id: _deferring_gate(payload, cfg, turn_id),
        ledger_root=cfg.get("ledger_root"),
        state_root=cfg.get("state_root"),
    )


def _deferring_gate(payload: dict, cfg: dict, turn_id: str) -> dict:
    with unread_snapshot.deferring() as held:
        result = _gate(payload, cfg, turn_id)
    if _denied(result) or not held or unread_snapshot.take(payload, cfg, _command(payload)):
        return result
    _record(payload, cfg, turn_id, held, time.monotonic())
    return deny(compact_block(held, cfg)[0])


def _gate(payload: dict, cfg: dict, turn_id: str, depth: int = 0) -> dict:
    command = _command(payload)
    inline = inline_alias(command, payload.get("cwd") or ".")
    checked: dict = {}
    if inline is not None:
        checked = _command_gate({**payload, "tool_input": {"command": inline}}, cfg, turn_id)
        if _denied(checked):
            return checked
    token = CHECKED_INLINE_ALIAS.set(command if inline is not None else None)
    try:
        result = _command_gate(payload, cfg, turn_id) or checked
        if _denied(result):
            return result
        for check in (_alias_gate, _script_gate):
            reply = check(payload, cfg, turn_id, depth)
            if _denied(reply):
                return reply
            result = result or reply
        return result
    finally:
        CHECKED_INLINE_ALIAS.reset(token)


def _denied(result: dict) -> bool:
    return result.get("decision") == "block"


def _alias_gate(payload: dict, cfg: dict, turn_id: str, depth: int) -> dict:
    result: dict = {}
    for body in alias_commands(_command(payload)):
        if body is None:
            gate = tests_policy.Gate(payload, cfg, time.time())
            rows = tests_policy.findings(gate, [], [_command(payload)])
            if rows:
                _record(payload, cfg, turn_id, rows, time.monotonic())
                return deny(compact_block(rows, cfg)[0])
            return deny("The shell gate cannot read the Git alias body. Use a literal command.")
        expanded = {**payload, "tool_input": {"command": body}}
        checked = _command_gate(expanded, cfg, turn_id)
        if _denied(checked):
            return checked
        result = result or checked
    return result


def _script_gate(payload: dict, cfg: dict, turn_id: str, depth: int) -> dict:
    result: dict = {}
    cwd = Path(payload.get("cwd") or ".")
    for target in _scan_targets(_command(payload)):
        for run, directory in changed_script_inputs(target, cwd):
            if run.name in {"sh", "bash"} and run.payload is not None and depth <= MAX_SHELL_PAYLOAD_DEPTH:
                nested = dict(payload, cwd=str(directory), tool_input={"command": run.payload})
                reply = _gate(nested, cfg, turn_id, depth + 1)
                if _denied(reply):
                    return reply
                result = result or reply
                continue
            rows = _file_rows(run, target)
            if rows and _unreadable_shell(run) and unread_snapshot.defer(rows):
                continue
            if rows:
                _record(payload, cfg, turn_id, rows, time.monotonic())
                return deny(compact_block(rows, cfg)[0])
    return result


def _unreadable_shell(run) -> bool:
    return run.name in {"sh", "bash"} and run.payload is None


def _file_rows(run, command):
    if run.name in {"sh", "bash"}:
        return [_finding("shell_payload_block", command)]
    return _run_findings(run, _finding_factory(command), "inline_interpreter_write")


def _command_gate(payload: dict, cfg: dict, turn_id: str) -> dict:
    started = time.monotonic()
    command = _command(payload)
    if not command:
        return allow()
    cwd = payload.get("cwd") or "."
    refusal = temp_authorship.scratch_refusal(payloads.session_id(payload), write_paths(command, cwd), cwd, cfg.get("state_root"))
    if refusal:
        return deny(refusal)
    _note_first_seen(payload, cwd, cfg)
    findings = command_findings(command, cfg, cwd=cwd) + target_findings(command, cfg, cwd=cwd) + opaque_write_findings(command, cfg, cwd=cwd)
    findings += dynamic_target_findings(command, cfg, cwd) + _test_findings(payload, cfg)
    if findings:
        reason, _ = compact_block(tests_policy.group_unresolved(findings), cfg)
        _record(payload, cfg, turn_id, findings, started)
        return deny(reason)
    scan_targets = _scan_targets(command)
    size = _largest_oversize(scan_targets, cwd)
    if size is not None:
        return deny(OVERSIZE_WRITE.format(size=size, cap=BASH_WRITE_CAP))
    owned, inherited = _write_shape_totals(scan_targets, cfg, Path(cwd))
    if not owned and not inherited:
        return allow()
    decisions = _record(payload, cfg, turn_id, owned, started) if owned else []
    return _verdict(decisions, cfg, inherited)


def _note_first_seen(payload: dict, cwd: str, cfg: dict) -> None:
    paths = [payloads.resolved_path(raw, Path(cwd)) for raw in payloads.edited_paths(payload)]
    imported_baseline.note_first_seen(
        payloads.session_id(payload), [str(path) for path in paths if not outside_project_temp(path, cwd)],
        cfg.get("state_root"), lambda path: committed_text(path) is not None,
    )


def _test_findings(payload: dict, cfg: dict) -> list[dict]:
    gate = tests_policy.Gate(payload, cfg, time.time())
    cwd = Path(payload.get("cwd") or ".")
    command = _command(payload)
    targets = _scan_targets(command)
    texts, unresolved, seen = _gathered(targets, cwd, reachable_directories(command, cwd))
    appended = {write.path for target in targets for write in literal_writes(target) if write.append}
    changes = [_landing(path, text, cwd, seen, path in appended) for path, text in texts.items()]
    removed = removal_targets(command, cwd) if targets == [command] else frozenset()
    deleted = only_files(removed)
    if targets == [command] and staged_only(command):
        return wording_debt.lock_findings(gate, changes, unresolved, deleted)
    return wording_debt.gate_findings(gate, changes, unresolved, deleted, removed)


def _gathered(targets: list[str], cwd: Path, payload_start: Directories) -> tuple[dict[str, str | None], list[str], Counter[Path]]:
    texts: dict[str, str | None] = {}
    unresolved: list[str] = []
    writes: Counter[Path] = Counter()
    named: Counter[Path] = Counter()
    for position, target in enumerate(targets):
        if position == 0:
            target = _located_redirect(target, cwd)
        texts.update(_shell_targets(target, cwd))
        scope = Scope(cwd, OUTER_START if position == 0 else payload_start, outer=position == 0)
        paths, unnamed = gated_paths(target, scope)
        writes.update(payloads.resolved_path(path, cwd) for path in paths)
        named.update(_named_paths(target, scope))
        texts.update({path: texts.get(path) for path in paths})
        unresolved.extend(unnamed)
        unresolved.extend(incoming_test_content(target, scope))
    return texts, unresolved, writes | named


def _located_redirect(command: str, cwd: Path) -> str:
    directory = trusted_directory(command, cwd)
    if directory is None or (not Path(directory).is_absolute() and os.environ.get("CDPATH")):
        return command
    match = re.fullmatch(r"cd [\w./-]+[ \t]+&&[ \t]+printf(?:[ \t]+[\w.%/-]+)+[ \t]*(>)[ \t]*([\w./-]+)", command)
    base = logical_directory(directory, cwd) if match else None
    if base is None or not re.fullmatch(r"[\w./-]+", str(base)):
        return command
    return command[:match.start(2)] + str(base / match[2])


def _named_paths(command: str, scope: Scope) -> Counter[Path]:
    named: Counter[Path] = Counter()
    segments = line_segments(command)
    folders = segment_directories(segments, scope.directories, command_expansions(command, scope.cwd).path)
    for segment, places in zip(segments, folders):
        words = sorted({part for word in segment for part in REDIRECT_SPLIT_RE.split(_bare(word)) if part})
        named.update({payloads.resolved_path(path, scope.cwd) for path in joined_paths(words, places or OUTER_START)})
    return named


def _landing(path: str, text: str | None, cwd: Path, writes: Counter[Path], append: bool) -> tests_policy.PendingChange:
    resolved = payloads.resolved_path(path, cwd)
    before = tests_policy.read_before(resolved)
    if text is None or writes[resolved] > 1:
        return tests_policy.PendingChange(path, before, None)
    return tests_policy.PendingChange(path, before, (before or "") + text if append else text)


def _write_shape_totals(scan_targets: list[str], cfg: dict, cwd: Path) -> tuple[list[dict], list[dict]]:
    owned: list[dict] = []
    inherited: list[dict] = []
    for target in scan_targets:
        target_owned, target_inherited = shaped_write_findings(target, cfg, cwd)
        owned.extend(target_owned)
        inherited.extend(target_inherited)
    return owned, inherited


def _record(payload: dict, cfg: dict, turn_id: str, findings: list[dict], started: float) -> list[tuple[dict, str]]:
    return record_findings(
        session_id=str(payload.get("session_id") or ""), hook="pre_bash",
        event="PreToolUse", findings=findings, turn_id=turn_id,
        tool_use_id=str(payload.get("tool_use_id") or ""),
        duration_ms=int((time.monotonic() - started) * 1000),
        root=cfg.get("ledger_root"), config=cfg,
    )


def _verdict(decisions: list[tuple[dict, str]], cfg: dict, inherited: list[dict] | None = None) -> dict:
    kind, reason = reporting.verdict_message(decisions, cfg)
    if kind == "block":
        return deny(reason)
    notice = reporting.inherited_advice(inherited or [], cfg)
    if kind == "observe":
        return advise("\n".join(part for part in (reason, notice) if part), "PreToolUse")
    return advise(notice, "PreToolUse") if notice else allow()


def _command(payload: dict) -> str:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or payload.get("input") or {}
    command = tool_input.get("command") if operator.is_(type(tool_input), dict) else None
    if not isinstance(command, str):
        raise TypeError("Bash tool_input.command must be a string")
    return literal_array_removal(command)


if __name__ == "__main__":
    write_payload(claude_pretool_response(run(read_payload())))
