from __future__ import annotations

import fnmatch
import hashlib
import math
import os
import re
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:
    from . import declared_tests, git_port, ignored_configs, tests_policy
    from .commit_git_resolution import _located_verb
    from .file_kinds import CONFIG_BASENAMES
    from .imported_baseline import file_digest, text_digest
    from .path_policy import PROJECT_MARKERS
    from .regular_file import open_regular
    from .scan_input import TEXT_SUFFIXES
    from .shell_parse import _bare, line_segments
    from .shell_text import shell_tokens
    from .shell_syntax import _basename, _command_word_index
except ImportError:
    import declared_tests, git_port, ignored_configs, tests_policy
    from commit_git_resolution import _located_verb
    from file_kinds import CONFIG_BASENAMES
    from imported_baseline import file_digest, text_digest
    from path_policy import PROJECT_MARKERS
    from regular_file import open_regular
    from scan_input import TEXT_SUFFIXES
    from shell_parse import _bare, line_segments
    from shell_text import shell_tokens
    from shell_syntax import _basename, _command_word_index

MAX_DIFF = 200
MAX_WALK_FILES = 20_000
WALK_SECONDS = 0.3
MAX_TEXT_BYTES = 1_000_000
EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()
MAX_SOURCES_BYTES = 8_000_000
SKIP_FOLDERS = frozenset({"node_modules", ".venv", "venv", ".git", "__pycache__", ".tox", "dist", "build"})
TEST_SUFFIXES = declared_tests.COMPILED.keys() | declared_tests.PYTHON_EXTS | declared_tests.RUST_EXTS
ABSENT = "absent"
UNKNOWN = "unknown"
GIT_OPERATIONS = frozenset({"merge", "revert", "cherry-pick"})
REPLAYING = frozenset({"rebase", "pull", "am"})
WALK_SUFFIXES = TEXT_SUFFIXES | {"", ".docx", ".odt", ".pptx", ".xlsx"}


def _git_verbs(segment: list[str], cwd: Path) -> list[str]:
    words = list(map(_bare, segment))
    index = _command_word_index(words)
    if index >= len(words) or _basename(words[index]) != "git":
        return []
    args = words[index:]
    located = _located_verb(args, cwd)
    return args[located[1]:] if located else []


def _exempt_segment(segment: list[str], cwd: Path) -> bool:
    verbs = _git_verbs(segment, cwd)
    return bool(verbs and (verbs[0] in GIT_OPERATIONS or verbs[:2] == ["worktree", "add"]))


def replays_commits(command: str, cwd: Path) -> bool:
    segments = line_segments(command)
    verbs = _git_verbs(segments[-1], cwd) if segments else []
    return bool(verbs and verbs[0] in REPLAYING
                and all(len(segment) == 2 and _bare(segment[0]) == "cd" for segment in segments[:-1]))


def is_worktree_move(command: str, cwd: Path, root: Path, until: float) -> bool:
    segments = line_segments(command)
    if len(segments) != 1 or runs_in_background(command):
        return False
    words = list(map(_bare, segments[0]))
    index = _command_word_index(words)
    if index >= len(words) or _basename(words[index]) != "git":
        return False
    args = words[index:]
    located = _located_verb(args, cwd)
    if located is None or not _moves_to_commit(args[located[1]:]):
        return False
    return git_port.view().toplevel(located[0], until=until) == root


def _moves_to_commit(args: list[str]) -> bool:
    verb, *operands = args
    if verb == "reset":
        return operands[:1] == ["--hard"] and len(operands) <= 2 and "--" not in operands
    if verb == "switch":
        return not any(word in {"--", "-p", "--patch"} for word in operands)
    flags = {"-f", "--force", "-q", "--quiet", "--detach"}
    positionals = [word for word in operands if word not in flags]
    return verb == "checkout" and len(positionals) == 1 and (positionals[0] == "-" or not positionals[0].startswith("-"))


def exempt_command(command: str, cwd: Path) -> bool:
    segments = line_segments(command)
    return bool(segments and _exempt_segment(segments[-1], cwd)
                and all(len(segment) == 2 and _bare(segment[0]) == "cd" for segment in segments[:-1]))


def runs_in_background(command: str) -> bool:
    return any(match.lastgroup == "separator" and match.group() == "&"
               for token in shell_tokens(command) if (match := token.match) is not None)


def project_path(root: Path, raw: str) -> Path | None:
    relative = Path(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
        return None
    path = root / relative
    return path if path.parent.resolve().is_relative_to(root) else None


def stamp(path: Path) -> list[int]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return []
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        return []
    return [info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino]


def _git_marks(root: Path, until: float | None) -> dict | None:
    rows = git_port.view().porcelain_status(root, until=until)
    if rows is None:
        return None
    marks = {}
    for row in rows:
        if until is not None:
            check_deadline(until)
        raw = row[3:]
        path = project_path(root, raw)
        if path is not None:
            marks[raw] = [row[:2], stamp(path)]
    return marks


class WalkOverflow(RuntimeError):
    pass


def check_deadline(until: float) -> None:
    if time.monotonic() >= until:
        raise WalkOverflow


def project_location(cwd: Path, *, until: float | None = None,
                     markers: frozenset[str] = PROJECT_MARKERS) -> tuple[Path, bool] | None:
    home = Path.home().resolve()
    if cwd == home:
        return None
    git_root = git_port.view().toplevel(cwd, until=until)
    if git_root is not None:
        return (git_root.resolve(), True) if git_root.resolve() != home else None
    for directory in (cwd, *cwd.parents):
        if directory == home or directory == directory.parent:
            break
        if (directory / ".git").exists():
            raise OSError("Git project root unavailable")
        if any((directory / marker).is_file() for marker in markers):
            return directory, False
    return None


@lru_cache(maxsize=512)
def _ignore_pattern(pattern: str) -> re.Pattern[str]:
    parts = pattern.split("/")
    expression = ""
    for index, part in enumerate(parts):
        if part == "**" and index + 1 < len(parts):
            expression += "(?:[^/]+/)*"
        elif part == "**":
            expression += ".*"
        else:
            segment = fnmatch.translate(part)[4:-3]
            expression += segment.replace(".*", "[^/]*").replace("(?s:.)", "[^/]")
            if index + 1 < len(parts):
                expression += "/"
    return re.compile(expression)


def _read_ignores(directory: Path) -> list[tuple[Path, str, bool, bool, bool]]:
    path = directory / ".gitignore"
    if path.is_symlink():
        return []
    try:
        with open_regular(path) as stream:
            raw = stream.read(65_537)
    except FileNotFoundError:
        return []
    if len(raw) > 65_536:
        raise WalkOverflow
    rules = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[negate:]
        if pattern.startswith((r"\#", r"\!")):
            pattern = pattern[1:]
        rules.append((directory, pattern.strip("/"), pattern.endswith("/"),
                      pattern.startswith("/") or "/" in pattern.rstrip("/"), negate))
    return rules


def _ignored(path: Path, is_dir: bool, rules: list) -> bool:
    ignored = False
    for base, pattern, directory_only, anchored, negate in rules:
        if directory_only and not is_dir:
            continue
        probe = path.relative_to(base).as_posix() if anchored else path.name
        if _ignore_pattern(pattern).fullmatch(probe):
            ignored = not negate
    return ignored


def _walk_candidate(path: Path, raw: str, root: Path) -> bool:
    return (path.suffix.lower() in WALK_SUFFIXES or path.name.lower() in CONFIG_BASENAMES
            or tests_policy.is_test_path(raw, str(root)))


def _walk_marks(root: Path, until: float) -> dict:
    observed = {}
    remaining = MAX_WALK_FILES
    pending = [(root, [])]
    while pending:
        check_deadline(until)
        directory, inherited = pending.pop()
        rules = inherited + _read_ignores(directory)
        with os.scandir(directory) as children:
            for entry in children:
                check_deadline(until)
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SKIP_FOLDERS and not _ignored(path, True, rules) and not (path / ".git").exists():
                        pending.append((path, rules))
                    continue
                remaining -= 1
                if remaining < 0:
                    raise WalkOverflow
                if _ignored(path, False, rules):
                    continue
                raw = path.relative_to(root).as_posix()
                if (entry.is_symlink() or _walk_candidate(path, raw, root)) and (mark := stamp(path)):
                    observed[raw] = ["", mark]
    return observed


def hidden_marks(root: Path, listed: tuple[str, ...], observed: dict) -> None:
    for raw in listed:
        if (path := project_path(root, raw)) and (mark := stamp(path)):
            observed.setdefault(raw, [ignored_configs.IGNORED_MARK, mark])


def ignored_listing(root: Path, in_git: bool, record: object, until: float, *, search: bool = True,
                    refresh: bool = False, trust_cheap: bool = False) -> ignored_configs.Listing:
    until = min(until, time.monotonic() + ignored_configs.SEARCH_SECONDS)
    return ignored_configs.listing(root, in_git, record, until, skip=SKIP_FOLDERS, search=search, refresh=refresh,
                                   trust_cheap=trust_cheap)


def config_marks(root: Path, cwd: Path, observed: dict) -> None:
    for directory in (cwd, *cwd.parents):
        if not directory.is_relative_to(root):
            break
        path = directory / ".agent-discipline.json"
        observed[path.relative_to(root).as_posix()] = ["", stamp(path)]


def marks(root: Path, in_git: bool, *, until: float | None = None, bound_git: bool = False) -> dict | None:
    try:
        return _git_marks(root, until if bound_git else None) if in_git else _walk_marks(root, until or time.monotonic() + WALK_SECONDS)
    except WalkOverflow:
        raise
    except (OSError, RuntimeError, ValueError):
        return None


def digest(path: Path, *, until: float | None = None) -> str:
    try:
        mark = stamp(path)
        if not mark:
            return ABSENT
        if stat.S_ISLNK(mark[0]):
            return "link:" + text_digest(os.readlink(path))
        check = (lambda: check_deadline(until)) if until is not None else (lambda: None)
        return file_digest(path, check) or UNKNOWN
    except OSError:
        return UNKNOWN


def source_key(raw: str, mark: list) -> str:
    return str(mark) + "\n" + raw


def read_text(path: Path) -> str | None:
    if path.is_symlink() or (path.suffix.lower() not in TEST_SUFFIXES and path.name.casefold() != ".agent-discipline.json"):
        return None
    try:
        with open_regular(path) as stream:
            raw = stream.read(MAX_TEXT_BYTES + 1)
        if len(raw) <= MAX_TEXT_BYTES and b"\0" not in raw:
            return raw.decode("utf-8")
    except (OSError, UnicodeError):
        pass
    return None


def _marked_source(path: Path, observed: list[int], until: float | None) -> tuple[str, str | None]:
    if not observed:
        return ABSENT, None
    if stat.S_ISREG(observed[0]) and observed[1] == 0:
        return EMPTY_DIGEST, None
    return digest(path, until=until), read_text(path)


def capture_sources(root: Path, observed: dict, cached: dict, until: float) -> dict:
    additions = {}
    text_bytes = 0
    for raw, mark in observed.items():
        check_deadline(until)
        key = source_key(raw, mark)
        if key in cached:
            continue
        path = root / raw
        fingerprint, text = _marked_source(path, mark[1], until)
        text_bytes += len(text.encode("utf-8")) if text else 0
        if text_bytes > MAX_SOURCES_BYTES:
            raise WalkOverflow
        additions[key] = [fingerprint, text]
    return additions


def _capture_until(root: Path, chosen: dict, cached: dict, until: float, budget: float) -> tuple[dict, list[str]]:
    additions = {}
    text_bytes = 0
    for raw, mark in chosen.items():
        key = source_key(raw, mark)
        if key in cached:
            continue
        try:
            check_deadline(until)
            fingerprint, text = _marked_source(root / raw, mark[1], until)
        except WalkOverflow:
            return additions, [late for late, late_mark in chosen.items()
                               if source_key(late, late_mark) not in cached and source_key(late, late_mark) not in additions]
        text_bytes += len(text.encode("utf-8")) if text else 0
        if text_bytes > budget:
            raise WalkOverflow
        additions[key] = [fingerprint, text]
    return additions, []


def blob_source(content: bytes) -> tuple[str, str | None]:
    text = content.decode("utf-8", errors="replace") if len(content) <= MAX_TEXT_BYTES else None
    return hashlib.sha256(content).hexdigest(), text


def head_sources(root: Path, raws: list[str], until: float) -> tuple[dict[str, tuple[str, str | None]], set[str]]:
    if not raws:
        return {}, set()
    blobs = git_port.view().head_blobs(root, raws, until=until) if time.monotonic() < until else None
    if blobs is None:
        return ({}, set(raws)) if time.monotonic() >= until else (dict.fromkeys(raws, (UNKNOWN, None)), set())
    return {raw: blob_source(blobs[raw]) if raw in blobs else (UNKNOWN, None) for raw in raws}, set()


def previous_sources(compared: Comparison, raws: list[str]) -> tuple[dict[str, tuple[str, str | None]], set[str]]:
    snapshot, found, wanted = compared.before, {}, []
    for raw in raws:
        mark = snapshot["marks"].get(raw)
        current = compared.marks.get(raw, [""])
        if raw in compared.moved:
            found[raw] = compared.moved[raw]
        elif mark is not None:
            fingerprint, text = compared.sources.get(source_key(raw, mark), (UNKNOWN, None))
            found[raw] = (fingerprint, text)
        elif not snapshot["git"] or current[0] in {"??", ignored_configs.IGNORED_MARK} or current[0].startswith("A"):
            found[raw] = (ABSENT, None)
        else:
            wanted.append(raw)
    fetched, late = head_sources(compared.root, wanted, compared.until)
    return found | fetched, late


def changed(before: dict, after: dict) -> list[str]:
    return sorted(raw for raw in before.keys() | after.keys() if before.get(raw) != after.get(raw))


@dataclass(frozen=True, slots=True)
class Comparison:
    root: Path
    before: dict
    marks: dict
    paths: tuple[str, ...]
    sources: dict
    skipped: int = 0
    tested: frozenset[str] = frozenset()
    late: frozenset[str] = frozenset()
    until: float = math.inf
    unsearched: str = ""
    ignored: dict | None = None
    dropped: tuple[str, ...] = ()
    moved: dict = field(default_factory=dict)

    @property
    def overflow(self) -> bool:
        return self.skipped > 0

    def current(self, raw: str) -> tuple[str, str | None]:
        mark = self.marks.get(raw)
        if mark is None:
            path = project_path(self.root, raw)
            return (digest(path, until=self.until), read_text(path)) if path else (UNKNOWN, None)
        fingerprint, text = self.sources.get(source_key(raw, mark), (UNKNOWN, None))
        return fingerprint, text


def _tool_explains(raw: str, marks: dict, entry: dict, started: int) -> bool:
    return entry.get("at_ns", 0) > started and entry["mark"] == marks.get(raw)


def _unknown_before(raw: str, before: dict, observed: dict, snapshot: dict,
                    listed: ignored_configs.Listing | None) -> bool:
    prior, after = before.get(raw), observed.get(raw)
    if prior is None:
        return bool(after and after[0] == ignored_configs.IGNORED_MARK and snapshot.get("unsearched"))
    return (after is None and prior[0] == ignored_configs.IGNORED_MARK and listed is not None
            and not listed.complete and raw not in listed.paths)


def observed_marks(snapshot: dict, until: float) -> dict | None:
    return marks(Path(snapshot["root"]), snapshot["git"], until=min(time.monotonic() + WALK_SECONDS, until),
                 bound_git=snapshot.get("discover_nested") is False)


def compare(snapshot: dict, cached: dict, explained: dict, *, exempt: bool, until: float,
            capture: bool = True, record: object = None, refresh: bool = False,
            observe: Callable[[], dict | None] | None = None, head_moved: dict | None = None,
            refreshed: ignored_configs.Listing | None = None) -> Comparison:
    root = Path(snapshot["root"])
    try:
        observed = observe() if observe is not None else observed_marks(snapshot, until)
    except WalkOverflow:
        if not exempt:
            raise
        observed = {}
        snapshot = {**snapshot, "configs_only": True}
    if observed is None:
        raise OSError("Snapshot unavailable")
    listed = None
    if "cwd" in snapshot:
        refresh = refresh or any(Path(raw).name == ".gitignore" for raw in changed(snapshot["marks"], observed))
        listed = refreshed if refresh and refreshed is not None else ignored_listing(
            root, snapshot["git"], record, until, refresh=refresh, trust_cheap=True)
        hidden_marks(root, listed.paths, observed)
        config_marks(root, Path(snapshot["cwd"]), observed)
    unsearched = "" if listed is None or listed.complete else str(root)
    before = snapshot["marks"]
    if snapshot.get("configs_only"):
        before = {raw: mark for raw, mark in before.items() if seal_candidate(raw, before)}
        observed = {raw: mark for raw, mark in observed.items() if seal_candidate(raw, observed)}
    moved = changed(before, observed)
    dropped = tuple(raw for raw in moved if _unknown_before(raw, before, observed, snapshot, listed))
    paths = [raw for raw in moved if raw not in dropped
             and not _tool_explains(raw, observed, explained.get(raw, {}), snapshot.get("at_ns", 0))]
    head_moved = head_moved or {}
    paths += [raw for raw in head_moved if raw not in before and raw not in observed and raw not in paths]
    facts = {"unsearched": unsearched, "ignored": listed.record if listed else None, "dropped": dropped, "moved": head_moved}
    if not capture:
        return Comparison(root, snapshot, observed, tuple(paths), dict(cached), until=until, **facts)
    paths.sort(key=lambda raw: not seal_candidate(raw, observed))
    scanned = paths if exempt else paths[:MAX_DIFF]
    tested = [] if exempt else [raw for raw in paths[MAX_DIFF:] if _test_candidate(raw, root) or seal_candidate(raw, observed)]
    try:
        sources, late = _capture_until(root, _selected(observed, scanned), cached, until, MAX_SOURCES_BYTES)
    except WalkOverflow:
        scanned, sources, late = [], {}, []
        tested = [raw for raw in paths if seal_candidate(raw, observed) or (not exempt and _test_candidate(raw, root))]
    if exempt and late:
        tested = [raw for raw in late if seal_candidate(raw, observed)]
        scanned = [raw for raw in scanned if raw not in tested]
    tests, late_tests = _capture_until(root, _selected(observed, tested), cached | sources, until, math.inf)
    return Comparison(root, snapshot, observed, (*scanned, *tested), {**cached, **sources, **tests},
                      len(paths) - len(scanned), frozenset(tested), frozenset((*late, *late_tests)), until, **facts)


def seal_candidate(raw: str, observed: dict) -> bool:
    mark = observed.get(raw, ["", []])[1]
    return Path(raw).name.casefold() == ".agent-discipline.json" or bool(mark and stat.S_ISLNK(mark[0]))


def _test_candidate(raw: str, root: Path) -> bool:
    return Path(raw).suffix.lower() in TEST_SUFFIXES or tests_policy.is_test_path(raw, str(root))


def _selected(observed: dict, chosen: list[str]) -> dict:
    return {raw: observed[raw] for raw in chosen if raw in observed}
