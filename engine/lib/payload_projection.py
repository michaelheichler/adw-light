from __future__ import annotations

import json
import re


STRING = r'"(?:[^"\\\x00-\x1f]++|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*+"'
TOKEN = re.compile(STRING + r'|[{}\[\],:]')


def without_response_strings(raw: str) -> str:
    if '"tool_response"' not in raw:
        return raw
    depth = 0
    response_depth = None
    batch = False
    key = ""
    pieces = []
    start = 0
    for token in TOKEN.finditer(raw):
        lead = raw[token.start()]
        if lead == '"':
            if response_depth is not None:
                pieces.extend((raw[start:token.start()], '""'))
                start = token.end()
            key = token.group() if token.end() - token.start() <= 15 else ""
        elif lead == ":" and key == '"tool_response"' and (depth == 1 or batch and depth == 3):
            response_depth = depth
        elif lead == ":" and depth == 1:
            batch = key == '"tool_calls"'
        elif lead in "[{":
            depth += 1
        elif lead in "]},":
            depth -= int(lead != ",")
            if response_depth is not None and depth <= response_depth:
                response_depth = None
            key = False
    pieces.append(raw[start:])
    return "".join(pieces)


def _prefix_fields(raw: str, start: int, fields: dict, nested: bool) -> None:
    decoder = json.JSONDecoder()
    pos = start
    keys = {"file_path"} if nested else {"session_id", "agent_id", "cwd", "tool_name", "hook_event_name"}
    while pos < len(raw):
        match = re.compile(r'\s*"([^"\\]+)"\s*:\s*').match(raw, pos)
        if not match:
            return
        key, pos = match.group(1), match.end()
        if not nested and key == "tool_input" and raw[pos:pos + 1] == "{":
            fields[key] = {}
            _prefix_fields(raw, pos + 1, fields[key], True)
        value, pos = decoder.raw_decode(raw, pos)
        if key in keys and isinstance(value, str):
            fields[key] = value
        comma = re.compile(r"\s*,").match(raw, pos)
        if not comma:
            return
        pos = comma.end()


def overflow_fields(prefix: str) -> dict:
    fields = {}
    raw = prefix.lstrip()
    if raw.startswith("{"):
        try:
            _prefix_fields(raw, 1, fields, False)
        except (ValueError, RecursionError):
            pass
    return fields
