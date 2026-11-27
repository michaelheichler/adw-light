from __future__ import annotations

import bz2
import lzma
import zlib
from typing import BinaryIO

ODC_MAGIC = b"070707"
NEWC_MAGICS = (b"070701", b"070702")
ODC_HEADER = 76
NEWC_HEADER = 110
TRAILER = "TRAILER!!!"
GZIP_MAGIC = b"\x1f\x8b"
BZIP2_MAGIC = b"BZh"
XZ_MAGIC = b"\xfd7zXZ\x00"


def cpio_names(handle: BinaryIO, limit: int, cap: int) -> list[str] | None:
    data = _expanded(handle.read(limit + 1), limit)
    if data is None:
        return None
    names: list[str] = []
    offset = 0
    while True:
        entry = _entry(data, offset)
        if entry is None:
            return None
        name, offset = entry
        if name == TRAILER:
            return names
        if len(names) >= cap:
            return None
        names.append(name)


def _expanded(raw: bytes, limit: int) -> bytes | None:
    if len(raw) > limit:
        return None
    try:
        data = _decompressed(raw, limit)
    except (OSError, EOFError, zlib.error, lzma.LZMAError):
        return None
    return data if len(data) <= limit else None


def _decompressed(raw: bytes, limit: int) -> bytes:
    if raw.startswith(GZIP_MAGIC):
        return zlib.decompressobj(zlib.MAX_WBITS | 16).decompress(raw, limit + 1)
    if raw.startswith(BZIP2_MAGIC):
        return bz2.BZ2Decompressor().decompress(raw, limit + 1)
    if raw.startswith(XZ_MAGIC):
        return lzma.LZMADecompressor().decompress(raw, limit + 1)
    return raw


def _entry(data: bytes, offset: int) -> tuple[str, int] | None:
    magic = data[offset:offset + 6]
    if magic == ODC_MAGIC:
        header = data[offset:offset + ODC_HEADER]
        name_size, file_size = _number(header[59:65], 8), _number(header[65:76], 8)
        start, name_pad, data_pad = offset + ODC_HEADER, 1, 1
    elif magic in NEWC_MAGICS:
        header = data[offset:offset + NEWC_HEADER]
        name_size, file_size = _number(header[94:102], 16), _number(header[54:62], 16)
        start, name_pad, data_pad = offset + NEWC_HEADER, 4, 4
    else:
        return None
    if name_size is None or file_size is None or name_size < 1:
        return None
    name_end = start + name_size
    body = _aligned(name_end - offset, name_pad) + offset
    following = body + _aligned(file_size, data_pad)
    if following > len(data):
        return None
    return data[start:name_end - 1].decode("utf-8", errors="replace"), following


def _number(field: bytes, base: int) -> int | None:
    try:
        return int(field.decode("ascii"), base)
    except (UnicodeDecodeError, ValueError):
        return None


def _aligned(size: int, step: int) -> int:
    return (size + step - 1) // step * step
