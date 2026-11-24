from __future__ import annotations

import os
import stat
from functools import lru_cache
from pathlib import Path

try:
    from . import config_roots as config_paths
except ImportError:
    import config_roots as config_paths

AGENT_FILE_SUFFIXES = frozenset({".md", ".toml"})
CLIENT_HOME_DIRS = frozenset({".claude", ".codex", ".pi", ".omp", ".agents", ".config", ".local"})
WIRING_FILES = frozenset({
    (".claude", "settings.json"),
    (".claude", "settings.local.json"),
    (".codex", "config.toml"),
    (".codex", "hooks.json"),
    (".pi", "agent", "settings.json"),
    (".omp", "agent", "settings.json"),
})


def _existing_metadata(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None


def _identity_stamp(home: Path) -> tuple[tuple[int, ...] | None, ...]:
    stamps = []
    roots = (*config_paths.protected_roots(home), *(home.joinpath(*parts) for parts in WIRING_FILES))
    for root in roots:
        metadata = _existing_metadata(root)
        stamps.append(None if metadata is None else (
            metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_ctime_ns,
        ))
    return tuple(stamps)


@lru_cache(maxsize=16)
def _protected_identities(home: Path, stamp: tuple) -> dict[tuple[int, int], tuple[Path, ...]]:
    del stamp
    identities: dict[tuple[int, int], tuple[Path, ...]] = {}
    for candidate in _protected_candidates(home):
        metadata = _existing_metadata(candidate)
        if metadata is None or metadata.st_nlink < 2 or not stat.S_ISREG(metadata.st_mode):
            continue
        identity = (metadata.st_dev, metadata.st_ino)
        identities[identity] = (*identities.get(identity, ()), candidate)
    return identities


def _protected_candidates(home: Path):
    yield from (home.joinpath(*parts) for parts in WIRING_FILES)
    for client in CLIENT_HOME_DIRS:
        for directory in (home / client / "agents", home / client / "agent" / "agents"):
            yield from _agent_candidates(directory)
    for root in config_paths.protected_roots(home):
        yield from _owned_candidates(root)


def _agent_candidates(directory: Path):
    try:
        entries = tuple(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.suffix.lower() in AGENT_FILE_SUFFIXES and not entry.is_symlink():
            yield entry


def _owned_candidates(root: Path):
    metadata = _existing_metadata(root)
    if metadata is None:
        return
    if stat.S_ISREG(metadata.st_mode):
        yield root
        return
    for directory, children, files in os.walk(root, followlinks=False, onerror=_walk_error):
        parent = Path(directory)
        children[:] = [name for name in children if not (parent / name).is_symlink()]
        for name in files:
            candidate = parent / name
            if not candidate.is_symlink():
                yield candidate


def _walk_error(error: OSError) -> None:
    if not isinstance(error, (FileNotFoundError, NotADirectoryError)):
        raise error
