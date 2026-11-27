from __future__ import annotations

from typing import Callable, NamedTuple

from lib import host, payloads, session_state, stop_scan, turn_retry
from lib.config import payload_hook_config
from lib.end_turn import turn_outcome
from lib.hookio import (
    PARSE_FAILURE,
    STATE_FAILURE,
    join_notices,
    read_payload,
    stop_block,
    system_message,
    write_payload,
)
from lib.reporting import run_with_ledger

STOP_EVENT = "Stop"


class _StopTurn(NamedTuple):
    payload: dict
    cfg: dict
    until: float
    config: dict | None


def _verdict(turn: _StopTurn, turn_id: str) -> dict:
    from lib import after_run
    payload, cfg, until, config = turn
    after_run_reason, after_run_notice = after_run.stop(payload, cfg, until, config=config)
    scan_paths = stop_scan.ScanPaths.from_cwd(payloads.cwd(payload))
    outcome, foreign = turn_outcome(payload, cfg, turn_id, until=until, scan_paths=scan_paths)
    recovered = session_state.take_recovery_notice(payloads.session_id(payload), cfg.get("state_root"))
    own = join_notices(recovered, outcome.notice, after_run_notice)
    reason = join_notices(outcome.reason, foreign.reason, after_run_reason)
    if reason:
        return {**stop_block(reason), **(system_message(own) if own else {})}
    if host.is_codex_host():
        return {}
    notice = join_notices(own, foreign.notice)
    return system_message(notice) if notice else {}


def _closed_turn_id(session_id: str, cfg: dict, retry: bool) -> str:
    if retry:
        return ""
    value = session_state.read_state(session_id, cfg.get("state_root")).get("turn_id")
    session_state.advance_turn(session_id, cfg.get("state_root"))
    return value if isinstance(value, str) else ""


def _gate(turn: _StopTurn) -> Callable[[str], dict]:
    def gate(turn_id: str) -> dict:
        return _verdict(turn, turn_id)

    return gate


def _run(payload: dict, config: dict | None, until: float) -> dict:
    cfg = payload_hook_config(config, payload)
    session_id = payloads.session_id(payload)
    retry_turn_id = turn_retry.retry_turn_id(session_id, cfg.get("state_root"))
    state_turn_id = _closed_turn_id(session_id, cfg, payloads.stop_hook_active(payload))
    known = payloads.turn_id(payload) or retry_turn_id or state_turn_id
    return run_with_ledger(
        hook="stop",
        payload=payload,
        gate=_gate(_StopTurn(payload, cfg, until, config)),
        ledger_root=cfg.get("ledger_root"),
        state_root=cfg.get("state_root"),
    )


def run(
    payload: dict,
    config: dict | None = None,
    *,
    provider: object | None = None,
    until: float | None = None,
) -> dict:
    try:
        if payload is PARSE_FAILURE or not payloads.session_id(payload):
            return stop_block(STATE_FAILURE + "invalid Stop payload")
        return _run(payload, config, stop_scan.deadline() if until is None else until)
    except Exception as exc:
        return stop_block(STATE_FAILURE + str(exc))


if __name__ == "__main__":
    write_payload(run(read_payload()))
