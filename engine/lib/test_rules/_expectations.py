from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import NamedTuple

try:
    from ._captures import EXCEPTION, LOG, Captures, capture_kind
    from ._formatting import formatted
except ImportError:
    from test_rules._captures import EXCEPTION, LOG, Captures, capture_kind
    from test_rules._formatting import formatted

ASSERTED = "asserted"
PLACEHOLDER = " 0 "
COMPUTED = "\x00"
_COMPARE_OPS = (ast.Eq, ast.NotEq, ast.In, ast.NotIn, ast.Is, ast.IsNot)
_PAIR_ASSERTS = frozenset({
    "assertEqual", "assertEquals", "assertNotEqual", "assertMultiLineEqual", "assertIn", "assertNotIn",
    "assertStartsWith", "assertNotStartsWith", "assertEndsWith", "assertNotEndsWith",
    "assertListEqual", "assertCountEqual", "assertSequenceEqual", "assertTupleEqual", "assertDictEqual",
    "assertSetEqual",
})
_REGEX_ASSERTS = frozenset({"assertRegex", "assertNotRegex"})
_RAISES_REGEX = frozenset({"assertRaisesRegex", "assertWarnsRegex"})
_RAISES_CALLS = frozenset({"pytest.raises", "pytest.warns", "raises", "warns"})
_TRUTH_ASSERTS = frozenset({"assertTrue", "assertFalse"})
_TEXT_METHODS = frozenset({"startswith", "endswith"})
_REGEX_CALLS = frozenset({"re.search", "re.match", "re.fullmatch"})
_ESCAPE_CALL = "re.escape"
_MATCH_KEYWORD = "match"
_MATCH_METHOD = "match"
_MOCK_ASSERTS = frozenset({"assert_called_with", "assert_called_once_with", "assert_any_call"})
_LOG_LEVELS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"})
_PAIR_KEYWORDS = (("first", "member"), ("second", "container"))
_REGEX_KEYWORDS = (("text",), ("expected_regex",))
_RAISES_KEYWORDS = (("expected_exception",), ("expected_regex",))
_TRUTH_KEYWORDS = (("expr",),)
_CONTAINERS = (ast.List, ast.Tuple, ast.Set)
_REGEX_CLASSES = frozenset("dDsSwWbBAZ")
_REGEX_SYNTAX = frozenset("^$*+?{}()|[].")
Bindings = dict[str, str]


class Scope(NamedTuple):
    bindings: Bindings
    captures: Captures


class Found(NamedTuple):
    text: str
    typed: str
    kind: str


class Expected(NamedTuple):
    line: int
    text: str
    typed: str
    kind: str


def dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + "." + node.attr
    return ""


def text_of(node: ast.expr | None, bindings: Bindings) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and any(isinstance(part, ast.Constant) for part in node.values):
        return "".join(part.value if isinstance(part, ast.Constant) else PLACEHOLDER for part in node.values)
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _joined(text_of(node.left, bindings), text_of(node.right, bindings))
    if isinstance(node, ast.Call) and dotted(node.func) == _ESCAPE_CALL and len(node.args) == 1:
        return text_of(node.args[0], bindings)
    return formatted(node, lambda template: text_of(template, bindings), _text_argument)


def _text_argument(node: ast.expr | None) -> str:
    return str(node.value) if isinstance(node, ast.Constant) else PLACEHOLDER


def _joined(left: str | None, right: str | None) -> str | None:
    return None if left is None or right is None else left + right


def regex_words(pattern: str) -> str:
    kept: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            escaped = pattern[index + 1]
            kept.append(" " if escaped in _REGEX_CLASSES else escaped)
            index += 2
            continue
        kept.append(" " if char in _REGEX_SYNTAX else char)
        index += 1
    return " ".join("".join(kept).split())


def _typed_part(part: ast.expr | None) -> str:
    if isinstance(part, ast.Constant):
        return str(part.value)
    if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Constant):
        return str(part.value.value)
    return COMPUTED


def typed_text(node: ast.expr | None, bindings: Bindings) -> str:
    if isinstance(node, ast.JoinedStr):
        return "".join(_typed_part(part) for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return typed_text(node.left, bindings) + typed_text(node.right, bindings)
    if isinstance(node, ast.Call) and dotted(node.func) == _ESCAPE_CALL and len(node.args) == 1:
        return typed_text(node.args[0], bindings)
    typed = formatted(node, lambda template: text_of(template, bindings), _typed_part)
    if typed is not None:
        return typed
    text = text_of(node, bindings)
    return COMPUTED if text is None else text


def _elements(node: ast.expr) -> list[ast.expr] | None:
    if isinstance(node, _CONTAINERS):
        return list(node.elts)
    if isinstance(node, ast.Dict):
        return [*(key for key in node.keys if key is not None), *node.values]
    return None


def _literal_texts(node: ast.expr | None, bindings: Bindings) -> tuple[tuple[str, ast.expr], ...] | None:
    text = text_of(node, bindings)
    if text is not None:
        return ((text, node),)
    if isinstance(node, ast.Constant):
        return ()
    elements = _elements(node) if node is not None else None
    if elements is None:
        return None
    nested = [_literal_texts(element, bindings) for element in elements]
    if any(found is None for found in nested):
        return None
    return tuple(pair for found in nested for pair in found)


def _kind(actual: ast.expr | None, scope: Scope) -> str:
    kind = None if actual is None else capture_kind(actual, scope.captures)
    return kind or ASSERTED


def _single(node: ast.expr | None, actual: ast.expr | None, scope: Scope, pattern: bool = False) -> Iterator[Found]:
    text = text_of(node, scope.bindings)
    if text is not None:
        typed = typed_text(node, scope.bindings)
        yield Found(regex_words(text) if pattern else text, regex_words(typed) if pattern else typed, _kind(actual, scope))


def _literal_side(first: ast.expr | None, second: ast.expr | None, scope: Scope) -> Iterator[Found]:
    left, right = _literal_texts(first, scope.bindings), _literal_texts(second, scope.bindings)
    if (left is None) == (right is None):
        return
    found, actual = (left, second) if left is not None else (right, first)
    kind = _kind(actual, scope)
    yield from (Found(text, typed_text(node, scope.bindings), kind) for text, node in found)


def _operands(call: ast.Call, names: tuple[tuple[str, ...], ...]) -> list[ast.expr | None]:
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
    found: list[ast.expr | None] = []
    for index, aliases in enumerate(names):
        positional = call.args[index] if index < len(call.args) else None
        found.append(positional or next((keywords[name] for name in aliases if name in keywords), None))
    return found


def _method(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else ""


def _call_texts(call: ast.Call, scope: Scope) -> Iterator[Found]:
    if not call.args:
        return
    if _method(call) in _TEXT_METHODS:
        yield from _single(call.args[0], call.func.value, scope)
    elif dotted(call.func) in _REGEX_CALLS:
        actual = call.args[1] if len(call.args) > 1 else None
        yield from _single(call.args[0], actual, scope, pattern=True)


def _condition_texts(test: ast.expr, scope: Scope) -> Iterator[Found]:
    if isinstance(test, ast.BoolOp):
        for value in test.values:
            yield from _condition_texts(value, scope)
    elif isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        yield from _condition_texts(test.operand, scope)
    elif isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], _COMPARE_OPS):
        yield from _literal_side(test.left, test.comparators[0], scope)
    elif isinstance(test, ast.Call):
        yield from _call_texts(test, scope)


def _assert_texts(call: ast.Call, scope: Scope) -> Iterator[Found]:
    method = _method(call)
    if method in _PAIR_ASSERTS:
        first, second = _operands(call, _PAIR_KEYWORDS)
        if first is not None and second is not None:
            yield from _literal_side(first, second, scope)
    elif method in _REGEX_ASSERTS:
        actual, expected = _operands(call, _REGEX_KEYWORDS)
        yield from _single(expected, actual, scope, pattern=True)
    elif method in _TRUTH_ASSERTS:
        condition = _operands(call, _TRUTH_KEYWORDS)[0]
        if condition is not None:
            yield from _condition_texts(condition, scope)


def _pattern(node: ast.expr | None, kind: str, scope: Scope) -> Iterator[Found]:
    text = text_of(node, scope.bindings)
    if text is not None:
        yield Found(regex_words(text), regex_words(typed_text(node, scope.bindings)), kind)


def _exception_texts(call: ast.Call, scope: Scope) -> Iterator[Found]:
    method = _method(call)
    if dotted(call.func) in _RAISES_CALLS:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
        yield from _pattern(keywords.get(_MATCH_KEYWORD), EXCEPTION, scope)
    elif method in _RAISES_REGEX:
        yield from _pattern(_operands(call, _RAISES_KEYWORDS)[1], EXCEPTION, scope)
    elif method == _MATCH_METHOD and call.args and _kind(call.func.value, scope) == EXCEPTION:
        yield from _pattern(call.args[0], EXCEPTION, scope)


def _mock_log_texts(call: ast.Call, scope: Scope) -> Iterator[Found]:
    func = call.func
    if _method(call) in _MOCK_ASSERTS and isinstance(func.value, ast.Attribute) and func.value.attr in _LOG_LEVELS:
        for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
            text = text_of(argument, scope.bindings)
            if text is not None:
                yield Found(text, typed_text(argument, scope.bindings), LOG)


def _call_found(call: ast.Call, scope: Scope) -> Iterator[Found]:
    yield from _assert_texts(call, scope)
    yield from _exception_texts(call, scope)
    yield from _mock_log_texts(call, scope)


def expected_texts(body: list[ast.stmt], scope: Scope) -> Iterator[Expected]:
    for root in body:
        for node in ast.walk(root):
            if isinstance(node, ast.Assert):
                yield from (Expected(node.lineno, *found) for found in _condition_texts(node.test, scope))
            elif isinstance(node, ast.Call):
                yield from (Expected(node.lineno, *found) for found in _call_found(node, scope))
