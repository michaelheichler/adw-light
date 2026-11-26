from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Callable, NamedTuple

from . import payloads, session_state

KEY = "unresolved_blockers"
REPORT_LINE_RE = re.compile(r"\nFull report: .*?(?=\n|$)")
RECORD_ERROR_KEY = "<record-error>"
BATCH_KEY = "<batch>"
BATCH_ERROR_KEY = "<batch-error>"
STALL_KEY = "stall"
DROPPED_KEY = "dropped_paths"
HELD_KEY = "held_files"
MAX_SCOPE_PATHS = 500
RESCAN_RELEASED_KEYS = frozenset({RECORD_ERROR_KEY})


@dataclass(frozen=True, slots=True)
class BlockerScope:
    session_id: str
    agent_id: str
    root: str | Path | None


def _scope_from_legacy(
    session_id: str,
    values: tuple[object, ...],
    tail_count: int,
) -> tuple[BlockerScope, tuple[object, ...]]:
    if len(values) not in {tail_count + 1, tail_count + 2}:
        raise TypeError("blocker state call has an invalid argument count")
    agent_id = values[0]
    if not isinstance(agent_id, str):
        raise TypeError("blocker state agent_id must be a string")
    has_root = len(values) == tail_count + 2
    root = values[-1] if has_root else None
    if root is not None and not isinstance(root, (str, Path)):
        raise TypeError("blocker state root must be a path or None")
    tail = values[1:-1] if has_root else values[1:]
    return BlockerScope(session_id, agent_id, root), tail


def scope(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("agent_id")
    return value if isinstance(value, str) else ""


def _bucket(state: dict, agent_id: str) -> dict:
    root = state.get(KEY)
    if not isinstance(root, dict):
        return {"pending": {}, "paths": [], "revision": 0}
    value = root.get(agent_id)
    return value if isinstance(value, dict) else {"pending": {}, "paths": [], "revision": 0}


def _normalized_bucket(bucket: dict) -> tuple[dict[str, str], list[str], int]:
    raw_pending = bucket.get("pending")
    raw_paths = bucket.get("paths")
    raw_revision = bucket.get("revision")
    pending = {
        key: value for key, value in raw_pending.items()
        if isinstance(key, str) and isinstance(value, str)
    } if isinstance(raw_pending, dict) else {}
    paths = [value for value in raw_paths if isinstance(value, str)] if isinstance(raw_paths, list) else []
    revision = raw_revision if isinstance(raw_revision, int) and not isinstance(raw_revision, bool) else 0
    return pending, paths, revision


def _update(scope: BlockerScope, mutate: Callable[[dict], dict]) -> None:
    if not scope.session_id:
        raise ValueError("blocker_state requires a non-empty session_id")

    def change(state: dict) -> dict:
        scopes = state.get(KEY)
        scopes = dict(scopes) if isinstance(scopes, dict) else {}
        bucket = _bucket(state, scope.agent_id)
        _pending, _paths, revision = _normalized_bucket(bucket)
        scopes[scope.agent_id] = {**mutate(bucket), "revision": revision + 1}
        return {**state, KEY: scopes}

    session_state.update_state_strict(scope.session_id, change, scope.root)


def set_pending(scope: BlockerScope | str, *values: object) -> None:
    if isinstance(scope, str):
        scope, values = _scope_from_legacy(scope, values, 2)
    if len(values) != 2 or not all(isinstance(value, str) for value in values):
        raise TypeError("set_pending requires string path and reason values")
    path, reason = values

    def mutate(bucket: dict) -> dict:
        pending, _paths, _revision = _normalized_bucket(bucket)
        pending[path or "<turn>"] = REPORT_LINE_RE.sub("", reason)
        return {**bucket, "pending": pending}

    _update(scope, mutate)


def clear_pending(scope: BlockerScope | str, *values: object) -> None:
    if isinstance(scope, str):
        scope, values = _scope_from_legacy(scope, values, 1)
    if len(values) != 1 or not isinstance(values[0], str):
        raise TypeError("clear_pending requires a string path")
    path = values[0]

    def mutate(bucket: dict) -> dict:
        pending, _paths, _revision = _normalized_bucket(bucket)
        pending.pop(path or "<turn>", None)
        return {**bucket, "pending": pending}

    _update(scope, mutate)


def touch_paths(scope: BlockerScope | str, *values: object) -> None:
    if isinstance(scope, str):
        scope, values = _scope_from_legacy(scope, values, 1)
    if (
        len(values) != 1
        or not isinstance(values[0], list)
        or not all(isinstance(path, str) for path in values[0])
    ):
        raise TypeError("touch_paths requires a list of string paths")
    paths = values[0]

    def mutate(bucket: dict) -> dict:
        current, existing, _revision = _normalized_bucket(bucket)
        return _capped(bucket, current, _joined(existing, paths))

    _update(scope, mutate)


def _joined(existing: list[str], paths: list[str]) -> list[str]:
    known = set(existing)
    added = [path for path in dict.fromkeys(paths) if path and path not in known]
    return existing + added


def _capped(bucket: dict, pending: dict[str, str], paths: list[str]) -> dict:
    excess = max(len(paths) - MAX_SCOPE_PATHS, 0)
    gone = set(islice((path for path in paths if path not in pending), excess))
    if not gone:
        return {**bucket, "pending": pending, "paths": paths}
    count = bucket.get(DROPPED_KEY)
    total = (count if isinstance(count, int) and not isinstance(count, bool) else 0) + len(gone)
    kept = [path for path in paths if path not in gone]
    return {**bucket, "pending": pending, "paths": kept, DROPPED_KEY: total}


def apply_paths(scope: BlockerScope, paths: list[str], pending: dict[str, str | None]) -> None:

    def mutate(bucket: dict) -> dict:
        current, existing, _revision = _normalized_bucket(bucket)
        for key, reason in pending.items():
            if reason is None:
                current.pop(key or "<turn>", None)
            else:
                current[key or "<turn>"] = REPORT_LINE_RE.sub("", reason)
        return _capped(bucket, current, _joined(existing, paths))

    _update(scope, mutate)


def take_dropped(scope: BlockerScope) -> int:
    stored = _bucket(session_state.read_state_strict(scope.session_id, scope.root), scope.agent_id).get(DROPPED_KEY)
    if not isinstance(stored, int) or isinstance(stored, bool) or stored <= 0:
        return 0
    claimed: list[int] = []

    def mutate(bucket: dict) -> dict:
        value = bucket.get(DROPPED_KEY)
        if isinstance(value, int) and not isinstance(value, bool):
            claimed.append(value)
        return {key: item for key, item in bucket.items() if key != DROPPED_KEY}

    _update(scope, mutate)
    return claimed[0] if claimed else 0


def stall(scope: BlockerScope, key: str = STALL_KEY) -> dict:
    stored = _bucket(session_state.read_state_strict(scope.session_id, scope.root), scope.agent_id).get(key)
    return dict(stored) if isinstance(stored, dict) else {}


def note_stall(scope: BlockerScope, record: dict, key: str = STALL_KEY) -> None:
    _update(scope, lambda bucket: {**bucket, key: record})


def _unscanned_paths(payload: dict) -> list[str]:
    cwd = Path(payloads.cwd(payload) or ".")
    writes = (call for call in payloads.tool_calls(payload) if payloads.tool_name(call).lower() in payloads.WRITE_TOOL_NAMES)
    calls = [payload, *({**call, "cwd": payloads.cwd(payload)} for call in writes)]
    raw_paths = [raw for call in calls for raw in payloads.edited_paths(call)]
    return list(dict.fromkeys(str(payloads.resolved_path(raw, cwd)) for raw in raw_paths))


def hold_undecidable(payload: object, config: object, key: str, reason: str) -> None:
    fields = payloads.exact_string_dict(payload)
    session_id = payloads.session_id(fields)
    if not session_id:
        return
    root = payloads.exact_string_dict(config).get("state_root")
    target = BlockerScope(session_id, scope(fields), root if isinstance(root, str) else None)
    try:
        paths = _unscanned_paths(fields)
        if paths:
            set_pending(target, key, reason)
            touch_paths(target, paths)
    except Exception as state_exc:
        sys.stderr.write(f"agent-discipline-watcher: blocker state update failed: {state_exc}\n")


def details(session_id: str, agent_id: str, root=None) -> tuple[dict[str, str], list[str], int]:
    bucket = _bucket(session_state.read_state_strict(session_id, root), agent_id)
    return _normalized_bucket(bucket)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(entry, str) for entry in value)


def _is_reconcile_shape(values: tuple[object, ...]) -> bool:
    if len(values) != 3:
        return False
    revision = values[0]
    if not isinstance(revision, int) or isinstance(revision, bool):
        return False
    return _is_string_list(values[1]) and _is_string_list(values[2])


def reconcile(scope: BlockerScope | str, *values: object) -> None:
    if isinstance(scope, str):
        scope, values = _scope_from_legacy(scope, values, 3)
    if not _is_reconcile_shape(values):
        raise TypeError("reconcile requires a revision and string key and path lists")
    revision, pending_keys, paths = values

    def mutate(bucket: dict) -> dict:
        if bucket.get("revision") != revision:
            return bucket
        pending, existing, _revision = _normalized_bucket(bucket)
        for key in pending_keys:
            pending.pop(key, None)
        removed = set(paths)
        return {**bucket, "pending": pending, "paths": [path for path in existing if path not in removed]}

    _update(scope, mutate)


class PartialScan(NamedTuple):
    clean: list[str]
    cut: str


def settle_partial(scope: BlockerScope, revision: int, partial: PartialScan) -> None:

    def mutate(bucket: dict) -> dict:
        if bucket.get("revision") != revision:
            return bucket
        pending, existing, _revision = _normalized_bucket(bucket)
        gone = {*partial.clean, partial.cut}
        for key in partial.clean:
            pending.pop(key, None)
        kept = [path for path in existing if path not in gone]
        return {**bucket, "pending": pending, "paths": kept + ([partial.cut] if partial.cut in existing else [])}

    _update(scope, mutate)


def snapshot(session_id: str, agent_id: str, root=None) -> tuple[list[str], list[str]]:
    pending, paths, _revision = details(session_id, agent_id, root)
    return list(pending.values()), paths


def _held(bucket: dict) -> dict[str, dict[str, str]]:
    stored = bucket.get(HELD_KEY)
    if not isinstance(stored, dict):
        return {}
    return {
        path: {key: value for key, value in entry.items() if isinstance(key, str) and isinstance(value, str)}
        for path, entry in stored.items() if isinstance(path, str) and isinstance(entry, dict)
    }


def hold_files(scope: BlockerScope, entries: dict[str, dict[str, str]]) -> list[str]:
    added: list[str] = []

    def mutate(bucket: dict) -> dict:
        held = _held(bucket)
        fresh = {path: entry for path, entry in entries.items() if path not in held}
        added.extend(fresh)
        return {**bucket, HELD_KEY: {**held, **fresh}}

    _update(scope, mutate)
    return added


def release_files(scope: BlockerScope, paths: list[str]) -> None:
    gone = set(paths)

    def mutate(bucket: dict) -> dict:
        kept = {path: entry for path, entry in _held(bucket).items() if path not in gone}
        return {key: value for key, value in bucket.items() if key != HELD_KEY} | ({HELD_KEY: kept} if kept else {})

    _update(scope, mutate)


def held_files(session_id: str, root=None) -> dict[str, dict[str, dict[str, str]]]:
    scopes = session_state.read_state_strict(session_id, root).get(KEY)
    if not isinstance(scopes, dict):
        return {}
    found = {agent: _held(bucket) for agent, bucket in scopes.items() if isinstance(agent, str) and isinstance(bucket, dict)}
    return {agent: held for agent, held in found.items() if held}


def scope_ids(session_id: str, root=None) -> list[str]:
    state = session_state.read_state_strict(session_id, root)
    scopes = state.get(KEY)
    return [key for key in scopes if isinstance(key, str)] if isinstance(scopes, dict) else []
