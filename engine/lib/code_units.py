from __future__ import annotations

import bisect
import re
from functools import lru_cache
from typing import NamedTuple

try:
    from .language_layer import language_of, layer_for
    from .language_model import PYTHON, CodeUnit, UnitMark
    from .language_shapes import MACRO_ASSERTION_CALL_RE
    from .language_views import code_view
    from .tests_policy import is_test_path
except ImportError:
    from language_layer import language_of, layer_for
    from language_model import PYTHON, CodeUnit, UnitMark
    from language_shapes import MACRO_ASSERTION_CALL_RE
    from language_views import code_view
    from tests_policy import is_test_path

ASSERTION_RE = re.compile(
    r"\b(?:assert|refute|expect|verify)\w*|\bshould\w*|\b(?:must|wont)_\w+|\bXCT(?:Assert|Fail|Unwrap)\w*|\.to\b"
    r"|\b[tbf]\.(?:Errorf?|Fatalf?|Fail(?:Now)?)\b|\b(?:require|Assert|Expect)\.\w+|\bfail\w*\s*\("
    r"|\braises\(|\bwarns\(|#(?:expect|require)\b",
    re.IGNORECASE,
)
SUITE_ROOT = "suite"
GO_RECEIVER_RE = re.compile(r"\bfunc\s*\(\s*(\w+)\s+\*?[\w.\[\]]+\s*\)\s*$")
SUITE_ASSERTIONS = (
    "Equal", "NotEqual", "Exactly", "Same", "NotSame", "NoError", "Error", "True", "False", "Nil", "NotNil",
    "Empty", "NotEmpty", "Zero", "NotZero", "Len", "Contains", "NotContains", "Subset", "NotSubset",
    "ElementsMatch", "Greater", "Less", "Positive", "Negative", "InDelta", "InEpsilon", "Panics", "NotPanics",
    "Eventually", "Never", "Implements", "IsType", "Regexp", "NotRegexp", "JSONEq", "YAMLEq", "FileExists",
    "NoFileExists", "DirExists", "NoDirExists", "WithinDuration", "Condition", "Fail", "FailNow",
)


class UnitRow(NamedTuple):
    line: int
    name: str
    end: int


class UnitScan(NamedTuple):
    partial: bool
    functions: tuple[UnitRow, ...]
    hollow: tuple[UnitRow, ...]
    errors: tuple[tuple[int, int], ...] = ()


@lru_cache(maxsize=4)
def unit_scan(path: str, text: str) -> UnitScan | None:
    language = language_of(path)
    if language is None or language == PYTHON:
        return None
    layer = layer_for(path, language, text)
    if not layer.precise:
        return None
    code = code_view(text, layer)
    newlines = [index for index, character in enumerate(text) if character == "\n"]
    rows = {unit: _row(unit, newlines) for unit in layer.units}
    marks = {UnitMark.MARKED, UnitMark.NAMED} if is_test_path(path) else {UnitMark.MARKED}
    hollow = tuple(
        row for unit, row in rows.items() if unit.test in marks and not _asserts(unit, text, code, language)
    )
    errors = tuple(
        (bisect.bisect_left(newlines, node.start) + 1, bisect.bisect_left(newlines, max(node.end - 1, node.start)) + 1)
        for node in layer.errors
    )
    return UnitScan(bool(layer.errors), tuple(rows.values()), hollow, errors)


def long_rows(scan: UnitScan, limit: int) -> list[tuple[int, str, int]]:
    return [(row.line, row.name, row.end) for row in scan.functions if row.end - row.line + 1 > limit]


def merged_rows(primary: list[tuple[int, str, int]], extra: list[tuple[int, str, int]]) -> list[tuple[int, str, int]]:
    seen = {row[0] for row in primary}
    return sorted(primary + [row for row in extra if row[0] not in seen])


def _asserts(unit: CodeUnit, text: str, code: str, language: str) -> bool:
    if unit.expects_failure or ASSERTION_RE.search(code, unit.body, unit.end):
        return True
    if MACRO_ASSERTION_CALL_RE.search(code, unit.body, unit.end):
        return True
    return language == "go" and bool(_suite_assertion_re(text, unit.start).search(code, unit.body, unit.end))


def _suite_assertion_re(text: str, start: int) -> re.Pattern[str]:
    receiver = GO_RECEIVER_RE.search(text, text.rfind("\n", 0, start) + 1, start)
    roots = {SUITE_ROOT} | ({receiver.group(1)} if receiver else set())
    names = "|".join(SUITE_ASSERTIONS)
    return re.compile(
        rf"\b(?:{'|'.join(map(re.escape, sorted(roots)))})\.(?:(?:Require|Assert)\(\)|T\(\)\.(?:Error|Fatal|Fail)"
        rf"|(?:{names})\w*\(\s*[^\s)])"
    )


def _row(unit: CodeUnit, newlines: list[int]) -> UnitRow:
    first = bisect.bisect_left(newlines, unit.start) + 1
    last = bisect.bisect_left(newlines, max(unit.end - 1, unit.start)) + 1
    return UnitRow(first, unit.name, last)
