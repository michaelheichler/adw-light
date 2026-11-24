from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Any, NamedTuple

from . import held_verdicts, imported_baseline, path_policy, review_scope, secure_fs, session_state
from .baseline import committed_text
from .imported_baseline import Snapshot
from .document_hunks import changed_hunks, hunk_chars
from .scanner import PROSE_EXTS


STATE_KEY = "claude_candidate_journal"
OVERFLOW_KEY = "claude_candidate_journal_overflow"
OVERFLOW_SENTINEL = "<journal-overflow>"
MAX_ROWS = 120
MAX_OVERFLOW_MARKERS = max(1, MAX_ROWS)
MAX_STOP_ROWS = 24
MAX_STOP_DOCUMENT_CHARS = 24_000
MAX_STOP_TOTAL_CHARS = 48_000
MAX_DOCUMENT_ROUNDS = 2
REVIEWED_KEY = "claude_candidate_journal_reviewed"
REVIEW_TURNS_KEY = "claude_candidate_journal_review_turns"
MAX_REVIEWED_DIGESTS = 4 * MAX_ROWS
MAX_CANDIDATE_CHARS = 320
MAX_PATTERN_CHARS = 1600
MAX_PATTERN_ROWS_PER_FILE = 40
MAX_FILE_BYTES = 128 * 1024
MAX_DOCUMENT_CHARS = MAX_FILE_BYTES


class _ReadOutcome(NamedTuple):
    status: str
    value: tuple[str, str] | None = None


def read_content(path: Path) -> _ReadOutcome:
    parent_fd = -1
    try:
        target = path_policy.lexical_absolute(path)
        parent_fd = secure_fs.open_parent(target)
        data = secure_fs.read_regular(parent_fd, target.name, MAX_FILE_BYTES)
        if data is None:
            return _ReadOutcome("missing")
        return _ReadOutcome("available", (hashlib.sha256(data).hexdigest(), data.decode("utf-8")))
    except FileNotFoundError:
        return _ReadOutcome("missing")
    except (OSError, ValueError):
        return _ReadOutcome("transient")
    finally:
        secure_fs.close_quietly(parent_fd)


def _canonical_path(path: str | Path) -> Path:
    try:
        return path_policy.real_path(path)
    except (OSError, RuntimeError, TypeError, ValueError):
        return Path(path)


def _row_identity(row: dict[str, Any]) -> str:
    raw = row.get("path_identity", row.get("path", ""))
    if not isinstance(raw, (str, Path)):
        return ""
    return str(_canonical_path(raw))


def _path_available(identity: str) -> bool:
    if not identity:
        return False
    return _path_status(identity) == "available"


def _path_status(identity: str) -> str:
    if not identity:
        return "missing"
    try:
        metadata = os.stat(_canonical_path(identity), follow_symlinks=False)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "transient"
    except ValueError:
        return "missing"
    return "available" if stat.S_ISREG(metadata.st_mode) else "missing"


def _kind(row: dict[str, Any]) -> str:
    role = str(row.get("role", ""))
    return f"{role}:{row.get('rule', '')}" if role == "pattern" else role


def candidate_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        _kind(row), _row_identity(row), str(row.get("content_hash", "")),
        str(row.get("line", "")), str(row.get("text", "")),
    )


def _document_row(path: Path, digest: str, text: str, turn_id: str, tool_use_id: str) -> dict[str, Any]:
    return {
        "role": "document",
        "path": str(path),
        "path_identity": str(path),
        "content_hash": digest,
        "source_context": text[:MAX_DOCUMENT_CHARS],
        "source_truncated": len(text) > MAX_DOCUMENT_CHARS,
        "turn_id": turn_id,
        "tool_use_id": tool_use_id,
    }


def _with_before(rows: list[dict[str, Any]], before: str | None) -> list[dict[str, Any]]:
    kept = before if before is not None and len(before) <= MAX_DOCUMENT_CHARS else None
    return [{**row, "before_context": kept} if row["role"] == "document" else row for row in rows]


def _candidate_rows(path: Path, digest: str, text: str, turn_id: str, tool_use_id: str) -> list[dict[str, Any]]:
    return []


def document_source_truncated(row: dict[str, Any]) -> bool:
    if row.get("role") != "document":
        return False
    if row.get("source_truncated") is True:
        return True
    if "source_truncated" in row:
        return False
    source = row.get("source_context")
    return isinstance(source, str) and len(source) >= MAX_STOP_DOCUMENT_CHARS


def _migrate_row(row: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(row)
    if document_source_truncated(row):
        migrated["source_truncated"] = True
    return migrated


def _stored_rows(state: dict) -> list:
    existing = state.get(STATE_KEY)
    return list(existing) if isinstance(existing, list) else []


def _stored_overflow(state: dict) -> dict[str, dict[str, Any]]:
    existing = state.get(OVERFLOW_KEY)
    if existing is None:
        return {}
    if not isinstance(existing, dict):
        raise ValueError("candidate journal overflow state is invalid")
    markers: dict[str, dict[str, Any]] = {}
    for path, marker in existing.items():
        if not isinstance(path, str) or not isinstance(marker, dict):
            raise ValueError("candidate journal overflow marker is invalid")
        markers[path] = dict(marker)
    return markers


def _set_overflow(state: dict, markers: dict[str, dict[str, Any]]) -> dict:
    updated = dict(state)
    if markers:
        updated[OVERFLOW_KEY] = markers
    else:
        updated.pop(OVERFLOW_KEY, None)
    return updated


def _drop_target(state: dict, target: str) -> dict:
    kept = [
        row for row in _stored_rows(state)
        if not (isinstance(row, dict) and _row_identity(row) == target)
    ]
    markers = _stored_overflow(state)
    markers.pop(target, None)
    return _set_overflow({**state, STATE_KEY: kept}, markers)


def _superseded(row: object, target: str, digest: str) -> bool:
    if not isinstance(row, dict):
        return False
    identity = _row_identity(row)
    if identity == target:
        return (
            row.get("content_hash") != digest
            or (row.get("role") == "document" and document_source_truncated(row))
        )
    return _path_status(identity) == "missing"


def _merged(rows: list, fresh: list) -> tuple[list, list]:
    added = []
    keys = {candidate_key(row) for row in rows if isinstance(row, dict)}
    for row in fresh:
        if candidate_key(row) in keys:
            continue
        rows.append(row)
        added.append(row)
        keys.add(candidate_key(row))
    return rows, added


def _trimmed(rows: list, held: set[str]) -> list:
    excess = len(rows) - MAX_ROWS
    if excess <= 0:
        return rows
    unheld = [index for index, row in enumerate(rows) if not (isinstance(row, dict) and _reviewed_key(row) in held)]
    evicted = set(unheld[:excess])
    return [row for index, row in enumerate(rows) if index not in evicted]


def _overflow_counts(rows: list) -> tuple[dict[str, int], dict[str, str], dict[str, str]]:
    all_counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    turns: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = _row_identity(row)
        if not identity:
            continue
        all_counts[identity] = all_counts.get(identity, 0) + 1
        digests.setdefault(identity, str(row.get("content_hash", "")))
        turns.setdefault(identity, str(row.get("turn_id", "")))
    return all_counts, digests, turns


def _retained_counts(rows: list) -> dict[str, int]:
    retained_counts: dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict):
            identity = _row_identity(row)
            if identity:
                retained_counts[identity] = retained_counts.get(identity, 0) + 1
    return retained_counts


def _remove_resolved_markers(markers: dict[str, dict[str, Any]], retained_counts: dict[str, int]) -> None:
    for identity, marker in tuple(markers.items()):
        if identity == OVERFLOW_SENTINEL:
            continue
        expected = marker.get("candidate_count")
        retained = retained_counts.get(identity, 0)
        if isinstance(expected, int) and not isinstance(expected, bool) and retained >= expected:
            markers.pop(identity, None)


def _add_overflow_marker(
    markers: dict[str, dict[str, Any]], identity: str, marker: dict[str, Any],
) -> None:
    if identity in markers or len(markers) < MAX_OVERFLOW_MARKERS - 1:
        markers[identity] = marker
        return
    markers[OVERFLOW_SENTINEL] = {
        "path_identity": OVERFLOW_SENTINEL,
        "content_hash": "",
        "turn_id": "",
        "candidate_count": marker["candidate_count"],
        "omitted_count": marker["omitted_count"],
    }


def _overflow_marker(
    identity: str,
    target: str,
    digest: str,
    turn_id: str,
    count: int,
    retained: int,
    digests: dict[str, str],
    turns: dict[str, str],
) -> dict[str, Any]:
    marker_digest = digest if identity == target else digests[identity]
    marker_turn = turn_id if identity == target else turns[identity]
    return {
        "path_identity": identity,
        "content_hash": marker_digest,
        "turn_id": marker_turn,
        "candidate_count": count,
        "omitted_count": count - retained,
    }


def _preserve_omitted_patterns(
    markers: dict[str, dict[str, Any]], counts: dict[str, int], pattern_counts: dict[str, int],
    target: str, digest: str, digests: dict[str, str],
) -> None:
    for identity in tuple(counts):
        marker = markers.get(identity)
        current_digest = digest if identity == target else digests.get(identity)
        if marker is None or marker.get("content_hash") != current_digest:
            continue
        counts[identity] = max(
            counts[identity] + max(0, marker.get("pattern_candidate_count", 0) - pattern_counts.get(identity, 0)),
            marker.get("candidate_count", 0),
        )
        pattern_counts[identity] = max(pattern_counts.get(identity, 0), marker.get("pattern_candidate_count", 0))


def _overflow_markers(
    state: dict,
    target: str,
    digest: str,
    turn_id: str,
    all_rows: list,
    retained_rows: list,
) -> dict[str, dict[str, Any]]:
    markers = _stored_overflow(state)
    all_counts, digests, turns = _overflow_counts(all_rows)
    pattern_counts = _retained_counts([
        row for row in all_rows if isinstance(row, dict) and row.get("role") == "pattern"
    ])
    _preserve_omitted_patterns(markers, all_counts, pattern_counts, target, digest, digests)
    markers.pop(target, None)
    retained_counts = _retained_counts(retained_rows)
    _remove_resolved_markers(markers, retained_counts)
    for identity, count in all_counts.items():
        if retained_counts.get(identity, 0) >= count:
            continue
        marker = _overflow_marker(
            identity, target, digest, turn_id, count, retained_counts.get(identity, 0), digests, turns,
        )
        if pattern_counts.get(identity, 0):
            marker["pattern_candidate_count"] = pattern_counts[identity]
        _add_overflow_marker(markers, identity, marker)
    return markers


def _pattern_limited(rows: list, held: set[str]) -> list:
    counts: dict[str, int] = {}
    retained = []
    valid = [row for row in rows if isinstance(row, dict)]
    for row in sorted(valid, key=lambda row: row.get("severity") != "block"):
        if row.get("role") == "pattern":
            identity = _row_identity(row)
            counts[identity] = counts.get(identity, 0) + 1
            if counts[identity] > MAX_PATTERN_ROWS_PER_FILE and _reviewed_key(row) not in held:
                continue
        retained.append(row)
    keys = {candidate_key(row) for row in retained}
    return [row for row in valid if candidate_key(row) in keys]


def _refresher(target: str, digest: str, turn_id: str, fresh: list, result: dict[str, list]):
    def update(state: dict) -> dict:
        held = held_verdicts.held_keys(state)
        kept = [row for row in _stored_rows(state) if not _superseded(row, target, digest)]
        merged, added = _merged(kept, fresh)
        retained = _trimmed(_pattern_limited(merged, held), held)
        retained_keys = {candidate_key(row) for row in retained}
        result["added"] = [row for row in added if row.get("role") != "pattern" or candidate_key(row) in retained_keys]
        markers = _overflow_markers(state, target, digest, turn_id, merged, retained)
        return _set_overflow({**state, STATE_KEY: retained}, markers)

    return update


def _prior_document(state: dict, target: str) -> dict[str, Any] | None:
    prior = None
    for row in _stored_rows(state):
        if isinstance(row, dict) and row.get("role") == "document" and _row_identity(row) == target:
            prior = row
    return prior


def _turn_before(prior: dict[str, Any] | None, turn_id: str, target: Path, first_seen: Snapshot | None, text: str) -> str | None:
    if prior is None or document_source_truncated(prior):
        if first_seen is not None:
            return text if first_seen.text is None else first_seen.text
        return committed_text(target)
    if prior.get("turn_id") == turn_id and "before_context" in prior:
        return prior["before_context"]
    return str(prior.get("source_context", ""))


def _before_text(session_id: str, turn_id: str, target: Path, state_root: str | Path | None, text: str) -> str | None:
    if target.suffix.lower() not in PROSE_EXTS:
        return None
    prior = _prior_document(session_state.read_state(session_id, state_root), str(target))
    first_seen = imported_baseline.lookup({"session_id": session_id, "state_root": state_root}, target)
    return _turn_before(prior, turn_id, target, first_seen, text)


def record_edit(
    session_id: str, turn_id: str, tool_use_id: str, path: str | Path, *,
    state_root: str | Path | None = None, project: str | Path | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(session_id, str) or not session_id:
        return []
    target = _canonical_path(path)
    if not review_scope.is_project_file(target, project):
        return []
    outcome = read_content(target)
    if outcome.status == "transient":
        return []
    if outcome.status == "missing" or outcome.value is None:
        session_state.update_state(session_id, lambda state: _drop_target(state, str(target)), state_root)
        return []
    digest, text = outcome.value
    before = _before_text(session_id, turn_id, target, state_root, text)
    fresh = _with_before(_candidate_rows(target, digest, text, turn_id, tool_use_id), before)
    result: dict[str, list] = {}
    session_state.update_state(session_id, _refresher(str(target), digest, turn_id, fresh, result), state_root)
    return result.get("added", [])


def current_source(path: str | Path) -> tuple[str, str] | None:
    outcome = read_content(_canonical_path(path))
    return outcome.value if outcome.status == "available" else None


def _pattern_rows(target: Path, digest: str, turn_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "role": "pattern",
        "rule": str(row["rule"]),
        "severity": str(row.get("severity", "observe")),
        "path": str(target),
        "path_identity": str(target),
        "line": int(row["line"]),
        "text": str(row["text"])[:MAX_PATTERN_CHARS],
        "text_truncated": len(str(row["text"])) > MAX_PATTERN_CHARS,
        "content_hash": digest,
        "turn_id": turn_id,
    } for row in rows]


def record_patterns(
    session_id: str, turn_id: str, path: str | Path, rows: list[dict[str, Any]], *,
    content_hash: str, state_root: str | Path | None = None, project: str | Path | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(session_id, str) or not session_id:
        return []
    target = _canonical_path(path)
    source = current_source(target) if review_scope.is_project_file(target, project) else None
    if source is None or source[0] != content_hash:
        return []
    fresh = _pattern_rows(target, content_hash, turn_id, rows)
    result: dict[str, list] = {}
    session_state.update_state(session_id, _refresher(str(target), content_hash, turn_id, fresh, result), state_root)
    return result.get("added", [])


def turn_before(session_id: str, path: str | Path, content_hash: str, state_root: str | Path | None = None) -> str | None:
    prior = _prior_document(session_state.read_state(session_id, state_root), str(_canonical_path(path)))
    if prior is None or prior.get("content_hash") != content_hash:
        return None
    before = prior.get("before_context")
    return before if isinstance(before, str) else None


def _state_rows(state: dict) -> list[dict[str, Any]]:
    rows = state.get(STATE_KEY)
    if not isinstance(rows, list):
        return []
    return [
        _migrate_row(row)
        for row in rows
        if isinstance(row, dict) and row.get("role") in {"comment", "document", "pattern"}
    ]


def read(session_id: str, *, state_root: str | Path | None = None) -> list[dict[str, Any]]:
    return _state_rows(session_state.read_state(session_id, state_root))


def _strict_state(session_id: str, state_root: str | Path | None) -> dict:
    try:
        session_state.session_directory(session_id, state_root)
    except ValueError:
        return {}
    state = session_state.read_state_strict(session_id, state_root)
    rows = state.get(STATE_KEY, [])
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        raise ValueError("current-session candidate journal rows are malformed")
    if OVERFLOW_KEY in state and type(state[OVERFLOW_KEY]) is not dict:
        raise ValueError("candidate journal overflow state is invalid")
    return state


def read_overflow(session_id: str, *, state_root: str | Path | None = None) -> list[dict[str, Any]]:
    return list(_stored_overflow(_strict_state(session_id, state_root)).values())


def _require_complete(state: dict, project: str | Path | None) -> None:
    for marker in _stored_overflow(state).values():
        identity = marker.get("path_identity")
        if identity == OVERFLOW_SENTINEL:
            raise ValueError("candidate journal overflow metadata is full; start a new session to recover omitted files")
        if not isinstance(identity, str) or not identity or not isinstance(marker.get("turn_id"), str):
            raise ValueError("current-session journal overflow state is malformed")
        if not review_scope.is_project_file(identity, project):
            continue
        count, omitted = marker.get("candidate_count"), marker.get("omitted_count")
        counts = f" ({count} candidates, {omitted} omitted)" if type(count) is int and type(omitted) is int else ""
        raise ValueError(
            f"the current-session journal was truncated for {identity[:512]}{counts}; "
            "re-edit the affected file with fewer candidates before retrying"
        )


def _complete_state(session_id: str, state_root: str | Path | None, project: str | Path | None) -> dict:
    state = _strict_state(session_id, state_root)
    _require_complete(state, project)
    return state


def read_complete(
    session_id: str, *, state_root: str | Path | None = None, project: str | Path | None = None,
) -> list[dict[str, Any]]:
    return _state_rows(_complete_state(session_id, state_root, project))


def _reviewed_key(row: dict[str, Any]) -> str:
    key = f"{row.get('path', '')}\n{row.get('content_hash', '')}"
    if row.get("role") != "pattern":
        return key
    text = hashlib.sha256(str(row.get("text", "")).encode("utf-8")).hexdigest()[:16]
    return f"pattern\n{key}\n{row.get('rule', '')}\n{row.get('line', '')}\n{text}"


def _reviewed(state: dict) -> list[str]:
    stored = state.get(REVIEWED_KEY)
    return [key for key in stored if isinstance(key, str)] if isinstance(stored, list) else []


def _stop_row(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("role") == "pattern":
        line = row.get("line")
        return {
            "role": "pattern",
            "path": str(row.get("path", ""))[:512],
            "content_hash": str(row.get("content_hash", ""))[:64],
            "rule": str(row.get("rule", ""))[:64],
            "line": line if isinstance(line, int) and not isinstance(line, bool) else 1,
            "text": str(row.get("text", ""))[:MAX_PATTERN_CHARS],
        }
    before = row.get("before_context")
    before = before if isinstance(before, str) else ""
    path = str(row.get("path", ""))
    return {
        "role": "document",
        "path": path[:512],
        "content_hash": str(row.get("content_hash", ""))[:64],
        "turn_id": str(row.get("turn_id", ""))[:64],
        "hunks": changed_hunks(path, before, str(row.get("source_context", ""))),
    }


def _row_chars(row: dict[str, Any]) -> int:
    if row.get("role") == "document":
        return hunk_chars(row["hunks"])
    return len(row.get("text") or "")


def _within_budget(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    total = 0
    for row in rows:
        total += _row_chars(row)
        if total > MAX_STOP_TOTAL_CHARS and kept:
            break
        kept.append(row)
    return kept


def _latest_documents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("role") == "document":
            identity = _row_identity(row)
            latest.pop(identity, None)
            latest[identity] = row
    return list(latest.values())


def _review_turns(state: dict) -> dict[str, str]:
    stored = state.get(REVIEW_TURNS_KEY)
    if not isinstance(stored, dict):
        return {}
    return {key: turn for key, turn in stored.items() if isinstance(key, str) and isinstance(turn, str)}


def _rounds_left(row: dict[str, Any], reviewed: set[str], turns: dict[str, str]) -> bool:
    key = _reviewed_key(row)
    prefix = f"{row.get('path', '')}\n"
    turn = str(row.get("turn_id", ""))
    rounds = sum(1 for seen in reviewed if seen.startswith(prefix) and turns.get(seen) == turn)
    return key in reviewed or rounds < MAX_DOCUMENT_ROUNDS


def read_stop(
    session_id: str, *, turn_id: str | None = None, state_root: str | Path | None = None,
    project: str | Path | None = None,
) -> list[dict[str, Any]]:
    state = _complete_state(session_id, state_root, project)
    reviewed = set(_reviewed(state))
    turns = _review_turns(state)
    current = turn_id if turn_id is not None else str(state.get("turn_id") or "")
    rows = _project_rows(_state_rows(state), project)

    def due(row: dict[str, Any]) -> bool:
        return (bool(current) and row.get("turn_id") == current) or _reviewed_key(row) not in reviewed

    patterns = [_stop_row(row) for row in rows if row.get("role") == "pattern" and _reviewed_key(row) not in reviewed]
    documents = [
        _stop_row(row) for row in _latest_documents(rows) if due(row) and _rounds_left(row, reviewed, turns)
    ]
    changed = [row for row in documents if _adds_text(row)]
    idle = [row for row in documents if not _adds_text(row)]
    if idle:
        mark_reviewed(session_id, idle, state_root=state_root)
    return _within_budget(patterns + changed[:MAX_STOP_ROWS])


def _project_rows(rows: list[dict[str, Any]], project: str | Path | None) -> list[dict[str, Any]]:
    return [row for row in rows if review_scope.is_project_file(str(row.get("path", "")), project)]


def _adds_text(row: dict[str, Any]) -> bool:
    return any(hunk["changed"] for hunk in row["hunks"])


def uphold(session_id: str, entries: list[tuple[list[str], str]], *, state_root: str | Path | None = None) -> None:
    if entries:
        session_state.update_state(session_id, lambda state: held_verdicts.with_upheld(state, entries, _live_keys(state)), state_root)


def upheld(session_id: str, *, state_root: str | Path | None = None) -> list[str]:
    state = session_state.read_state(session_id, state_root)
    unchanged, unverifiable = _held_liveness(state)
    texts = held_verdicts.upheld_texts(state, unchanged | unverifiable, MAX_ROWS)
    return [*texts, held_verdicts.UPHELD_UNVERIFIABLE] if unverifiable and texts else texts


def _held_liveness(state: dict) -> tuple[set[str], set[str]]:
    held = held_verdicts.held_keys(state)
    rows = [row for row in _stored_rows(state) if isinstance(row, dict) and _reviewed_key(row) in held]
    prints = {identity: held_verdicts.fingerprint(identity, MAX_FILE_BYTES) for identity in {_row_identity(row) for row in rows}}
    unchanged = {_reviewed_key(row) for row in rows if prints[_row_identity(row)] == ("read", row.get("content_hash"))}
    unverifiable = {_reviewed_key(row) for row in rows if prints[_row_identity(row)] == held_verdicts.UNVERIFIABLE}
    return unchanged, unverifiable


def _live_keys(state: dict) -> set[str]:
    return {_reviewed_key(row) for row in _stored_rows(state) if isinstance(row, dict)}


def mark_reviewed(session_id: str, rows: list[dict[str, Any]], *, state_root: str | Path | None = None) -> None:
    keys = list(dict.fromkeys(_reviewed_key(row) for row in rows))
    fresh = {_reviewed_key(row): str(row.get("turn_id", "")) for row in rows if row.get("role") == "document"}

    def update(state: dict) -> dict:
        merged = ([key for key in _reviewed(state) if key not in keys] + keys)[-MAX_REVIEWED_DIGESTS:]
        turns = {**_review_turns(state), **fresh}
        return {**state, REVIEWED_KEY: merged, REVIEW_TURNS_KEY: {key: turns[key] for key in merged if key in turns}}

    session_state.update_state(session_id, update, state_root)


read_for_stop = read_stop
