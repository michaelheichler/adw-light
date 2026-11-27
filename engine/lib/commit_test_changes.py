from __future__ import annotations

from pathlib import Path

try:
    from . import declared_tests, git_port, shell_snapshot, tests_policy
except ImportError:
    import declared_tests, git_port, shell_snapshot, tests_policy

TEST = "test"
TEST_ACTION = "Reset the commit away and let the test writer agent make the test change."


def written_test_rows(root: Path, revision: str, parents: list[str], blobs: dict[str, bytes], until: float) -> list[dict]:
    if len(parents) > 1:
        return []
    removed = git_port.view().commit_removed_paths(root, revision, until=until)
    if removed is None:
        raise OSError("Git commit scan unavailable")
    parent = next(iter(parents), "")
    changed = [path for path in (*blobs, *removed) if _touches(root, path, blobs.get(path), parent, until)]
    return [tests_policy._finding(str(root / path)) for path in dict.fromkeys(changed)]


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
