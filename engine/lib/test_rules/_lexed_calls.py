from __future__ import annotations

import re

try:
    from ..brace_functions import masked_code
    from ..language_shapes import COMPUTED, Operand, Role, Shape
    from ..test_units import JAVASCRIPT, Unit
except ImportError:
    from brace_functions import masked_code
    from language_shapes import COMPUTED, Operand, Role, Shape
    from test_units import JAVASCRIPT, Unit

CALL_RE = re.compile(r"[A-Za-z_$][\w$]*!?(?=\s*\()")
NAME_CHARS = re.compile(r"[\w$!]")
KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "function", "return", "typeof", "await", "new", "match", "elif",
    "fn", "async", "yield", "sizeof", "loop",
})
LINKS = ("?.", "::", "->", ".")
OPENERS = {"(": ")", "[": "]", "{": "}"}
QUOTED_RE = re.compile(r"""^(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`|r(#*)"(.*)"\4)$""", re.DOTALL)
REGEX_RE = re.compile(r"^/((?:[^/\\\n]|\\.)+)/[a-z]*$")
SUBSTITUTION_RE = re.compile(r"\$\{[^}]*\}")
SPACE_RE = re.compile(r"\s+")
BINDING_RE = re.compile(
    r"""\b(?:const|let|var|val)\s+(?:mut\s+)?([A-Za-z_$][\w$]*)\s*(?::\s*[&\w']+\s*)?=\s*"""
    r"""("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`[^`]*`)""",
)


def literal_operand(source: str, start: int, end: int) -> Operand:
    stripped = source.strip()
    shift = len(source) - len(source.lstrip())
    begin, finish = start + shift, start + shift + len(stripped)
    regex = REGEX_RE.match(stripped)
    if regex is not None:
        return Operand(begin, finish, regex.group(1), True)
    quoted = QUOTED_RE.match(stripped)
    if quoted is None:
        return Operand(begin, finish)
    double, single, template, _, raw = quoted.groups()
    if template is not None:
        return Operand(begin, finish, SUBSTITUTION_RE.sub(COMPUTED, template))
    return Operand(begin, finish, next(group for group in (double, single, raw) if group is not None))


def _pairs(code: str) -> dict[int, int]:
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for index, character in enumerate(code):
        if character in OPENERS:
            stack.append(index)
        elif character in OPENERS.values() and stack:
            opening = stack.pop()
            if OPENERS[code[opening]] == character:
                pairs[opening] = index
    return pairs


def _argument_spans(code: str, body: str, opening: int, closing: int, pairs: dict[int, int]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = index = opening + 1
    while index < closing:
        if index in pairs:
            index = pairs[index] + 1
            continue
        if code[index] == ",":
            spans.append((start, index))
            start = index + 1
        index += 1
    if body[start:closing].strip():
        spans.append((start, closing))
    return spans


def _chain_start(code: str, start: int, closers: dict[int, int]) -> int:
    position = start
    while True:
        cursor = position - 1
        while cursor >= 0 and code[cursor].isspace():
            cursor -= 1
        if cursor < 0:
            return position
        link = next((link for link in LINKS if code.endswith(link, 0, cursor + 1)), None)
        if link is None:
            return position
        cursor -= len(link)
        while cursor >= 0 and code[cursor].isspace():
            cursor -= 1
        if cursor >= 0 and code[cursor] == ")" and cursor in closers:
            cursor = closers[cursor] - 1
        while cursor >= 0 and NAME_CHARS.match(code[cursor]):
            cursor -= 1
        if cursor + 1 >= position:
            return position
        position = cursor + 1


def lexed_shapes(unit: Unit) -> tuple[Shape, ...]:
    body = unit.body
    code = masked_code(body, javascript=unit.language == JAVASCRIPT)
    pairs = _pairs(code)
    closers = {closing: opening for opening, closing in pairs.items()}
    shapes: list[Shape] = []
    for found in CALL_RE.finditer(code):
        if found.group().rstrip("!") in KEYWORDS:
            continue
        opening = code.index("(", found.end())
        closing = pairs.get(opening)
        if closing is None:
            continue
        start = _chain_start(code, found.start(), closers)
        name = SPACE_RE.sub("", body[start:found.end()]).replace("!", "")
        operands = tuple(
            literal_operand(body[first:last], first + unit.offset, last + unit.offset)
            for first, last in _argument_spans(code, body, opening, closing, pairs)
        )
        shapes.append(Shape(Role.CALL, start + unit.offset, closing + 1 + unit.offset, name, start + unit.offset, (), operands))
    return tuple(shapes)


def lexed_bindings(text: str) -> tuple[Shape, ...]:
    shapes = []
    for found in BINDING_RE.finditer(text):
        operand = literal_operand(found.group(2), found.start(2), found.end(2))
        shapes.append(Shape(Role.BINDING, found.start(), found.end(), found.group(1), found.start(), (), (operand,)))
    return tuple(shapes)
