from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

try:
    from . import git_port, payloads, session_state, tests_policy, unread_files, unread_git, unread_markers
except ImportError:
    import git_port
    import payloads
    import session_state
    import tests_policy
    import unread_files
    import unread_git
    import unread_markers

STATE_KEY = "unread_script_snapshots"
WRITTEN_KEY = "unread_writer_paths"
MAX_WRITTEN = 2000
MAX_OPEN = 8
MAX_FILES = 5000
MAX_HASH_FILES = 2000
MAX_COMMAND_CHARS = 200
_DEFERRED: ContextVar[list[dict] | None] = ContextVar("adw_unread_scripts", default=None)


@contextmanager
def deferring() -> Iterator[list[dict]]:
    held: list[dict] = []
    token = _DEFERRED.set(held)
    try:
        yield held
    finally:
        _DEFERRED.reset(token)


def defer(rows: list[dict]) -> bool:
    held = _DEFERRED.get()
    if held is None:
        return False
    held.extend(rows)
    return True


def take(payload: dict, cfg: dict, command: str) -> bool:
    session = payloads.session_id(payload)
    root = git_port.view().toplevel(Path(payloads.cwd(payload) or "."))
    if not session or root is None:
        return False
    key = payloads.tool_use_id(payload) or f"unkeyed-{time.time_ns()}"
    agent = payloads.agent_id(payload)
    state_root = cfg.get("state_root")
    if _joined(session, state_root, key, agent, str(root)):
        return True
    started = time.time_ns()
    try:
        record = _captured(root, started)
    except OSError:
        return False
    if record is None:
        return False
    record.update({
        "ids": [key], "session_id": session, "agent_id": agent, "agent_type": payloads.agent_type(payload),
        "cwd": payloads.cwd(payload), "command": command[:MAX_COMMAND_CHARS],
    })
    stored: list[str] = []

    def mutate(state: dict) -> dict:
        opened = _records(state)
        if len(opened) >= MAX_OPEN:
            return state
        stored.append(key)
        return _stored(session, state_root, state, {**opened, key: record})

    try:
        session_state.update_state_strict(session, mutate, state_root)
    except OSError:
        return False
    return bool(stored)


def _captured(root: Path, started: int) -> dict | None:
    files = _files(root)
    now = unread_git.history(root)
    control = unread_git.control_entries(root)
    if files is None or now is None or control is None:
        return None
    return {
        "root": str(root), "real": os.path.realpath(root), "started_ns": started, "files": files, "credited": [],
        "head": now.head, "index": now.index, "refs": now.refs, "control": control, "settled": False,
    }


def _stored(session: str, state_root, state: dict, records: dict[str, dict]) -> dict:
    unread_markers.sync(session, state_root, records)
    rest = {key: value for key, value in state.items() if key != STATE_KEY}
    return {**rest, STATE_KEY: records} if records else rest


def _joined(session: str, state_root, key: str, agent: str, root: str) -> bool:
    joined: list[str] = []

    def mutate(state: dict) -> dict:
        opened = _records(state)
        match = next((name for name, record in opened.items() if record.get("agent_id") == agent and record.get("root") == root), None)
        if match is None:
            return state
        joined.append(match)
        ids = [value for value in opened[match].get("ids", []) if isinstance(value, str)]
        return _stored(session, state_root, state, {**opened, match: {**opened[match], "ids": list(dict.fromkeys([*ids, key]))}})

    session_state.update_state_strict(session, mutate, state_root)
    return bool(joined)


def snapshot_paths(root: Path) -> list[str] | None:
    view = git_port.view()
    listed = view.disk_paths(root)
    ignored = view.ignored_files(root)
    if listed is None or ignored is None:
        return None
    real = os.path.realpath(root)
    tests = [rel for rel in ignored if rel.endswith("/") or tests_policy.is_test_path(os.path.join(real, rel), real)]
    return sorted({*listed, *tests})


def _files(root: Path) -> dict[str, list] | None:
    listed = snapshot_paths(root)
    if listed is None or len(listed) > MAX_FILES or unread_git.nested(root, listed):
        return None
    clean = git_port.view().clean_blob_ids(root)
    if clean is None:
        return None
    real = Path(os.path.realpath(root))
    files: dict[str, list] = {}
    hashed = cost = 0
    for rel in listed:
        printed = unread_files.stat_print(real / rel)
        if printed is None:
            continue
        like = unread_files.BLOB + clean[rel] if rel in clean else unread_files.SHA
        if not like.startswith(unread_files.BLOB):
            hashed += 1
            cost += unread_files.hash_cost(printed)
        if hashed > MAX_HASH_FILES or cost > unread_files.MAX_HASH_BYTES:
            return None
        ref = like if rel in clean else unread_files.current_ref(real / rel, printed, like)
        files[rel] = [*printed, ref]
    return files


def _records(state: dict) -> dict[str, dict]:
    stored = state.get(STATE_KEY)
    if not isinstance(stored, dict):
        return {}
    return {key: record for key, record in stored.items() if isinstance(key, str) and _valid(record)}


def _valid(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    fields = (
        ("files", dict), ("real", str), ("root", str), ("started_ns", int), ("agent_id", str), ("credited", list),
        ("index", dict), ("control", dict), ("ids", list),
    )
    return all(isinstance(record.get(name), kind) for name, kind in fields)


def open_records(session: str, state_root) -> dict[str, dict]:
    state = session_state.read_state_strict(session, state_root)
    stored = state.get(STATE_KEY)
    records = _records(state)
    if isinstance(stored, dict) and len(records) != len(stored):
        raise ValueError("unreadable script snapshot state is malformed")
    return records


def _change(session: str, state_root, mutate: Callable[[dict[str, dict]], dict[str, dict]]) -> None:
    session_state.update_state_strict(session, lambda state: _stored(session, state_root, state, mutate(_records(state))), state_root)


def verify(session: str, state_root) -> bool:
    if not session_state.read_state_strict(session, state_root).get(STATE_KEY) and not unread_markers.present(session, state_root):
        return True
    agreed: list[bool] = []

    def check(state: dict) -> dict:
        stored = state.get(STATE_KEY)
        agreed.append(unread_markers.consistent(session, state_root, stored if isinstance(stored, dict) else {}))
        return _stored(session, state_root, state, _records(state))

    session_state.update_state_strict(session, check, state_root)
    return agreed[0]


def rebaseline(session: str, state_root, accepted: dict[str, dict[str, list | None]], fields: dict[str, dict] | None = None) -> None:
    def mutate(records: dict[str, dict]) -> dict[str, dict]:
        for key, record in list(records.items()):
            entries = accepted.get(key) or {}
            files = {**record["files"], **{rel: entry for rel, entry in entries.items() if entry is not None}}
            gone = {rel for rel, entry in entries.items() if entry is None}
            credited = [path for path in record["credited"] if os.path.relpath(path, record["real"]) not in entries]
            kept = {rel: entry for rel, entry in files.items() if rel not in gone}
            records[key] = {**record, **(fields or {}).get(key, {}), "files": kept, "credited": credited}
        return records

    _change(session, state_root, mutate)


def credit(session: str, state_root, paths: set[str]) -> None:
    if paths:
        _change(session, state_root, lambda records: {
            key: {**record, "credited": sorted({*record["credited"], *paths})} for key, record in records.items()
        })


def note_written(session: str, state_root, agent: str, paths: set[str]) -> None:
    if not paths:
        return

    def mutate(state: dict) -> dict:
        stored = _written(state)
        mine = sorted({*stored.get(agent, []), *paths})
        return {**state, WRITTEN_KEY: {**stored, agent: mine[:MAX_WRITTEN]}}

    session_state.update_state_strict(session, mutate, state_root)


def take_written(session: str, state_root, agent: str) -> set[str]:
    taken: list[str] = []

    def mutate(state: dict) -> dict:
        stored = _written(state)
        taken.extend(stored.pop(agent, []))
        rest = {key: value for key, value in state.items() if key != WRITTEN_KEY}
        return {**rest, WRITTEN_KEY: stored} if stored else rest

    session_state.update_state_strict(session, mutate, state_root)
    return set(taken)


def _written(state: dict) -> dict[str, list[str]]:
    stored = state.get(WRITTEN_KEY)
    if not isinstance(stored, dict):
        return {}
    return {
        agent: [path for path in paths if isinstance(path, str)]
        for agent, paths in stored.items() if isinstance(agent, str) and isinstance(paths, list)
    }


def has_id(session: str, state_root, key: str) -> bool:
    return any(key in record["ids"] for record in open_records(session, state_root).values())


def close(session: str, state_root, agent: str | None = None) -> None:
    _change(session, state_root, lambda records: {
        key: record for key, record in records.items() if agent is not None and record["agent_id"] != agent
    })


def racy(record: dict, entry: list) -> bool:
    return entry[unread_files.MTIME_FIELD] >= record["started_ns"] - unread_files.RACY_NS
