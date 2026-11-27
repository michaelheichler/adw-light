from __future__ import annotations

import bisect
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from functools import lru_cache
from importlib import import_module, metadata
from pathlib import Path, PurePath
from types import ModuleType
from typing import Any, Protocol

try:
    from . import config_roots
    from .language_model import (
        PYTHON,
        CodeUnit,
        Kind,
        Layer,
        Node,
        UnitMark,
        comment_kind,
        lexer_layer,
        python_layer,
    )
    from .language_operands import OperandReader
    from .language_shapes import Outline, Role, Shape
    from .path_policy import real_path
except ImportError:
    import config_roots
    from language_model import (
        PYTHON,
        CodeUnit,
        Kind,
        Layer,
        Node,
        UnitMark,
        comment_kind,
        lexer_layer,
        python_layer,
    )
    from language_operands import OperandReader
    from language_shapes import Outline, Role, Shape
    from path_policy import real_path
try:
    from .tree_layer_walk import (
        BODY_SUFFIXES,
        CLOCK_STRIDE,
        CONSTRUCTOR_TYPES,
        EXPANSIONS,
        EXPECTED_FAILURE_RE,
        FUNCTION_TYPES,
        GO_TEST_NAME_RE,
        INTERPOLATION_MARKS,
        JS_TEST_CALLS,
        MARK_TYPES,
        NAME_TYPES,
        NAMING_PARENTS,
        NON_ASCII_RE,
        STRING_MARKS,
        TEST_MARK_RE,
        TEST_NAME_RE,
        _Offsets,
        _OverBudget,
        _TreeWalk,
        _container,
        _interpolation,
        _name_node,
        _test_call,
        _tree_layer,
    )
    from .outline_walk import (
        ARGUMENT_TYPES,
        ASSERT_TYPES,
        ATTRIBUTE_TYPES,
        BRANCH_TYPES,
        CALL_TYPES,
        CATCH_CASES,
        CLOSURE_SEARCH_DEPTH,
        CLOSURE_TYPES,
        LOOP_TYPES,
        LUA_CLOSURE_TYPES,
        NATIVE_LANGUAGES,
        OUTLINE_FUNCTION_TYPES,
        RUBY_CLOSURE_TYPES,
        WHITESPACE_RE,
        WRAPPER_TYPES,
        _OutlineWalk,
        _extra_closures,
    )
except ImportError:
    from tree_layer_walk import (
        BODY_SUFFIXES,
        CLOCK_STRIDE,
        CONSTRUCTOR_TYPES,
        EXPANSIONS,
        EXPECTED_FAILURE_RE,
        FUNCTION_TYPES,
        GO_TEST_NAME_RE,
        INTERPOLATION_MARKS,
        JS_TEST_CALLS,
        MARK_TYPES,
        NAME_TYPES,
        NAMING_PARENTS,
        NON_ASCII_RE,
        STRING_MARKS,
        TEST_MARK_RE,
        TEST_NAME_RE,
        _Offsets,
        _OverBudget,
        _TreeWalk,
        _container,
        _interpolation,
        _name_node,
        _test_call,
        _tree_layer,
    )
    from outline_walk import (
        ARGUMENT_TYPES,
        ASSERT_TYPES,
        ATTRIBUTE_TYPES,
        BRANCH_TYPES,
        CALL_TYPES,
        CATCH_CASES,
        CLOSURE_SEARCH_DEPTH,
        CLOSURE_TYPES,
        LOOP_TYPES,
        LUA_CLOSURE_TYPES,
        NATIVE_LANGUAGES,
        OUTLINE_FUNCTION_TYPES,
        RUBY_CLOSURE_TYPES,
        WHITESPACE_RE,
        WRAPPER_TYPES,
        _OutlineWalk,
        _extra_closures,
    )

PACK = "tree_sitter_language_pack"
REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"
FETCHER = Path(__file__).with_name("grammar_fetch.py")
FETCH_SECONDS = 3.0
PARSE_SECONDS = 1.5
PRECISE_CHARS = 262_144
RETRY_SECONDS = 300
LIBRARY_MISSING = "library_missing"
VERSION_MISMATCH = "version_mismatch"
DOWNLOAD_FAILED = "download_failed"
DOWNLOAD_PENDING = "download_pending"
PARSER_FAILED = "parser_failed"
SIZE_BUDGET = "size_budget"
TIME_BUDGET = "time_budget"
GRAMMARS = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift", ".rb": "ruby", ".php": "php", ".sh": "bash", ".bash": "bash",
    ".zsh": "bash", ".bats": "bash", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".cs": "csharp", ".scala": "scala",
    ".lua": "lua", ".dart": "dart", ".ex": "elixir", ".exs": "elixir",
}
PYTHON_SUFFIXES = frozenset({".py", ".pyi"})


def _fetch_timeout() -> float:
    try:
        seconds = float(os.environ.get("ADW_FETCH_SECONDS", ""))
    except ValueError:
        return FETCH_SECONDS
    return seconds if 0 < seconds < FETCH_SECONDS else FETCH_SECONDS


_FETCH_TIMEOUT = _fetch_timeout()


class _Parser(Protocol):
    def parse(self, source: bytes, /) -> Any: ...


_logged: set[tuple[str, str, str]] = set()


class _Budget:
    def __init__(self) -> None:
        self.chars = PRECISE_CHARS
        self.seconds = PARSE_SECONDS
        self.fetch_deadline: float | None = None
        self.charged: set[str] = set()

    def charge(self, text: str) -> bool:
        if not self.charged.isdisjoint((text, text + "\n", text.removesuffix("\n"))):
            return True
        if len(text) > self.chars:
            return False
        self.chars -= len(text)
        self.charged.add(text)
        return True

    def fetch_seconds(self) -> float:
        if self.fetch_deadline is None:
            self.fetch_deadline = time.monotonic() + _FETCH_TIMEOUT
        return max(0.0, self.fetch_deadline - time.monotonic())


_budget = _Budget()


@contextmanager
def own_budget(chars: int, deadline: float):
    global _budget
    saved = _budget
    _budget = _Budget()
    _budget.chars = chars
    _budget.seconds = max(0.0, min(PARSE_SECONDS, deadline - time.monotonic()))
    _budget.fetch_deadline = deadline
    try:
        yield
    finally:
        _budget = saved


def language_of(path: str) -> str | None:
    suffix = PurePath(path.lower()).suffix
    return PYTHON if suffix in PYTHON_SUFFIXES else GRAMMARS.get(suffix)


def pins() -> dict[str, str]:
    rows = (line.strip().partition("==") for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines())
    return {name.strip(): version.strip() for name, _, version in rows if version}


def layer_for(path: str, language: str, text: str) -> Layer:
    layer = parse(language, text, config_roots.grammar_cache_root())
    if not layer.precise:
        log_reduced_precision(path, language, layer.reason)
    return layer


@lru_cache(maxsize=8)
def parse(language: str, text: str, cache_root: Path) -> Layer:
    if language == PYTHON:
        return python_layer(text)
    parser, reason = _parser(language, cache_root)
    if parser is None:
        return lexer_layer(language, reason)
    return _budgeted_layer(language, text, cache_root)


def _budgeted_layer(language: str, text: str, cache_root: Path) -> Layer:
    if not _budget.charge(text):
        return lexer_layer(language, SIZE_BUDGET)
    if _budget.seconds <= 0:
        return lexer_layer(language, TIME_BUDGET)
    started = time.monotonic()
    try:
        return _tree_layer(language, text, _syntax_tree(language, text, cache_root), started + _budget.seconds)
    except _OverBudget:
        return lexer_layer(language, TIME_BUDGET)
    except Exception:
        return lexer_layer(language, PARSER_FAILED)
    finally:
        _budget.seconds -= time.monotonic() - started


def outline_for(path: str, language: str, text: str) -> Outline | None:
    if language == PYTHON:
        return None
    layer = layer_for(path, language, text)
    if not layer.precise:
        return None
    try:
        return Outline(_outline(language, text, config_roots.grammar_cache_root()), bool(layer.errors))
    except Exception:
        log_reduced_precision(path, language, PARSER_FAILED)
        return None


@lru_cache(maxsize=4)
def _syntax_tree(language: str, text: str, cache_root: Path) -> Any:
    parser, reason = _parser(language, cache_root)
    if parser is None:
        raise LookupError(reason)
    return parser.parse(text.encode("utf-8", "replace"))


@lru_cache(maxsize=4)
def _outline(language: str, text: str, cache_root: Path) -> tuple[Shape, ...]:
    walk = _OutlineWalk(language, text, _Offsets(text))
    walk.collect(_syntax_tree(language, text, cache_root).root_node)
    return tuple(sorted(walk.shapes, key=lambda shape: (shape.start, -shape.end)))


def log_reduced_precision(path: str, language: str, reason: str) -> None:
    key = (str(real_path(path)), language, reason)
    if key in _logged:
        return
    _logged.add(key)
    try:
        reporting = import_module(__package__ + ".reporting" if __package__ else "reporting")
        reporting.append_row({
            "ts": reporting.now_iso(), "hook": "language_layer", "event": "reduced_precision",
            "path": path, "language": language, "reason": reason,
        })
    except Exception as error:
        print(f"ADW Lite scanned {path} with the fallback lexer ({reason}): {error}", file=sys.stderr)


@lru_cache(maxsize=64)
def _parser(language: str, cache_root: Path) -> tuple[_Parser | None, str]:
    try:
        return _load_parser(language, cache_root)
    except Exception:
        return None, PARSER_FAILED


def _load_parser(language: str, cache_root: Path) -> tuple[_Parser | None, str]:
    pack = _pack()
    if pack is None:
        return None, LIBRARY_MISSING
    if not _pinned():
        return None, VERSION_MISMATCH
    reason = _grammar_state(pack, cache_root, language)
    return (pack.get_parser(language), "") if not reason else (None, reason)


def _pack() -> ModuleType | None:
    try:
        return import_module(PACK)
    except ImportError:
        pass
    site = _environment_site()
    if site is None:
        return None
    sys.path.append(str(site))
    try:
        return import_module(PACK)
    except ImportError:
        return None


def _environment_site() -> Path | None:
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = config_roots.language_environment() / "lib" / version / "site-packages"
    return site if (site / PACK).is_dir() else None


def _pinned() -> bool:
    return all(_installed_version(name) == version for name, version in pins().items())


def _installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        pass
    site = _environment_site()
    origin = getattr(sys.modules.get(PACK), "__file__", None)
    if site is None or origin is None or Path(origin).resolve().parents[1] != site.resolve():
        return None
    found = next(iter(metadata.distributions(name=name, path=[str(site)])), None)
    return found.version if found else None


def _grammar_state(pack: ModuleType, cache: Path, language: str) -> str:
    try:
        pack.configure(pack.PackConfig(cache_dir=str(cache)))
        if language in pack.downloaded_languages():
            return ""
    except Exception:
        return DOWNLOAD_FAILED
    marker = cache / "failed" / language
    if _recent(marker):
        return DOWNLOAD_FAILED
    _mark(marker)
    reason = _fetch(pack, cache, language)
    if not reason and language in pack.downloaded_languages():
        _clear(marker)
        return ""
    return reason or DOWNLOAD_FAILED


def _recent(marker: Path) -> bool:
    try:
        return time.time() - marker.stat().st_mtime < RETRY_SECONDS
    except OSError:
        return False


def _clear(marker: Path) -> None:
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        return


def _mark(marker: Path) -> None:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        return


def _fetch(pack: ModuleType, cache: Path, language: str) -> str:
    origin = pack.__file__
    if origin is None:
        raise TypeError(PACK)
    site = Path(origin).resolve().parents[1]
    command = [sys.executable, "-I", "-S", "-B", str(FETCHER), str(cache), language, str(site)]
    try:
        child = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return "" if child.wait(timeout=_budget.fetch_seconds()) == 0 else DOWNLOAD_FAILED
    except subprocess.TimeoutExpired:
        return DOWNLOAD_PENDING
    except OSError:
        return DOWNLOAD_FAILED


