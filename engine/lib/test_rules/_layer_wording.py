from __future__ import annotations

import bisect
import re
from collections import Counter
from collections.abc import Iterator
from functools import lru_cache
from typing import NamedTuple

try:
    from ..language_operands import bound_name
    from ..language_shapes import COMPUTED, Operand, Role, Shape, callee_parts, is_assertion
    from ..test_units import Unit
    from ._captures import EXCEPTION, LOG
    from ._expectations import ASSERTED, PLACEHOLDER, Expected, regex_words
    from ._frameworks import Order, Row, Skip, framework_row
    from ._lexed_calls import lexed_bindings, lexed_shapes
    from ._text_function import near_copy, shares_words
    from ._ui_locators import is_locator, selector_text, selects
except ImportError:
    from language_operands import bound_name
    from language_shapes import COMPUTED, Operand, Role, Shape, callee_parts, is_assertion
    from test_units import Unit
    from test_rules._captures import EXCEPTION, LOG
    from test_rules._expectations import ASSERTED, PLACEHOLDER, Expected, regex_words
    from test_rules._frameworks import Order, Row, Skip, framework_row
    from test_rules._lexed_calls import lexed_bindings, lexed_shapes
    from test_rules._text_function import near_copy, shares_words
    from test_rules._ui_locators import is_locator, selector_text, selects

LOCATOR = "locator"
TESTING_HANDLE_RE = re.compile(r"^(?:t|b|tb|f|\w+\.T\(\))$")
JUPITER = "org.junit.jupiter"
MESSAGE_RE = re.compile(r"getMessage\(\)|getLocalizedMessage\(\)|\.[Mm]essage\b|\berr(?:or)?\.Error\(\)")
LOG_RE = re.compile(r"(?i)\b(?:caplog|logs?|logger|log_?output|log_?lines|log_?records)\b|\bconsole\.(?:log|warn|error)\b")
FAILURE_RE = re.compile(r"\b(?:throw|fail|panic|flunk)\b")
FLAG_RE = re.compile(r"^--?[A-Za-z][\w-]*$")
RUNNER_NAMES = frozenset({
    "run", "_", "value", "expect", "it", "test", "describe", "context", "beforeEach", "afterEach",
    "TEST_CASE", "TEST_CASE_METHOD", "TEMPLATE_TEST_CASE", "SCENARIO", "SECTION", "GIVEN", "WHEN", "THEN",
})
CHECK_MACROS = frozenset({"REQUIRE", "CHECK", "REQUIRE_FALSE", "CHECK_FALSE", "require"})
RUNNER_ROOTS = RUNNER_NAMES | frozenset({
    "t", "b", "cy", "page", "screen", "assert", "require", "console", "JSON", "Math", "Object", "Array", "fmt",
    "strings", "bytes", "reflect", "Assert", "Assertions",
})


class Resolved(NamedTuple):
    text: str
    typed: str


class LayerExpectations(NamedTuple):
    found: list[Expected]
    inputs: tuple[tuple[str, ...], ...]


class FileBindings(NamedTuple):
    starts: tuple[int, ...]
    shapes: tuple[Shape, ...]
    shared: tuple[tuple[str, str], ...]


def _unique(shapes: tuple[Shape, ...]) -> tuple[tuple[str, str], ...]:
    counts = Counter(shape.name for shape in shapes)
    return tuple((shape.name, shape.operands[0].literal) for shape in shapes if counts[shape.name] == 1)


@lru_cache(maxsize=8)
def file_bindings(path: str, layered: bool, text: str) -> FileBindings:
    outline = None
    if layered:
        layer = _language_layer()
        language = layer.language_of(path)
        outline = layer.outline_for(path, language, text) if language else None
    shapes = outline.shapes if outline is not None else lexed_bindings(text)
    found = tuple(sorted(
        (shape for shape in shapes if shape.role is Role.BINDING and shape.operands), key=lambda shape: shape.start,
    ))
    return FileBindings(tuple(shape.start for shape in found), found, _unique(found))


class _Scan:
    def __init__(self, unit: Unit, text: str) -> None:
        self.unit = unit
        self.text = text
        self.shapes = unit.shapes[1:] if unit.shapes else lexed_shapes(unit)
        self.calls = [shape for shape in self.shapes if shape.role is Role.CALL]
        self.bindings = self.bound(unit, text)

    def bound(self, unit: Unit, text: str) -> dict[str, str]:
        found = file_bindings(unit.path, bool(unit.shapes), text)
        merged = dict(found.shared)
        if unit.shapes:
            scope = unit.shapes[0]
            first = bisect.bisect_left(found.starts, scope.start)
            last = bisect.bisect_left(found.starts, scope.end)
            merged.update(_unique(found.shapes[first:last]))
        return merged

    def source(self, start: int, end: int) -> str:
        return self.unit.body[max(start - self.unit.offset, 0):max(end - self.unit.offset, 0)]

    def line(self, position: int) -> int:
        return self.unit.start + self.unit.body.count("\n", 0, max(position - self.unit.offset, 0))

    def resolved(self, operand: Operand) -> Resolved | None:
        literal = operand.literal
        if literal is None:
            literal = self.bindings.get(bound_name(self.source(operand.start, operand.end)))
        if literal is None or not literal.replace(COMPUTED, "").strip():
            return None
        return Resolved(literal.replace(COMPUTED, PLACEHOLDER), literal)

    def expected(self, operand: Operand, kind: str, pattern: bool) -> Expected | None:
        found = self.resolved(operand)
        if found is None:
            return None
        text, typed = found
        if pattern or operand.pattern:
            text, typed = regex_words(text), regex_words(typed)
        return Expected(self.line(operand.start), text, typed, kind)

    def guarded(self, shape: Shape) -> bool:
        for guard in self.shapes:
            if guard.role is Role.GUARD and guard.start <= shape.start < guard.end and guard.parts:
                start, end = guard.parts[0]
                if FAILURE_RE.search(self.source(start, end)) or any(
                    start <= call.start < end and (is_assertion(call) or self.row(call)) for call in self.calls
                ):
                    return True
        return False

    def checked(self, shape: Shape) -> bool:
        return any(check.role is Role.CHECK and check.start <= shape.start < check.end for check in self.shapes)

    def row(self, shape: Shape) -> Row | None:
        return framework_row(self.unit.language, callee_parts(shape.name))

    def skipped(self, row: Row, operands: tuple[Operand, ...]) -> tuple[Operand, ...]:
        if row.skip is Skip.TESTING_HANDLE and operands:
            if TESTING_HANDLE_RE.match(self.source(operands[0].start, operands[0].end).strip()):
                return operands[1:]
        if row.skip is Skip.JUNIT4_MESSAGE and self.unit.language == "java" and len(operands) >= 3:
            return operands if JUPITER in self.text else operands[1:]
        return operands

    def picked(self, order: Order, operands: tuple[Operand, ...], shape: Shape) -> tuple[list[Operand], str]:
        if order is Order.EXPECTED_FIRST and operands:
            return [operands[0]], self.text_of(operands[1:2])
        if order is Order.ACTUAL_FIRST and len(operands) >= 2:
            return [operands[1]], self.text_of(operands[:1])
        if order is Order.MATCHER:
            return list(operands[:1]), shape.name
        if order is Order.LAST:
            kept = [operand for operand in operands if not FLAG_RE.match(operand.literal or "")]
            return kept[-1:], self.text_of(operands[:1]) if len(operands) > 1 else shape.name
        if order is Order.NONE:
            return self.literal_side(operands[:2])
        return [], ""

    def literal_side(self, operands: tuple[Operand, ...]) -> tuple[list[Operand], str]:
        if len(operands) != 2:
            return [], ""
        left, right = (self.resolved(operand) is not None for operand in operands)
        if left == right:
            return [], ""
        chosen, actual = (operands[0], operands[1]) if left else (operands[1], operands[0])
        return [chosen], self.text_of((actual,))

    def text_of(self, operands: tuple[Operand, ...]) -> str:
        return " ".join(self.source(operand.start, operand.end) for operand in operands)

    def assertion_found(self, shape: Shape, row: Row) -> Iterator[Expected]:
        if row.guarded and not self.guarded(shape):
            return
        chosen, actual = self.picked(row.order, self.skipped(row, shape.operands), shape)
        kind = row.kind if row.kind != ASSERTED else actual_kind(actual)
        for operand in chosen:
            found = self.expected(operand, kind, row.pattern)
            if found is not None:
                yield found

    def locator_found(self, shape: Shape, parts: list[str]) -> Iterator[Expected]:
        if is_locator(parts):
            yield from filter(None, (self.expected(operand, LOCATOR, False) for operand in shape.operands))
        elif selects(parts):
            for operand in shape.operands:
                found = self.resolved(operand)
                chosen = selector_text(found.text) if found is not None else None
                if chosen is not None:
                    yield Expected(self.line(operand.start), chosen, chosen, LOCATOR)

    def asserted(self, shape: Shape) -> bool:
        holders = [
            holder for holder in self.shapes
            if holder.role in (Role.CALL, Role.ASSERT) and holder.start <= shape.start and shape.end <= holder.end
            and holder != shape
        ]
        if not holders:
            return False
        inner = max(holders, key=lambda holder: (holder.start, -holder.end))
        parts = callee_parts(inner.name)
        return is_assertion(inner) or (bool(parts) and parts[-1] in CHECK_MACROS)

    def compare_found(self, shape: Shape) -> Iterator[Expected]:
        if not (self.checked(shape) or self.guarded(shape) or self.asserted(shape)):
            return
        chosen, actual = self.literal_side(shape.operands)
        for operand in chosen:
            found = self.expected(operand, actual_kind(actual), False)
            if found is not None:
                yield found

    def found(self) -> Iterator[Expected]:
        for shape in self.shapes:
            if shape.role is Role.COMPARE:
                yield from self.compare_found(shape)
            elif shape.role is Role.CALL:
                parts = callee_parts(shape.name)
                row = framework_row(self.unit.language, parts)
                if row is not None:
                    yield from self.assertion_found(shape, row)
                yield from self.locator_found(shape, parts)

    def under_test(self) -> list[Shape]:
        return [call for call in self.calls if self.tested(call)]

    def tested(self, call: Shape) -> bool:
        parts = callee_parts(call.name)
        if not parts or parts[0] in RUNNER_ROOTS or parts[-1] in RUNNER_NAMES or not parts[0][:1].isalpha():
            return False
        if is_assertion(call) or self.row(call) is not None or is_locator(parts) or selects(parts):
            return False
        return True

    def inputs(self) -> tuple[tuple[str, ...], ...]:
        return tuple(
            tuple(found.text for found in map(self.resolved, call.operands) if found is not None)
            for call in self.under_test()
        )


def _language_layer():
    try:
        from .. import language_layer
    except ImportError:
        import language_layer
    return language_layer


def actual_kind(actual: str) -> str:
    if MESSAGE_RE.search(actual):
        return EXCEPTION
    return LOG if LOG_RE.search(actual) else ASSERTED


def layer_expectations(unit: Unit, text: str) -> LayerExpectations:
    scan = _Scan(unit, text)
    return LayerExpectations(sorted(set(scan.found())), scan.inputs())


def is_layer_transformation(inputs: tuple[tuple[str, ...], ...], expected: str) -> bool:
    if len(inputs) != 1:
        return False
    given = [text for text in inputs[0] if shares_words(expected, text)]
    return bool(given) and not any(near_copy(text, expected) for text in given)
