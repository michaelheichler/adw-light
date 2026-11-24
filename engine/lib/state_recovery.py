from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from collections.abc import Callable, Iterator
import time
from pathlib import Path
from typing import BinaryIO, TextIO

BACKUP_SUFFIX = ".bak"
CORRUPT_SUFFIX = ".corrupt-"
RECOVERY_KEY = "state_recovered"


@contextmanager
def temporary_file(
    directory: Path, *, binary: bool = False,
) -> Iterator[tuple[Path, TextIO | BinaryIO]]:
    with _staged_file(None, str(directory / "adw"), binary, 0o600) as (name, stream):
        yield Path(name), stream


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    with temporary_file(path.parent) as (temporary, stream):
        stream.close()
        yield temporary
        os.replace(temporary, path)


class TemporaryExhausted(OSError):
    pass


def _temporary_at(parent_fd: int | None, name: str, mode: int) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    target = Path(name)
    for _attempt in range(32):
        temporary = str(target.with_name(f".{target.name}.{os.urandom(16).hex()}.tmp"))
        try:
            return temporary, os.open(temporary, flags, mode, dir_fd=parent_fd)
        except FileExistsError:
            continue
    raise TemporaryExhausted("could not allocate state temporary file")


@contextmanager
def _staged_file(parent_fd: int | None, name: str, binary: bool, mode: int | None) -> Iterator[tuple[str, TextIO | BinaryIO]]:
    temporary, descriptor = _temporary_at(parent_fd, name, 0o666 if mode is None else mode)
    try:
        stream = os.fdopen(descriptor, "wb" if binary else "w", encoding=None if binary else "utf-8")
        descriptor = -1
        with stream:
            yield temporary, stream
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _remove_temporary(temporary, parent_fd)


def _remove_temporary(name: str, parent_fd: int | None) -> None:
    try:
        os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass


def _finish_stream(stream: TextIO | BinaryIO, mode: int | None, sync: bool) -> None:
    if sync:
        stream.flush()
        os.fsync(stream.fileno())
    if mode is not None:
        os.fchmod(stream.fileno(), mode)


@contextmanager
def atomic_open(
    path: Path, *, binary: bool = False, mode: int | None = 0o600,
    sync: bool = False, parent_fd: int | None = None,
    before_replace: Callable[[], None] | None = None,
) -> Iterator[TextIO | BinaryIO]:
    name = str(path) if parent_fd is None else path.name
    with _staged_file(parent_fd, name, binary, mode) as (temporary, stream):
        yield stream
        _finish_stream(stream, mode, sync)
        stream.close()
        if before_replace is not None:
            before_replace()
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)


def sync_directory(path: Path, *, strict: bool = False) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        if strict:
            raise
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: object, *, ensure_ascii: bool = False, **options) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=ensure_ascii), **options)


def atomic_write(
    path: Path, payload: str | bytes, *, mode: int | None = 0o600, sync: bool = False,
    sync_parent: bool = False,
) -> None:
    with atomic_open(path, binary=isinstance(payload, bytes), mode=mode, sync=sync) as stream:
        stream.write(payload)
    if sync_parent:
        sync_directory(path.parent)


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + BACKUP_SUFFIX)


def parsed(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid session state object: {path}")
    return data


def _backup(path: Path) -> dict | None:
    try:
        return parsed(backup_path(path))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def restored(path: Path) -> dict | None:
    backup = _backup(path)
    if backup is None:
        return None
    try:
        return parsed(path)
    except (OSError, ValueError, UnicodeDecodeError):
        pass
    aside = path.with_name(f"{path.name}{CORRUPT_SUFFIX}{time.time_ns()}")
    shutil.copyfile(path, aside)
    notice = (
        f"agent-discipline-watcher: the session state file was unreadable. ADW copied it to {aside} "
        "and the last good copy was restored. Changes after that copy are lost."
    )
    state = {**backup, RECOVERY_KEY: notice}
    atomic_write(path, json.dumps(state, ensure_ascii=True))
    return state
