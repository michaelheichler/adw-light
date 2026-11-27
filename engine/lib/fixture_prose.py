from __future__ import annotations

import os
import posixpath
import re
import subprocess
from collections.abc import Callable, Iterable, Iterator
from functools import lru_cache
from pathlib import Path, PurePath, PurePosixPath
from typing import NamedTuple

try:
    from . import declared_tests, git_port, test_units, tests_policy
    from .regular_file import open_regular
except ImportError:
    import declared_tests
    import git_port
    import test_units
    import tests_policy
    from regular_file import open_regular

PROSE_SWITCH = "prose"
MAX_WALKED_ENTRIES = 20000
MAX_READ_FILES = 5000
MAX_REFERRER_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
SKIPPED_FOLDERS = frozenset({"node_modules", "vendor"})
TEST_DECLARING_EXTS = frozenset(declared_tests.COMPILED) | declared_tests.PYTHON_EXTS | declared_tests.RUST_EXTS
FAILURES = (OSError, UnicodeError, ValueError, subprocess.SubprocessError)


class SearchLimit(Exception):
    pass


class NotARepository(Exception):
    pass


class Source(NamedTuple):
    listing: tuple[str, ...]
    candidates: Callable[[str], Iterable[str]]
    read: Callable[[str], bytes]


class Reference(NamedTuple):
    path: str
    pattern: re.Pattern[str]
    unique_suffixes: frozenset[str]


def scan_config(cfg: dict, path: str | os.PathLike[str], root: str | os.PathLike[str]) -> dict:
    return _exempted(cfg, used_fixture(os.fspath(path), os.fspath(root), disk_source))


def index_scan_config(cfg: dict, path: str | os.PathLike[str], repo: str | os.PathLike[str]) -> dict:
    return _exempted(cfg, used_fixture(os.fspath(path), os.fspath(repo), index_source))


def _exempted(cfg: dict, used: bool) -> dict:
    return {**cfg, PROSE_SWITCH: False} if used else cfg


def used_fixture(path: str, root: str, source: Callable[[str], Source]) -> bool:
    try:
        base = os.path.abspath(root)
        fixture = _fixture_path(path, base)
        return fixture is not None and _referenced(fixture, source(base))
    except (*FAILURES, SearchLimit):
        return False


def _fixture_path(path: str, base: str) -> str | None:
    absolute = os.path.abspath(path if os.path.isabs(path) else os.path.join(base, path))
    relative = PurePath(os.path.relpath(absolute, base)).as_posix()
    if relative == ".." or relative.startswith("../"):
        return None
    return relative if tests_policy.in_fixture_dir(relative) else None


def _referenced(fixture: str, source: Source) -> bool:
    reference = _reference(fixture, source.listing)
    name = PurePosixPath(fixture).name
    return any(
        _names(reference, path, text) and _real_test(path, text)
        for path, text in _texts(source, name)
    )


def _reference(fixture: str, listing: Iterable[str]) -> Reference:
    name = PurePosixPath(fixture).name
    pattern = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*" + re.escape(name) + r")(?![\w/-]|\.\w)")
    return Reference(fixture, pattern, _unique_suffixes(fixture, listing))


def _unique_suffixes(fixture: str, listing: Iterable[str]) -> frozenset[str]:
    parts = PurePosixPath(fixture).parts
    others = [path for path in listing if path != fixture]
    suffixes = ("/".join(parts[-count:]) for count in range(1, len(parts)))
    return frozenset(
        suffix for suffix in suffixes
        if ("/" in suffix or PurePosixPath(suffix).suffix) and not any(_ends_with(other, suffix) for other in others)
    )


def _ends_with(path: str, suffix: str) -> bool:
    return path == suffix or path.endswith("/" + suffix)


def _names(reference: Reference, referrer: str, text: str) -> bool:
    if PurePosixPath(reference.path).name not in text:
        return False
    return any(_accepted(_plain(match[1]), referrer, reference) for match in reference.pattern.finditer(text))


def _plain(token: str) -> str:
    return re.sub(r"^(?:\./)+", "", token)


def _accepted(token: str, referrer: str, reference: Reference) -> bool:
    beside = posixpath.normpath(posixpath.join(posixpath.dirname(referrer), token))
    return reference.path in (token, beside) or token in reference.unique_suffixes


def _real_test(path: str, text: str) -> bool:
    if tests_policy.in_test_dir(path):
        return True
    return declared_tests.declares_tests(path, text) or bool(tests_policy.inline_test_bodies(path, text))


def _may_hold_tests(path: str) -> bool:
    suffix = PurePosixPath(path.lower()).suffix
    if suffix in tests_policy.DOC_EXTS or tests_policy.in_fixture_dir(path):
        return False
    return tests_policy.in_test_dir(path) or suffix in TEST_DECLARING_EXTS or test_units.language_of(path) is not None


def _texts(source: Source, name: str) -> Iterator[tuple[str, str]]:
    spent = 0
    for count, path in enumerate(source.candidates(name), start=1):
        if count > MAX_READ_FILES:
            raise SearchLimit("too many files to read for fixture references")
        try:
            raw = source.read(path)
        except FAILURES:
            continue
        spent += len(raw)
        if spent > MAX_TOTAL_BYTES:
            raise SearchLimit("fixture reference search read too many bytes")
        yield path, raw.decode("utf-8", errors="replace")


def _kept(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and not path.endswith("/") and not any(
        part.startswith(".") or part in SKIPPED_FOLDERS for part in parts[:-1]
    ) and not parts[-1].startswith(".")


def _readable(listing: Iterable[str]) -> list[str]:
    candidates = [path for path in listing if _may_hold_tests(path)]
    return sorted(candidates, key=lambda path: not tests_policy.in_test_dir(path))


def _index_file() -> Path | None:
    index = os.environ.get("GIT_INDEX_FILE")
    return Path(index) if index else None


def disk_source(root: str) -> Source:
    listing = _disk_listing(git_port.view(), root)
    return Source(listing, lambda name: _readable(listing), lambda path: _disk_bytes(root, path))


def index_source(repo: str) -> Source:
    listing = _index_listing(git_port.view(), repo, _index_file())
    return Source(listing, lambda name: _index_candidates(repo, listing, name), lambda path: _index_bytes(repo, path))


def _disk_bytes(root: str, path: str) -> bytes:
    with open_regular(os.path.join(root, path)) as handle:
        return handle.read(MAX_REFERRER_BYTES)


def _index_bytes(repo: str, path: str) -> bytes:
    return git_port.view().index_bytes(Path(repo), path, _index_file(), limit=MAX_REFERRER_BYTES)


def _index_candidates(repo: str, listing: tuple[str, ...], name: str) -> list[str]:
    hits = set(git_port.view().grep_index(Path(repo), name, _index_file()))
    return [path for path in _readable(listing) if path in hits]


@lru_cache(maxsize=4)
def _disk_listing(git_view: git_port.GitView, root: str) -> tuple[str, ...]:
    try:
        listing = git_view.disk_paths(Path(root))
    except NotARepository:
        return _walked(root)
    _check_entries(len(listing))
    return tuple(path for path in listing if _kept(path))


@lru_cache(maxsize=4)
def _index_listing(git_view: git_port.GitView, repo: str, index_file: Path | None) -> tuple[str, ...]:
    return tuple(path for path in git_view.index_paths(Path(repo), index_file) if _kept(path))


def _check_entries(entries: int) -> None:
    if entries > MAX_WALKED_ENTRIES:
        raise SearchLimit("too many files to look for fixture references")


def _walked(root: str) -> tuple[str, ...]:
    found: list[str] = []
    entries = 0
    for directory, folders, files in os.walk(root):
        folders[:] = [name for name in folders if _walkable(directory, name)]
        entries += len(folders) + len(files)
        _check_entries(entries)
        found.extend(PurePath(os.path.relpath(os.path.join(directory, name), root)).as_posix() for name in files)
    return tuple(path for path in found if _kept(path))


def _walkable(directory: str, name: str) -> bool:
    if name.startswith(".") or name in SKIPPED_FOLDERS:
        return False
    return not os.path.lexists(os.path.join(directory, name, ".git"))
