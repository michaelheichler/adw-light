from __future__ import annotations

import math
import time
from contextlib import AbstractContextManager

try:
    from . import after_commit, after_run_state, reporting
    from .after_run import (
        COMMIT_WORK,
        Hook,
        _check_snapshots,
        _commit_work,
        _hook,
        _ledger,
        _publish,
        _recheck_pending,
        _render,
        _save,
        _snapshot_rule,
        _stored,
    )
    from .after_run_snapshot import drop_missing_roots, missing_roots, turn_snapshots
    from .hookio import join_notices
except ImportError:
    import after_commit
    import after_run_state
    import reporting
    from after_run import (
        COMMIT_WORK,
        Hook,
        _check_snapshots,
        _commit_work,
        _hook,
        _ledger,
        _publish,
        _recheck_pending,
        _render,
        _save,
        _snapshot_rule,
        _stored,
    )
    from after_run_snapshot import drop_missing_roots, missing_roots, turn_snapshots
    from hookio import join_notices

AFTER_RUN_SHARE = 2 / 3


def _share(until: float | None) -> float | None:
    if until is None or math.isinf(until):
        return until
    now = time.monotonic()
    return now + max(0.0, until - now) * AFTER_RUN_SHARE


def _phase(hook: Hook) -> AbstractContextManager[None]:
    return after_run_state.batched(
        hook.scope.session_id, hook.scope.root, lambda error: hook.timing(_snapshot_rule(error))
    )


def _drop_missing(hook: Hook, data: dict, actors: set[str]) -> dict:
    if not missing_roots(data, actors, hook.scope.agent_id):
        return data
    _save(hook, lambda state: drop_missing_roots(state, actors, hook.scope.agent_id))
    return _stored(hook)


def _stop_work(hook: Hook, data: dict) -> tuple[dict, str]:
    agent = hook.scope.agent_id
    entries = data.get("pending", {}).get(agent, {})
    commits = after_commit.compare(hook, {}, entries, _commit_work(data, agent), stop=True)
    pending = {key: value for key, value in (entries | commits.changes).items() if value}

    def store(state: dict) -> dict:
        work = dict(state.get(COMMIT_WORK, {}))
        work.pop(agent, None)
        everyone = dict(state.get("pending", {}))
        if pending:
            everyone[agent] = pending
        else:
            everyone.pop(agent, None)
        return {**state, "pending": everyone, COMMIT_WORK: work}

    _save(hook, store)
    _publish(hook, entries, pending, agent)
    observed = _ledger(hook, commits.found) if commits.found else ""
    return pending, join_notices(commits.notice, observed)


def _stop_scope(hook: Hook, data: dict, config: dict | None) -> tuple[dict, str]:
    agent = hook.scope.agent_id
    snapshots = turn_snapshots(data, agent)
    if snapshots:
        return _check_snapshots(hook, snapshots, config, stop=True)
    if _commit_work(data, agent):
        try:
            return _stop_work(hook, data)
        except (OSError, ValueError, RuntimeError) as error:
            hook.timing(_snapshot_rule(error))
    entries = data.get("pending", {}).get(agent, {})
    return _recheck_pending(hook, entries), ""


def _stop_hooks(hook: Hook, data: dict) -> list[Hook]:
    if hook.scope.agent_id:
        return [hook]
    actors = data.get("turns", {}).keys() | data.get("pending", {}).keys()
    actors |= data.get(COMMIT_WORK, {}).keys()
    scoped_hooks = (
        Hook({**hook.payload, "agent_id": agent}, hook.cfg, time.monotonic(), hook.event,
             hook.until)
        for agent in sorted(actors - {""})
    )
    return [hook, *scoped_hooks]


def stop(
    payload: dict, cfg: dict, until: float | None = None, *, config: dict | None = None,
) -> tuple[str, str]:
    hook = _hook(payload, cfg, "Stop", _share(until))
    with _phase(hook):
        data = _stored(hook)
        if not data:
            return "", ""
        hooks = _stop_hooks(hook, data)
        data = _drop_missing(hook, data, {scoped.scope.agent_id for scoped in hooks})
        paths: dict[str, list[dict]] = {}
        notices, entries = [], []
        for scoped in hooks:
            scoped_data = data if scoped == hook else _stored(scoped)
            pending, notice = _stop_scope(scoped, scoped_data, config)
            for path, entry in pending.items():
                paths.setdefault(path, []).extend(entry["rows"])
            entries.extend(pending.values())
            notices.append(notice)
        reason = _render_stop(hook, paths, after_commit.actions(entries))
        return reason, join_notices(*dict.fromkeys(notices))


def _render_stop(hook: Hook, paths: dict[str, list[dict]], action: str = "") -> str:
    shown = [rows[0] for rows in paths.values()]
    rows = [row for findings in paths.values() for row in findings]
    if not rows:
        return ""
    if len(shown) == len(rows):
        return join_notices(_render(hook, shown), action)
    cfg = {**hook.cfg, "session_id": hook.scope.session_id}
    report_path = reporting.write_full_report(rows, cfg)
    reason = reporting.compact_block(shown, cfg, report_path=report_path)[0]
    return join_notices(reason, action)


def subagent_stop(
    payload: dict, cfg: dict, until: float | None = None, *, config: dict | None = None,
) -> tuple[str, str]:
    hook = _hook(payload, cfg, "SubagentStop", _share(until))
    with _phase(hook):
        data = _drop_missing(hook, _stored(hook), {hook.scope.agent_id})
        pending, notice = _stop_scope(hook, data, config)
    action = after_commit.actions(pending.values())
    paths = {path: entry["rows"] for path, entry in pending.items()}
    return _render_stop(hook, paths, action), notice
