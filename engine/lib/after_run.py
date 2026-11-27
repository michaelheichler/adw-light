from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

try:
    from . import after_commit, after_run_config, agent_types, blocker_state, host, ignored_configs, nested_repos
    from . import payloads, reporting, session_state, shell_snapshot, tests_policy
    from .commit_test_changes import known_target
    from .config import effective_hook_config, payload_hook_config, resolve_outcome
    from .hookio import advise, join_notices, stop_block
    from .after_run_snapshot import (
        Prefetched, advance_snapshot, capture_roots, close_turn_roots, compare_snapshot, content_rows,
        direct_comparison, extend_snapshots, legacy_clean_path, nested_locations, new_commit_snapshots, prefetch, project_locations,
        prune_sources, record_call, root_snapshot, shared_worktree_heads, snapshots, turn_snapshots,
    )
except ImportError:
    import after_commit, after_run_config, agent_types, blocker_state, host, ignored_configs, nested_repos
    import payloads, reporting, session_state, shell_snapshot, tests_policy
    from commit_test_changes import known_target
    from config import effective_hook_config, payload_hook_config, resolve_outcome
    from hookio import advise, join_notices, stop_block
    from after_run_snapshot import (
        Prefetched, advance_snapshot, capture_roots, close_turn_roots, compare_snapshot, content_rows,
        direct_comparison, extend_snapshots, legacy_clean_path, nested_locations, new_commit_snapshots, prefetch, project_locations,
        prune_sources, record_call, root_snapshot, shared_worktree_heads, snapshots, turn_snapshots,
    )

STATE = "after_run"
BLOCKER = blocker_state.AFTER_RUN_KEY
LIVE_BLOCK = "_after_run_block"
MAX_CALLS = 100
DIRECT_WRITERS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"})
OVERFLOW_NOTE = "ADW Lite judged every changed test path but skipped the prose and comment scan for {count} changed {noun}."
DEFERRED_NOTE = ("ADW Lite ran short of time, judged every changed test path by its name "
                 "and moved the prose and comment scan of {count} changed {noun} to the Stop check.")
DEADLINE_SECONDS = 6.0
UNEXPLAINED_RULE = "unexplained_change"
DEFERRED_RULE = "after_run_deferred"
COMMIT_WORK = "commit_work"


@dataclass(frozen=True, slots=True)
class Hook:
    payload: dict
    cfg: dict
    started: float
    event: str
    until: float = math.inf

    @property
    def scope(self) -> blocker_state.BlockerScope:
        return blocker_state.BlockerScope(
            payloads.session_id(self.payload), payloads.agent_id(self.payload), self.cfg.get("state_root"),
        )

    @property
    def call_id(self) -> str:
        return payloads.tool_use_id(self.payload)

    def timing(self, rule: str = "", path: str = "", **counts: int) -> None:
        fields = {
            "session_id": self.scope.session_id, "hook": "after_run", "event": self.event, "family": "after_run",
            "rule": rule, "path": path, "tool_use_id": self.call_id, "turn_id": payloads.turn_id(self.payload),
            "outcome": "release", "duration_ms": int((time.monotonic() - self.started) * 1000),
        }
        if counts:
            reporting.append_row({"ts": reporting.now_iso(), **fields, **counts}, self.cfg.get("ledger_root"))
            return
        reporting.record_decision(**fields, root=self.cfg.get("ledger_root"))


def _hook(payload: dict, cfg: dict, event: str, until: float | None) -> Hook:
    started = time.monotonic()
    return Hook(payload, cfg, started, event, started + DEADLINE_SECONDS if until is None else until)


def _ledger(hook: Hook, findings: list[dict]) -> str:
    decisions = reporting.record_findings(
        session_id=hook.scope.session_id, hook="after_run", event=hook.event, findings=findings,
        tool_use_id=hook.call_id, turn_id=payloads.turn_id(hook.payload),
        duration_ms=int((time.monotonic() - hook.started) * 1000),
        root=hook.cfg.get("ledger_root"), config=hook.cfg,
    )
    observed = [(row, outcome) for row, outcome in decisions if outcome != "block"]
    kind, reason = reporting.verdict_message(observed, hook.cfg)
    return reason if kind == "observe" else ""


def _stored(hook: Hook) -> dict:
    return session_state.read_state(hook.scope.session_id, hook.scope.root).get(STATE, {})


def _save(hook: Hook, update: Callable[[dict], dict]) -> None:
    def mutate(state: dict) -> dict:
        return {**state, STATE: update(state.get(STATE, {}))}

    session_state.update_state_strict(hook.scope.session_id, mutate, hook.scope.root)


def _window(hook: Hook) -> bool:
    return tests_policy.window_seconds_left(hook.cfg, time.time()) > 0


def _snapshot_hook(hook: Hook, snapshot: dict, config: dict | None) -> Hook:
    if not snapshot.get("linked_project"):
        return hook
    settings = effective_hook_config(config, Path(snapshot["root"]))
    settings["session_id"] = hook.scope.session_id
    return replace(hook, cfg=settings)


def _capture(hook: Hook, stored: dict, config: dict | None) -> tuple[list[tuple[dict, dict, dict | None]], dict]:
    raw_cwd = payloads.cwd(hook.payload)
    if not raw_cwd:
        return [], {}
    cwd = Path(raw_cwd).absolute()
    locations = project_locations(hook, cwd)
    records = nested_locations(hook, locations, stored)
    prepared = []
    for root, (in_git, linked) in locations.items():
        snapshot = root_snapshot(hook, cwd, root, in_git, linked, discover=str(root) in records)
        scoped = _snapshot_hook(hook, snapshot, config)
        snapshot.update(_call_facts(scoped, cwd.resolve()), commit_config=config or {},
                        nested_before=records.get(str(root), {}).get("repos", []))
        prepared.append((scoped, snapshot))
    prepared.extend(new_commit_snapshots(hook, cwd, locations, _call_facts(hook, cwd.resolve()), config))
    captured = []
    for (_, snapshot), result in zip(prepared, capture_roots(stored, prepared)):
        if not isinstance(result, BaseException):
            captured.append(result)
        elif len(prepared) == 1:
            raise result
        else:
            hook.timing(_snapshot_rule(result), snapshot["root"])
    if prepared and not captured:
        _keep_failure(hook)
    return captured, records


def _call_facts(hook: Hook, cwd: Path) -> dict:
    source = payloads._tool_input(hook.payload)
    command = source.get("command", "")
    exempt = _window(hook) or (isinstance(command, str) and shell_snapshot.exempt_command(command, cwd))
    return {
        "exempt": exempt, "at_ns": time.monotonic_ns(), "wall_ns": time.time_ns(),
        "agent_id": hook.scope.agent_id, "agent_type": agent_types.type_of(hook.payload, hook.scope.root),
        "background": not exempt and (source.get("run_in_background") is True
                                      or isinstance(command, str) and shell_snapshot.runs_in_background(command)),
    }


def _store_failure(hook: Hook) -> None:
    facts = _call_facts(hook, Path(payloads.cwd(hook.payload) or ".").resolve())

    def store(data: dict) -> dict:
        calls = {**data.get("calls", {}), hook.call_id: {**facts, "failed": True}}
        return {**data, "calls": dict(list(calls.items())[-MAX_CALLS:])}

    _save(hook, store)


def _store_call(hook: Hook, captured: list[tuple[dict, dict, dict | None]], records: dict) -> None:
    _save(hook, lambda data: record_call(nested_repos.remember(data, records), hook.scope.agent_id, hook.call_id,
                                         captured, MAX_CALLS))


def pre(payload: dict, config: dict | None) -> None:
    if not payloads.session_id(payload) or not payloads.tool_use_id(payload):
        return
    hook = _hook(payload, payload_hook_config(config, payload), "PreToolUse", None)
    try:
        captured, records = _capture(hook, _stored(hook), config)
        if captured:
            _store_call(hook, captured, records)
            hook.timing()
    except (OSError, ValueError, RuntimeError, after_commit.baseline.OutOfTime) as error:
        _keep_failure(hook)
        hook.timing(_snapshot_rule(error))


def _keep_failure(hook: Hook) -> None:
    try:
        _store_failure(hook)
    except (OSError, ValueError, RuntimeError):
        return


def _test_gate(hook: Hook, snapshot: dict) -> bool:
    actor = {**hook.payload, "agent_id": snapshot["agent_id"], "agent_type": snapshot["agent_type"]}
    return not tests_policy.gate_open(tests_policy.Gate(actor, hook.cfg, time.time()))


@dataclass(frozen=True, slots=True)
class Judged:
    changes: dict
    found: list
    deferred: list
    unsearched: list = field(default_factory=list)


def _path_change(hook: Hook, compared: shell_snapshot.Comparison, raw: str, prior: dict, old: tuple,
                 *, guard_tests: bool) -> tuple[dict, list[dict]]:
    digest, after = compared.current(raw)
    path = shell_snapshot.project_path(compared.root, raw)
    if path is None or digest == shell_snapshot.UNKNOWN:
        return prior, []
    before_digest, before = old
    if before_digest == digest:
        return prior, []
    expected = _unrestored(path, compared.root, prior.get("test"), digest, after)
    if expected is None and not prior.get("test") and guard_tests and before_digest != shell_snapshot.UNKNOWN:
        change = tests_policy.PendingChange(str(path), before, after)
        named = tests_policy.is_test_path(raw, str(compared.root))
        if named or tests_policy.touches_tests(change, str(compared.root)):
            expected = {"digest": before_digest, "inline": not named, "before": before if not named else None,
                        "checked_worktree": True}
    return _scan_entry(hook, path, digest, expected, compared.root, scan=raw not in compared.tested,
                       base=compared.moved.get(raw))


def _late_change(hook: Hook, compared: shell_snapshot.Comparison, raw: str, prior: dict, old: tuple,
                 *, guard_tests: bool) -> tuple[dict, list[dict], bool]:
    path = shell_snapshot.project_path(compared.root, raw)
    if path is None:
        return prior, [], False
    deferred = raw not in compared.tested
    if prior.get("test") or not guard_tests or not tests_policy.is_test_path(raw, str(compared.root)):
        return prior, [], deferred
    expected = {"digest": old[0], "inline": False, "before": None, "checked_worktree": True}
    current, found = _scan_entry(hook, path, shell_snapshot.UNKNOWN, expected, compared.root, scan=False)
    return current, found, deferred


def _bounded(hook: Hook, keys: list, judge: Callable) -> dict:
    from lib import stop_scan

    results: dict = {}
    try:
        with stop_scan.scan_alarm(hook.until):
            for key in keys:
                shell_snapshot.check_deadline(hook.until)
                results[key] = judge(key)
    except (stop_scan.OutOfTime, shell_snapshot.WalkOverflow):
        pass
    return results


def _unrestored(path: Path, root: Path, expected: dict | None, digest: str, text: str | None) -> dict | None:
    if expected is None or expected["digest"] == digest:
        return None
    if expected["inline"] and (text is not None or digest == shell_snapshot.ABSENT):
        change = tests_policy.PendingChange(str(path), expected["before"], text)
        if not tests_policy.touches_tests(change, str(root)):
            return None
    return expected


def _scan_entry(hook: Hook, path: Path, digest: str, expected: dict | None, root: Path, *,
                scan: bool = True, base: tuple | None = None) -> tuple[dict, list[dict]]:
    found = content_rows(hook, path, digest, root, base) if scan else []
    if expected is not None:
        found.insert(0, tests_policy._finding(str(path)))
    blocking = [row for row in found if resolve_outcome(row, hook.cfg) == "block"]
    return ({"test": expected, "digest": digest, "rows": blocking, "root": str(root)} if blocking else {}), found


def _known_before(root: Path, path: Path, expected: dict | None, until: float) -> dict | None:
    if expected is None or expected["digest"] != shell_snapshot.UNKNOWN:
        return expected
    raw = path.relative_to(root).as_posix()
    fetched, _ = shell_snapshot.head_sources(root, [raw], until)
    return {**expected, "digest": fetched.get(raw, (shell_snapshot.UNKNOWN, None))[0]}


def _refreshed(hook: Hook, raw: str, prior: dict) -> dict | None:
    if prior.get("commit"):
        current = after_commit.recheck(prior, hook)
        return current if current != prior else None
    root = Path(prior["root"])
    path = shell_snapshot.project_path(root, str(Path(raw).relative_to(root)))
    if path is None:
        return None
    digest = shell_snapshot.digest(path, until=hook.until)
    if digest == shell_snapshot.UNKNOWN:
        return None
    known = _known_before(root, path, prior.get("test"), hook.until)
    expected = _unrestored(path, root, known, digest, shell_snapshot.read_text(path))
    if expected and not expected.get("checked_worktree") and prior.get("digest") == digest:
        if legacy_clean_path(hook, _stored(hook), root, path, digest):
            expected = None
    current, _ = _scan_entry(hook, path, digest, expected, root)
    if "seal" in prior:
        current, _ = after_run_config.seal_entry(path, root, current, prior["seal"], digest, prior.get("seal_text"))
    return current if current != prior else None


def _restored(hook: Hook, entries: dict, changes: dict) -> dict:
    keys = [raw for raw in entries if raw not in changes]
    results = _bounded(hook, keys, lambda raw: _refreshed(hook, raw, entries[raw]))
    return {raw: current for raw, current in results.items() if current is not None}


def _changes(hook: Hook, compared: shell_snapshot.Comparison, entries: dict) -> Judged:
    changes, found, deferred = {}, [], []
    guard_tests = _test_gate(hook, compared.before)
    old, late = shell_snapshot.previous_sources(compared, list(compared.paths))
    late |= compared.late

    def judge(raw: str) -> tuple[dict, list[dict]]:
        prior = entries.get(str(compared.root / raw), {})
        return _path_change(hook, compared, raw, prior, old[raw], guard_tests=guard_tests)

    judged = _bounded(hook, [raw for raw in compared.paths if raw not in late], judge)
    for raw in compared.paths:
        path = str(compared.root / raw)
        prior = entries.get(path, {})
        if raw in judged:
            current, rows = judged[raw]
        else:
            current, rows, later = _late_change(hook, compared, raw, prior, old.get(raw, (shell_snapshot.UNKNOWN, None)),
                                                guard_tests=guard_tests)
            if later:
                deferred.append(path)
        if current != prior:
            changes[path] = current
        found.extend(rows)
    judged = Judged(changes, found, deferred)
    after_run_config.seal_changes(compared, entries, judged, old)
    return judged


def _explaining_call(hook: Hook, snapshot: dict, *, stop: bool) -> str:
    if not stop:
        return hook.call_id
    return snapshot.get("background_call", "") if hook.event == "SubagentStop" else ""


def _settle(
    hook: Hook, compared: shell_snapshot.Comparison, changes: dict, *, stop: bool, keep_turn: bool = False,
) -> dict:
    settled: dict = {}
    agent, root = compared.before["agent_id"], str(compared.root)

    def update(data: dict) -> dict:
        pending = dict(data.get("pending", {}))
        entries = {**pending.get(agent, {}), **changes}
        settled.update({path: entry for path, entry in entries.items() if entry})
        if settled:
            pending[agent] = settled
        else:
            pending.pop(agent, None)
        calls, turns = dict(data.get("calls", {})), dict(data.get("turns", {}))
        finished = calls.get(hook.call_id, {})
        for actor, record in turns.items():
            roots = snapshots(record)
            snapshot = roots.get(root)
            call_id = _explaining_call(hook, compared.before, stop=stop)
            if stop and actor != agent and not call_id:
                continue
            if snapshot is None:
                if actor != agent or not compared.before.get("new_repo"):
                    continue
                snapshot = compared.before
            advanced = advance_snapshot(snapshot, compared, call_id)
            if actor == agent and not stop:
                advanced["windows"] = _windows(advanced, compared.before)
                if finished.get("background") or snapshots(finished).get(root, {}).get("background"):
                    advanced.update(background=True, background_call=hook.call_id)
            turns[actor] = {"roots": {**roots, root: advanced}}
        if stop and not keep_turn and not settled and not compared.overflow:
            turns.pop(agent, None)
        cache = dict(data.get("sources", {}))
        cache[root] = {**cache.get(root, {}), **compared.sources}
        updated = {**data, "calls": calls, "turns": turns, "pending": pending, "sources": cache}
        return ignored_configs.remember({**updated, "sources": prune_sources(updated)}, root, compared.ignored)

    _save(hook, update)
    return settled


def _windows(turn: dict, before: dict) -> list:
    window = [before.get("wall_ns", 0), time.time_ns()]
    return [*turn.get("windows", []), window][-MAX_CALLS:]


def _windowed(compared: shell_snapshot.Comparison, snapshot: dict) -> tuple[str, ...]:
    return tuple(raw for raw in compared.paths if raw not in compared.before["marks"]
                 and ignored_configs.made_during(compared.marks.get(raw), snapshot.get("windows")))


def _render(hook: Hook, rows: list[dict]) -> str:
    if not rows:
        return ""
    return reporting.compact_block(rows, {**hook.cfg, "session_id": hook.scope.session_id})[0]


def _publish(hook: Hook, before: dict, pending: dict, agent: str) -> str:
    scope = blocker_state.BlockerScope(hook.scope.session_id, agent, hook.scope.root)
    if not pending:
        if before:
            blocker_state.clear_pending(scope, BLOCKER)
        return ""
    if pending == before:
        current, _, _ = blocker_state.details(scope.session_id, scope.agent_id, scope.root)
        if current.get(BLOCKER):
            return current[BLOCKER]
    reason = _render(hook, [row for entry in pending.values() for row in entry["rows"]])
    reason = join_notices(reason, after_commit.actions(pending.values()))
    blocker_state.set_pending(scope, BLOCKER, reason)
    return reason


def _check(hook: Hook, data: dict, snapshot: dict, *, stop: bool, multiple: bool = False,
           fetched: Prefetched | None = None, other_heads: list[str] | None = None) -> tuple[dict, str, bool]:
    exempt = _window(hook) or (not stop and snapshot["exempt"])
    agent = snapshot["agent_id"]
    entries = data.get("pending", {}).get(agent, {})
    blamed = not stop or bool(snapshot.get("background"))
    compared = compare_snapshot(hook, data, snapshot, stop=stop, exempt=exempt, blamed=blamed, fetched=fetched)
    for path in (compared.unsearched, *(str(compared.root / raw) for raw in compared.dropped)):
        if path:
            hook.timing("config_search_incomplete", path)
    judged = _judge_worktree(hook, compared, entries, stop=stop) if blamed and not exempt else Judged({}, [], [])
    if blamed and exempt:
        after_run_config.seal_changes(compared, entries, judged)
    windowed = () if blamed or exempt else _windowed(compared, snapshot)
    if windowed:
        after_run_config.seal_changes(replace(compared, paths=windowed), entries, judged)
    guard = not exempt and (stop or not snapshot.get("replays")) and _test_gate(hook, snapshot)
    commits = after_commit.compare(hook, snapshot, entries, _commit_work(data, agent) if stop else None, stop=stop,
                                   current_heads=fetched.current_heads() if fetched else None, guard_tests=guard,
                                   cached=data.get("sources", {}).get(snapshot["root"]), other_heads=other_heads)
    compared.before["checked_heads"] = commits.checked
    changes = {**judged.changes, **commits.changes}
    if not exempt and (stop or host.is_codex_host()):
        restorable = {path: entry for path, entry in entries.items() if entry["root"] == snapshot["root"]} if multiple else entries
        changes.update(_restored(hook, restorable, changes))
    pending = _settle(hook, compared, changes, stop=stop, keep_turn=multiple)
    _store_work(hook, agent, commits.deferred, stop=stop, held=_commit_work(data, agent))
    _publish(hook, entries, pending, agent)
    found = judged.found + commits.found
    observed = _ledger(hook, found) if found else ""
    if not blamed and not exempt:
        for raw in compared.paths:
            if raw not in windowed:
                hook.timing(UNEXPLAINED_RULE, str(compared.root / raw))
    for raw in judged.unsearched:
        hook.timing("config_search_incomplete", raw)
    _defer(hook, agent, judged.deferred)
    if compared.overflow:
        hook.timing("snapshot_overflow", skipped=compared.skipped)
    else:
        hook.timing()
    notice = join_notices(commits.notice, observed, _overflow_note(compared.skipped),
                          _counted(DEFERRED_NOTE, len(judged.deferred)))
    return (_window_entries(pending) if exempt else pending), notice, not compared.overflow


def _judge_worktree(hook: Hook, compared: shell_snapshot.Comparison, entries: dict, *, stop: bool) -> Judged:
    if stop or not compared.before.get("worktree_move") or not known_target(compared.root, compared.before, hook.until):
        return _changes(hook, compared, entries)
    hook.timing("worktree_move", str(compared.root))
    paths = tuple(raw for raw in compared.paths if raw in compared.marks or shell_snapshot.seal_candidate(raw, {}))
    return _changes(hook, replace(compared, paths=paths), entries)


def _commit_work(data: dict, agent: str) -> dict:
    return data.get(COMMIT_WORK, {}).get(agent, {})


def _store_work(hook: Hook, agent: str, deferred: dict, *, stop: bool, held: dict) -> None:
    if not deferred and not (stop and held):
        return

    def store(data: dict) -> dict:
        work = dict(data.get(COMMIT_WORK, {}))
        mine = {} if stop else dict(work.get(agent, {}))
        for root, items in deferred.items():
            mine[root] = [*mine.get(root, []), *items]
        if mine:
            work[agent] = mine
        else:
            work.pop(agent, None)
        return {**data, COMMIT_WORK: work}

    _save(hook, store)


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


def _defer(hook: Hook, agent: str, paths: list[str]) -> None:
    if not paths:
        return
    blocker_state.touch_paths(blocker_state.BlockerScope(hook.scope.session_id, agent, hook.scope.root), paths)
    hook.timing(DEFERRED_RULE, deferred=len(paths))


def _counted(template: str, count: int) -> str:
    if not count:
        return ""
    return template.format(count=count, noun="path" if count == 1 else "paths")


def _overflow_note(skipped: int) -> str:
    return _counted(OVERFLOW_NOTE, skipped)


def _discard_call(hook: Hook) -> None:
    def discard(data: dict) -> dict:
        calls = dict(data.get("calls", {}))
        calls.pop(hook.call_id, None)
        updated = {**data, "calls": calls}
        return {**updated, "sources": prune_sources(updated)}

    _save(hook, discard)


def _turn_fallback(data: dict, marker: dict) -> dict[str, dict]:
    if not marker.get("failed"):
        return snapshots(marker)
    facts = {key: marker[key] for key in ("agent_id", "agent_type", "exempt", "background")}
    return {root: {**snapshot, **facts, "worktree_move": False}
            for root, snapshot in turn_snapshots(data, marker["agent_id"]).items()}


def _check_snapshots(hook: Hook, snapshots: dict[str, dict], config: dict | None, *, stop: bool) -> tuple[dict, str]:
    snapshots, records = extend_snapshots(hook, snapshots, _stored(hook), stop=stop)
    if records:
        _save(hook, lambda state: nested_repos.remember(state, records))
    multiple = len(snapshots) > 1
    fetched = prefetch(hook, snapshots, _stored(hook), stop=stop)
    shared_heads = shared_worktree_heads(fetched) if multiple else {}
    notices = []
    exempt_roots, complete_roots = set(), set()
    for root, snapshot in snapshots.items():
        scoped = _snapshot_hook(hook, snapshot, config)
        if _window(scoped) or (not stop and snapshot["exempt"]):
            exempt_roots.add(root)
        try:
            pending, notice, complete = _check(scoped, _stored(hook), snapshot, stop=stop, multiple=multiple,
                                               fetched=fetched[root], other_heads=shared_heads.get(root))
        except (OSError, ValueError, RuntimeError) as error:
            hook.timing(_snapshot_rule(error), root)
        else:
            if not multiple:
                return pending, notice
            notices.append(notice)
            if complete:
                complete_roots.add(root)
    if not multiple:
        return {}, ""
    pending = _stored(hook).get("pending", {}).get(hook.scope.agent_id, {})
    if stop and not pending and complete_roots:
        _save(hook, lambda state: close_turn_roots(state, hook.scope.agent_id, complete_roots))
    visible = _window_entries(pending) | {path: entry for path, entry in pending.items()
                                           if entry["root"] not in exempt_roots}
    return visible, join_notices(*dict.fromkeys(notices))


def post(payload: dict, config: dict | None, event: str = "PostToolUse", *, until: float | None = None) -> dict:
    if not payloads.session_id(payload) or not payloads.tool_use_id(payload):
        return {}
    hook = _hook(payload, payload_hook_config(config, payload), event, until)
    data = _stored(hook)
    marker = data.get("calls", {}).get(hook.call_id)
    if marker is None:
        return {}
    snapshots = _turn_fallback(data, marker)
    if not snapshots:
        _discard_call(hook)
        hook.timing("snapshot_failed")
        return {}
    pending, notice = _check_snapshots(hook, snapshots, config, stop=False)
    _discard_call(hook)
    if pending and host.is_codex_host():
        reason = _publish(hook, pending, pending, hook.scope.agent_id)
        return {**stop_block(join_notices(reason, notice)), LIVE_BLOCK: True}
    return advise(notice, hook.event) if notice else {}


def _snapshot_rule(error: Exception) -> str:
    return "snapshot_overflow" if isinstance(error, shell_snapshot.WalkOverflow) else "snapshot_failed"


def _window_entries(entries: dict) -> dict:
    return {key: entry for key, entry in entries.items() if "seal" in entry or entry.get("commit")}


def _recheck_pending(hook: Hook, entries: dict) -> dict:
    exempt = {}
    if _window(hook):
        window_entries = _window_entries(entries)
        exempt = {key: entries[key] for key in entries.keys() - window_entries.keys()}
        entries = window_entries
    if not entries:
        return {}
    agent = hook.scope.agent_id
    changes = _restored(hook, entries, {})
    pending = {path: value for path, value in (entries | changes).items() if value}
    if changes:
        _save(hook, lambda state: {**state, "pending": {**state["pending"], agent: exempt | pending}})
    _publish(hook, entries, pending, agent)
    return pending


def batch_reason(payload: dict, cfg: dict, until: float | None = None) -> str:
    if not payloads.session_id(payload):
        return ""
    hook = _hook(payload, cfg, "PostToolBatch", until)
    entries = _stored(hook).get("pending", {}).get(hook.scope.agent_id, {})
    pending = _recheck_pending(hook, entries)
    if entries:
        hook.timing()
    return _publish(hook, pending, pending, hook.scope.agent_id)


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
    return [hook, *(Hook({**hook.payload, "agent_id": agent}, hook.cfg, time.monotonic(), hook.event, hook.until)
                    for agent in sorted(actors - {""}))]


def stop(
    payload: dict, cfg: dict, until: float | None = None, *, config: dict | None = None,
) -> tuple[str, str]:
    hook = _hook(payload, cfg, "Stop", until)
    data = _stored(hook)
    if not data:
        return "", ""
    paths: dict[str, list[dict]] = {}
    notices, entries = [], []
    for scoped in _stop_hooks(hook, data):
        pending, notice = _stop_scope(scoped, data if scoped == hook else _stored(scoped), config)
        for path, entry in pending.items():
            paths.setdefault(path, []).extend(entry["rows"])
        entries.extend(pending.values())
        notices.append(notice)
    return _render_stop(hook, paths, after_commit.actions(entries)), join_notices(*dict.fromkeys(notices))


def _render_stop(hook: Hook, paths: dict[str, list[dict]], action: str = "") -> str:
    shown = [rows[0] for rows in paths.values()]
    rows = [row for findings in paths.values() for row in findings]
    if not rows:
        return ""
    if len(shown) == len(rows):
        return join_notices(_render(hook, shown), action)
    cfg = {**hook.cfg, "session_id": hook.scope.session_id}
    reason = reporting.compact_block(shown, cfg, report_path=reporting.write_full_report(rows, cfg))[0]
    return join_notices(reason, action)


def note_direct_write(payload: dict, config: dict | None, *, until: float | None = None, expected: str | None = None) -> None:
    if not payloads.session_id(payload) or not payloads.tool_use_id(payload):
        return
    hook = _hook(payload, payload_hook_config(config, payload), "PostToolUse", until)
    data = _stored(hook)
    turns = {root: snapshot for record in data.get("turns", {}).values() for root, snapshot in snapshots(record).items()}
    for root, snapshot in turns.items():
        try:
            cached = data.get("sources", {}).get(root, {})
            if compared := direct_comparison(hook, snapshot, cached):
                if expected is None or all(compared.current(raw)[0] == expected for raw in compared.paths):
                    entries = data.get("pending", {}).get(hook.scope.agent_id, {})
                    changes = {}
                    for raw in compared.paths:
                        key = str(compared.root / raw)
                        if "seal" in entries.get(key, {}) and (current := _refreshed(hook, key, entries[key])) is not None:
                            changes[key] = current
                    pending = _settle(hook, compared, changes, stop=False)
                    if changes:
                        _publish(hook, entries, pending, hook.scope.agent_id)
                    if expected is not None:
                        after_run_config.release_owner_paths(hook, compared)
        except (OSError, ValueError, RuntimeError) as error:
            hook.timing(_snapshot_rule(error))


def prime_ignored(session_id: str, cwd: str, state_root: str | None) -> None:
    location = shell_snapshot.project_location(Path(cwd).resolve())
    if location is None:
        return
    root, in_git = location
    until = time.monotonic() + ignored_configs.START_SECONDS
    nested = nested_repos.listing(root, {}, until)
    listed = shell_snapshot.ignored_listing(root, in_git, None, until)

    def store(state: dict) -> dict:
        remembered = ignored_configs.remember(state.get(STATE, {}), str(root), listed.record)
        return {**state, STATE: nested_repos.remember(remembered, {str(root): nested})}

    session_state.update_state_strict(session_id, store, state_root)


def subagent_stop(
    payload: dict, cfg: dict, until: float | None = None, *, config: dict | None = None,
) -> tuple[str, str]:
    hook = _hook(payload, cfg, "SubagentStop", until)
    pending, notice = _stop_scope(hook, _stored(hook), config)
    action = after_commit.actions(pending.values())
    return _render_stop(hook, {path: entry["rows"] for path, entry in pending.items()}, action), notice
