from __future__ import annotations

try:
    from . import Hit, Unit
    from ..language_shapes import SCOPE_ROLES, Role, Shape, callee_parts, is_assertion
except ImportError:
    from test_rules import Hit, Unit
    from language_shapes import SCOPE_ROLES, Role, Shape, callee_parts, is_assertion

SUT = "sut"


class ShapeView:
    def __init__(self, unit: Unit) -> None:
        self.unit = unit
        self.scope = unit.shapes[0]
        inner = unit.shapes[1:]
        self.nested = [shape for shape in inner if shape.role in SCOPE_ROLES]
        self.free = [shape for shape in inner if not self.enclosed(shape.start)]
        self.assertions = [shape for shape in self.free if is_assertion(shape)]

    def enclosed(self, position: int) -> bool:
        return any(shape.start <= position < shape.end for shape in self.nested)

    def line(self, position: int) -> int:
        return self.unit.start + self.unit.body.count("\n", 0, max(position - self.unit.offset, 0))

    def hit(self, rule: str, position: int) -> Hit:
        line = self.line(position)
        lines = self.unit.body.splitlines()
        index = line - self.unit.start
        return Hit(rule, line, lines[index] if 0 <= index < len(lines) else self.unit.name)

    def first_assertion(self, start: int, end: int) -> Shape | None:
        return next((shape for shape in self.assertions if start <= shape.start < end), None)

    def loop_hits(self, rule: str) -> list[Hit]:
        loops = [shape for shape in self.free if shape.role is Role.LOOP]
        outer = [loop for loop in loops if not any(
            other is not loop and other.start <= loop.start and loop.end <= other.end for other in loops
        )]
        found = (self.first_assertion(loop.start, loop.end) for loop in outer)
        return sorted({self.hit(rule, shape.start) for shape in found if shape is not None})

    def branch_hits(self, rule: str) -> list[Hit]:
        branches = (shape for shape in self.free if shape.role is Role.BRANCH and len(shape.parts) == 2)
        starts = {
            self.line(shape.start): shape.start for shape in branches
            if all(self.first_assertion(start, end) is not None for start, end in shape.parts)
        }
        return [self.hit(rule, starts[line]) for line in sorted(starts)]

    def acts(self) -> tuple[list[int], list[int], list[tuple[int, int]]]:
        statements = list(self.scope.parts)
        calls = [shape for shape in self.free if shape.role is Role.CALL and callee_parts(shape.name)[:1] == [SUT]]
        asserts = [index for index, (start, end) in enumerate(statements) if self.first_assertion(start, end)]
        acting = [
            index for index, (start, end) in enumerate(statements)
            if index not in asserts and any(start <= call.start < end for call in calls)
        ]
        return acting, asserts, statements
