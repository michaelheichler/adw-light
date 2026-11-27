from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Callable, NamedTuple

from . import path_policy, payloads, scan_cache, session_state

KEY = "unresolved_blockers"
REPORT_LINE_RE = re.compile(r"\nFull report: .*?(?=\n|$)")
RECORD_ERROR_KEY = "<record-error>"
BATCH_KEY = "<batch>"
BATCH_ERROR_KEY = "<batch-error>"
STALL_KEY = "stall"
DEFERRED_STALL_KEY = "deferred_stall"
DROPPED_KEY = "dropped_paths"
TOUCHED_KEY = "touched_at"
MAX_SCOPE_PATHS = 500
RESCAN_RELEASED_KEYS = frozenset({RECORD_ERROR_KEY})
RETIRED_KEYS = frozenset({"<after-run>"})


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
        if isinstance(key, str) and isinstance(value, str) and key not in RETIRED_KEYS
    } if isinstance(raw_pending, dict) else {}
    paths = [value for value in raw_paths if isinstance(value, str)] if isinstance(raw_paths, list) else []
    revision = raw_revision if isinstance(raw_revision, int) and not isinstance(raw_revision, bool) else 0
    return pending, paths, revision


def _marks(bucket: dict) -> dict[str, int] | None:
    stored = bucket.get(TOUCHED_KEY)
    if not isinstance(stored, dict):
        return None
    return {key: value for key, value in stored.items() if isinstance(key, str) and type(value) is int}


def _marked(bucket: dict, keys: list[str]) -> dict:
    revision = _normalized_bucket(bucket)[2] + 1
    return {**bucket, TOUCHED_KEY: {**(_marks(bucket) or {}), **dict.fromkeys((key for key in keys if key), revision)}}


def _pruned(bucket: dict) -> dict:
    pending, paths, _revision = _normalized_bucket(bucket)
    live = {*pending, *paths}
    return {**bucket, TOUCHED_KEY: {key: value for key, value in (_marks(bucket) or {}).items() if key in live}}


def _touched_since(bucket: dict, revision: int) -> set[str]:
    pending, paths, current = _normalized_bucket(bucket)
    if current == revision:
        return set()
    marks = _marks(bucket)
    if marks is None:
        return {*pending, *paths}
    return {key for key, value in marks.items() if value > revision}


def _update_many(session_id: str, root: str | Path | None, mutations: dict[str, Callable[[dict], dict]]) -> None:
    if not session_id:
        raise ValueError("blocker_state requires a non-empty session_id")

    def change(state: dict) -> dict:
        scopes = state.get(KEY)
        scopes = dict(scopes) if isinstance(scopes, dict) else {}
        for agent_id, mutate in mutations.items():
            bucket = _bucket(state, agent_id)
            _pending, _paths, revision = _normalized_bucket(bucket)
            scopes[agent_id] = {**mutate(bucket), "revision": revision + 1}
        return {**state, KEY: scopes}

    session_state.update_state_strict(session_id, change, root)


def _update(scope: BlockerScope, mutate: Callable[[dict], dict]) -> None:
    _update_many(scope.session_id, scope.root, {scope.agent_id: mutate})


def set_pending(scope: BlockerScope | str, *values: object) -> None:
    if isinstance(scope, str):
        scope, values = _scope_from_legacy(scope, values, 2)
    if len(values) != 2 or not all(isinstance(value, str) for value in values):
        raise TypeError("set_pending requires string path and reason values")
    path, reason = values

    def mutate(bucket: dict) -> dict:
        pending, _paths, _revision = _normalized_bucket(bucket)
        pending[path or "<turn>"] = REPORT_LINE_RE.sub("", reason)
        return _marked({**bucket, "pending": pending}, [path or "<turn>"])

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
        return _pruned(_capped(_marked(bucket, paths), current, _joined(existing, paths)))

    _update(scope, mutate)


def _joined(existing: list[str], paths: list[str]) -> list[str]:
    touched = dict.fromkeys(path for path in paths if path)
    return [path for path in existing if path not in touched] + list(touched)


def _capped(bucket: dict, pending: dict[str, str], paths: list[str]) -> dict:
    excess = max(len(paths) - MAX_SCOPE_PATHS, 0)
    if not excess:
        return {**bucket, "pending": pending, "paths": paths}
    directory = _scan_directory(bucket.get("scan_directory"))
    cursor = bucket.get("scan_cursor")
    marks = (*pending, *scan_cache.stamps(bucket.get("scan_stamps")), *([cursor] if isinstance(cursor, str) and cursor else []))
    protected = {path_policy.anchored(path, directory) for path in marks}
    removable = paths[:max(len(_normalized_bucket(bucket)[1]), excess)]
    gone = set(islice((path for path in removable if path_policy.anchored(path, directory) not in protected), excess))
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
        keys = [*paths, *(key or "<turn>" for key, reason in pending.items() if reason is not None)]
        return _pruned(_capped(_marked(bucket, keys), current, _joined(existing, paths)))

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


def stalls(session_id: str, root: str | Path | None, key: str = STALL_KEY) -> dict[str, dict]:
    scopes = session_state.read_state_strict(session_id, root).get(KEY)
    if not isinstance(scopes, dict):
        return {}
    return {
        agent_id: dict(bucket[key]) for agent_id, bucket in scopes.items()
        if isinstance(agent_id, str) and isinstance(bucket, dict) and isinstance(bucket.get(key), dict)
    }


def note_stalls(session_id: str, root: str | Path | None, records: dict[str, dict], key: str = STALL_KEY) -> None:
    def stamped(record: dict) -> Callable[[dict], dict]:
        return lambda bucket: {**bucket, key: record}

    _update_many(session_id, root, {agent_id: stamped(record) for agent_id, record in records.items()})


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
        touched = _touched_since(bucket, revision)
        pending, existing, _revision = _normalized_bucket(bucket)
        for key in set(pending_keys) - touched:
            pending.pop(key, None)
        removed = set(paths) - touched
        findings, stamps = _stored_scan(bucket, _anchored_forms(bucket.get("scan_directory"), touched))
        return _pruned({**bucket, "pending": pending, "paths": [path for path in existing if path not in removed],
                        "scan_findings": findings, "scan_cursor": "", "scan_stamps": stamps, "scan_pause": []})

    _update(scope, mutate)


def _scan_directory(location: object) -> Path:
    return Path(location if isinstance(location, str) else ".").absolute()


def _anchored_forms(location: object, paths: set[str]) -> set[str]:
    directory = _scan_directory(location)
    return {str(path_policy.anchored(path, directory)) for path in paths}


def _stored_scan(bucket: dict, held: set[str]) -> tuple[list[dict], dict[str, str]]:
    rows = [row for row in scan_cache.rows(bucket.get("scan_findings")) if row["path"] in held]
    stamps = {path: stamp for path, stamp in scan_cache.stamps(bucket.get("scan_stamps")).items() if path in held}
    return rows, stamps


class ScanState(NamedTuple):
    pending: dict[str, str]
    paths: list[str]
    revision: int
    findings: list[dict]
    stamps: dict[str, str]
    pause: list[str]


def scan_state(scope: BlockerScope) -> ScanState:
    bucket = _bucket(session_state.read_state_strict(scope.session_id, scope.root), scope.agent_id)
    pending, paths, revision = _normalized_bucket(bucket)
    cursor = bucket.get("scan_cursor")
    at = paths.index(cursor) if cursor in paths else 0
    return ScanState(pending, paths[at:] + paths[:at], revision, scan_cache.rows(bucket.get("scan_findings")),
                     scan_cache.stamps(bucket.get("scan_stamps")), bucket["scan_pause"] if _is_string_list(bucket.get("scan_pause")) else [])


class PartialScan(NamedTuple):
    clean: list[str]
    cursor: str
    findings: list[dict]
    stamps: dict[str, str]
    pause: list[str]
    directory: str


def settle_partial(scope: BlockerScope, revision: int, partial: PartialScan) -> None:

    def change(state: dict) -> dict:
        bucket = _bucket(state, scope.agent_id)
        scopes = dict(state[KEY]) if isinstance(state.get(KEY), dict) else {}
        pending, existing, current = _normalized_bucket(bucket)
        touched = _touched_since(bucket, revision)
        gone = set(partial.clean) - touched
        for key in gone:
            pending.pop(key, None)
        findings, stamps = _merged_scan(bucket, partial, touched)
        scopes[scope.agent_id] = _pruned({
            **bucket, "pending": pending, "paths": [path for path in existing if path not in gone],
            "scan_cursor": partial.cursor, "scan_findings": scan_cache.compact(findings, _row_limit(scopes, scope.agent_id)),
            "scan_directory": partial.directory, "scan_stamps": stamps,
            "scan_pause": [path for path in partial.pause if path not in touched], "revision": current + 1,
        })
        return {**state, KEY: scopes}

    session_state.update_state_strict(scope.session_id, change, scope.root)


def _merged_scan(bucket: dict, partial: PartialScan, touched: set[str]) -> tuple[list[dict], dict[str, str]]:
    fresh = {row["path"] for row in partial.findings} | partial.stamps.keys()
    rows, stamps = _stored_scan(bucket, _anchored_forms(partial.directory, touched) - fresh)
    return [*partial.findings, *rows], {**stamps, **partial.stamps}


def _row_limit(scopes: dict, agent_id: str) -> int:
    used = sum(len(scan_cache.rows(other.get("scan_findings"))) for agent, other in scopes.items()
               if agent != agent_id and isinstance(other, dict))
    return max(0, min(scan_cache.SCOPE_LIMIT, scan_cache.SESSION_LIMIT - used))


def snapshot(session_id: str, agent_id: str, root=None) -> tuple[list[str], list[str]]:
    pending, paths, _revision = details(session_id, agent_id, root)
    return list(pending.values()), paths


class QueuedScope(NamedTuple):
    paths: list[object]
    has_pending: bool


def _queued_scope(bucket: dict) -> QueuedScope:
    paths = bucket.get("paths")
    pending, _paths, _revision = _normalized_bucket(bucket)
    return QueuedScope(paths if isinstance(paths, list) else [], bool(pending))


def queued_scopes(session_id: str, root: str | Path | None) -> dict[str, QueuedScope]:
    scopes = session_state.read_state_strict(session_id, root).get(KEY)
    if not isinstance(scopes, dict):
        return {}
    return {
        agent_id: _queued_scope(bucket) for agent_id, bucket in scopes.items()
        if isinstance(agent_id, str) and isinstance(bucket, dict)
    }
