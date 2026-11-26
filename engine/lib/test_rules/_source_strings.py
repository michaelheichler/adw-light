from __future__ import annotations

import ast
import functools
import hashlib
import html
import json
import os
import re
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

try:
    from ..config_roots import WORDING_INDEX_DIR, state_root
except ImportError:
    from config_roots import WORDING_INDEX_DIR, state_root

CACHE_DIR = WORDING_INDEX_DIR
CACHE_VERSION = 3
CACHE_MODE = 0o600
MAX_FILES = 4000
MAX_VISITED = 20_000
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 24_000_000
MAX_CACHE_BYTES = 8_000_000
MAX_CACHE_FILES = 16
PROJECT_MARKER = ".git"
SOURCE_DIRS = frozenset({"src", "lib", "app", "engine"})
SKIPPED_DIRS = frozenset({"node_modules", "__pycache__", "venv", "env", "dist", "build", "target"})
PYTHON_SUFFIX = ".py"
QUOTED_SUFFIXES = frozenset({
    ".sh", ".bash", ".zsh", ".fish", ".ksh", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".vue", ".svelte", ".html", ".htm", ".rb", ".go", ".rs", ".java", ".kt", ".kts", ".swift",
    ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".lua", ".pl", ".ps1", ".json", ".yaml",
    ".yml", ".toml", ".ini", ".cfg", ".mts", ".cts", ".po", ".pot", ".strings", ".stringsdict", ".arb", ".properties",
    ".ftl", ".xlf", ".xliff", ".resx", ".xml", ".lang", ".neon",
})
MARKUP_SUFFIXES = frozenset({
    ".html", ".htm", ".vue", ".svelte", ".jsx", ".tsx", ".xlf", ".xliff", ".resx", ".xml", ".stringsdict",
})
KEY_VALUE_SUFFIXES = frozenset({".yaml", ".yml", ".properties", ".ftl", ".ini", ".cfg", ".lang", ".neon"})
TEXT_NODE_RE = re.compile(r">([^<>]+)<")
PLAIN_VALUE_RE = re.compile(r"""^[ \t]*[-\w.$@"']+[ \t]*[:=][ \t]*(?P<value>[^\s"'#|>&*!{\[][^\n#]*?)[ \t]*$""", re.MULTILINE)
QUOTED_RE = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|`((?:[^`\\\n]|\\.)*)`')
SPACE_RE = re.compile(r"\s+")
Extracted = tuple[int, int, str]


class SourceString(NamedTuple):
    path: str
    line: int
    end: int
    text: str


def normalized(text: str) -> str:
    return SPACE_RE.sub(" ", text).strip().casefold()


def _spaced(text: str) -> bool:
    return " " in normalized(text)


def project_root(path: str) -> Path | None:
    start = Path(os.path.abspath(path)).parent
    return next((directory for directory in (start, *start.parents) if (directory / PROJECT_MARKER).exists()), None)


def _test_path_check() -> Callable[[str, str | None], bool]:
    try:
        from .. import tests_policy
    except ImportError:
        import tests_policy
    return tests_policy.is_test_path


def _python_strings(text: str) -> list[Extracted] | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    return [
        (node.lineno, node.end_lineno or node.lineno, normalized(node.value))
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _spaced(node.value)
    ]


def _quoted_strings(text: str) -> list[Extracted]:
    found: list[Extracted] = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in QUOTED_RE.finditer(line):
            value = next(group for group in match.groups() if group is not None)
            if _spaced(value):
                found.append((number, number, normalized(value)))
    return found


def _line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _text_nodes(text: str) -> list[Extracted]:
    found: list[Extracted] = []
    for match in TEXT_NODE_RE.finditer(text):
        value = html.unescape(match.group(1))
        if _spaced(value):
            start = match.start(1) + len(match.group(1)) - len(match.group(1).lstrip())
            end = match.start(1) + len(match.group(1).rstrip())
            found.append((_line_of(text, start), _line_of(text, end), normalized(value)))
    return found


def _plain_values(text: str) -> list[Extracted]:
    return [
        (_line_of(text, match.start("value")), _line_of(text, match.start("value")), normalized(match.group("value")))
        for match in PLAIN_VALUE_RE.finditer(text) if _spaced(match.group("value"))
    ]


def _indexed_kind(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix == PYTHON_SUFFIX or suffix in QUOTED_SUFFIXES or suffix in MARKUP_SUFFIXES or not suffix


def _strings_of(path: Path, text: str) -> list[Extracted]:
    suffix = path.suffix.lower()
    parsed = _python_strings(text) if suffix == PYTHON_SUFFIX else None
    if parsed is not None:
        return parsed
    found = _quoted_strings(text) if suffix or text.startswith("#!") else []
    if suffix in MARKUP_SUFFIXES:
        found.extend(_text_nodes(text))
    if suffix in KEY_VALUE_SUFFIXES:
        found.extend(_plain_values(text))
    return sorted(found)


def _regular(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    return info if stat.S_ISREG(info.st_mode) and info.st_size <= MAX_FILE_BYTES else None


def _read(path: Path) -> bytes | None:
    if _regular(path) is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _folder_order(name: str) -> tuple[bool, str]:
    return name not in SOURCE_DIRS, name


def _walk(root: Path) -> Iterator[Path]:
    visited = 0
    for directory, folders, files in os.walk(root):
        kept = (name for name in folders if not name.startswith(".") and name not in SKIPPED_DIRS)
        folders[:] = sorted(kept, key=_folder_order if Path(directory) == root else None)
        visited += len(folders) + len(files)
        yield from (Path(directory) / name for name in sorted(files))
        if visited >= MAX_VISITED:
            return


def _candidates(root: Path) -> list[Path]:
    is_test = _test_path_check()
    found: list[Path] = []
    for path in _walk(root):
        if _indexed_kind(path) and not is_test(str(path), str(root)):
            found.append(path)
        if len(found) >= MAX_FILES:
            break
    return found


def _cache_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:24]
    return state_root() / CACHE_DIR / (digest + ".json")


def _valid_item(item: object) -> bool:
    if type(item) is not list or len(item) != 3:
        return False
    return type(item[0]) is int and type(item[1]) is int and type(item[2]) is str


def _valid_entry(entry: object) -> bool:
    if type(entry) is not dict or type(entry.get("sha")) is not str or type(entry.get("strings")) is not list:
        return False
    if type(entry.get("size")) is not int or type(entry.get("mtime_ns")) is not int:
        return False
    return all(_valid_item(item) for item in entry["strings"])


def _load_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if type(data) is not dict or data.get("version") != CACHE_VERSION or type(data.get("files")) is not dict:
        return {}
    return data["files"]


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, CACHE_MODE)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def _prune_caches(folder: Path, kept: Path) -> None:
    others = [path for path in folder.glob("*.json") if path != kept]
    others.sort(key=lambda path: path.stat().st_mtime_ns)
    for stale in others[:max(len(others) - MAX_CACHE_FILES + 1, 0)]:
        stale.unlink(missing_ok=True)


def _encoded(files: dict, partial: bool) -> bytes:
    return json.dumps({"version": CACHE_VERSION, "partial": partial, "files": files}).encode("utf-8")


def _fitting(files: dict) -> bytes:
    data = _encoded(files, False)
    if len(data) <= MAX_CACHE_BYTES:
        return data
    kept: dict = {}
    size = len(_encoded({}, True))
    for name, entry in files.items():
        cost = len(json.dumps({name: entry})) + 2
        if size + cost > MAX_CACHE_BYTES:
            break
        kept[name], size = entry, size + cost
    return _encoded(kept, True)


def _store_cache(path: Path, files: dict) -> None:
    data = _fitting(files)
    if len(data) > MAX_CACHE_BYTES:
        return
    temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary.unlink(missing_ok=True)
        _write_private(temporary, data)
        os.replace(temporary, path)
        _prune_caches(path.parent, path)
    except OSError:
        temporary.unlink(missing_ok=True)


class Budget:
    __slots__ = ("left",)

    def __init__(self, left: int) -> None:
        self.left = left

    def spend(self, size: int) -> bool:
        if size > self.left:
            return False
        self.left -= size
        return True


def _unchanged(entry: object, info: os.stat_result) -> bool:
    return _valid_entry(entry) and entry["size"] == info.st_size and entry["mtime_ns"] == info.st_mtime_ns


def _fresh_entry(path: Path, raw: bytes, info: os.stat_result, cached: object) -> dict:
    stamp = {"size": info.st_size, "mtime_ns": info.st_mtime_ns}
    sha = hashlib.sha256(raw).hexdigest()
    if _valid_entry(cached) and cached["sha"] == sha:
        return {**cached, **stamp}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {**stamp, "sha": sha, "strings": []}
    return {**stamp, "sha": sha, "strings": [list(item) for item in _strings_of(path, text)]}


def _file_entry(path: Path, root: Path, cached: dict, budget: Budget) -> tuple[str, dict] | None:
    info = _regular(path)
    if info is None:
        return None
    relative = path.relative_to(root).as_posix()
    entry = cached.get(relative)
    if _unchanged(entry, info):
        return relative, entry
    raw = _read(path) if budget.spend(info.st_size) else None
    return None if raw is None else (relative, _fresh_entry(path, raw, info, entry))


@functools.lru_cache(maxsize=4)
def indexed_strings(root: str) -> tuple[SourceString, ...]:
    base = Path(root)
    cache = _cache_path(base)
    cached = _load_cache(cache)
    budget = Budget(MAX_TOTAL_BYTES)
    entries = (_file_entry(path, base, cached, budget) for path in _candidates(base))
    files = dict(entry for entry in entries if entry)
    if files != cached:
        _store_cache(cache, files)
    return tuple(
        SourceString(relative, line, end, text)
        for relative, entry in files.items() for line, end, text in entry["strings"]
    )


def _exact_line(match: SourceString, needle: str, root: Path) -> SourceString:
    raw = _read(root / match.path) if match.end > match.line else None
    if raw is None:
        return match
    lines = raw.decode("utf-8", "replace").splitlines()
    span = range(match.line, min(match.end, len(lines)) + 1)
    for probe in (needle, needle.split(" ")[0]):
        number = next((number for number in span if probe in normalized(lines[number - 1])), None)
        if number is not None:
            return match._replace(line=number, end=number)
    return match


def locate(expected: str, test_path: str) -> SourceString | None:
    root = project_root(test_path)
    if root is None:
        return None
    needle = normalized(expected)
    match = next((item for item in indexed_strings(str(root)) if needle in item.text), None)
    return None if match is None else _exact_line(match, needle, root)
