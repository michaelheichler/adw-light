from __future__ import annotations

import os
import re
from pathlib import Path

from lib import git_port
from lib.archive_commands import stdin_source, tar_read, unzip_read
from lib.archive_members import ArchiveRead, landed_paths
from lib.captured_output import captured_outputs
from lib.flag_outputs import FLAG_WRITERS, flag_targets
from lib.shell_parse import _write_path_writes
from lib.shell_syntax import _bare, _basename, _command_word_index, _is_literal_token, _segments, _without_redirects
from lib.temp_copies import source_key
from lib.write_targets import _tar_extracts, _tar_to_stdout, _unzip_reads

DOWNLOAD_VERBS = frozenset({"curl", "wget"})
MAX_CLONE_FILES = 2_000
UNSAFE_NAME_CHARS = frozenset("$`*?[~{")
REMOTE_URL_RE = re.compile(r"^(?:https?|ftp)://", re.IGNORECASE)
REMOTE_REPO_RE = re.compile(r"^(?:(?:https?|ssh|git)://|[\w.-]+@[\w.-]+:)", re.IGNORECASE)
LOCAL_URL_RE = re.compile(r"^file:", re.IGNORECASE)
CLONE_VALUE_FLAGS = frozenset({
    "-b", "--branch", "--depth", "-o", "--origin", "-c", "--config", "--reference", "--separate-git-dir",
    "-j", "--jobs", "--filter", "--template", "-u", "--upload-pack",
})
Cwd = str | os.PathLike[str] | None


def external_landings(command: str, cwd: Cwd, authored: frozenset[str]) -> list[str]:
    base = Path(cwd or ".").absolute()
    found: list[str] = []
    for segment in _segments(command):
        found.extend(_segment_landings(segment, base, authored))
    found.extend(path for path in captured_outputs(command, base) if source_key(path, base) not in authored)
    present = [path for path in dict.fromkeys(found) if Path(path).is_file()]
    return ignored_paths(present, base)


def ignored_paths(paths: list[str], base: Path) -> list[str]:
    if not paths:
        return []
    ignored = git_port.view().ignored_paths(base, paths)
    return [path for path in paths if path in ignored]


def _segment_landings(segment: list[str], base: Path, authored: frozenset[str]) -> list[str]:
    index = _command_word_index(segment)
    verb = _basename(segment[index]) if index < len(segment) else ""
    args = segment[index + 1:]
    if verb in DOWNLOAD_VERBS:
        return _downloads(verb, segment, base)
    if verb in {"tar", "unzip"}:
        read = _archive_read(verb, args)
        return [] if read is None or source_key(read.source, base) in authored else _extracted(read, base)
    if verb == "git" and [_bare(word) for word in args[:1]] == ["clone"]:
        return _cloned([_bare(word) for word in args[1:]], base)
    return []


def _downloads(verb: str, segment: list[str], base: Path) -> list[str]:
    index = _command_word_index(segment)
    words = _without_redirects(segment[index + 1:])
    bare = [_bare(word) for word in words]
    if any(LOCAL_URL_RE.match(word) for word in bare) or not any(REMOTE_URL_RE.match(word) for word in bare):
        return []
    flagged = flag_targets(verb, words, lambda word: _bare(word) if _is_literal_token(word) else None) if verb in FLAG_WRITERS else None
    named = list(flagged.paths) if flagged is not None else []
    redirected = [path for path, _append in _write_path_writes(segment) if not UNSAFE_NAME_CHARS.intersection(path)]
    return [str(base / path) for path in named + redirected if path]


def _archive_read(verb: str, args: list[str]) -> ArchiveRead | None:
    words = [_bare(token) for token in _without_redirects(args)]
    if verb == "tar":
        return tar_read(words, stdin_source(args)) if _tar_extracts(words) and not _tar_to_stdout(words) else None
    return None if _unzip_reads(words) else unzip_read(words)


def _extracted(read: ArchiveRead, base: Path) -> list[str]:
    landed = landed_paths(read, base)
    return [] if landed is None else [str(base / path) for path in landed]


def _cloned(words: list[str], base: Path) -> list[str]:
    positional = _clone_operands(words)
    if not positional or not REMOTE_REPO_RE.match(positional[0]):
        return []
    target = positional[1] if len(positional) > 1 else Path(positional[0].rstrip("/")).name.removesuffix(".git")
    root = base / target
    files = [path for path in root.rglob("*") if path.is_file() and ".git" not in path.relative_to(root).parts] if root.is_dir() else []
    return [] if len(files) > MAX_CLONE_FILES else [str(path) for path in files]


def _clone_operands(words: list[str]) -> list[str]:
    operands: list[str] = []
    position = 0
    while position < len(words):
        word = words[position]
        position += 2 if word in CLONE_VALUE_FLAGS else 1
        operands.extend([] if word.startswith("-") else [word])
    return operands
