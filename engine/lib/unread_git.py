from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import NamedTuple

try:
    from . import git_port, unread_files
except ImportError:
    import git_port
    import unread_files

CONTROL_NAMES = ("config", "info/exclude", "info/attributes")
HOOKS = "hooks"
GLOBAL_CONFIG_ENV = "GIT_CONFIG_GLOBAL"
XDG_CONFIG_ENV = "XDG_CONFIG_HOME"
HISTORY_REF = "g:"
CONTROL_REF = "c:"
GITLINK_MODE = "160000"


class History(NamedTuple):
    head: str | None
    index: dict[str, str]
    refs: dict[str, str]


class Control(NamedTuple):
    paths: list[Path]
    unreadable: list[Path]


def history(root: Path) -> History | None:
    view = git_port.view()
    index = view.index_entries(root)
    refs = view.ref_heads(root)
    return None if index is None or refs is None else History(view.resolve_commit(root, "HEAD"), index, refs)


def changed_paths(root: Path, record: dict, now: History) -> list[str] | None:
    old = record["index"]
    before = record.get("refs")
    if not isinstance(before, dict):
        return None
    paths = {path for path in old.keys() | now.index.keys() if old.get(path) != now.index.get(path)}
    spans = [(record["head"], now.head), *((before.get(name) or record["head"], tip) for name, tip in now.refs.items())]
    for start, end in spans:
        if start == end:
            continue
        moved = _head_paths(root, start, end)
        if moved is None:
            return None
        paths.update(moved)
    return sorted(paths)


def _head_paths(root: Path, old: str | None, new: str | None) -> list[str] | None:
    view = git_port.view()
    if old and new:
        return view.diff_paths(root, [old, new])
    one = old or new
    return view.tree_paths(root, one, []) if one else []


def nested(root: Path, listed: list[str]) -> bool:
    index = git_port.view().index_entries(root)
    return index is None or any(rel.endswith("/") for rel in listed) or any(meta.startswith(GITLINK_MODE) for meta in index.values())


def global_configs() -> list[Path]:
    named = os.environ.get(GLOBAL_CONFIG_ENV)
    if named:
        return [Path(named).expanduser()]
    xdg = Path(os.environ.get(XDG_CONFIG_ENV) or Path.home() / ".config")
    return [Path.home() / ".gitconfig", xdg / "git" / "config"]


def control(root: Path) -> Control | None:
    common = git_port.view().common_dir(root)
    if common is None:
        return None
    named = [*(Path(common) / name for name in CONTROL_NAMES), *global_configs()]
    hooks = Path(common) / HOOKS
    try:
        entries = sorted(hooks / name for name in os.listdir(hooks))
    except (FileNotFoundError, NotADirectoryError):
        entries = []
    except OSError:
        return Control(named, [hooks])
    return Control([*named, *entries], [])


def control_entries(root: Path) -> dict[str, list] | None:
    found = control(root)
    if found is None or found.unreadable:
        return None
    entries: dict[str, list] = {}
    for path in found.paths:
        printed = unread_files.stat_print(path)
        if printed is not None:
            entries[str(path)] = [*printed, unread_files.current_ref(path, printed, unread_files.SHA)]
    return entries


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8", "surrogateescape")).hexdigest()


def history_ref(head: str | None, refs: object, entry: object) -> str:
    return HISTORY_REF + _digest([head, refs, entry])


def control_ref(entries: dict[str, list]) -> str:
    return CONTROL_REF + _digest({path: entry[-1] for path, entry in entries.items()})


def ref_matches(ref: str, root: str, rel: str, seen: dict) -> bool:
    if not root:
        return False
    if ref.startswith(HISTORY_REF):
        now = _memo(seen, ("history", root), lambda: history(Path(root)))
        return now is not None and history_ref(now.head, now.refs, now.index.get(rel) if rel else now.index) == ref
    found = _memo(seen, ("control", root), lambda: _control_or_none(Path(root)))
    return found is not None and control_ref(found) == ref


def _control_or_none(root: Path) -> dict[str, list] | None:
    try:
        return control_entries(root)
    except OSError:
        return None


def _memo(seen: dict, key: tuple, compute):
    if key not in seen:
        seen[key] = compute()
    return seen[key]
