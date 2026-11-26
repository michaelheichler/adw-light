from __future__ import annotations

import os
import time
from pathlib import Path
from typing import NamedTuple

try:
    from . import blocker_state, git_port, payloads, tests_policy, unread_files, unread_git, unread_markers, unread_snapshot
    from .config import resolve_outcome
except ImportError:
    import blocker_state
    import git_port
    import payloads
    import tests_policy
    import unread_files
    import unread_git
    import unread_markers
    import unread_snapshot
    from config import resolve_outcome

TEST_REASON = "test file"
RULES_REASON = "rules "
UNREADABLE_REASON = "unreadable after the script"
CONTROL_REASON = "git control file changed"
HISTORY_REASON = "git history changed in a way ADW could not read"
STATE_REASON = "ADW state changed during an unreadable script"
NESTED_REASON = "nested git repo appeared, ADW cannot see its files"


class Change(NamedTuple):
    rel: str
    before: str
    after: str
    printed: list[int] | None


class Hold(NamedTuple):
    path: str
    reason: str
    ref: str
    root: str = ""
    rel: str = ""

    def entry(self) -> dict[str, str]:
        found = {"ref": self.ref, "why": self.reason}
        return found | ({"root": self.root, "rel": self.rel} if self.root else {})


class Phase(NamedTuple):
    after: bool = False
    own: str = ""


def changes(record: dict) -> list[Change]:
    listed = unread_snapshot.snapshot_paths(Path(record["root"]))
    if listed is None:
        raise OSError("git could not list the project files")
    real = Path(record["real"])
    files = record["files"]
    found = []
    for rel in sorted(set(files) | set(listed)):
        change = unread_files.change_of(real / rel, files.get(rel), record["started_ns"])
        if change is not None:
            found.append(Change(rel, *change))
    return found


def settle(payload: dict, cfg: dict, phase: Phase = Phase()) -> list[Hold]:
    session = payloads.session_id(payload)
    state_root = cfg.get("state_root")
    holds: list[Hold] = []
    accepted: dict[str, dict[str, list | None]] = {}
    fields: dict[str, dict] = {}
    for key, record in unread_snapshot.open_records(session, state_root).items():
        judge = not phase.after or not record.get("settled") or phase.own in record["ids"]
        fresh, accepted[key] = _file_holds(record, cfg)
        git_fresh, fields[key] = _git_holds(record, cfg, judge)
        merged = {hold.path: hold for hold in fresh} | {hold.path: hold for hold in git_fresh}
        holds.extend(hold_all(session, record["agent_id"], state_root, record["real"], list(merged.values())))
    unread_snapshot.rebaseline(session, state_root, accepted, fields)
    return holds


def hold_all(session: str, agent: str, state_root, real: str, fresh: list[Hold]) -> list[Hold]:
    if not fresh:
        return []
    scope = blocker_state.BlockerScope(session, agent, state_root)
    added = set(blocker_state.hold_files(scope, {hold.path: hold.entry() for hold in fresh}))
    shared = {hold.path: hold.entry() for hold in fresh if hold.reason != STATE_REASON}
    unread_markers.checkout_hold(session, state_root, real, shared)
    return [hold for hold in fresh if hold.path in added]


def _file_holds(record: dict, cfg: dict) -> tuple[list[Hold], dict[str, list | None]]:
    held: list[Hold] = []
    kept: dict[str, list | None] = {}
    credited = set(record["credited"])
    for change in changes(record):
        path = str(Path(record["real"]) / change.rel)
        readable = change.after != unread_files.UNREADABLE
        taken = readable and path in credited
        reason = "" if taken else _reason(record, change, path, cfg)
        if reason:
            held.append(Hold(path, reason, change.before))
        else:
            kept[change.rel] = None if change.printed is None else [*change.printed, change.after]
    return held, kept


def _reason(record: dict, change: Change, path: str, cfg: dict) -> str:
    if change.rel.endswith("/"):
        return NESTED_REASON
    if change.after == unread_files.UNREADABLE:
        return TEST_REASON if tests_policy.is_test_path(path, record["real"]) else UNREADABLE_REASON
    if _test_change(record, change, path) and not tests_policy.gate_open(tests_policy.Gate(_runner(record), cfg, time.time())):
        return TEST_REASON
    if change.after in {unread_files.ABSENT, unread_files.FOLDER, unread_files.OTHER}:
        return ""
    rules = blocking_rules(path, record["real"], cfg)
    return RULES_REASON + ", ".join(rules) if rules else ""


def _git_holds(record: dict, cfg: dict, judge: bool) -> tuple[list[Hold], dict]:
    root = Path(record["root"])
    now = unread_git.history(root)
    fields = {"settled": True} | ({"head": now.head, "index": now.index, "refs": now.refs} if now is not None else {})
    if not judge:
        try:
            control = unread_git.control_entries(root)
        except OSError:
            control = None
        return [], fields | ({"control": control} if control is not None else {})
    return [*_history_holds(record, cfg, now), *_control_holds(record)], fields


def _history_holds(record: dict, cfg: dict, now: unread_git.History | None) -> list[Hold]:
    real, root = record["real"], record["root"]
    paths = None if now is None else unread_git.changed_paths(Path(root), record, now)
    if paths is None:
        return [Hold(real, HISTORY_REASON, _history_ref(record, None), root)]
    holds: list[Hold] = []
    for rel in paths:
        path = os.path.join(real, rel)
        if tests_policy.is_test_path(path, real):
            holds.append(Hold(path, TEST_REASON, _history_ref(record, rel), root, rel))
        elif os.path.isfile(path) and (rules := blocking_rules(path, real, cfg)):
            holds.append(Hold(path, RULES_REASON + ", ".join(rules), _history_ref(record, rel), root, rel))
    return holds


def _history_ref(record: dict, rel: str | None) -> str:
    entry = record["index"] if rel is None else record["index"].get(rel)
    return unread_git.history_ref(record["head"], record.get("refs"), entry)


def _control_holds(record: dict) -> list[Hold]:
    root = record["root"]
    found = unread_git.control(Path(root))
    whole = unread_git.control_ref(record["control"])
    if found is None:
        return [Hold(os.path.join(record["real"], ".git"), CONTROL_REASON, whole, root)]
    before = record["control"]
    holds = [Hold(str(path), CONTROL_REASON, whole, root) for path in found.unreadable]
    for path in sorted(set(before) | {str(path) for path in found.paths}):
        change = unread_files.change_of(Path(path), before.get(path), record["started_ns"])
        if change is not None:
            holds.append(Hold(path, CONTROL_REASON, change[0]))
    return holds


def _test_change(record: dict, change: Change, path: str) -> bool:
    root = record["real"]
    if tests_policy.is_test_path(path, root):
        return True
    if change.after == unread_files.ABSENT:
        return False
    after = tests_policy.read_before(Path(path))
    pending = tests_policy.PendingChange(path, _before_text(record, change), after)
    return after is not None and tests_policy.touches_tests(pending, root)


def _before_text(record: dict, change: Change) -> str | None:
    if not change.before.startswith(unread_files.BLOB):
        return None
    text = git_port.view().head_text(Path(record["root"]), change.rel)
    return text if text is not None and unread_files.text_ref(text, change.before) == change.before else None


def _runner(record: dict) -> dict:
    return {
        "session_id": record.get("session_id") or "", "agent_id": record["agent_id"], "agent_type": record.get("agent_type") or "", "cwd": record["real"],
    }


def blocking_rules(path: str, real: str, cfg: dict) -> list[str]:
    import record as recorder
    owned, _inherited = recorder._scan_paths([path], Path(real), cfg)
    return sorted({row["rule"] for row in owned if resolve_outcome(row, cfg) == "block"})


def held(session: str, state_root, cwd: str) -> dict[str, dict[str, str]]:
    own = {path: entry for entries in blocker_state.held_files(session, state_root).values() for path, entry in entries.items()}
    return {**unread_markers.checkout_held(session, state_root, cwd), **own}


def release(session: str, state_root, paths: set[str]) -> None:
    if not paths:
        return
    for agent, entries in blocker_state.held_files(session, state_root).items():
        gone = [path for path in entries if path in paths]
        if gone:
            blocker_state.release_files(blocker_state.BlockerScope(session, agent, state_root), gone)
    unread_markers.checkout_release(session, state_root, paths)


def release_restored(session: str, state_root, cwd: str) -> dict[str, dict[str, str]]:
    found = held(session, state_root, cwd)
    seen: dict = {}
    restored = {path for path, entry in found.items() if _restored(path, entry, seen)}
    release(session, state_root, restored)
    return {path: entry for path, entry in found.items() if path not in restored}


def _restored(path: str, entry: dict[str, str], seen: dict) -> bool:
    ref = entry.get("ref", "")
    if ref.startswith((unread_git.HISTORY_REF, unread_git.CONTROL_REF)):
        return unread_git.ref_matches(ref, entry.get("root", ""), entry.get("rel", ""), seen)
    return unread_files.matches(Path(path), ref)


def release_taken(session: str, state_root, cwd: str, paths: set[str]) -> None:
    found = held(session, state_root, cwd)
    release(session, state_root, {path for path in paths if found.get(path, {}).get("why") == TEST_REASON})


def release_fixed(session: str, state_root, cwd: str, paths: set[str], cfg: dict) -> None:
    found = held(session, state_root, cwd)
    fixed = set()
    for path in paths:
        if not found.get(path, {}).get("why", "").startswith(RULES_REASON):
            continue
        root = git_port.view().toplevel(Path(path).parent)
        if root is not None and os.path.isfile(path) and not blocking_rules(path, os.path.realpath(root), cfg):
            fixed.add(path)
    release(session, state_root, fixed)


def real_paths(payload: dict) -> set[str]:
    cwd = Path(payloads.cwd(payload) or ".")
    raw = (*payloads.edited_paths(payload), *payloads.removed_paths(payload))
    return {os.path.realpath(payloads.resolved_path(path, cwd)) for path in raw}
