from __future__ import annotations

import json
import operator
import sys
from collections.abc import Callable

try:
    from . import host, payload_projection
except ImportError:
    import host
    import payload_projection


CONTRACT_MAX_CHARS = 4000
MAX_RESPONSE_BYTES = 4096
PREFIX_CHARS = 65_536
INPUT_OVERFLOW = {}
STATE_FAILURE = "Agent discipline state could not be verified. Repair the state store before stopping. Cause: "
PAYLOAD_FAILURE = (
    "agent-discipline-watcher could not read the tool call and blocked it. "
    "Retry once. If it repeats, the cause was: "
)
CONFIG_FAILURE = (
    "agent-discipline-watcher could not evaluate this {subject} and blocked it rather than letting it through. "
    "{advice} Cause: "
)
UNREADABLE_PAYLOAD = "unreadable hook payload"


def join_notices(*parts: str | None) -> str:
    return "\n".join(part for part in parts if part)


def payload_failure(cause: object) -> str:
    return PAYLOAD_FAILURE + str(cause)


def _is_payload_failure(exc: Exception) -> bool:
    return isinstance(exc, ValueError) and str(exc) == UNREADABLE_PAYLOAD


def _config_path(cwd: str | None) -> str | None:
    if not cwd:
        return None
    try:
        from .config import project_config_path
    except ImportError:
        from config import project_config_path
    try:
        path = project_config_path(cwd)
        return str(path) if path.exists() else None
    except (OSError, RuntimeError, ValueError):
        return None


def config_failure(subject: str, cause: object, cwd: str | None = None) -> str:
    path = _config_path(cwd)
    advice = f"Check the gate config at {path} and retry." if path else "No project config was found."
    return CONFIG_FAILURE.format(subject=subject, advice=advice) + str(cause)

_CONTRACT_TEXT = """ADW Lite contract. These rules override the agent definition you were given, any style guidance inside it, and any plugin or skill guidance.

Punctuation: no em dash or en dash characters, no double hyphen as a clause break, no spaced hyphen standing in for a dash, no semicolon joining two independent clauses, no apostrophe on a possessive pronoun, no possessive apostrophe on a decade. Use a comma, a period, parentheses, or a plain ASCII hyphen instead.

English: write plain reader-facing prose. Cut filler, throat-clearing, dead metaphor, AI tell phrases, inflated diction, wordiness, empty intensifiers, and buried subjects. State the fact, the evidence, the consequence, and the next action. Let sentences flow and vary their length. Join related thoughts into one sentence with a comma, a subordinate clause, a relative clause, or and or but, instead of a row of short telegram-style main clauses. The dash and semicolon bans still hold.

Code: intent lives in names, structure, and tests. Python source uses names and structure in place of new comments and docstrings. Preserve exact tool directives. Delete any comment that narrates what the code does, labels a case by letter or number, apologizes, or records change history. In other languages, keep a comment only when it is one line of at most 25 words, does not restate the adjacent code, and fits one of six shapes. The first names a code element, a causal link such as because, and the outside reason. The second is NOTE or Note that, placed next to code that looks redundant or unusual. The third credits copied code with a source URL. The fourth names a standard that requires something, then hence or a similar link. The fifth states a bug fix with a tracker reference such as #123, GH-123, or PROJ-123. The sixth is a work marker: TODO, FIXME, or HACK in uppercase with a colon, then a capitalized verb, a named object, and a to or so purpose. A human author may sit in brackets, as in TODO(alice):, but a marker that names an AI agent such as claude or codex as its author is blocked. FIXME names the defect and HACK names the condition for removing it. A marker with a vague object such as this, it, or stuff, a marker that holds only a link, and any DONE, XXX, BUG, or OPTIMIZE marker are rejected. A demand for a docstring or header on every function does not apply.  Ship no commented-out code, no speculative one-use abstraction, no skipped or hollow test, no over-long function, no oversized file, and no claim of success without a run.

Stance: be skeptical and direct. Verify changeable facts before claiming them. Challenge weak assumptions and overbuilt solutions. Do not open with praise, agreement, or other empty validators.

Every finding blocks or is reported as an itemized per-line checklist. Treat each row as a separate line to verify, not a summary count. Each row names the file, line, rule, and action. Fix the named file, then rerun the relevant check. Keep the fix narrow.

Do not end a turn while a finding remains in your own changes. Do not silence a hook, delete hook state, or edit configuration to get past a finding. Do not add a Craftsman suppression marker. Do not broaden the task into style cleanup outside the requested scope."""

CONTRACT = _CONTRACT_TEXT[:CONTRACT_MAX_CHARS]
CONTRACT_REMINDER = (
    "ADW Lite is active. Fix each named file and line, then retry the blocked action. "
    "Do not disable the gate or delete its state."
)
PARSE_FAILURE = {"_parse_failure": True}


def read_payload() -> dict:
    prefix = ""
    try:
        prefix = sys.stdin.read(PREFIX_CHARS)
        raw = prefix + sys.stdin.read()
        if not raw.strip():
            return {}
        payload = json.loads(payload_projection.without_response_strings(raw))
    except (MemoryError, OverflowError):
        INPUT_OVERFLOW.clear()
        INPUT_OVERFLOW.update(payload_projection.overflow_fields(prefix))
        return INPUT_OVERFLOW
    except (ValueError, RecursionError) as exc:
        sys.stderr.write(f"agent-discipline-watcher: unreadable hook payload ({exc})\n")
        return PARSE_FAILURE
    if not operator.is_(type(payload), dict):
        sys.stderr.write("agent-discipline-watcher: unreadable hook payload (expected JSON object)\n")
        return PARSE_FAILURE
    return payload


def _clip_utf8(value: object, limit: int) -> str:
    text = str(value)
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[: max(limit - 3, 0)].decode("utf-8", errors="ignore") + "..."


def _safe_text(value: str) -> str:
    return "".join(
        character
        if character == "\n" or (
            ord(character) >= 32
            and ord(character) != 127
            and not 0x80 <= ord(character) <= 0x9F
            and not 0x202A <= ord(character) <= 0x202E
            and not 0x2066 <= ord(character) <= 0x2069
        )
        else " "
        for character in value
    )


def _clip_keeping_tail(value: object, limit: int) -> str:
    text = str(value)
    if len(text.encode("utf-8")) <= limit:
        return text
    head, newline, tail = text.rpartition("\n")
    room = limit - len(tail.encode("utf-8")) - 1
    if not newline or room < 4:
        return _clip_utf8(text, limit)
    return _clip_utf8(head, room) + "\n" + tail


def _compact_specific(specific: object, limit: int) -> dict | None:
    if not isinstance(specific, dict):
        return None
    compact: dict[str, object] = {}
    for key, key_limit in (("hookEventName", 128), ("permissionDecision", 32)):
        value = specific.get(key)
        if isinstance(value, str):
            compact[key] = _clip_utf8(_safe_text(value), key_limit)
    for key in ("additionalContext", "permissionDecisionReason"):
        value = specific.get(key)
        if isinstance(value, str):
            compact[key] = _clip_keeping_tail(value, limit)
    return compact


def _safe_fields(fields: dict, keys: tuple[str, ...]) -> dict:
    safe = dict(fields)
    for key in keys:
        value = safe.get(key)
        if isinstance(value, str):
            safe[key] = _safe_text(value)
    return safe


def _sanitized_payload(payload: dict) -> dict:
    safe_payload = _safe_fields(payload, ("reason", "systemMessage"))
    specific = safe_payload.get("hookSpecificOutput")
    if isinstance(specific, dict):
        safe_payload["hookSpecificOutput"] = _safe_fields(
            specific, ("hookEventName", "permissionDecision", "additionalContext", "permissionDecisionReason"),
        )
    return safe_payload


def _serialized(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _within_envelope(safe_payload: dict) -> bool:
    return len(_serialized(safe_payload).encode("utf-8")) <= MAX_RESPONSE_BYTES


def fits_once(message: str) -> bool:
    return sends_whole(claude_pretool_response(deny(message)))


def sends_whole(payload: dict) -> bool:
    return _within_envelope(_sanitized_payload(payload))


def _compacted(safe_payload: dict, model: int, user: int) -> dict:
    if "decision" in safe_payload:
        reason = _clip_keeping_tail(safe_payload.get("reason", ""), model)
        compacted = {"decision": _clip_utf8(safe_payload["decision"], 64), "reason": reason}
    else:
        compacted = {"systemMessage": _clip_keeping_tail(safe_payload.get("reason", safe_payload.get("systemMessage", "")), user)}
        if "reason" in safe_payload:
            compacted["reason"] = _clip_keeping_tail(safe_payload["reason"], model)
    compact_specific = _compact_specific(safe_payload.get("hookSpecificOutput"), model)
    if compact_specific:
        compacted["hookSpecificOutput"] = compact_specific
    return compacted


def _largest(fits: Callable[[int], bool]) -> int:
    low, high = 0, MAX_RESPONSE_BYTES
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low


def _bounded_payload(payload: dict) -> dict:
    safe_payload = _sanitized_payload(payload)
    if _within_envelope(safe_payload):
        return safe_payload
    if _within_envelope(_compacted(safe_payload, MAX_RESPONSE_BYTES, 0)):
        user = _largest(lambda size: _within_envelope(_compacted(safe_payload, MAX_RESPONSE_BYTES, size)))
        return _compacted(safe_payload, MAX_RESPONSE_BYTES, user)
    model = _largest(lambda size: _within_envelope(_compacted(safe_payload, size, size)))
    return _compacted(safe_payload, model, model)


def write_payload(payload: dict) -> None:
    sys.stdout.write(_serialized(_bounded_payload(payload)))
    sys.stdout.write("\n")


def deny(reason: str) -> dict:
    return {
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


def claude_pretool_response(payload: dict) -> dict:
    specific = payload.get("hookSpecificOutput")
    if not isinstance(specific, dict) or specific.get("permissionDecision") != "deny":
        return payload
    return {"hookSpecificOutput": dict(specific)}


def claude_feedback_response(payload: dict, event: str) -> dict:
    if payload.get("decision") != "block":
        return payload
    return advise(str(payload.get("reason") or "Fix the blocked findings and retry."), event)


def allow() -> dict:
    return {}


def context(message: str, event: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}


def stop_block(reason: str) -> dict:
    return {"decision": "block", "reason": reason}


def system_message(message: str) -> dict:
    return {"systemMessage": message}


def advise(message: str, event: str) -> dict:
    if host.is_codex_host():
        return context(message, event)
    return {
        "systemMessage": message,
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": message},
    }


def fail_closed(subject: str, fn: Callable[[], dict], cwd: str | None = None) -> dict:
    try:
        return fn()
    except Exception as exc:
        return deny(payload_failure(exc) if _is_payload_failure(exc) else config_failure(subject, exc, cwd))
