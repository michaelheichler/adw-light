from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

try:
    from . import baseline, git_port, payloads, session_state, shell_snapshot, stop_scan
    from .commit_test_changes import TEST, TEST_ACTION, content_digest, disk_content, holds_test_row, snapshot_sources, written_test_rows
    from .config import SURFACE_COMMIT, effective_hook_config, resolve_outcome
    from .hookio import join_notices
    from .findings import finding_row
    from .fixture_prose import index_scan_config
    from .scan_input import is_binary_content
    from .scanner import file_length_findings, scan_all, scannable_text
except ImportError:
    import baseline, git_port, payloads, session_state, shell_snapshot, stop_scan
    from commit_test_changes import TEST, TEST_ACTION, content_digest, disk_content, holds_test_row, snapshot_sources, written_test_rows
    from config import SURFACE_COMMIT, effective_hook_config, resolve_outcome
    from hookio import join_notices
    from findings import finding_row
    from fixture_prose import index_scan_config
    from scan_input import is_binary_content
    from scanner import file_length_findings, scan_all, scannable_text

LIMIT = 1000
SKEW_SECONDS = 5
RECEIPTS = "commit_scan_receipts"
PREFIX = "commit:"
MESSAGE = "message"
CONTENT = "content"
RETRY = "retry"
MESSAGE_ACTION = "Amend or reword the commit so its message passes, or reset it away."
CONTENT_ACTION = "Fix the file and commit the fix, or amend the commit."
RETRY_ACTION = "Retry the commit scan at Stop."
WALK_NOTE = "ADW Lite could not finish checking nested repositories before the deadline."
LISTED_NOTE = "ADW Lite listed the newest {limit} new commits in {root} and skipped the older ones."
DEFERRED_NOTE = "ADW Lite ran short of time and moved the scan of {count} new {noun} to the Stop check."
UNSCANNED_NOTE = "ADW Lite ran out of time at Stop and did not scan {count} new {noun} in {root}."
UNCHECKED_NOTE = "ADW Lite ran short of time and moved the check for new commits to the Stop check."
UNCHECKED_STOP_NOTE = "ADW Lite ran out of time at Stop and did not check {root} for new commits."


def _signature(parent, message, blobs):
    digest = hashlib.sha256()
    for part in (parent.encode(), message.encode(), *(v for p in sorted(blobs) for v in (os.fsencode(p), blobs[p]))):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def record_receipt(payload, state_root, repo: Path, parent: str, message: str, blobs: dict[str, bytes]) -> None:
    call = payloads.tool_use_id(payload)
    session = payloads.session_id(payload)
    if not call or not session:
        return
    root = str(repo.resolve())
    signature = _signature(parent, message, blobs)

    def store(state):
        receipts = dict(state.get(RECEIPTS, {}))
        receipts[call] = {**receipts.get(call, {}), root: signature}
        return {**state, RECEIPTS: dict(list(receipts.items())[-100:])}

    session_state.update_state_strict(session, store, state_root)


def repo_record(root: Path, until: float) -> dict | None:
    found = git_port.view().head_tips(root, until=until)
    if found is None:
        return None
    head, tips = found
    local = {name: tip for name, tip in tips.items() if name.startswith("refs/heads/")}
    known = sorted({*tips.values(), *([head] if head else [])})
    return {"head": head, "local": local, "tips": known, "since": int(time.time())}


def snapshot_heads(root: Path, in_git: bool, until: float) -> tuple[dict, bool]:
    if not in_git and not (root / ".git").exists():
        return {}, True
    try:
        return {str(root): repo_record(root, until)}, True
    except baseline.OutOfTime:
        return {str(root): None}, False


def heads(root, in_git, until):
    result = {}
    if in_git:
        try:
            result[str(root)] = repo_record(root, until)
        except baseline.OutOfTime:
            return {str(root): None}, False
    limit = min(until, time.monotonic() + shell_snapshot.WALK_SECONDS)
    for directory, folders, files in os.walk(root):
        if time.monotonic() >= limit:
            return result, False
        if directory not in result and (".git" in folders or ".git" in files):
            try:
                result[directory] = repo_record(Path(directory), until)
            except baseline.OutOfTime:
                return result, False
        folders[:] = [name for name in folders if name not in shell_snapshot.SKIP_FOLDERS]
    return result, True


def _record(value) -> dict | None:
    return value if isinstance(value, dict) and isinstance(value.get("tips"), list) else None


def pairs(entry: dict) -> list[tuple[dict, dict]]:
    return list(zip(entry.get("rows", []), entry.get("held", [])))


def entry_of(root, held: list[tuple[dict, dict]]) -> dict:
    unique = []
    for pair in held:
        if pair not in unique:
            unique.append(pair)
    if not unique:
        return {}
    return {"commit": True, "root": str(root), "rows": [row for row, _ in unique], "held": [meta for _, meta in unique]}


def actions(entries) -> str:
    kinds = {meta.get("kind") for entry in entries if entry.get("commit") for _, meta in pairs(entry)}
    known = ((MESSAGE, MESSAGE_ACTION), (TEST, TEST_ACTION), (CONTENT, CONTENT_ACTION), (RETRY, RETRY_ACTION))
    return join_notices(*(text for kind, text in known if kind in kinds))


def _counted(template: str, count: int, **fields) -> str:
    return template.format(count=count, noun="commit" if count == 1 else "commits", **fields) if count else ""


def _message_rows(message, cfg):
    from pre_commit import COMMIT_MESSAGE_PATH, _commit_metadata_ranges

    return [{**row, "path": COMMIT_MESSAGE_PATH, "surface": SURFACE_COMMIT} for row in scan_all(
        COMMIT_MESSAGE_PATH, message, cfg, document=False, hidden_ranges=_commit_metadata_ranges(message))]


def _file_rows(root, path, raw, parents, hook):
    return rows_against(root, path, raw, lambda: [git_port.view().head_text(root, path, p, until=hook.until) for p in parents], hook)


def rows_against(root, path: str, raw: bytes, prior, hook) -> list[dict]:
    if is_binary_content(path, raw):
        return []
    text = raw.decode("utf-8", "replace")
    cfg = hook.cfg
    rows = (file_length_findings(path, text) if scannable_text(text, cfg) is None else
            scan_all(path, text, index_scan_config(cfg, path, root)))
    if rows:
        rows, _ = baseline.split_against(prior(), path, rows, cfg, text)
    return [{**row, "path": path} for row in rows]


def _read(value):
    if value is None:
        raise OSError("Git commit scan unavailable")
    return value


def _scan(root: Path, revision: str, hook, receipt: str | None, sources: dict | None) -> list[tuple[dict, dict]]:
    git, until = git_port.view(), hook.until
    parents = _read(git.commit_parents(root, revision, until=until))
    message = _read(git.commit_message(root, revision, until=until))
    paths = _read(git.commit_paths(root, revision, until=until))
    blobs = {p: _read(git.commit_bytes(root, revision, p, until=until)) for p in paths}
    meta = {"revision": revision, "parents": parents}
    found = _committed_test_pairs(root, revision, parents, blobs, until, sources) if sources is not None else []
    if len(parents) <= 1 and receipt and receipt == _signature(next(iter(parents), ""), message, blobs):
        return found
    found.extend((row, {**meta, "kind": MESSAGE}) for row in _message_rows(message, hook.cfg))
    for path, raw in blobs.items():
        baseline.remaining(until)
        found.extend((row, {**meta, "kind": CONTENT}) for row in _file_rows(root, path, raw, parents, hook))
    return found


def _committed_test_pairs(root: Path, revision: str, parents: list[str], blobs: dict[str, bytes], until: float,
                          sources: dict) -> list[tuple[dict, dict]]:
    found = []
    for row in written_test_rows(root, revision, parents, blobs, until, sources=sources):
        raw = Path(row["path"]).relative_to(root).as_posix()
        disk = disk_content(root, raw, until)
        matches = None if disk == shell_snapshot.UNKNOWN else disk == content_digest(blobs.get(raw))
        found.append((row, {"kind": TEST, "revision": revision, "disk_matches": matches}))
    return found


@dataclass(slots=True)
class Resolved:
    held: list = field(default_factory=list)
    found: list = field(default_factory=list)
    rest: dict | None = None
    failed: bool = False
    listed_over: bool = False


def _new_revisions(root: Path, item: dict, hook) -> tuple[list[str], bool]:
    after = _record(item.get("after")) or _read(repo_record(root, hook.until))
    before = item["before"]
    other_heads = set(item.get("other_heads", ()))
    tips = [tip for tip in (after["head"], *after["local"].values()) if tip and tip not in other_heads]
    since = int(before.get("since", 0)) - SKEW_SECONDS if before.get("since") else 0
    revisions = _read(git_port.view().new_commits(root, tips, before["tips"], since=since, limit=LIMIT + 1, until=hook.until))
    return revisions[:LIMIT], len(revisions) > LIMIT


def resolve(root: Path, item: dict, hook, receipt=None) -> Resolved:
    scoped = replace(hook, cfg=effective_hook_config(item.get("config") or {}, str(root)))
    result = Resolved()
    revisions = item.get("revisions")
    done = 0
    try:
        with stop_scan.scan_alarm(hook.until):
            if revisions is None:
                revisions, result.listed_over = _new_revisions(root, item, scoped)
            for revision in revisions:
                sources = item.get("test_sources", {}) if item.get("tests") is True else None
                found = _scan(root, revision, scoped, receipt, sources)
                result.found.extend(row for row, _ in found)
                result.held.extend(pair for pair in found if resolve_outcome(pair[0], scoped.cfg) == "block")
                done += 1
    except (baseline.OutOfTime, OSError) as error:
        result.failed = not isinstance(error, baseline.OutOfTime)
        left = {"revisions": revisions[done:]} if revisions is not None else {key: item[key] for key in ("before", "after") if key in item}
        result.rest = {**left, "config": item.get("config") or {}, "tests": item.get("tests") is True,
                       "test_sources": item.get("test_sources", {}), "other_heads": item.get("other_heads", [])}
    return result




def retry_pair(root, item: dict) -> tuple[dict, dict]:
    row = finding_row("code", "after_run_deferred", 1, "Commit scan incomplete", "HEAD", RETRY_ACTION)
    return {**row, "path": str(root)}, {"kind": RETRY, "item": item}


def _holds(root: Path, row: dict, meta: dict, hook) -> bool:
    git = git_port.view()
    if meta["kind"] == TEST:
        return holds_test_row(root, row, meta, hook.until)
    if meta["kind"] == MESSAGE:
        return _read(git.commit_reachable(root, meta["revision"], until=hook.until))
    head = _read(git.commit_head(root, until=hook.until))
    if head == meta["revision"]:
        return True
    path = row["path"]
    raw = git.commit_bytes(root, "HEAD", path, until=hook.until) if head else None
    if raw is None:
        listed = _read(git.tree_paths(root, "HEAD", [path], until=hook.until)) if head else []
        if path in listed:
            raise OSError("Committed file unreadable")
        return _held_on_branch(root, row, meta, hook)
    return _still_found(root, row, raw, meta, hook)


def _still_found(root: Path, row: dict, raw: bytes, meta: dict, hook) -> bool:
    rows = _file_rows(root, row["path"], raw, meta["parents"], hook)
    return any(found["rule"] == row["rule"] and found["snippet"] == row["snippet"] for found in rows)


def _held_on_branch(root: Path, row: dict, meta: dict, hook) -> bool:
    git = git_port.view()
    tips = _read(git.ref_tips(root, until=hook.until))
    for name, tip in tips.items():
        if name.startswith("refs/heads/") and git.is_ancestor(root, meta["revision"], tip):
            raw = git.commit_bytes(root, tip, row["path"], until=hook.until)
            if raw is not None and _still_found(root, row, raw, meta, hook):
                return True
    return False


def recheck(entry: dict, hook) -> dict:
    root = Path(entry["root"])
    kept = []
    try:
        with stop_scan.scan_alarm(hook.until):
            for row, meta in pairs(entry):
                if meta.get("kind") != RETRY:
                    if _holds(root, row, meta, hook):
                        kept.append((row, meta))
                    continue
                result = resolve(root, meta["item"], hook)
                kept.extend(result.held)
                if result.rest is not None:
                    kept.append(retry_pair(root, result.rest))
    except (baseline.OutOfTime, OSError):
        return entry
    return entry_of(root, kept)


@dataclass(slots=True)
class Compared:
    changes: dict = field(default_factory=dict)
    found: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    checked: dict = field(default_factory=dict)
    deferred: dict = field(default_factory=dict)
    unscanned: int = 0
    unchecked: bool = False

    @property
    def notice(self) -> str:
        return join_notices(*self.notes)


def _add(compared: Compared, entries: dict, root, held: list) -> None:
    key = PREFIX + str(root)
    base = compared.changes.get(key, entries.get(key, {}))
    if held:
        compared.changes[key] = entry_of(root, pairs(base) + held)


def _settle_item(compared: Compared, entries: dict, root, result: Resolved, *, stop: bool) -> None:
    compared.found.extend(result.found)
    held = list(result.held)
    if result.listed_over:
        compared.notes.append(LISTED_NOTE.format(limit=LIMIT, root=root))
    if result.rest is not None and result.failed:
        held.append(retry_pair(root, result.rest))
    elif result.rest is not None and stop:
        revisions = result.rest.get("revisions")
        compared.notes.append(UNCHECKED_STOP_NOTE.format(root=root) if revisions is None else
                              _counted(UNSCANNED_NOTE, len(revisions), root=root))
    elif result.rest is not None:
        compared.deferred.setdefault(str(root), []).append(result.rest)
        revisions = result.rest.get("revisions")
        compared.unscanned += len(revisions or [])
        compared.unchecked = compared.unchecked or revisions is None
    _add(compared, entries, root, held)


def _current(root: str, current: dict, complete: bool, hook) -> tuple[bool, dict | None]:
    if root in current:
        return True, current[root]
    if complete or not Path(root).exists():
        return False, None
    try:
        return True, repo_record(Path(root), hook.until)
    except baseline.OutOfTime:
        return True, None


def compare(hook, snapshot: dict, entries: dict, work: dict | None = None, *, stop: bool = False,
            current_heads: tuple[dict, bool] | None = None, guard_tests: bool = False,
            cached: dict | None = None, other_heads: list[str] | None = None) -> Compared:
    compared = Compared()
    for key, entry in entries.items():
        if key.startswith(PREFIX) and entry.get("commit"):
            compared.changes[key] = recheck(entry, hook)
    receipts = session_state.read_state(hook.scope.session_id, hook.scope.root).get(RECEIPTS, {}).get(hook.call_id, {}) if hook.call_id else {}
    config = snapshot.get("commit_config") or {}
    old = snapshot.get("heads", {})
    head_reader = snapshot_heads if "discover_nested" in snapshot else heads
    if current_heads is not None:
        current, complete = current_heads
    else:
        current, complete = head_reader(Path(snapshot["root"]), snapshot["git"], hook.until) if snapshot.get("root") else ({}, True)
    if not complete or snapshot.get("head_walk_complete") is False:
        compared.notes.append(WALK_NOTE)
    for root in sorted(old.keys() | current.keys()):
        before = _record(old.get(root))
        present, after = _current(root, current, complete, hook)
        if not present:
            continue
        if after is not None:
            compared.checked[root] = after
        if before and after and after["head"] == before["head"] and after["local"] == before["local"]:
            continue
        start = before or {"tips": [], "since": snapshot.get("since", 0)}
        sources = snapshot_sources(snapshot, cached or {}) if guard_tests and root == snapshot.get("root") else {}
        item = {"before": start, "config": config, "tests": guard_tests, "test_sources": sources,
                "other_heads": other_heads or [], **({"after": after} if after else {})}
        _settle_item(compared, entries, root, resolve(Path(root), item, hook, receipts.get(root)), stop=stop)
    for root, items in (work or {}).items():
        for item in items if Path(root).exists() else []:
            _settle_item(compared, entries, root, resolve(Path(root), item, hook), stop=stop)
    compared.notes.append(_counted(DEFERRED_NOTE, compared.unscanned))
    compared.notes.append(UNCHECKED_NOTE if compared.unchecked else "")
    return compared
