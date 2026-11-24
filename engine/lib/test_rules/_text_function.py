from __future__ import annotations

import ast
import re
from collections.abc import Callable
from difflib import SequenceMatcher

MOST_WORDS = 0.6
NEAR_COPY_WORDS = 2
PADDING_TAGS = frozenset({"insert", "delete"})
WORD_RE = re.compile(r"[^\W_]+")
ASSERT_PREFIX = "assert"
_FRAMEWORK_ROOTS = frozenset({"self", "cls", "pytest", "unittest", "mock", "re"})
_HELPERS = frozenset({
    "len", "str", "repr", "list", "tuple", "set", "frozenset", "sorted", "dict", "int", "float", "bool",
    "type", "isinstance", "print", "range", "enumerate", "zip", "any", "all", "min", "max", "next", "iter",
})
Resolve = Callable[[ast.expr], "str | None"]


def _words(text: str) -> set[str]:
    return set(WORD_RE.findall(text.casefold()))


def _letters(text: str) -> str:
    return "".join(WORD_RE.findall(text))


def shares_words(expected: str, given: str) -> bool:
    wanted = _words(expected)
    return bool(wanted) and len(wanted & _words(given)) >= MOST_WORDS * len(wanted)


def _padded(given: str, expected: str) -> bool:
    matcher = SequenceMatcher(None, WORD_RE.findall(given), WORD_RE.findall(expected), autojunk=False)
    edits = [(tag, high - low, end - start) for tag, low, high, start, end in matcher.get_opcodes() if tag != "equal"]
    return all(tag in PADDING_TAGS for tag, _, _ in edits) and sum(max(sizes) for _, *sizes in edits) <= NEAR_COPY_WORDS


def near_copy(given: str, expected: str) -> bool:
    return _letters(given) == _letters(expected) or _padded(given, expected)


def _framework_call(func: ast.expr) -> bool:
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in _FRAMEWORK_ROOTS


def _under_test(call: ast.Call) -> bool:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name.startswith(ASSERT_PREFIX) or (isinstance(func, ast.Name) and name in _HELPERS):
        return False
    return not _framework_call(func)


def calls_under_test(body: list[ast.stmt]) -> list[ast.Call]:
    return [node for root in body for node in ast.walk(root) if isinstance(node, ast.Call) and _under_test(node)]


def _inputs(call: ast.Call, resolve: Resolve) -> list[str]:
    values = [*call.args, *(keyword.value for keyword in call.keywords)]
    return [text for text in map(resolve, values) if text is not None]


def is_transformation(body: list[ast.stmt], expected: str, resolve: Resolve) -> bool:
    calls = calls_under_test(body)
    if len(calls) != 1:
        return False
    inputs = [given for given in _inputs(calls[0], resolve) if shares_words(expected, given)]
    return bool(inputs) and not any(near_copy(given, expected) for given in inputs)
