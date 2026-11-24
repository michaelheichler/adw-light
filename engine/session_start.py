from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from lib import adw_copies, first_run, retention, session_state, wording_debt
from lib.hookio import CONTRACT, CONTRACT_REMINDER, context, read_payload, write_payload

SESSION_START_EVENT = "SessionStart"
REMINDER_SOURCES = frozenset({"resume", "clear", "compact"})


def run(payload: dict | None = None, config: dict | None = None, *, drift: Callable[[], str] | None = None) -> dict:
    fields = payload if isinstance(payload, dict) else {}
    session_id = fields.get("session_id")
    settings = config if isinstance(config, dict) else {}
    if isinstance(session_id, str) and session_id:
        state_root = settings.get("state_root") if isinstance(settings.get("state_root"), str) else None
        ledger_root = settings.get("ledger_root") if isinstance(settings.get("ledger_root"), str) else None
        session_state.acquire_session_lease(session_id, state_root)
        retention.sweep(state_root=state_root, ledger_root=ledger_root)
        _count_wording_debt(session_id, fields.get("cwd"), state_root)
        _start_setup(fields.get("cwd"))
    message = CONTRACT_REMINDER if fields.get("source") in REMINDER_SOURCES else CONTRACT
    notice = (drift or _drift_notice)()
    return {**context(message, SESSION_START_EVENT), **({"systemMessage": notice} if notice else {})}


def _count_wording_debt(session_id: str, cwd: object, state_root: str | None) -> None:
    if not isinstance(cwd, str) or not cwd:
        return
    try:
        wording_debt.note_session_start(session_id, cwd, state_root)
    except (OSError, ValueError, RecursionError, MemoryError):
        return


def _start_setup(cwd: object) -> None:
    project = Path(cwd) if isinstance(cwd, str) and cwd and Path(cwd).is_dir() else None
    try:
        first_run.start(project)
    except (OSError, ValueError):
        return


def _drift_notice() -> str:
    try:
        return adw_copies.notice(adw_copies.stale())
    except (OSError, RuntimeError, ValueError):
        return ""


if __name__ == "__main__":
    write_payload(run(read_payload()))
