from __future__ import annotations

import json
from hashlib import sha256

SCOPE_LIMIT = 96
SESSION_LIMIT = 768
FIELDS = frozenset({"path", "rule", "line", "content_hash", "inherited", "action"})


def rows(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [row for row in value[:SCOPE_LIMIT] if isinstance(row, dict) and not row.keys() - FIELDS
            and all(isinstance(row.get(key), str) and row[key] for key in ("path", "rule", "content_hash"))
            and type(row.get("line")) is int and isinstance(row.get("action", ""), str)
            and type(row.get("inherited", False)) is bool]



def stamps(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, str) and len(item) == 65}


def identities(findings: list[dict]) -> dict[str, str]:
    grouped: dict[str, list[tuple]] = {}
    for row in findings:
        grouped.setdefault(row["path"], []).append((row["rule"], row["line"], row["content_hash"], bool(row.get("inherited"))))
    return {path: str(int(any(not item[-1] for item in items))) + sha256(json.dumps(sorted(items)).encode()).hexdigest()
            for path, items in grouped.items()}


def compact(findings: list[dict], limit: int) -> list[dict]:
    return [{key: (value[:256] if key == "action" else value) for key, value in row.items() if key in FIELDS}
            for row in findings[:limit]]
