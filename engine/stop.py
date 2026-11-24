from __future__ import annotations

from typing import Callable, NamedTuple

from lib import host, payloads, session_state, stop_scan, turn_retry
from lib.config import payload_hook_config
from lib.end_turn import foreign_outcome, stop_outcome
from lib.hookio import (
    PARSE_FAILURE,
    STATE_FAILURE,
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


def _verdict(payload: dict, cfg: dict, turn_id: str = "") -> dict:
    until = stop_scan.deadline()
    outcome = stop_outcome(payload, cfg, hook="stop", turn_id=turn_id, until=until)
    recovered = session_state.take_recovery_notice(payloads.session_id(payload), cfg.get("state_root"))
    own = "\n".join(part for part in (recovered, outcome.notice) if part)
    foreign = foreign_outcome(payload, cfg, turn_id, until=until)
    reason = "\n".join(part for part in (outcome.reason, foreign.reason) if part)
    if reason:
        return {**stop_block(reason), **(system_message(own) if own else {})}
    if host.is_codex_host():
        return {}
    notice = "\n".join(part for part in (own, foreign.notice) if part)
    return system_message(notice) if notice else {}


def _closed_turn_id(session_id: str, cfg: dict, retry: bool) -> str:
    if retry:
        return ""
    value = session_state.read_state(session_id, cfg.get("state_root")).get("turn_id")
    session_state.advance_turn(session_id, cfg.get("state_root"))
    return value if isinstance(value, str) else ""


def _gate(turn: _StopTurn) -> Callable[[str], dict]:
    def gate(turn_id: str) -> dict:
        return _verdict(turn.payload, turn.cfg, turn_id)

    return gate


def _run(payload: dict, config: dict | None, provider: object | None) -> dict:
    cfg = payload_hook_config(config, payload)
    session_id = payloads.session_id(payload)
    retry_turn_id = turn_retry.retry_turn_id(session_id, cfg.get("state_root"))
    state_turn_id = _closed_turn_id(session_id, cfg, payloads.stop_hook_active(payload))
    known = payloads.turn_id(payload) or retry_turn_id or state_turn_id
    return run_with_ledger(
        hook="stop",
        payload=payload,
        gate=_gate(_StopTurn(payload, cfg)),
        ledger_root=cfg.get("ledger_root"),
        state_root=cfg.get("state_root"),
    )


def run(
    payload: dict,
    config: dict | None = None,
    *,
    provider: object | None = None,
) -> dict:
    try:
        if payload is PARSE_FAILURE or not payloads.session_id(payload):
            return stop_block(STATE_FAILURE + "invalid Stop payload")
        return _run(payload, config, provider)
    except Exception as exc:
        return stop_block(STATE_FAILURE + str(exc))


if __name__ == "__main__":
    write_payload(run(read_payload()))
