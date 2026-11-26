from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

ABSENT = "absent"
FOLDER = "folder"
OTHER = "other"
UNVERIFIED = "unverified"
UNREADABLE = "unreadable"
LOCKED = "locked"
BLOB = "b:"
SHA = "s:"
SHA256_HEX = 64
MAX_HASH_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
MODE_FIELD = 5
MTIME_FIELD = 3
RACY_NS = 2_000_000_000


def stat_print(path: Path) -> list[int] | None:
    try:
        found = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return [found.st_dev, found.st_ino, found.st_size, found.st_mtime_ns, found.st_ctime_ns, found.st_mode]


def same_stat(before: list, now: list[int]) -> bool:
    return list(before[:MODE_FIELD + 1]) == now


def current_ref(path: Path, printed: list[int], like: str) -> str:
    mode = printed[MODE_FIELD]
    if stat.S_ISDIR(mode):
        return FOLDER
    if not stat.S_ISREG(mode) and not stat.S_ISLNK(mode):
        return OTHER
    if printed[2] > MAX_HASH_BYTES:
        return UNVERIFIED
    if like.startswith(BLOB):
        algorithm = "sha256" if len(like) - len(BLOB) == SHA256_HEX else "sha1"
        return BLOB + _digest(path, mode, algorithm, blob=True)
    return SHA + _digest(path, mode, "sha256", blob=False)


def text_ref(text: str, like: str) -> str:
    data = text.encode("utf-8")
    algorithm = "sha256" if len(like) - len(BLOB) == SHA256_HEX else "sha1"
    return BLOB + hashlib.new(algorithm, b"blob %d\0" % len(data) + data).hexdigest()


def matches(path: Path, ref: str) -> bool:
    if ref in {LOCKED, UNREADABLE, UNVERIFIED}:
        return False
    try:
        printed = stat_print(path)
        if ref == ABSENT or printed is None:
            return ref == ABSENT and printed is None
        return current_ref(path, printed, ref) == ref
    except OSError:
        return False


def change_of(path: Path, entry: list | None, started_ns: int) -> tuple[str, str, list[int] | None] | None:
    before = entry[-1] if entry else ABSENT
    try:
        printed = stat_print(path)
    except OSError:
        return before, UNREADABLE, None
    if printed is None:
        return None if before == ABSENT else (before, ABSENT, None)
    if entry and same_stat(entry, printed) and entry[MTIME_FIELD] < started_ns - RACY_NS:
        return None
    after = safe_ref(path, printed, before)
    return None if after == before else (before, after, printed)


def safe_ref(path: Path, printed: list[int], like: str) -> str:
    try:
        return current_ref(path, printed, like)
    except OSError:
        return UNREADABLE


def hash_cost(printed: list[int]) -> int:
    mode = printed[MODE_FIELD]
    return printed[2] if stat.S_ISREG(mode) or stat.S_ISLNK(mode) else 0


def _digest(path: Path, mode: int, algorithm: str, *, blob: bool) -> str:
    digest = hashlib.new(algorithm)
    chunks = [os.fsencode(os.readlink(path))] if stat.S_ISLNK(mode) else _regular_chunks(path)
    data = b"".join(chunks) if blob else None
    if data is not None:
        digest.update(b"blob %d\0" % len(data))
        digest.update(data)
        return digest.hexdigest()
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def _regular_chunks(path: Path) -> list[bytes]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f"not a regular file: {path}")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_HASH_BYTES:
                raise OSError(f"file grew past the hash cap: {path}")
            chunks.append(chunk)
        return chunks
    finally:
        os.close(descriptor)
