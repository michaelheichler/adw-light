from __future__ import annotations

import operator
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from lib.cd_landing import LandedCommand, ShellScope, landed_commands
from lib import imported_baseline, payloads, reporting, tests_policy, wording_debt
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
from lib.path_policy import anchored
from lib.reporting import compact_block, record_findings, run_with_ledger
from lib.shell_expansion import literal_array_removal
from lib.shell_parse import _bare, command_expansions, expansion_memo, line_segments, literal_writes
from lib.shell_syntax import SHELL_C_INTERPRETERS, interpreter_invocation
from lib.incoming_content import incoming_test_content
from lib.shell_payload_scan import (
    MAX_SHELL_PAYLOAD_DEPTH,
    SHELL_SCRIPTS,
    _scan_targets,
    nested_target_findings,
    _literal_shell_c_payloads,
    _substitution_bodies,
    _literal_shell_stdin_payloads,
    _pipe_stdin_payload,
    _literal_shell_pipe_payloads,
)
from lib.shell_self_protection import (
    STATE_DELETE_VERBS,
    RULES,
    command_findings,
    _protected_target_hits,
    _segment_views,
    _command_segments,
    _segment_cwds,
    _session_path,
    _is_mutating,
    _mutates_install_surface,
    _finding,
)
from lib.shell_scripts import changed_script_inputs
from lib.shell_test_targets import (
    OUTER_START,
    Scope,
    gated_paths,
    only_files,
    reachable_directories,
    removal_targets,
)
from lib.shell_write_paths import (
    write_paths,
    target_findings,
    _shell_targets,
    _destinations,
    write_findings,
)
from lib.temp_scope import Directories, joined_paths, landing_directories, outside_project_temp, segment_directories
from lib.write_shape import WriteConfig, grouped_write_rows, shaped_write_findings, write_config

REDIRECT_SPLIT_RE = re.compile(r"[<>&|]+")


class Located(NamedTuple):
    text: str
    outer: bool
    landed: bool
    deferred: bool = False
    created: frozenset[Path] = frozenset()


def run(payload: dict, config: dict | None = None) -> dict:
    return fail_closed("command", lambda: _checked_run(payload, config), payloads.cwd(payload))


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
        gate=lambda turn_id: _gate(payload, cfg, turn_id, config=config),
        ledger_root=cfg.get("ledger_root"),
        state_root=cfg.get("state_root"),
    )


def _gate(payload: dict, cfg: dict, turn_id: str, depth: int = 0, *, config: dict | None = None) -> dict:
    command = _command(payload)
    inline = inline_alias(command, payload.get("cwd") or ".")
    settings_for = write_config(config, payload, cfg)
    checked: dict = {}
    if inline is not None:
        body, folder = inline
        checked = _expanded_alias_gate(payload, cfg, turn_id, settings_for, body, folder)
        if _denied(checked):
            return checked
    token = CHECKED_INLINE_ALIAS.set(command if inline is not None else None)
    try:
        result = _command_gate(payload, cfg, turn_id, settings_for) or checked
        if _denied(result):
            return result
        alias = _alias_gate(payload, cfg, turn_id, settings_for)
        if _denied(alias):
            return alias
        script = _script_gate(payload, cfg, turn_id, depth, config)
        return script if _denied(script) else result or alias or script
    finally:
        CHECKED_INLINE_ALIAS.reset(token)


def _denied(result: dict) -> bool:
    return result.get("decision") == "block"


def _alias_gate(payload: dict, cfg: dict, turn_id: str, settings_for: WriteConfig) -> dict:
    result: dict = {}
    for found in alias_commands(_command(payload), payload.get("cwd") or "."):
        if found is None:
            continue
        body, folder = found
        checked = _expanded_alias_gate(payload, cfg, turn_id, settings_for, body, folder)
        if _denied(checked):
            return checked
        result = result or checked
    return result


def _expanded_alias_gate(
    payload: dict, cfg: dict, turn_id: str, settings_for: WriteConfig, body: str, folder: str,
) -> dict:
    expanded = {**payload, "cwd": folder, "tool_input": {"command": body}}
    cwd = Path(folder).absolute()
    return _command_gate(expanded, cfg, turn_id, lambda path: settings_for(str(anchored(path, cwd))))


def _script_gate(payload: dict, cfg: dict, turn_id: str, depth: int, config: dict | None) -> dict:
    result: dict = {}
    cwd = Path(payload.get("cwd") or ".")
    for target in _scan_targets(_command(payload)):
        for body, directory in changed_script_inputs(target, cwd):
            if depth >= MAX_SHELL_PAYLOAD_DEPTH:
                continue
            nested = dict(payload, cwd=str(directory), tool_input={"command": body})
            reply = _gate(nested, cfg, turn_id, depth + 1, config=config)
            if _denied(reply):
                return reply
            result = result or reply
    return result


def _command_gate(payload: dict, cfg: dict, turn_id: str, settings_for: WriteConfig) -> dict:
    started = time.monotonic()
    command = _command(payload)
    if not command:
        return allow()
    cwd = payload.get("cwd") or "."
    _note_first_seen(payload, cwd, cfg)
    findings = command_findings(command, cfg, cwd=cwd) + target_findings(command, cfg, cwd=cwd)
    findings += nested_target_findings(command, cfg, cwd) + _test_findings(payload, cfg, settings_for)
    debt = [row for row in findings if row["rule"] in wording_debt.RULES]
    findings = [row for row in findings if row["rule"] not in wording_debt.RULES]
    decisions = _record(payload, cfg, turn_id, debt, started) if debt else []
    if findings:
        reason, _ = compact_block(findings + reporting.reported_beside(decisions), cfg)
        _record(payload, cfg, turn_id, findings, started)
        return deny(reason)
    scan_targets = _scan_targets(command)
    owned, inherited = _write_shape_totals(scan_targets, cfg, Path(cwd), settings_for)
    if not owned and not inherited and not decisions:
        return allow()
    for settings, rows in grouped_write_rows(owned, settings_for, cfg):
        decisions.extend(_record(payload, settings, turn_id, rows, started))
    return _verdict(decisions, cfg, inherited)


def _note_first_seen(payload: dict, cwd: str, cfg: dict) -> None:
    paths = [payloads.resolved_path(raw, Path(cwd)) for raw in payloads.edited_paths(payload)]
    imported_baseline.note_first_seen(
        payloads.session_id(payload), [str(path) for path in paths if not outside_project_temp(path, cwd)],
        cfg.get("state_root"), lambda path: committed_text(path) is not None,
    )


def _test_findings(payload: dict, cfg: dict, settings_for: WriteConfig) -> list[dict]:
    gate = tests_policy.Gate(payload, cfg, time.time())
    cwd = Path(payload.get("cwd") or ".")
    command = _command(payload)
    targets = _scan_targets(command)
    located = _located(targets, cwd)
    staged: set[str] = set()
    texts, seen = _gathered(located, cwd, reachable_directories(command, cwd), staged)
    appended = {write.path for item in located for write in literal_writes(item.text) if write.append}
    removed = removal_targets(command, cwd) if targets == [command] else frozenset()
    deleted = only_files(removed)
    changes = [_landing(path, text, cwd, seen, path in appended, removed) for path, text in texts.items()]
    if not staged:
        return wording_debt.gate_findings(gate, changes, deleted=deleted, removed=removed, settings_for=settings_for)
    touched = {payloads.resolved_path(path, cwd) for path in texts}
    staged_changes = [_landing(path, None, cwd, seen, False, removed) for path in sorted(staged)
                      if payloads.resolved_path(path, cwd) not in touched]
    denied = tests_policy.findings(gate, changes, (), removed, settings_for=settings_for)
    return denied + wording_debt.lock_findings(gate, [*changes, *staged_changes], deleted=deleted)


def _located(targets: list[str], cwd: Path) -> list[Located]:
    if not targets:
        return []
    first, *nested = targets
    landed = landed_commands(first, cwd)
    outer = landed if landed is not None else [LandedCommand(first, (ShellScope(first, cwd, None),))]
    located = _landed_targets(outer, outer=True) if landed is not None else [Located(first, True, False)]
    starts = _shell_payload_starts(outer) if nested else {}
    direct_payloads = set(_literal_shell_c_payloads(first)) if landed is not None else set()
    for target in nested:
        if target not in starts:
            if target not in direct_payloads:
                located.append(Located(target, False, False))
            continue
        for start, cdpath in starts[target]:
            variants = landed_commands(target, cwd, start=start, cdpath=cdpath)
            if variants is None:
                located.append(Located(target, False, False))
            else:
                located.extend(_landed_targets(variants, outer=False))
    return list(dict.fromkeys(located))


def _landed_targets(commands: list[LandedCommand], *, outer: bool) -> list[Located]:
    landed = [Located(variant.text, outer, True, created=frozenset(path for scope in variant.scopes for path in scope.created))
              for variant in commands]
    return landed + [Located(scope.text, outer, False, True) for variant in commands
                     for scope in variant.scopes if scope.directory is None]


def _shell_payload_starts(commands: list[LandedCommand]) -> dict[str, set[tuple[Path, str | None]]]:
    starts: dict[str, set[tuple[Path, str | None]]] = {}
    for command in commands:
        for scope in command.scopes:
            if scope.directory is None:
                continue
            for segment, directory in _segment_cwds(scope.text, scope.directory, None):
                invocation = interpreter_invocation(segment)
                if invocation and invocation.interpreter in SHELL_C_INTERPRETERS and invocation.payload is not None:
                    starts.setdefault(invocation.payload, set()).add((directory, scope.exported_cdpath))
    return starts


def _gathered(
    located: list[Located], cwd: Path, payload_start: Directories, staged: set[str],
) -> tuple[dict[str, str | None], Counter[Path]]:
    texts: dict[str, str | None] = {}
    writes: Counter[Path] = Counter()
    named: Counter[Path] = Counter()
    for target, outer, landed, deferred, created in located:
        directories = None if deferred else OUTER_START if outer or landed else payload_start
        scope = Scope(cwd, directories, outer=outer and not deferred, created=created)
        named_writes = _shell_targets(target, cwd, created=created)
        texts.update({path: text for path, text in named_writes.items() if not deferred or Path(path).is_absolute()})
        paths = gated_paths(target, scope, landed=landed, staged=staged)
        paths_by_location = {payloads.resolved_path(path, cwd): path for path in paths}
        writes.update(paths_by_location.keys())
        named.update(_named_paths(target, scope, landed=landed))
        for path in paths:
            texts.setdefault(path, None)
        for change in incoming_test_content(target, scope, landed=landed):
            canonical = payloads.resolved_path(change.path, cwd)
            if writes[canonical] <= 1:
                key = paths_by_location.get(canonical, change.path)
                if texts.get(key) is None:
                    texts[key] = change.after
    return texts, writes | named


def _named_paths(command: str, scope: Scope, *, landed: bool) -> Counter[Path]:
    named: Counter[Path] = Counter()
    segments = line_segments(command)
    resolve = command_expansions(command, scope.cwd).path
    folders = (landing_directories(segments, scope.cwd, resolve, created=scope.created) if landed
               else segment_directories(segments, scope.directories, resolve))
    for segment, places in zip(segments, folders):
        words = sorted({part for word in segment for part in REDIRECT_SPLIT_RE.split(_bare(word)) if part})
        named.update({payloads.resolved_path(path, scope.cwd) for path in joined_paths(words, places or OUTER_START)})
    return named


def _landing(
    path: str, text: str | None, cwd: Path, writes: Counter[Path], append: bool, removed: frozenset[Path],
) -> tests_policy.PendingChange:
    resolved = payloads.resolved_path(path, cwd)
    before = tests_policy.read_before(resolved)
    if text is None or writes[resolved] > 1:
        return tests_policy.PendingChange(path, before, None, after_unknown=not tests_policy._deleted(str(resolved), removed))
    return tests_policy.PendingChange(path, before, (before or "") + text if append else text)


def _write_shape_totals(
    scan_targets: list[str], cfg: dict, cwd: Path, settings_for: WriteConfig,
) -> tuple[list[dict], list[dict]]:
    owned: list[dict] = []
    inherited: list[dict] = []
    for target in scan_targets:
        target_owned, target_inherited = shaped_write_findings(target, cfg, cwd, settings_for=settings_for)
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
