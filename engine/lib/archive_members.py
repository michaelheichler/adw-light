from __future__ import annotations

import fnmatch
import os
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NamedTuple

try:
    from .cpio_members import cpio_names
    from .regular_file import open_regular
except ImportError:
    from cpio_members import cpio_names
    from regular_file import open_regular

MAX_MEMBERS = 20_000
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_PATCH_BYTES = 4 * 1024 * 1024
TAR, ZIP, PATCH, CPIO = "tar", "zip", "patch", "cpio"
PATCH_HEADERS = ("--- ", "+++ ")
GIT_CONFIG_HEADERS = PATCH_HEADERS + ("diff --git ", "rename from ", "rename to ", "copy from ", "copy to ")
PATCH_HAZARDS = ("rename from ", "rename to ", "copy from ", "copy to ", "GIT binary patch", "Binary files ")
DEV_NULL = "/dev/null"


class ArchiveRead(NamedTuple):
    kind: str
    source: str
    directory: str = ""
    strip: int = 0
    patterns: tuple[str, ...] = ()
    junk_paths: bool = False
    absolute: bool = False


def landed_paths(read: ArchiveRead, folder: Path) -> list[str] | None:
    names = _names(read, folder / read.source)
    if names is None:
        return None
    landed: list[str] = []
    for name in names:
        placed = _placed(read, name)
        if placed is None:
            return None
        landed.extend(os.path.normpath(os.path.join(read.directory, path)) for path in placed)
    return landed


def config_member_paths(read: ArchiveRead, folder: Path, basename: str) -> list[str]:
    landing = folder / read.directory
    try:
        with _open_config_source(read, folder) as handle:
            limit = MAX_PATCH_BYTES if read.kind == PATCH else MAX_ARCHIVE_BYTES
            if os.fstat(handle.fileno()).st_size > limit:
                return []
            if read.kind == PATCH:
                patch = handle.read(limit + 1)
                return [str(landing / basename)] if len(patch) <= limit and _patch_has_config(patch, basename) else []
            names = _selected_names(read, _config_archive_names(read.kind, handle))
    except (OSError, tarfile.TarError, zipfile.BadZipFile, EOFError, ValueError):
        return []
    return list(dict.fromkeys(path for name in names or () for path in _config_landings(read, landing, name, basename)))


def _config_landings(read: ArchiveRead, landing: Path, name: str, basename: str) -> list[str]:
    parts = PurePosixPath(name).parts
    if not parts or parts[-1].casefold() != basename:
        return []
    if read.junk_paths:
        return [str(landing / parts[-1])]
    if read.absolute and name.startswith("/"):
        return [os.path.normpath(name)]
    relative = [part for part in parts if part != "/"][read.strip:]
    if not relative:
        return []
    kept = [part for part in relative if part != ".."]
    return [os.path.normpath(os.path.join(landing, *relative)), str(landing.joinpath(*kept))]


def _open_config_source(read: ArchiveRead, folder: Path) -> BinaryIO:
    source = folder / read.source
    try:
        return open_regular(source)
    except FileNotFoundError:
        if read.kind != ZIP:
            raise
        return open_regular(f"{source}.zip")


def _config_archive_names(kind: str, handle: BinaryIO) -> list[str] | None:
    if kind == TAR:
        with tarfile.open(fileobj=handle, mode="r:*") as archive:
            return _plain_members(archive, allow_special=True)
    if kind == CPIO:
        return cpio_names(handle, MAX_ARCHIVE_BYTES, MAX_MEMBERS)
    with zipfile.ZipFile(handle) as archive:
        members = archive.infolist()
        return [member.filename for member in members] if len(members) <= MAX_MEMBERS else None


def _patch_has_config(patch: bytes, basename: str) -> bool:
    for line in patch.decode("utf-8", errors="replace").splitlines():
        prefix = next((header for header in GIT_CONFIG_HEADERS if line.startswith(header)), "")
        if not prefix:
            continue
        if Path(line[len(prefix):].partition("\t")[0].strip().strip('"')).name.casefold() == basename:
            return True
    return False


def _names(read: ArchiveRead, source: Path) -> list[str] | None:
    try:
        size = source.stat().st_size
    except OSError:
        return None
    if read.kind == PATCH:
        return _patch_names(source) if size <= MAX_PATCH_BYTES else None
    if size > MAX_ARCHIVE_BYTES:
        return None
    names = _tar_names(source) if read.kind == TAR else _zip_names(source)
    return _selected_names(read, names)


def _selected_names(read: ArchiveRead, names: list[str] | None) -> list[str] | None:
    if names is None or not read.patterns:
        return names
    if read.kind == TAR:
        return [name for name in names if _tar_selects(name, read.patterns)]
    return [name for name in names if any(fnmatch.fnmatchcase(name, pattern) for pattern in read.patterns)]


def _tar_selects(name: str, operands: tuple[str, ...]) -> bool:
    parts = PurePosixPath(_trimmed(name)).parts
    spellings = ["/".join(parts[:end]) for end in range(1, len(parts) + 1)]
    wanted = [_trimmed(operand) for operand in operands]
    return any(spelling == operand or fnmatch.fnmatchcase(spelling, operand) for spelling in spellings for operand in wanted)


def _trimmed(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name.rstrip("/") or name


def _placed(read: ArchiveRead, name: str) -> list[str] | None:
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or ".." in parts:
        return None
    if read.kind == PATCH:
        return ["/".join(parts[index:]) for index in range(len(parts))]
    if read.junk_paths:
        return [parts[-1]]
    kept = parts[read.strip:]
    return ["/".join(kept)] if kept else []


def _tar_names(source: Path) -> list[str] | None:
    try:
        with tarfile.open(source, "r:*") as archive:
            return _plain_members(archive)
    except (OSError, tarfile.TarError, EOFError, ValueError):
        return None


def _plain_members(archive: tarfile.TarFile, *, allow_special: bool = False) -> list[str] | None:
    names: list[str] = []
    for member in archive:
        if len(names) >= MAX_MEMBERS or (not allow_special and not (member.isfile() or member.isdir())):
            return None
        names.append(member.name)
    return names


def _zip_names(source: Path) -> list[str] | None:
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile, ValueError):
        return None
    if len(members) > MAX_MEMBERS or any(stat.S_ISLNK(member.external_attr >> 16) for member in members):
        return None
    return [member.filename for member in members]


def _patch_names(source: Path) -> list[str] | None:
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if any(line.startswith(PATCH_HAZARDS) for line in lines):
        return None
    names = [_header_path(line) for line in lines if line.startswith(PATCH_HEADERS)]
    found = [name for name in names if name and name != DEV_NULL]
    return found if found and len(found) <= MAX_MEMBERS else None


def _header_path(line: str) -> str:
    return line[4:].split("\t", 1)[0].strip()
