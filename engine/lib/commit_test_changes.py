from __future__ import annotations

import hashlib
from pathlib import Path

try:
    from . import declared_tests, git_port, shell_snapshot, tests_policy
except ImportError:
    import declared_tests, git_port, shell_snapshot, tests_policy

TEST = "test"
TEST_ACTION = "Reset the commit away and let the test writer agent make the test change."


def written_test_rows(root: Path, revision: str, parents: list[str], blobs: dict[str, bytes], until: float,
                      *, sources: dict | None = None) -> list[dict]:
    if len(parents) > 1:
        return []
    removed = git_port.view().commit_removed_paths(root, revision, until=until)
    if removed is None:
        raise OSError("Git commit scan unavailable")
    parent = next(iter(parents), "")
    changed = {path: blobs.get(path) for path in (*blobs, *removed)
               if _touches(root, path, blobs.get(path), parent, until)}
    existing = _existing_content(root, changed, sources, until) if sources and changed else set()
    return [tests_policy._finding(str(root / path)) for path in changed if path not in existing]


def capture_index(root: Path, marks: dict) -> dict[str, str]:
    staged = [path for path, (status, _) in marks.items() if _has_staged_change(status)]
    entries = git_port.view().index_entries(root) if staged else {}
    index = dict.fromkeys((path for path, (status, _) in marks.items() if status == "??"), shell_snapshot.ABSENT)
    for path in staged:
        index[path] = _index_blob(entries, path)
    return index


def _has_staged_change(status: str) -> bool:
    return bool(status) and status[0] not in {" ", "?", "!"}


def _index_blob(entries: dict[str, str] | None, path: str) -> str:
    if entries is None:
        return shell_snapshot.UNKNOWN
    if path not in entries:
        return shell_snapshot.ABSENT
    fields = entries[path].split()
    if len(fields) == 1:
        return fields[0]
    if len(fields) == 3 and fields[2] == "0" and fields[0] != "160000":
        return fields[1]
    return shell_snapshot.UNKNOWN


def snapshot_sources(snapshot: dict, cached: dict) -> dict:
    before = _head_record(snapshot.get("heads", {}).get(snapshot["root"]))
    if before is None or snapshot.get("configs_only"):
        return {}
    disk = {path: cached.get(shell_snapshot.source_key(path, mark), (shell_snapshot.UNKNOWN, None))[0]
            for path, mark in snapshot["marks"].items()}
    index = {path: shell_snapshot.ABSENT if status == "??" else shell_snapshot.UNKNOWN
             for path, (status, _) in snapshot["marks"].items() if status == "??" or _has_staged_change(status)}
    return {"head": before["head"], "disk": disk, "index": index | snapshot.get("test_index", {})}


def _existing_content(root: Path, changed: dict[str, bytes | None], sources: dict, until: float) -> set[str]:
    disk, index = sources["disk"], sources["index"]
    if not sources["head"]:
        head = {}
    else:
        paths = [path for path in changed if path not in disk or path not in index]
        head = git_port.view().revision_blobs(root, sources["head"], paths, until=until) if paths else {}
    return {path for path, raw in changed.items() if _already_present(path, raw, disk, index, head)}


def _already_present(path: str, raw: bytes | None, disk: dict, index: dict, head: dict | None) -> bool:
    fingerprint = content_digest(raw)
    if disk.get(path, shell_snapshot.UNKNOWN).removeprefix("link:") == fingerprint:
        return True
    if path in index and _matches_index(raw, index[path]):
        return True
    return (head is not None and (path not in index or path not in disk)
            and content_digest(head.get(path)) == fingerprint)


def _matches_index(raw: bytes | None, oid: str) -> bool:
    if raw is None:
        return oid == shell_snapshot.ABSENT
    if len(oid) not in {40, 64}:
        return False
    digest = hashlib.sha1() if len(oid) == 40 else hashlib.sha256()
    digest.update(b"blob %d\0" % len(raw))
    digest.update(raw)
    return digest.hexdigest() == oid


def content_digest(raw: bytes | None) -> str:
    return shell_snapshot.ABSENT if raw is None else hashlib.sha256(raw).hexdigest()


def disk_content(root: Path, raw: str, until: float) -> str:
    path = shell_snapshot.project_path(root, raw)
    if path is None:
        return shell_snapshot.UNKNOWN
    try:
        return shell_snapshot.digest(path, until=until).removeprefix("link:")
    except shell_snapshot.WalkOverflow:
        return shell_snapshot.UNKNOWN


def holds_test_row(root: Path, row: dict, meta: dict, until: float) -> bool:
    git = git_port.view()
    revision = meta["revision"]
    reachable = git.commit_reachable(root, revision, until=until)
    if reachable is None:
        raise OSError("Git commit reachability unavailable")
    if not reachable:
        return False
    raw = Path(row["path"]).relative_to(root).as_posix()
    committed = _revision_content(root, revision, raw, until)
    if "disk_matches" in meta:
        current = _settled_content(root, raw, until)
        if current is None:
            return True
        if current == committed:
            return meta["disk_matches"] is not False
        return not git.is_ancestor(root, revision)
    if _settled_content(root, raw, until) == committed:
        return False
    siblings = git.worktree_roots(root) or []
    return not any(sibling != root and sibling.is_dir() and git.is_ancestor(sibling, revision)
                   and _settled_content(sibling, raw, until) == committed for sibling in siblings)


def _revision_content(root: Path, revision: str, raw: str, until: float) -> str:
    blobs = git_port.view().revision_blobs(root, revision, [raw], until=until)
    if blobs is None:
        raise OSError("Git test content unavailable")
    return content_digest(blobs.get(raw))


def _settled_content(root: Path, raw: str, until: float) -> str | None:
    disk = disk_content(root, raw, until)
    if disk != shell_snapshot.UNKNOWN and disk == _revision_content(root, "HEAD", raw, until):
        return disk
    return None


def known_target(root: Path, snapshot: dict, until: float) -> bool:
    before = _head_record(snapshot.get("heads", {}).get(str(root)))
    head = git_port.view().commit_head(root, until=until)
    return before is not None and bool(head) and _reached(root, head, before["tips"], until)


def moved_sources(root: Path, snapshot: dict, current: tuple[dict, bool] | None, until: float, skew: int) -> dict:
    before = _head_record(snapshot.get("heads", {}).get(str(root)))
    after = _head_record(current[0].get(str(root))) if current else None
    if before is None or after is None or not before["head"] or after["head"] in {"", before["head"]}:
        return {}
    if _reached(root, after["head"], before["tips"], until) or _scanned_commit(root, after["head"], before, until, skew):
        return {}
    git = git_port.view()
    paths = git.tree_changes(root, before["head"], after["head"], until=until)
    if paths is None:
        raise OSError("Git tree comparison unavailable")
    blobs = git.revision_blobs(root, before["head"], paths, until=until) or {}
    return {raw: shell_snapshot.blob_source(blobs[raw]) if raw in blobs else (shell_snapshot.ABSENT, None) for raw in paths}


def _head_record(value: object) -> dict | None:
    if isinstance(value, dict) and isinstance(value.get("tips"), list) and isinstance(value.get("head"), str):
        return value
    return None


def _scanned_commit(root: Path, head: str, before: dict, until: float, skew: int) -> bool:
    since = int(before.get("since", 0)) - skew if before.get("since") else 0
    return git_port.view().new_commits(root, [head], before["tips"], since=since, limit=1, until=until) == [head]


def _reached(root: Path, head: str, tips: list[str], until: float) -> bool:
    return head in tips or git_port.view().new_commits(root, [head], tips, since=0, limit=1, until=until) == []


def _touches(root: Path, path: str, raw: bytes | None, parent: str, until: float) -> bool:
    if tests_policy.is_test_path(path, str(root)):
        return True
    after = None if raw is None else raw.decode("utf-8", "replace")
    if not after or not (tests_policy.inline_test_bodies(path, after) or declared_tests.declares_tests(path, after)):
        return False
    before = git_port.view().head_text(root, path, parent, until=until) if parent else None
    return tests_policy.touches_tests(tests_policy.PendingChange(path, before, after), str(root))
