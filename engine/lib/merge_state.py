from __future__ import annotations

import re
import time
from pathlib import Path

try:
    from . import git_port, session_state
except ImportError:
    import git_port
    import session_state

GIT_TIMEOUT_SECONDS = 30
SESSION_STARTED_KEY = "session_started_at"
STATE_FILES = (("MERGE_HEAD", ""), ("CHERRY_PICK_HEAD", ""), ("REVERT_HEAD", "^"))
FULL_OBJECT_NAME = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
PUBLISHED_REFS = ("refs/heads", "refs/remotes", "refs/tags")
SHARED_REFS = ("refs/remotes", "refs/tags")


def revision_text(repo: Path, revision: str, path: str) -> str | None:
    return git_port.view().head_text(repo, path, revision, timeout=GIT_TIMEOUT_SECONDS)


def _state_object_names(repo: Path, state_name: str) -> list[str]:
    return git_port.view().state_names(repo, state_name)


def _carried(repo: Path, commit: str, refs: tuple[str, ...]) -> bool:
    return any(line.strip() for line in git_port.view().refs_containing(repo, commit, refs))


def _older_than(repo: Path, commit: str, started: float) -> bool:
    stamp = git_port.view().commit_time(repo, commit)
    return stamp is not None and stamp < int(started)


def _published(repo: Path, commit: str, started: float | None) -> bool:
    if started is None:
        if _carried(repo, commit, PUBLISHED_REFS):
            return True
        return git_port.view().is_ancestor(repo, commit, "HEAD")
    return _carried(repo, commit, SHARED_REFS) or _older_than(repo, commit, started)


def _resolved(repo: Path, name: str, suffix: str, started: float | None) -> str | None:
    if not FULL_OBJECT_NAME.fullmatch(name):
        return None
    commit = git_port.view().resolve_commit(repo, f"{name}{suffix}")
    return commit if commit and _published(repo, commit, started) else None


def _state_commits(repo: Path, state_name: str, suffix: str, started: float | None) -> list[str]:
    names = _state_object_names(repo, state_name)
    return [commit for commit in (_resolved(repo, name, suffix, started) for name in names) if commit]


def incoming_commits(repo: Path, session_started: float | None = None) -> list[str]:
    return [
        commit
        for state_name, suffix in STATE_FILES
        for commit in _state_commits(repo, state_name, suffix, session_started)
    ]


def session_started(session_id: str, state_root: str | Path | None) -> float | None:
    try:
        value = session_state.read_state(session_id, state_root).get(SESSION_STARTED_KEY)
    except (OSError, ValueError, TypeError):
        return None
    return float(value) if type(value) in (int, float) else None


def mark_session_start(session_id: str, state_root: str | Path | None) -> None:
    if session_state.read_state(session_id, state_root):
        return
    session_state.update_state(
        session_id, lambda state: state or {SESSION_STARTED_KEY: time.time()}, state_root,
    )
