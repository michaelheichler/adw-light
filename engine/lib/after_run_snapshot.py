from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from . import after_commit, baseline, git_port, ignored_configs, nested_repos, payloads, shell_snapshot
    from . import tests_policy
    from .commit_command import GitCall, UnreadableCommit, bash_command, git_calls
    from .commit_test_changes import capture_index, moved_sources
    from .named_directories import named_directories
    from .parallel_calls import attempted, gathered
    from .path_policy import PROJECT_MARKERS, anchored_identity, exited_project, marked_project
    from .regular_file import open_regular
    from .shell_parse import command_expansions
    from .shell_self_protection import _is_mutating, _segment_cwds
    from .shell_write_paths import write_paths
    from .write_targets import mutation_targets
except ImportError:
    import after_commit, baseline, git_port, ignored_configs, nested_repos, payloads, shell_snapshot
    import tests_policy
    from lib.commit_command import GitCall, UnreadableCommit, bash_command, git_calls
    from commit_test_changes import capture_index, moved_sources
    from named_directories import named_directories
    from parallel_calls import attempted, gathered
    from path_policy import PROJECT_MARKERS, anchored_identity, exited_project, marked_project
    from regular_file import open_regular
    from shell_parse import command_expansions
    from shell_self_protection import _is_mutating, _segment_cwds
    from shell_write_paths import write_paths
    from write_targets import mutation_targets

if TYPE_CHECKING:
    from .after_run import Hook


def snapshots(record: dict) -> dict[str, dict]:
    if "root" in record:
        return {record["root"]: record}
    return record.get("roots", {})


def turn_snapshots(data: dict, agent: str) -> dict[str, dict]:
    return snapshots(data.get("turns", {}).get(agent, {}))


def project_locations(hook: Hook, cwd: Path) -> dict[Path, tuple[bool, bool]]:
    physical = cwd.resolve()
    linked = exited_project(cwd)
    candidates = [(physical, False)]
    command = payloads._tool_input(hook.payload).get("command", "")
    paths = _named_destinations(command, cwd) if isinstance(command, str) else ()
    if linked is not None:
        named_return = any(path.is_relative_to(linked) for path in paths)
        candidates.insert(0 if named_return else 1, (linked, True))
    locations = {}
    for directory, is_linked in candidates:
        try:
            location = shell_snapshot.project_location(directory, until=hook.until)
        except (OSError, ValueError, RuntimeError):
            if len(candidates) == 1:
                raise
            hook.timing("snapshot_failed", str(directory))
            continue
        if location is not None:
            root, in_git = location
            locations.setdefault(root, (in_git, is_linked))
    _add_target_projects(hook, locations, paths)
    _add_commit_repos(hook, cwd, locations)
    return locations


def _named_destinations(command: str, cwd: Path) -> tuple[Path, ...]:
    expansions = command_expansions(command, cwd)
    paths = [anchored_identity(path, cwd) for path in write_paths(command, cwd)]
    for segment, folder in _segment_cwds(command, cwd, None):
        if _is_mutating(segment):
            local = expansions._replace(cwd=str(folder))
            paths.extend(anchored_identity(path, folder) for path in mutation_targets(segment, local))
    return tuple(dict.fromkeys([*paths, *named_directories(command, cwd)]))


def _add_target_projects(hook: Hook, locations: dict[Path, tuple[bool, bool]], paths: tuple[Path, ...]) -> None:
    visited: set[Path] = set()
    until = min(hook.until, time.monotonic() + 0.05)
    for path in paths:
        if time.monotonic() >= until:
            hook.timing("target_project_search_incomplete")
            break
        directory = next((parent for parent in (path, *path.parents) if parent.is_dir()), None)
        if directory is None or directory in visited:
            continue
        visited.add(directory)
        if any(directory.is_relative_to(root) for root in locations) or marked_project(directory) is None:
            continue
        try:
            location = shell_snapshot.project_location(directory, until=until,
                                                       markers=PROJECT_MARKERS - {ignored_configs.CONFIG_NAME})
        except (OSError, ValueError, RuntimeError):
            hook.timing("snapshot_failed", str(directory))
            continue
        if location is not None:
            root, in_git = location
            locations.setdefault(root, (in_git, True))


def discover_nested(hook: Hook, root: Path, stored: dict, *, refresh: bool = False) -> dict:
    known = stored.get(nested_repos.CACHE, {}).get(str(root), {})
    record = nested_repos.listing(root, known, hook.until, refresh=refresh)
    if not record["complete"]:
        hook.timing("nested_repo_search_incomplete", str(root))
    if record is not known and record["skipped"]:
        hook.timing("nested_repo_search_skipped", str(root), skipped=len(record["skipped"]))
    return record


def nested_locations(hook: Hook, locations: dict[Path, tuple[bool, bool]], stored: dict) -> dict[str, dict]:
    records = {}
    for root in tuple(locations):
        record = discover_nested(hook, root, stored)
        records[str(root)] = record
        for raw in record["repos"]:
            locations.setdefault(root / raw, (True, True))
    return records


def extend_snapshots(hook: Hook, roots: dict[str, dict], stored: dict, *, stop: bool) -> tuple[dict, dict]:
    expanded, records = dict(roots), {}
    for root, snapshot in roots.items():
        if not snapshot.get("discover_nested"):
            continue
        record = discover_nested(hook, Path(root), stored, refresh=stop)
        if record is not stored.get(nested_repos.CACHE, {}).get(root):
            records[root] = record
        for raw in sorted(set(record["repos"]).difference(snapshot.get("nested_before", ()))):
            nested = str(Path(root) / raw)
            if nested not in expanded:
                expanded[nested] = _new_repo_snapshot(snapshot, nested)
    return expanded, records


def _new_repo_snapshot(parent: dict, nested: str) -> dict:
    return {**parent, "root": nested, "cwd": nested, "git": True, "linked_project": True,
            "discover_nested": False, "worktree_move": False, "new_repo": True, "marks": {}, "heads": {}, "explained": {}}


def _add_commit_repos(hook: Hook, cwd: Path, locations: dict[Path, tuple[bool, bool]]) -> None:
    for call in _commit_calls(hook, cwd):
        if call.verb not in {"commit", "commit-tree", "update-ref", "reset"} or call.new_folder:
            continue
        if call.parent_repo is None and _within_walk(call.cwd.resolve(), locations):
            continue
        root = call.repo()
        if root is not None:
            locations.setdefault(root.resolve(), (True, True))


def _commit_calls(hook: Hook, cwd: Path) -> list[GitCall]:
    command = bash_command(hook.payload)
    if not command:
        return []
    try:
        return git_calls(command, cwd)
    except UnreadableCommit:
        return []


def _within_walk(directory: Path, locations: dict[Path, tuple[bool, bool]]) -> bool:
    return any(directory.is_relative_to(root) and shell_snapshot.SKIP_FOLDERS.isdisjoint(directory.relative_to(root).parts)
               for root in locations)


def _new_commit_roots(hook: Hook, cwd: Path, locations: dict[Path, tuple[bool, bool]]) -> set[Path]:
    roots = set()
    for call in _commit_calls(hook, cwd):
        if call.verb != "commit":
            continue
        root = call.cwd if call.new_folder else _skipped_repo(call, locations)
        if root is not None and not _within_walk(root, locations):
            roots.add(root)
    return roots


def _skipped_repo(call: GitCall, locations: dict[Path, tuple[bool, bool]]) -> Path | None:
    folder = call.cwd.resolve()
    if not any(folder.is_relative_to(root) for root in locations) or not folder.is_dir():
        return None
    return call.repo()


def _known_heads(root: Path, until: float) -> dict:
    if not (root / ".git").exists():
        return {}
    try:
        return {str(root): after_commit.repo_record(root, until)}
    except (OSError, baseline.OutOfTime):
        return {str(root): None}


def new_commit_snapshots(hook: Hook, cwd: Path, locations: dict[Path, tuple[bool, bool]],
                         facts: dict, config: dict | None) -> list[tuple[Hook, dict]]:
    return [(hook, {**facts, "root": str(root), "cwd": str(root), "git": False, "marks": {},
                    "new_commit_root": True, "configs_only": True, "commit_config": config or {}})
            for root in sorted(_new_commit_roots(hook, cwd, locations))]


def compare_snapshot(hook: Hook, data: dict, snapshot: dict, *, stop: bool, exempt: bool,
                     blamed: bool, fetched: Prefetched | None) -> shell_snapshot.Comparison:
    root = snapshot["root"]
    if snapshot.get("new_commit_root") and not Path(root).is_dir():
        return shell_snapshot.Comparison(Path(root), snapshot, {}, (), {}, until=hook.until)
    cached = data.get("sources", {}).get(root, {})
    explained = {} if stop else turn_snapshots(data, snapshot["agent_id"]).get(root, {}).get("explained", {})
    current = fetched.current_heads() if fetched else None
    previous = _previous_head(snapshot, current)
    moved = head_moves(hook, snapshot, current, skip=exempt or (not stop and snapshot.get("replays")))
    return shell_snapshot.compare({**snapshot, "previous_head": previous}, cached, explained, exempt=exempt, until=hook.until,
                                  capture=exempt or blamed, record=data.get("ignored", {}).get(root), refresh=stop,
                                  observe=fetched.observe if fetched else None,
                                  refreshed=fetched.listing() if fetched else None,
                                  head_moved=moved)


def snapshot_directory(cwd: Path, root: Path) -> Path:
    for directory in (cwd, *cwd.parents):
        physical = directory.resolve()
        if physical.is_relative_to(root):
            return physical
    return root


def _direct_paths(hook: Hook, root: Path) -> set[str]:
    cwd = Path(payloads.cwd(hook.payload) or str(root))
    sources = payloads.edited_paths(hook.payload)
    if payloads.tool_name(hook.payload) == "NotebookEdit":
        notebook = payloads._tool_input(hook.payload).get("notebook_path")
        if isinstance(notebook, str) and notebook:
            sources = (*sources, notebook)
    resolved = (payloads.resolved_path(raw, cwd) for raw in sources)
    return {path.relative_to(root).as_posix() for path in resolved if path.is_relative_to(root)}


def direct_comparison(hook: Hook, snapshot: dict, cached: dict) -> shell_snapshot.Comparison | None:
    root = Path(snapshot["root"])
    paths = tuple(sorted(_direct_paths(hook, root)))
    if not paths:
        return None
    until = min(time.monotonic() + shell_snapshot.WALK_SECONDS, hook.until)
    if snapshot["git"]:
        observed = shell_snapshot.marks(root, True, until=until)
        if observed is None:
            raise OSError("Snapshot unavailable")
        shell_snapshot.config_marks(root, Path(snapshot.get("cwd", root)), observed)
        marks = {raw: observed[raw] for raw in paths if raw in observed}
    else:
        marks = {}
        for raw in paths:
            if (path := shell_snapshot.project_path(root, raw)) and (mark := shell_snapshot.stamp(path)):
                marks[raw] = ["", mark]
    additions = shell_snapshot.capture_sources(root, marks, cached, until)
    before = {**snapshot, "agent_id": hook.scope.agent_id}
    return shell_snapshot.Comparison(root, before, marks, paths, cached | additions, until=hook.until)


def root_snapshot(hook: Hook, cwd: Path, root: Path, in_git: bool, linked: bool, *, discover: bool) -> dict:
    command = payloads._tool_input(hook.payload).get("command")
    move = in_git and isinstance(command, str) and shell_snapshot.is_worktree_move(command, cwd, root, hook.until)
    replays = isinstance(command, str) and shell_snapshot.replays_commits(command, cwd)
    return {"root": str(root), "git": in_git, "linked_project": linked, "discover_nested": discover,
            "cwd": str(snapshot_directory(cwd, root)), "worktree_move": move, "replays": replays}


FETCH_ERRORS = (OSError, ValueError, RuntimeError, after_commit.baseline.OutOfTime)


@dataclass(frozen=True, slots=True)
class Prefetched:
    marks: object
    heads: object
    listed: object = None

    def observe(self) -> dict | None:
        return _raised(self.marks)

    def listing(self) -> ignored_configs.Listing | None:
        return _raised(self.listed)

    def current_heads(self) -> tuple[dict, bool] | None:
        return _raised(self.heads)


def _raised(value: object):
    if isinstance(value, BaseException):
        raise value
    return value


def prefetch(hook: Hook, roots: dict[str, dict], stored: dict, *, stop: bool) -> dict[str, Prefetched]:
    calls = []
    for snapshot in roots.values():
        calls.append(attempted(lambda chosen=snapshot: shell_snapshot.observed_marks(chosen, hook.until), FETCH_ERRORS))
        calls.append(attempted(lambda chosen=snapshot: _current_heads(chosen, hook.until), FETCH_ERRORS))
        calls.append(attempted(lambda chosen=snapshot: _refreshed_listing(chosen, stored, hook.until, stop), FETCH_ERRORS))
    results = gathered(calls)
    return {root: Prefetched(*results[3 * index:3 * index + 3]) for index, root in enumerate(roots)}


def shared_worktree_heads(fetched: dict[str, Prefetched]) -> dict[str, list[str]]:
    current = {root: head_results.heads[0].get(root) for root, head_results in fetched.items()
               if isinstance(head_results.heads, tuple) and head_results.heads[0].get(root)}
    heads = {root: record["head"] for root, record in current.items() if record["head"]}
    shared = {}
    for root, record in current.items():
        tips = set(record["local"].values()) - {record["head"]}
        candidates = {sibling: head for sibling, head in heads.items() if head in tips}
        if candidates and (common := git_port.view().common_dir(Path(root))) is not None:
            shared[root] = [head for sibling, head in candidates.items()
                            if git_port.view().common_dir(Path(sibling)) == common]
    return shared


def _refreshed_listing(snapshot: dict, stored: dict, until: float, stop: bool) -> ignored_configs.Listing | None:
    if not stop or "cwd" not in snapshot:
        return None
    record = stored.get("ignored", {}).get(snapshot["root"])
    return shell_snapshot.ignored_listing(Path(snapshot["root"]), snapshot["git"], record, until, refresh=True)


def legacy_clean_path(hook: Hook, stored: dict, root: Path, path: Path, digest: str) -> bool:
    snapshot = turn_snapshots(stored, hook.scope.agent_id).get(str(root), {})
    windows = snapshot.get("windows", [])
    mark = shell_snapshot.stamp(path)
    if not mark or not windows or snapshot.get("background"):
        return False
    changed_at = mark[3]
    if changed_at >= windows[-1][0] or any(start <= changed_at <= end for start, end in windows):
        return False
    raw = path.relative_to(root).as_posix()
    head, _ = shell_snapshot.head_sources(root, [raw], hook.until)
    return head.get(raw, (shell_snapshot.UNKNOWN, None))[0] == digest.removeprefix("link:")


def writer_explained(stored: dict, types: dict[str, str], agent: str, root: Path, path: Path, digest: str,
                     until: float) -> bool:
    raw = path.relative_to(root).as_posix()
    entry = turn_snapshots(stored, agent).get(str(root), {}).get("explained", {}).get(raw)
    if type(entry) is not dict or types.get(str(entry.get("agent_id"))) not in tests_policy.TEST_WRITER_TYPES:
        return False
    mark = entry.get("mark")
    if mark is not None:
        return type(mark) is list and len(mark) == 2 and mark[1] == shell_snapshot.stamp(path)
    head, _ = shell_snapshot.head_sources(root, [raw], until)
    return head.get(raw, (shell_snapshot.UNKNOWN, None))[0] == digest.removeprefix("link:")


def content_rows(hook: Hook, path: Path, digest: str, root: Path, base: tuple | None = None) -> list[dict]:
    if digest in {shell_snapshot.ABSENT, shell_snapshot.UNKNOWN} or digest.startswith("link:"):
        return []
    if base is None:
        from record import scanned_rewrite

        owned = scanned_rewrite(path, hook.cfg)
    else:
        owned = _moved_rows(hook, path, root, base[1])
    return [{**row, "path": str(path), "content_hash": digest} for row in owned]


def _moved_rows(hook: Hook, path: Path, root: Path, before: str | None) -> list[dict]:
    try:
        with open_regular(path) as stream:
            raw = stream.read(shell_snapshot.MAX_TEXT_BYTES + 1)
    except OSError:
        return []
    if len(raw) > shell_snapshot.MAX_TEXT_BYTES:
        return []
    return after_commit.rows_against(root, path.relative_to(root).as_posix(), raw, lambda: [before], hook)


def _previous_head(snapshot: dict, current: tuple[dict, bool] | None) -> str | None:
    root = snapshot["root"]
    before = snapshot.get("heads", {}).get(root)
    after = current[0].get(root) if current else None
    return before["head"] if before and after and before["head"] != after["head"] else None


def head_moves(hook: Hook, snapshot: dict, current: tuple[dict, bool] | None, *, skip: bool) -> dict:
    if skip or not snapshot.get("git") or current is None:
        return {}
    return moved_sources(Path(snapshot["root"]), snapshot, current, hook.until, after_commit.SKEW_SECONDS)


def _current_heads(snapshot: dict, until: float) -> tuple[dict, bool] | None:
    if "discover_nested" not in snapshot:
        return None
    return after_commit.snapshot_heads(Path(snapshot["root"]), snapshot["git"], until)


def capture_roots(stored: dict, prepared: list[tuple[Hook, dict]]) -> list:
    calls = [attempted(lambda scoped=scoped, snapshot=snapshot: capture_root(scoped, stored, snapshot),
                       (OSError, ValueError, RuntimeError)) for scoped, snapshot in prepared]
    return gathered(calls)


def _root_marks(hook: Hook, snapshot: dict, until: float) -> dict:
    root = Path(snapshot["root"])
    try:
        marks = shell_snapshot.marks(root, snapshot["git"], until=until, bound_git=not snapshot["discover_nested"])
    except shell_snapshot.WalkOverflow:
        if not snapshot["exempt"]:
            raise
        snapshot["configs_only"] = True
        hook.timing("snapshot_overflow", str(root))
        return {}
    if marks is None:
        raise OSError("Snapshot unavailable")
    return marks


def capture_root(hook: Hook, stored: dict, snapshot: dict) -> tuple[dict, dict, dict | None]:
    root, in_git = Path(snapshot["root"]), snapshot["git"]
    if snapshot.get("new_commit_root"):
        return {**snapshot, "since": int(time.time()), "heads": _known_heads(root, hook.until)}, {}, None
    shell_snapshot.check_deadline(hook.until)
    until = min(time.monotonic() + shell_snapshot.WALK_SECONDS, hook.until)
    since = int(time.time())
    record = stored.get("ignored", {}).get(str(root))
    marks, listed, heads = gathered([
        lambda: _root_marks(hook, snapshot, until),
        lambda: shell_snapshot.ignored_listing(root, in_git, record, hook.until, search=record is None),
        lambda: after_commit.snapshot_heads(root, in_git, hook.until),
    ])
    shell_snapshot.hidden_marks(root, listed.paths, marks)
    unsearched = "" if listed.complete else str(root)
    if unsearched:
        hook.timing("config_search_incomplete", unsearched)
    shell_snapshot.config_marks(root, Path(snapshot["cwd"]), marks)
    snapshot.update(unsearched=unsearched, marks=marks, since=since)
    snapshot["heads"], snapshot["head_walk_complete"] = heads
    if in_git and not snapshot["exempt"]:
        snapshot["test_index"] = capture_index(root, marks)
    cached = stored.get("sources", {}).get(str(root), {})
    try:
        return snapshot, shell_snapshot.capture_sources(root, marks, cached, until), listed.record
    except shell_snapshot.WalkOverflow:
        if not snapshot["exempt"]:
            raise
        configs = {raw: mark for raw, mark in marks.items() if shell_snapshot.seal_candidate(raw, marks)}
        snapshot.update(marks=configs, configs_only=True)
        hook.timing("snapshot_overflow", str(root))
        return snapshot, shell_snapshot.capture_sources(root, configs, cached, hook.until), listed.record


def advance_snapshot(snapshot: dict, compared: shell_snapshot.Comparison, call_id: str) -> dict:
    marks, explained = dict(snapshot["marks"]), dict(snapshot.get("explained", {}))
    now = time.monotonic_ns()
    for raw in compared.paths:
        mark = compared.marks.get(raw)
        if mark is None:
            marks.pop(raw, None)
        else:
            marks[raw] = mark
        if call_id:
            explained[raw] = {"mark": mark, "tool_use_id": call_id, "agent_id": compared.before["agent_id"], "at_ns": now}
    heads = {**snapshot.get("heads", {}), **compared.before.get("checked_heads", {})}
    return {**snapshot, "marks": marks, "explained": explained, "heads": heads, "test_index": {}}


def prune_sources(data: dict) -> dict:
    by_root: dict[str, set[str]] = {}
    for record in (*data.get("calls", {}).values(), *data.get("turns", {}).values()):
        for root, snapshot in snapshots(record).items():
            by_root.setdefault(root, set()).update(
                shell_snapshot.source_key(raw, mark) for raw, mark in snapshot["marks"].items()
            )
    return {root: {key: value for key, value in entries.items() if key in by_root[root]}
            for root, entries in data.get("sources", {}).items() if root in by_root}


def close_turn_roots(data: dict, agent: str, completed: set[str]) -> dict:
    if data.get("pending", {}).get(agent):
        return data
    turns = dict(data.get("turns", {}))
    remaining = {root: snapshot for root, snapshot in turn_snapshots(data, agent).items() if root not in completed}
    if remaining:
        turns[agent] = {"roots": remaining}
    else:
        turns.pop(agent, None)
    updated = {**data, "turns": turns}
    return {**updated, "sources": prune_sources(updated)}


def drop_missing_roots(data: dict, actors: set[str], own: str) -> dict:
    turns = dict(data.get("turns", {}))
    for actor in actors & turns.keys():
        if kept := _existing(snapshots(turns[actor])):
            turns[actor] = {"roots": kept}
        else:
            turns.pop(actor)
    calls = {call: {**record, "roots": _existing(record["roots"])} if "roots" in record and _owner(record) == own else record
             for call, record in data.get("calls", {}).items()}
    updated = {**data, "turns": turns, "calls": calls}
    return {**updated, "sources": prune_sources(updated)}


def _existing(roots: dict[str, dict]) -> dict[str, dict]:
    return {root: snapshot for root, snapshot in roots.items() if snapshot.get("new_commit_root") or Path(root).is_dir()}


def _owner(record: dict) -> str | None:
    return next((snapshot.get("agent_id") for snapshot in record["roots"].values()), None)


def missing_roots(data: dict, actors: set[str], own: str) -> bool:
    records = [*(snapshots(data.get("turns", {}).get(actor, {})) for actor in actors),
               *(record["roots"] for record in data.get("calls", {}).values() if "roots" in record and _owner(record) == own)]
    return any(roots != _existing(roots) for roots in records)


def unchanged_root(snapshot: dict, fetched: Prefetched, held: bool) -> bool:
    if held or "cwd" not in snapshot or snapshot.get("new_repo") or snapshot.get("configs_only"):
        return False
    if snapshot.get("head_walk_complete") is False:
        return False
    try:
        observed, current, listed = fetched.observe(), fetched.current_heads(), fetched.listing()
    except FETCH_ERRORS:
        return False
    if observed is None or current is None or listed is None or not listed.complete or not current[1]:
        return False
    root, marks = Path(snapshot["root"]), dict(observed)
    shell_snapshot.hidden_marks(root, listed.paths, marks)
    shell_snapshot.config_marks(root, Path(snapshot["cwd"]), marks)
    before, after = snapshot.get("heads", {}), current[0]
    return marks == snapshot["marks"] and before.keys() == after.keys() and all(
        _same_head(before[key], after[key]) for key in before)


def _same_head(before: object, after: object) -> bool:
    return (isinstance(before, dict) and isinstance(after, dict)
            and before.get("head") == after.get("head") and before.get("local") == after.get("local"))


def record_call(data: dict, agent: str, call_id: str, captured: list[tuple[dict, dict, dict | None]], limit: int) -> dict:
    roots = {snapshot["root"]: snapshot for snapshot, _, _ in captured}
    calls = {**data.get("calls", {}), call_id: {"roots": roots}}
    previous = turn_snapshots(data, agent)
    turns = {**previous, **{root: {**snapshot, "explained": {}, "background": False}
                           for root, snapshot in roots.items() if root not in previous}}
    cache = dict(data.get("sources", {}))
    updated = {**data, "calls": dict(list(calls.items())[-limit:]), "sources": cache,
               "turns": {**data.get("turns", {}), agent: {"roots": turns}}}
    for snapshot, sources, record in captured:
        root = snapshot["root"]
        cache[root] = {**cache.get(root, {}), **sources}
        updated = ignored_configs.remember(updated, root, record)
    return {**updated, "sources": prune_sources(updated)}
