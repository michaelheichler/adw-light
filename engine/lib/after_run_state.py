from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import reduce

try:
    from . import session_state
except ImportError:
    import session_state

STATE = "after_run"


@dataclass(slots=True)
class Phase:
    session_id: str
    root: object
    state: dict
    updates: list[Callable[[dict], dict]] = field(default_factory=list)


_ACTIVE: list[Phase] = []


def _active(session_id: str, root: object) -> Phase | None:
    return next(
        (
            phase
            for phase in reversed(_ACTIVE)
            if phase.session_id == session_id and phase.root == root
        ),
        None,
    )


def read(session_id: str, root: object) -> dict:
    phase = _active(session_id, root)
    return phase.state if phase is not None else session_state.read_state(session_id, root)


def stored(session_id: str, root: object) -> dict:
    return read(session_id, root).get(STATE, {})


def save(session_id: str, root: object, update: Callable[[dict], dict]) -> None:
    def mutate(state: dict) -> dict:
        return {**state, STATE: update(state.get(STATE, {}))}

    phase = _active(session_id, root)
    if phase is None:
        session_state.update_state_strict(session_id, mutate, root)
    else:
        phase.state = mutate(phase.state)
        phase.updates.append(mutate)


@contextmanager
def batched(session_id: str, root: object, failed: Callable[[Exception], None]) -> Iterator[None]:
    if _active(session_id, root) is not None:
        yield
        return
    phase = Phase(session_id, root, session_state.read_state(session_id, root))
    _ACTIVE.append(phase)
    try:
        yield
    finally:
        _ACTIVE.remove(phase)
        _flush(phase, failed)


def _flush(phase: Phase, failed: Callable[[Exception], None]) -> None:
    if not phase.updates:
        return
    try:
        session_state.update_state_strict(
            phase.session_id,
            lambda state: reduce(lambda data, step: step(data), phase.updates, state),
            phase.root,
        )
    except (OSError, ValueError, RuntimeError) as error:
        failed(error)
