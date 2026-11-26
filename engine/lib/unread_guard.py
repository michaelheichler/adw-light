from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

try:
    from . import agent_types, payloads, session_state, unread_commands, unread_compare, unread_files, unread_git, unread_markers, unread_snapshot
    from .config import payload_hook_config
    from .shell_scripts import unresolved_shell_script
    from .findings import Finding
    from .reporting import record_findings
    from .tests_policy import TEST_WRITER, TEST_WRITER_TYPES
except ImportError:
    import agent_types
    import payloads
    import session_state
    import unread_commands
    import unread_compare
    import unread_files
    import unread_git
    import unread_markers
    import unread_snapshot
    from config import payload_hook_config
    from shell_scripts import unresolved_shell_script
    from findings import Finding
    from reporting import record_findings
    from tests_policy import TEST_WRITER, TEST_WRITER_TYPES

RULE = "unread_script_lock"
READ_TOOLS = frozenset({"Read", "Glob", "Grep", "LS"})
HANDOVER_TOOLS = frozenset({"Agent", "Task"})
DIRECT_WRITERS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"})
GATE = "gate"
ALLOW = "allow"
DENY = "deny"
MAX_NAMED = 40
LEAD = (
    "ADW Lite holds a lock because a script that the gate could not read changed the files or git state below. "
    "Every tool call except reads stays refused until each entry below clears through the exit it names."
)
TAIL = (
    f"Reads, read-only git, the restore commands above, chmod on a held path or its folder, and a hand-over to the {TEST_WRITER} agent still run. "
    f"A hand-over clears only the test files that the {TEST_WRITER} agent writes itself, once that agent stops."
)
DETAIL = "Script the gate could not read changed "
ACTION = "Clear the entry through the exit that the lock names for it."
HISTORY_BACK = "ask the user to put HEAD, the branch heads and its index entry back to their state before the script"


def join_notices(*parts: str | None) -> str:
    return "\n".join(part for part in parts if part)


class Decision(NamedTuple):
    kind: str
    reason: str = ""


def before_tool(payload: dict, config: dict | None) -> Decision:
    try:
        return _before_tool(payload, config)
    except unread_markers.MarkerError as exc:
        return Decision(DENY, str(exc))


def _before_tool(payload: dict, config: dict | None) -> Decision:
    session = payloads.session_id(payload)
    if not session:
        return Decision(GATE)
    cfg = payload_hook_config(config, payload)
    state_root = cfg.get("state_root")
    cwd = payloads.cwd(payload)
    _check_state(payload, session, state_root)
    if unread_snapshot.open_records(session, state_root):
        unread_compare.settle(payload, cfg)
    held = unread_compare.release_restored(session, state_root, cwd)
    if not held:
        return Decision(GATE)
    kind = _call_kind(payload, held, state_root)
    if kind != DENY:
        return Decision(kind)
    _record(payload, cfg, held)
    return Decision(DENY, lock_reason(held, cwd))


def _check_state(payload: dict, session: str, state_root) -> list[unread_compare.Hold]:
    if unread_snapshot.verify(session, state_root):
        return []
    hold = unread_compare.Hold(str(session_state.session_directory(session, state_root)), unread_compare.STATE_REASON, unread_files.LOCKED)
    return unread_compare.hold_all(session, payloads.agent_id(payload), state_root, "", [hold])


def _call_kind(payload: dict, held: dict[str, dict[str, str]], state_root) -> str:
    name = payloads.tool_name(payload)
    tests_only = all(entry.get("why") == unread_compare.TEST_REASON for entry in held.values())
    if name in READ_TOOLS or (tests_only and _writer(payload, state_root)):
        return GATE
    tool_input = payloads.exact_string_dict(payload.get("tool_input"))
    if name in HANDOVER_TOOLS and tool_input.get("subagent_type") in TEST_WRITER_TYPES:
        return GATE
    if name in DIRECT_WRITERS:
        return GATE if _source_fix(payload, held) else DENY
    command = tool_input.get("command")
    if name != "Bash" or not isinstance(command, str):
        return DENY
    reading = unread_commands.classify(command, Path(payloads.cwd(payload) or "."))
    if not reading.known or not all(_covered(item, held) for item in reading.restores):
        return DENY
    if reading.restores and all(_restorable(item, held) for item in reading.restores):
        return ALLOW
    return GATE


def _covered(item: unread_commands.Restore, held: dict[str, dict[str, str]]) -> bool:
    if item.path in held:
        return True
    if item.kind != unread_commands.BY_CHMOD or not os.path.isdir(item.path):
        return False
    return any(Path(path).is_relative_to(item.path) for path, entry in held.items() if entry.get("why") != unread_compare.STATE_REASON)


def _source_fix(payload: dict, held: dict[str, dict[str, str]]) -> bool:
    paths = unread_compare.real_paths(payload)
    return bool(paths) and all(held.get(path, {}).get("why", "").startswith(unread_compare.RULES_REASON) for path in paths)


def _restorable(item: unread_commands.Restore, held: dict[str, dict[str, str]]) -> bool:
    entry = held.get(item.path)
    if item.kind == unread_commands.BY_CHMOD:
        return entry is None or entry.get("why") != unread_compare.STATE_REASON
    if entry is None:
        return False
    ref = entry.get("ref", "")
    return ref.startswith(unread_files.BLOB) if item.kind == unread_commands.BY_GIT else ref == unread_files.ABSENT


def _writer(payload: dict, state_root) -> bool:
    return agent_types.type_of(payload, state_root) in TEST_WRITER_TYPES


def _record(payload: dict, cfg: dict, held: dict[str, dict[str, str]]) -> None:
    rows = [Finding(
        family="self_protection", rule=RULE, line=1, detail=DETAIL + path, force=True, snippet=path[:180],
        action=ACTION, path=path, severity=None, tool_use_id=None,
    ).to_dict() for path in sorted(held)]
    record_findings(
        session_id=payloads.session_id(payload), hook="pre_tool", event="PreToolUse", findings=rows,
        turn_id=payloads.turn_id(payload), tool_use_id=payloads.tool_use_id(payload),
        root=cfg.get("ledger_root"), config=cfg,
    )


def lock_reason(held: dict[str, dict[str, str]], cwd: str) -> str:
    names = sorted(held)
    lines = [_line(path, held[path], cwd) for path in names[:MAX_NAMED]]
    more = [f"- and {len(names) - MAX_NAMED} more files"] if len(names) > MAX_NAMED else []
    return "\n".join([LEAD, *lines, *more, TAIL])


def _line(path: str, entry: dict[str, str], cwd: str) -> str:
    shown = _shown(path, cwd)
    why = entry.get("why", "")
    fix = _fix(shown, entry.get("ref", ""), why)
    if why.startswith(unread_compare.RULES_REASON):
        fix += ", or fix it with Edit or Write until it passes the source rules"
    if why == unread_compare.UNREADABLE_REASON:
        fix = f"ADW could not read it, make it readable with chmod u+r {shown}, then {fix}"
    return f"- {shown} ({why}), {fix}"


def _fix(shown: str, ref: str, why: str) -> str:
    if ref == unread_files.LOCKED:
        return "ADW cannot trust its own state for this session, start a new session"
    if why == unread_compare.NESTED_REASON:
        return f"ask the user to remove {shown} or move it out of the project"
    if ref.startswith(unread_git.HISTORY_REF):
        return _history_fix(why)
    if ref.startswith(unread_git.CONTROL_REF):
        return "ask the user to put the git config, hooks and info files back to their state before the script"
    if ref == unread_files.ABSENT:
        return f"the script created it, remove it with rm {shown}"
    if ref.startswith(unread_files.BLOB):
        return f"restore it with git restore -- {shown}"
    if why == unread_compare.TEST_REASON:
        return f"git has no copy of its earlier state, hand it to the {TEST_WRITER} agent to rewrite it, or ask the user to restore its earlier content"
    return "git has no copy of its earlier state, ask the user to restore its earlier content"


def _history_fix(why: str) -> str:
    if why == unread_compare.HISTORY_REASON:
        return "ask the user to put HEAD, the branch heads and the index back to their state before the script"
    if why == unread_compare.TEST_REASON:
        return f"it changed in git history, hand it to the {TEST_WRITER} agent to rewrite it, or {HISTORY_BACK}"
    return f"it changed in git history, {HISTORY_BACK}"


def _shown(path: str, cwd: str) -> str:
    base = os.path.realpath(cwd or ".")
    inside = Path(path).is_relative_to(base)
    return os.path.relpath(path, base) if inside else path


def after_tool(payload: dict, config: dict | None, *, credit: bool = True) -> str:
    session = payloads.session_id(payload)
    if not session:
        return ""
    cfg = payload_hook_config(config, payload)
    state_root = cfg.get("state_root")
    cwd = payloads.cwd(payload)
    fresh = _check_state(payload, session, state_root) + _unrecorded(payload, session, state_root)
    writer = credit and _writer(payload, state_root)
    if writer:
        unread_snapshot.note_written(session, state_root, payloads.agent_id(payload), unread_compare.real_paths(payload))
    if unread_snapshot.open_records(session, state_root):
        if credit:
            _credit(payload, session, state_root, writer)
        phase = unread_compare.Phase(True, payloads.tool_use_id(payload))
        fresh += unread_compare.settle(payload, cfg, phase)
    if credit:
        _exits(payload, cfg, session, state_root)
    unread_compare.release_restored(session, state_root, cwd)
    return _fresh_reason(fresh, cwd)


def _credit(payload: dict, session: str, state_root, writer: bool) -> None:
    if payloads.tool_name(payload) not in DIRECT_WRITERS and not writer:
        return
    unread_snapshot.credit(session, state_root, unread_compare.real_paths(payload))


def _unrecorded(payload: dict, session: str, state_root) -> list[unread_compare.Hold]:
    command = payloads.exact_string_dict(payload.get("tool_input")).get("command")
    if payloads.tool_name(payload) != "Bash" or not isinstance(command, str):
        return []
    key = payloads.tool_use_id(payload)
    if key and unread_snapshot.has_id(session, state_root, key):
        return []
    if not unresolved_shell_script(command, Path(payloads.cwd(payload) or ".")):
        return []
    hold = unread_compare.Hold(str(session_state.session_directory(session, state_root)), unread_compare.STATE_REASON, unread_files.LOCKED)
    return unread_compare.hold_all(session, payloads.agent_id(payload), state_root, "", [hold])


def _exits(payload: dict, cfg: dict, session: str, state_root) -> None:
    if payloads.tool_name(payload) in DIRECT_WRITERS:
        unread_compare.release_fixed(session, state_root, payloads.cwd(payload), unread_compare.real_paths(payload), cfg)


def at_stop(payload: dict, config: dict | None, agent: str | None = None) -> str:
    session = payloads.session_id(payload)
    if not session:
        return ""
    cfg = payload_hook_config(config, payload)
    state_root = cfg.get("state_root")
    fresh = _check_state(payload, session, state_root)
    if unread_snapshot.open_records(session, state_root):
        fresh += unread_compare.settle(payload, cfg)
        unread_snapshot.close(session, state_root, agent)
    if _writer(payload, state_root):
        written = unread_snapshot.take_written(session, state_root, payloads.agent_id(payload))
        unread_compare.release_taken(session, state_root, payloads.cwd(payload), written)
    unread_compare.release_restored(session, state_root, payloads.cwd(payload))
    return _fresh_reason(fresh, payloads.cwd(payload))


def _fresh_reason(fresh: list[unread_compare.Hold], cwd: str) -> str:
    if not fresh:
        return ""
    return lock_reason({hold.path: hold.entry() for hold in fresh}, cwd)
