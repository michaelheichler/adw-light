from __future__ import annotations

import bisect
import re
from pathlib import PurePath
from typing import NamedTuple

try:
    from . import language_layer
    from .language_model import PYTHON
    from .language_shapes import DART_TEST_CALLS, JS_TEST_HOOKS, SCOPE_ROLES, Role, Shape, callee_parts
    from .test_units import JAVASCRIPT, RUST, Unit
except ImportError:
    import language_layer
    from language_model import PYTHON
    from language_shapes import DART_TEST_CALLS, JS_TEST_HOOKS, SCOPE_ROLES, Role, Shape, callee_parts
    from test_units import JAVASCRIPT, RUST, Unit

UNIT_LANGUAGES = {"rust": RUST, "javascript": JAVASCRIPT, "typescript": JAVASCRIPT, "tsx": JAVASCRIPT}
TEST_DIRS = frozenset({"test", "tests", "spec", "specs", "__tests__", "testing"})
CYPRESS_NAME_RE = re.compile(r"\.cy\.[cm]?[jt]sx?$", re.IGNORECASE)
TEST_STEM_RE = re.compile(r"(?:^|[_.-])(?:test|spec)s?(?:$|[_.-])|^test|[a-z0-9](?:Test|Tests|Spec|IT)$")
MARKER_RE = re.compile(
    r"@(?:[\w.]+\.)?(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b"
    r"|\[\s*(?:[\w.]+\.)?(?:Test|TestMethod|DataTestMethod|TestCase|Fact|Theory)\b"
    r"|#\[\s*(?:\w+::)*(?:test|Test)\b"
    r"|\b(?:TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P|TEST_CASE|TEST_CASE_METHOD|TEMPLATE_TEST_CASE|SCENARIO)\s*\("
)
GTEST_RE = re.compile(r"(?:TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P)\s*\(")
GO_TEST_RE = re.compile(r"Test(?:[A-Z0-9_]|$)")
NAMED_TEST_RE = re.compile(r"test(?:[A-Z0-9_]|$)")
TEST_CALLS = frozenset({"test", "it", "specify", "scenario"})
BATS_SUFFIX = ".bats"
BATS_TEST_RE = re.compile(r'''^[ \t]*@test[ \t]+(?P<name>"[^"\n]*"|'[^'\n]*'|\S+)[ \t]*\{[ \t]*$''', re.MULTILINE)
BATS_CLOSE_RE = re.compile(r"^\}[ \t]*$", re.MULTILINE)


class LayerUnits(NamedTuple):
    units: tuple[Unit, ...]
    broken: bool


def named_test_path(path: str) -> bool:
    pure = PurePath(path)
    folders = pure.parts[:-1]
    if any(part.lower() in TEST_DIRS or part.endswith("Test") for part in folders):
        return True
    stems = pure.name.split(".")[:-1] or [pure.name]
    return bool(CYPRESS_NAME_RE.search(pure.name)) or any(TEST_STEM_RE.search(stem) for stem in stems)


def layer_units(path: str, text: str) -> LayerUnits | None:
    language = language_layer.language_of(path)
    if language is None or language == PYTHON:
        return None
    named_tests = named_test_path(path) or path.lower().endswith(BATS_SUFFIX)
    if not named_tests and not MARKER_RE.search(text):
        return None
    outline = language_layer.outline_for(path, language, text)
    if outline is None:
        return None
    builder = _Builder(path, text, UNIT_LANGUAGES.get(language, language), outline.shapes, named_tests)
    return LayerUnits(tuple(builder.units()), outline.broken)


class _Builder:
    def __init__(self, path: str, text: str, language: str, shapes: tuple[Shape, ...], named_tests: bool) -> None:
        self.path = path
        self.text = text
        self.language = language
        self.shapes = shapes
        self.named_tests = named_tests
        self.scopes = {(shape.start, shape.end): shape for shape in shapes if shape.role in SCOPE_ROLES}
        self.newlines = [index for index, character in enumerate(text) if character == "\n"]
        self.starts = [shape.start for shape in shapes]

    def units(self):
        for shape in self.shapes:
            found = self.unit_scope(shape)
            if found is not None:
                yield self.unit(shape.start, *found)
        if self.path.lower().endswith(BATS_SUFFIX):
            yield from self.bats_units()

    def bats_units(self):
        for found in BATS_TEST_RE.finditer(self.text):
            closing = BATS_CLOSE_RE.search(self.text, found.end())
            end = closing.end() if closing is not None else len(self.text)
            scope = Shape(Role.FUNCTION, found.start(), end, found.group("name").strip("\"'"), found.end())
            yield self.unit(found.start(), scope.name, scope)

    def unit_scope(self, shape: Shape) -> tuple[str, Shape] | None:
        if shape.role is Role.FUNCTION and self.declared_unit(shape):
            return shape.name, shape
        if shape.role is not Role.CALL or not shape.parts or not self.named_tests:
            return None
        parts = callee_parts(shape.name)
        if self.language == JAVASCRIPT and JS_TEST_HOOKS.intersection(parts[1:]):
            return None
        scope = self.scopes.get(shape.parts[-1])
        test_calls = DART_TEST_CALLS if self.language == "dart" else TEST_CALLS
        if not parts or parts[0] not in test_calls or len(parts) > 2 or scope is None:
            return None
        return self.text[shape.start:scope.start].split(",", 1)[0].strip(), scope

    def declared_unit(self, shape: Shape) -> bool:
        if MARKER_RE.search(self.text, shape.start, shape.head) or GTEST_RE.match(shape.name):
            return True
        if not self.named_tests:
            return False
        pattern = GO_TEST_RE if self.path.endswith(".go") else NAMED_TEST_RE
        return bool(pattern.match(shape.name))

    def line(self, position: int) -> int:
        return bisect.bisect_left(self.newlines, position) + 1

    def unit(self, head: int, name: str, scope: Shape) -> Unit:
        line_start = self.text.rfind("\n", 0, head) + 1
        window = self.shapes[bisect.bisect_left(self.starts, scope.start):bisect.bisect_left(self.starts, scope.end)]
        inner = tuple(shape for shape in window if shape.end <= scope.end and shape is not scope)
        return Unit(
            self.path, name, self.line(line_start), self.line(max(scope.end - 1, line_start)),
            self.text[line_start:scope.end], self.language, self.text[line_start:scope.head],
            (scope, *inner), line_start,
        )
