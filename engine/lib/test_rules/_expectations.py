from __future__ import annotations

import ast
import re
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
_STRFTIME_RE = re.compile(r"%[-_0^#]?[A-Za-z%]")
_SPEC_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_NESTING_LIMIT = 8
_NO_CONVERSION = -1
Bindings = dict[str, str | ast.JoinedStr]
Pairs = tuple[tuple[str, ast.expr], ...]


class Scope(NamedTuple):
    bindings: Bindings
    captures: Captures


class Found(NamedTuple):
    text: str
    typed: str
    kind: str
    contract: str = ""


class Expected(NamedTuple):
    line: int
    text: str
    typed: str
    kind: str
    contract: str = ""


def dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + "." + node.attr
    return ""


def text_of(node: ast.expr | None, bindings: Bindings) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return _fstring_text(node)
    if isinstance(node, ast.Name):
        bound = bindings.get(node.id)
        return _fstring_text(bound) if isinstance(bound, ast.JoinedStr) else bound
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        text = _operand_text(node, bindings)
        return text if text is None or text.strip() else None
    if isinstance(node, ast.Call) and dotted(node.func) == _ESCAPE_CALL and len(node.args) == 1:
        return text_of(node.args[0], bindings)
    return formatted(node, lambda template: text_of(template, bindings), _text_argument)


def _bound(node: ast.expr | None, bindings: Bindings) -> ast.expr | None:
    if isinstance(node, ast.Name):
        bound = bindings.get(node.id)
        if isinstance(bound, ast.JoinedStr):
            return bound
    return node


def _literal_parts(node: ast.JoinedStr) -> str:
    return "".join(part.value if isinstance(part, ast.Constant) else " " for part in node.values)


def _fstring_text(node: ast.JoinedStr) -> str | None:
    literal = _literal_parts(node)
    return literal if literal.strip() else None


def _operand_text(node: ast.expr, bindings: Bindings) -> str | None:
    target = _bound(node, bindings)
    if isinstance(target, ast.JoinedStr):
        return _literal_parts(target)
    if isinstance(target, ast.BinOp) and isinstance(target.op, ast.Add):
        return _joined(_operand_text(target.left, bindings), _operand_text(target.right, bindings))
    return text_of(target, bindings)


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
    node = _bound(node, bindings)
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


def _machine_spec(text: str) -> bool:
    return _SPEC_WORD_RE.search(_STRFTIME_RE.sub(" ", text)) is None


def _spec_texts(spec: ast.expr, bindings: Bindings, depth: int) -> Pairs:
    if not isinstance(spec, ast.JoinedStr):
        return ()
    text = _fstring_text(spec)
    head = () if text is None or _machine_spec(text) else ((text, spec),)
    return (*head, *_inner_texts(spec, bindings, depth))


def _inner_texts(node: ast.JoinedStr, bindings: Bindings, depth: int) -> Pairs:
    if depth >= _NESTING_LIMIT:
        return ()
    pairs: list[tuple[str, ast.expr]] = []
    for part in node.values:
        if isinstance(part, ast.FormattedValue):
            pairs.extend(_string_texts(part.value, bindings, depth + 1) or ())
            if part.format_spec is not None:
                pairs.extend(_spec_texts(part.format_spec, bindings, depth + 1))
    return tuple(pairs)


def _chain(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return [*_chain(node.left), *_chain(node.right)]
    return [node]


def _operand_inner(node: ast.expr, bindings: Bindings, depth: int) -> Pairs | None:
    target = _bound(node, bindings)
    if isinstance(target, ast.JoinedStr):
        return _inner_texts(target, bindings, depth)
    return () if text_of(target, bindings) is not None else None


def _chain_texts(node: ast.BinOp, bindings: Bindings, depth: int) -> Pairs | None:
    inner = [_operand_inner(operand, bindings, depth) for operand in _chain(node)]
    if any(found is None for found in inner):
        return None
    text = text_of(node, bindings)
    head = () if text is None else ((text, node),)
    return (*head, *(pair for found in inner for pair in found))


def _string_texts(node: ast.expr | None, bindings: Bindings, depth: int = 0) -> Pairs | None:
    target = _bound(node, bindings)
    if isinstance(target, ast.JoinedStr):
        outer = _fstring_text(target)
        head = () if outer is None else ((outer, target),)
        return (*head, *_inner_texts(target, bindings, depth))
    if isinstance(target, ast.BinOp) and isinstance(target.op, ast.Add):
        return _chain_texts(target, bindings, depth)
    text = text_of(target, bindings)
    return None if text is None else ((text, target),)


def _literal_texts(node: ast.expr | None, bindings: Bindings, nested: bool = False) -> Pairs | None:
    if node is None:
        return None
    texts = _string_texts(node, bindings)
    if texts is not None:
        return texts if texts or nested else None
    if isinstance(node, ast.Constant):
        return ()
    elements = _elements(node)
    if elements is None:
        return None
    found = [_literal_texts(element, bindings, nested=True) for element in elements]
    if any(item is None for item in found):
        return None
    return tuple(pair for item in found for pair in item)


def _kind(actual: ast.expr | None, scope: Scope) -> str:
    kind = None if actual is None else capture_kind(actual, scope.captures)
    return kind or ASSERTED


def _pure(node: ast.expr, bindings: Bindings) -> bool:
    target = _bound(node, bindings)
    if isinstance(target, ast.JoinedStr):
        return all(isinstance(part, ast.Constant) for part in target.values)
    if isinstance(target, (ast.Constant, ast.Name)):
        return True
    if isinstance(target, ast.BinOp) and isinstance(target.op, ast.Add):
        return _pure(target.left, bindings) and _pure(target.right, bindings)
    elements = _elements(target)
    return elements is not None and all(_pure(element, bindings) for element in elements)


def _placeholder(part: ast.FormattedValue) -> str | None:
    conversion = "" if part.conversion == _NO_CONVERSION else "!" + chr(part.conversion)
    if part.format_spec is None:
        return "{" + conversion + "}"
    spec = _fstring_template(part.format_spec) if isinstance(part.format_spec, ast.JoinedStr) else None
    return None if spec is None else "{" + conversion + ":" + spec + "}"


def _fstring_template(node: ast.JoinedStr) -> str | None:
    if all(isinstance(part, ast.Constant) for part in node.values):
        return "".join(part.value for part in node.values)
    if any(isinstance(part, ast.Constant) and "{}" in part.value for part in node.values):
        return None
    pieces = [part.value if isinstance(part, ast.Constant) else _placeholder(part) for part in node.values]
    return None if None in pieces else "".join(pieces)


def _template(node: ast.expr | None, bindings: Bindings) -> str | None:
    target = _bound(node, bindings)
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        return target.value
    if isinstance(target, ast.Name):
        bound = bindings.get(target.id)
        return bound if isinstance(bound, str) else None
    if isinstance(target, ast.JoinedStr):
        return _fstring_template(target)
    if isinstance(target, ast.BinOp) and isinstance(target.op, ast.Add):
        return _joined(_template(target.left, bindings), _template(target.right, bindings))
    return None


def _exact(node: ast.expr | None, bindings: Bindings) -> str:
    return _template(node, bindings) or ""


def _single(node: ast.expr | None, actual: ast.expr | None, scope: Scope, pattern: bool = False) -> Iterator[Found]:
    kind = _kind(actual, scope)
    for text, piece in _string_texts(node, scope.bindings) or ():
        typed = typed_text(piece, scope.bindings)
        if pattern:
            yield Found(regex_words(text), regex_words(typed), kind)
        else:
            yield Found(text, typed, kind, _exact(piece, scope.bindings))


def _literal_side(first: ast.expr | None, second: ast.expr | None, scope: Scope) -> Iterator[Found]:
    bindings = scope.bindings
    left, right = _literal_texts(first, bindings), _literal_texts(second, bindings)
    if left is not None and right is not None and _pure(first, bindings) and _pure(second, bindings):
        return
    for found, actual in ((left, second), (right, first)):
        kind = _kind(actual, scope)
        for text, node in found or ():
            yield Found(text, typed_text(node, bindings), kind, _exact(node, bindings))


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
    for text, piece in _string_texts(node, scope.bindings) or ():
        yield Found(regex_words(text), regex_words(typed_text(piece, scope.bindings)), kind)


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
            for text, piece in _string_texts(argument, scope.bindings) or ():
                yield Found(text, typed_text(piece, scope.bindings), LOG)


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
