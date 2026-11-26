from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    from . import config_roots, session_state
    from .state_recovery import atomic_write
except ImportError:
    import config_roots
    import session_state
    from state_recovery import atomic_write

FOLDER_NAME = "adw-unread-markers"
CHECKOUTS = "checkouts"
LOCK_NAME = ".lock"
NAME_CHARS = 32
PRIVATE = 0o700


def _name(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()[:NAME_CHARS]


class MarkerError(PermissionError):
    pass


def _owned(path: Path) -> Path:
    try:
        found = os.lstat(path)
    except FileNotFoundError:
        return path
    except NotADirectoryError as exc:
        raise MarkerError(_blocked(path)) from exc
    if not stat.S_ISDIR(found.st_mode):
        raise MarkerError(_blocked(path))
    if found.st_uid != os.getuid():
        raise MarkerError(f"ADW Lite unread script marker folder {path} belongs to another user, so ADW cannot trust it. Ask the user to remove it.")
    return path


def _blocked(path: Path) -> str:
    return f"ADW Lite unread script marker path {path} is not a plain folder, so ADW cannot check the script lock. Ask the user to remove it."


def root() -> Path:
    return config_roots.cache_root() / FOLDER_NAME


def folder(session: str, state_root) -> Path:
    state = os.path.abspath(session_state.session_directory(session, state_root).parent)
    cache = _owned(config_roots.cache_root())
    return _owned(_owned(cache / FOLDER_NAME) / _name(state))


def _ready(path: Path) -> Path:
    path.mkdir(mode=PRIVATE, parents=True, exist_ok=True)
    return _owned(path)


def digest(record: object) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8", "surrogateescape")).hexdigest()


def _marker(session: str, state_root) -> Path:
    return folder(session, state_root) / f"{_name(session)}.json"


def present(session: str, state_root) -> bool:
    return _marker(session, state_root).exists()


def sync(session: str, state_root, records: dict) -> None:
    path = _marker(session, state_root)
    if not records:
        path.unlink(missing_ok=True)
        return
    _ready(path.parent)
    atomic_write(path, json.dumps({key: digest(record) for key, record in records.items()}, sort_keys=True))


def consistent(session: str, state_root, records: dict) -> bool:
    try:
        stored = json.loads(_marker(session, state_root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        stored = {}
    except (OSError, ValueError):
        return False
    return stored == {key: digest(record) for key, record in records.items()}


@contextmanager
def _locked(session: str, state_root) -> Iterator[Path]:
    base = _ready(folder(session, state_root) / CHECKOUTS)
    descriptor = os.open(base / LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield base
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _entries(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise ValueError("checkout lock entries are malformed")
    return {
        path: {key: item for key, item in entry.items() if isinstance(key, str) and isinstance(item, str)}
        for path, entry in value.items() if isinstance(path, str) and isinstance(entry, dict)
    }


def _read(path: Path) -> tuple[str, dict[str, dict[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("real"), str):
        raise ValueError(f"checkout lock is malformed: {path}")
    return data["real"], _entries(data.get("held"))


def checkout_held(session: str, state_root, cwd: str) -> dict[str, dict[str, str]]:
    base = folder(session, state_root) / CHECKOUTS
    if not base.is_dir():
        return {}
    here = Path(os.path.realpath(cwd or "."))
    held: dict[str, dict[str, str]] = {}
    for path in sorted(base.glob("*.json")):
        real, entries = _read(path)
        if here.is_relative_to(real):
            held.update(entries)
    return held


def checkout_hold(session: str, state_root, real: str, entries: dict[str, dict[str, str]]) -> None:
    if not entries:
        return
    with _locked(session, state_root) as base:
        path = base / f"{_name(real)}.json"
        held = _read(path)[1] if path.exists() else {}
        merged = {**entries, **held}
        atomic_write(path, json.dumps({"real": real, "held": merged}, sort_keys=True))


def checkout_release(session: str, state_root, paths: set[str]) -> None:
    base = folder(session, state_root) / CHECKOUTS
    if not paths or not base.is_dir():
        return
    with _locked(session, state_root) as base:
        for path in sorted(base.glob("*.json")):
            real, held = _read(path)
            kept = {name: entry for name, entry in held.items() if name not in paths}
            if not kept:
                path.unlink()
            elif len(kept) != len(held):
                atomic_write(path, json.dumps({"real": real, "held": kept}, sort_keys=True))
