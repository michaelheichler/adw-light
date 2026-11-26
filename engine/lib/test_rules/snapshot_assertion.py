from __future__ import annotations

import ast
import re
from collections.abc import Iterator

try:
    from . import Hit, Rule, RuleSet, module_scopes, module_tree
    from ..brace_functions import masked_code
    from ..test_units import JAVASCRIPT, PYTHON, Unit, javascript_assertions
except ImportError:
    from test_rules import Hit, Rule, RuleSet, module_scopes, module_tree
    from brace_functions import masked_code
    from test_units import JAVASCRIPT, PYTHON, Unit, javascript_assertions

RULE = "snapshot_assertion"
OBSERVE = "observe"
SNAPSHOT_FIXTURE = "snapshot"
APPROVAL_PACKAGE = "approvaltests"
_SNAPSHOT_METHODS = frozenset({"assert_match", "assert_match_dir"})
_SNAPSHOT_TEST_CASE_METHOD = "assertMatchSnapshot"
_PAIR_ASSERTS = frozenset({
    "assertEqual", "assertEquals", "assertMultiLineEqual", "assertListEqual", "assertDictEqual",
    "assertSequenceEqual",
})
_READ_CALLS = frozenset({"read_text", "read_bytes", "read", "readlines", "load", "open"})
_GOLDEN_RE = re.compile(r"expected|golden", re.IGNORECASE)
_JS_SNAPSHOT_RE = re.compile(r"\.\s*(?:toMatch\w*Snapshot|toThrowErrorMatching\w*Snapshot)\s*\(")
_JS_READ_RE = re.compile(r"\b(?:readFileSync|readFile)\s*\(")
_JS_READ_BINDING_RE = re.compile(r"\b(?:const|let|var)\s+(?P<name>[\w$]+)\s*=\s*(?P<value>[^;\n]*\b(?:readFileSync|readFile)\b[^;\n]*)")
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
Bindings = dict[str, str]


def _root(node: ast.expr) -> str:
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        else:
            return ""


def _callee(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else ""


def _parameters(test: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    arguments = test.args
    return frozenset(arg.arg for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs))


def _approval_names(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == APPROVAL_PACKAGE:
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0]
                         for alias in node.names if alias.name.split(".")[0] == APPROVAL_PACKAGE)
    return frozenset(names)


def _snapshot_lines(test: ast.FunctionDef | ast.AsyncFunctionDef, approvals: frozenset[str]) -> Iterator[int]:
    fixture = SNAPSHOT_FIXTURE in _parameters(test)
    for node in ast.walk(test):
        if isinstance(node, ast.Compare) and fixture and any(
            _root(side) == SNAPSHOT_FIXTURE for side in (node.left, *node.comparators)
        ):
            yield node.lineno
        elif isinstance(node, ast.Call) and _is_snapshot_call(node, fixture, approvals):
            yield node.lineno


def _is_snapshot_call(call: ast.Call, fixture: bool, approvals: frozenset[str]) -> bool:
    name = _callee(call)
    if name == _SNAPSHOT_TEST_CASE_METHOD:
        return True
    if name in _SNAPSHOT_METHODS:
        return fixture and _root(call.func) == SNAPSHOT_FIXTURE
    return name.startswith("verify") and _root(call.func) in approvals


def _read_bindings(test: ast.FunctionDef | ast.AsyncFunctionDef) -> Bindings:
    bindings: Bindings = {}
    for node in ast.walk(test):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if _has_read(node.value):
                bindings[node.targets[0].id] = ast.unparse(node.value)
    return bindings


def _has_read(node: ast.expr) -> bool:
    return any(isinstance(child, ast.Call) and _callee(child) in _READ_CALLS for child in ast.walk(node))


def _is_golden(operand: ast.expr, bindings: Bindings) -> bool:
    names = {child.id for child in ast.walk(operand) if isinstance(child, ast.Name)}
    bound = [bindings[name] for name in names if name in bindings]
    if not (bound or _has_read(operand)):
        return False
    return bool(_GOLDEN_RE.search(" ".join([ast.unparse(operand), *names, *bound])))


def _compared(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
        compare = node.test
        return [compare.left, *compare.comparators] if all(isinstance(op, (ast.Eq, ast.NotEq)) for op in compare.ops) else []
    if isinstance(node, ast.Call) and _callee(node) in _PAIR_ASSERTS:
        return list(node.args[:2])
    return []


def _golden_lines(test: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[int]:
    bindings = _read_bindings(test)
    for node in ast.walk(test):
        if any(_is_golden(operand, bindings) for operand in _compared(node)):
            yield node.lineno


def _python_lines(unit: Unit, text: str) -> set[int]:
    tree = module_tree(text)
    test = module_scopes(tree).get((unit.start, unit.end)) if tree is not None else None
    if test is None:
        return set()
    return {*_snapshot_lines(test, _approval_names(tree)), *_golden_lines(test)}


def _line_of(unit: Unit, offset: int) -> int:
    return unit.start + unit.body.count("\n", 0, offset)


def _javascript_golden_offsets(unit: Unit, code: str) -> Iterator[int]:
    bound = {found.group("name"): found.group("value") for found in _JS_READ_BINDING_RE.finditer(unit.body)}
    for start in javascript_assertions(code):
        end = code.find(";", start)
        statement = unit.body[start:end if end != -1 else len(unit.body)]
        references = [value for name, value in bound.items() if re.search(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", statement)]
        if (references or _JS_READ_RE.search(statement)) and _GOLDEN_RE.search(" ".join([statement, *references])):
            yield start


def _javascript_lines(unit: Unit) -> set[int]:
    code = masked_code(unit.body, javascript=True)
    offsets = [found.start() for found in _JS_SNAPSHOT_RE.finditer(code)]
    return {_line_of(unit, offset) for offset in (*offsets, *_javascript_golden_offsets(unit, code))}


def _lines_for(unit: Unit, text: str) -> set[int]:
    if unit.language == PYTHON:
        return _python_lines(unit, text)
    return _javascript_lines(unit) if unit.language == JAVASCRIPT else set()


def check(unit: Unit, text: str) -> list[Hit]:
    written = text.splitlines()
    return [
        Hit(RULE, line, written[line - 1].strip() if line <= len(written) else unit.name)
        for line in sorted(_lines_for(unit, text))
    ]


RULE_SET = RuleSet(
    rules=(
        Rule(RULE, "Test compares output with a stored snapshot or golden file, so the stored copy decides what passes",
             "Assert the behavior that matters with an explicit expected value, and review the stored copy when it changes.",
             OBSERVE),
    ),
    check=check,
)
