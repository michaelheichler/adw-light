from __future__ import annotations

import re
from enum import StrEnum
from typing import NamedTuple

SELF_ROOTS = frozenset({"this", "self", "$this", "static", "parent", "super"})
ASSERT_PREFIXES = ("assert", "expect", "xctassert", "refute", "should", "must_", "wont_")
ASSERT_ROOTS = frozenset({"assert", "Assert", "Assertions", "require", "expect", "XCTAssert", "Truth"})
GO_FAILURES = frozenset({"Error", "Errorf", "Fatal", "Fatalf", "Fail", "FailNow"})
GO_RECEIVERS = frozenset({"t", "b"})
GROUP_RE = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
MACRO_ASSERTION_NAME = r"(?:TEST_ASSERT|CU_ASSERT|REQUIRE|CHECK)(?:_\w+)?"
MACRO_ASSERTION_NAME_RE = re.compile(MACRO_ASSERTION_NAME)
MACRO_ASSERTION_CALL_RE = re.compile(rf"\b{MACRO_ASSERTION_NAME}\s*\(")


class Role(StrEnum):
    FUNCTION = "function"
    CLOSURE = "closure"
    CALL = "call"
    ASSERT = "assert"
    LOOP = "loop"
    BRANCH = "branch"
    COMPARE = "compare"
    GUARD = "guard"
    CHECK = "check"
    BINDING = "binding"


SCOPE_ROLES = frozenset({Role.FUNCTION, Role.CLOSURE})
COMPUTED = "\x00"


class Operand(NamedTuple):
    start: int
    end: int
    literal: str | None = None
    pattern: bool = False


class Shape(NamedTuple):
    role: Role
    start: int
    end: int
    name: str = ""
    head: int = 0
    parts: tuple[tuple[int, int], ...] = ()
    operands: tuple[Operand, ...] = ()


class Outline(NamedTuple):
    shapes: tuple[Shape, ...]
    broken: bool


def callee_parts(callee: str) -> list[str]:
    text = callee.replace("?.", ".").replace("->", ".").replace("::", ".").replace("!", "")
    previous = None
    while previous != text:
        previous, text = text, GROUP_RE.sub("", text)
    parts = [part.strip() for part in text.split(".") if part.strip()]
    while len(parts) > 1 and parts[0] in SELF_ROOTS:
        parts.pop(0)
    return parts


def is_assertion(shape: Shape) -> bool:
    if shape.role is Role.ASSERT:
        return True
    parts = callee_parts(shape.name) if shape.role is Role.CALL else []
    if not parts:
        return False
    tail = parts[-1]
    lowered = tail.lower()
    if lowered == "expect" and len(parts) > 1:
        return False
    if MACRO_ASSERTION_NAME_RE.fullmatch(tail):
        return True
    if lowered.startswith(ASSERT_PREFIXES) or (len(parts) > 1 and parts[0] in ASSERT_ROOTS):
        return True
    return len(parts) == 2 and parts[0] in GO_RECEIVERS and tail in GO_FAILURES
