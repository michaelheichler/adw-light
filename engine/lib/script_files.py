from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .shell_syntax import SHELL_C_INTERPRETERS

MAX_SCRIPT_BYTES = 512 * 1024
MAX_SEARCH_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
HEAD_BYTES = 256
COMMIT = b"commit"
OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
Result = TypeVar("Result")


def script_text(path: Path) -> str | None:
    data = _with_regular(path, lambda descriptor: _read_up_to(descriptor, MAX_SCRIPT_BYTES + 1))
    return None if data is None or len(data) > MAX_SCRIPT_BYTES else data.decode("utf-8", errors="replace")


def oversized(path: Path) -> bool:
    size = _with_regular(path, lambda descriptor: os.fstat(descriptor).st_size)
    return size is not None and size > MAX_SCRIPT_BYTES


def is_missing(path: Path) -> bool:
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except (OSError, ValueError):
        return False
    return False


def names_commit(path: Path) -> bool:
    return bool(_with_regular(path, _stream_names_commit))


def runs_as_shell(path: Path) -> bool:
    head = _with_regular(path, lambda descriptor: _read_up_to(descriptor, HEAD_BYTES))
    if head is None:
        return False
    if not head.startswith(b"#!"):
        return b"\0" not in head
    return _interpreter(head) in SHELL_C_INTERPRETERS


def shebang_shell(path: Path) -> str:
    head = _with_regular(path, lambda descriptor: _read_up_to(descriptor, HEAD_BYTES))
    return _interpreter(head) if head is not None and head.startswith(b"#!") else ""


def _interpreter(head: bytes) -> str:
    words = [Path(word.decode("utf-8", errors="replace")).name for word in head[2:].split(b"\n", 1)[0].split()]
    if words[:1] == ["env"]:
        words = [word for word in words[1:] if not word.startswith("-")]
    return words[0] if words else ""


def _with_regular(path: Path, read: Callable[[int], Result]) -> Result | None:
    try:
        descriptor = os.open(os.path.realpath(path), OPEN_FLAGS)
    except (OSError, ValueError):
        return None
    try:
        return read(descriptor) if stat.S_ISREG(os.fstat(descriptor).st_mode) else None
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _read_up_to(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size < limit and (chunk := os.read(descriptor, min(CHUNK_BYTES, limit - size))):
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def _stream_names_commit(descriptor: int) -> bool:
    tail, read = b"", 0
    while chunk := os.read(descriptor, CHUNK_BYTES):
        if COMMIT in tail + chunk:
            return True
        tail, read = chunk[-(len(COMMIT) - 1):], read + len(chunk)
        if read >= MAX_SEARCH_BYTES:
            return True
    return False
