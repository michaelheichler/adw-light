from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING

try:
    from .code_units import UnitRow, unit_scan
    from .comment_free import comment_free_lines
    from .findings import finding_row, spanning
    from .language_views import merged_findings
    from .scan_lines import _line_or_blank
except ImportError:
    from code_units import UnitRow, unit_scan
    from comment_free import comment_free_lines
    from findings import finding_row, spanning
    from language_views import merged_findings
    from scan_lines import _line_or_blank
if TYPE_CHECKING:
    try:
        from .scanner import _ScanContext
    except ImportError:
        from scanner import _ScanContext


ASSERT_RE = re.compile(r"\bassert\b|\.assert|expect\(|\bexpect\.(?:poll|soft)\(|raises\(|warns\(|should\b|\.to\b|require\.|verify(?:_\w+)?\(", re.IGNORECASE)
TEST_START_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+test\w*\s*\([^)]*\):|^(\s*)(?:it|test|describe)\s*\(", re.IGNORECASE)
PASS_WORD_RE = re.compile(r"\bpass\b", re.IGNORECASE)
PATCH_SUFFIXES = (".patch", ".diff")
PYTHON_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
PYTHON_SCOPES = (*PYTHON_FUNCTIONS, ast.ClassDef, ast.Lambda)
PythonFunction = ast.FunctionDef | ast.AsyncFunctionDef
ScopeFunctions = dict[ast.AST, dict[str, PythonFunction]]


def _hollow_test_findings(context: _ScanContext, lines: list[str]) -> list[dict]:
    if context.path.lower().endswith(PATCH_SUFFIXES):
        return []
    if isinstance(context.tree, ast.Module):
        return _hollow_rows(context, _python_hollow_rows(context.tree, context.text))
    scan = unit_scan(context.path, context.text)
    if scan is not None and not scan.partial:
        return _hollow_rows(context, scan.hollow)
    lexer = _scan_hollow_test_blocks(context.path, lines, comment_free_lines(context.path, context.text))
    return merged_findings(_hollow_rows(context, scan.hollow), lexer) if scan is not None else lexer


def _hollow_rows(context: _ScanContext, rows: Iterable[UnitRow]) -> list[dict]:
    return [
        spanning(finding_row(
            "code", "hollow_test", row.line, "Test body has no assertion in " + context.path,
            _line_or_blank(context.lines, row.line), "Add an assertion or delete the hollow test.",
        ), row.end)
        for row in rows
    ]


def _python_hollow_rows(tree: ast.Module, text: str) -> list[UnitRow]:
    scopes, parents = _python_scopes(tree)
    tests = [node for node in parents if isinstance(node, PYTHON_FUNCTIONS) and node.name.lower().startswith("test")]
    if not tests:
        return []
    asserting, returns_value = _python_body_checks(scopes, parents)
    line_numbers = _python_line_numbers(text)
    return [
        UnitRow(line_numbers[node.lineno - 1], node.name, line_numbers[(node.end_lineno or node.lineno) - 1])
        for node in tests if node not in asserting and node not in returns_value
    ]


def _python_line_numbers(text: str) -> list[int]:
    numbers = []
    number = 1
    for line in re.split(r"\r\n?|\n", text):
        numbers.append(number)
        number += len((line + "\n").splitlines())
    return numbers


def _python_body_nodes(body: list[ast.stmt]) -> Iterator[ast.AST]:
    nodes: list[ast.AST] = list(reversed(body))
    while nodes:
        node = nodes.pop()
        yield node
        if not isinstance(node, PYTHON_SCOPES):
            nodes.extend(reversed(list(ast.iter_child_nodes(node))))


def _python_scopes(tree: ast.Module) -> tuple[ScopeFunctions, dict[ast.AST, ast.AST]]:
    scopes: ScopeFunctions = {tree: {}}
    parents: dict[ast.AST, ast.AST] = {}
    pending: list[ast.Module | ast.ClassDef | PythonFunction] = [tree]
    while pending:
        owner = pending.pop()
        for node in _python_body_nodes(owner.body):
            if isinstance(node, (ast.ClassDef, *PYTHON_FUNCTIONS)):
                scopes[node] = {}
                parents[node] = owner
                pending.append(node)
            if isinstance(node, PYTHON_FUNCTIONS):
                scopes[owner][node.name] = node
    return scopes, parents


def _python_body_checks(
    scopes: ScopeFunctions, parents: dict[ast.AST, ast.AST],
) -> tuple[set[PythonFunction], set[PythonFunction]]:
    asserting: set[PythonFunction] = set()
    returns_value: set[PythonFunction] = set()
    callers: defaultdict[PythonFunction, set[PythonFunction]] = defaultdict(set)
    for function in parents:
        if not isinstance(function, PYTHON_FUNCTIONS):
            continue
        for node in _python_body_nodes(function.body):
            if isinstance(node, ast.Assert):
                asserting.add(function)
            elif isinstance(node, ast.Return) and node.value is not None:
                returns_value.add(function)
            elif isinstance(node, ast.Call):
                target = _python_callee(node.func, function, scopes, parents)
                if target is not None:
                    callers[target].add(function)
                elif _python_assertion_call(node.func):
                    asserting.add(function)
    pending = list(asserting)
    while pending:
        for caller in callers[pending.pop()] - asserting:
            asserting.add(caller)
            pending.append(caller)
    return asserting, returns_value


def _python_callee(
    callee: ast.expr, function: PythonFunction, scopes: ScopeFunctions, parents: dict[ast.AST, ast.AST],
) -> PythonFunction | None:
    if isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name):
        owner = parents[function]
        positional = function.args.posonlyargs + function.args.args
        if isinstance(owner, ast.ClassDef) and positional and callee.value.id == positional[0].arg:
            return scopes[owner].get(callee.attr)
    if isinstance(callee, ast.Name):
        scope: ast.AST | None = function
        while scope is not None:
            if not isinstance(scope, ast.ClassDef) and callee.id in scopes[scope]:
                return scopes[scope][callee.id]
            scope = parents.get(scope)
    return None


def _python_assertion_call(callee: ast.expr) -> bool:
    if isinstance(callee, ast.Name):
        return bool(ASSERT_RE.search(callee.id + "("))
    if isinstance(callee, ast.Attribute):
        receiver = callee.value.id if isinstance(callee.value, ast.Name) else ""
        return bool(ASSERT_RE.search(receiver + "." + callee.attr + "("))
    return False


def _hollow_test_line_findings(path: str, line_number: int, line: str) -> list[dict]:
    if path.lower().endswith(PATCH_SUFFIXES) or not _looks_like_empty_test(line):
        return []
    return [finding_row(
        "code",
        "hollow_test",
        line_number,
        "Test body has no assertion in " + path,
        line,
        "Add an assertion or delete the hollow test.",
    )]


def _scan_hollow_test_blocks(path: str, lines: list[str], code_lines: list[str]) -> list[dict]:
    findings: list[dict] = []
    index = 0
    if len(code_lines) != len(lines):
        code_lines = lines
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        match = TEST_START_RE.match(line)
        if not match:
            index += 1
            continue
        if PASS_WORD_RE.search(stripped):
            index += 1
            continue
        block, next_index = _test_block(code_lines, index)
        if block and not any(ASSERT_RE.search(part) for part in block):
            findings.append(spanning(finding_row(
                "code",
                "hollow_test",
                index + 1,
                "Test body has no assertion in " + path,
                line,
                "Add an assertion or delete the hollow test.",
            ), max(next_index, index + 1)))
        index = max(next_index, index + 1)
    return findings


def _indented_test_body(lines: list[str], start: int, indent: int) -> tuple[list[str], int]:
    body: list[str] = []
    index = start + 1
    while index < len(lines):
        current = lines[index]
        if current.strip() and len(current) - len(current.lstrip()) <= indent:
            break
        body.append(current)
        index += 1
    return body, index


def _test_block(lines: list[str], start: int) -> tuple[list[str], int]:
    line = lines[start]
    if line.rstrip().endswith(":"):
        indent = len(line) - len(line.lstrip())
        return _indented_test_body(lines, start, indent)
    body = [line]
    depth = line.count("{") - line.count("}")
    if depth <= 0:
        return body, start + 1
    index = start + 1
    while index < len(lines):
        body.append(lines[index])
        depth += lines[index].count("{") - lines[index].count("}")
        if depth <= 0:
            return body, index + 1
        index += 1
    return body, index


def _looks_like_empty_test(line: str) -> bool:
    stripped = line.strip()
    if not re.match(r"(def|function|it|test)\b.*\btest", stripped, re.IGNORECASE):
        return False
    return bool(PASS_WORD_RE.search(stripped)) and not ASSERT_RE.search(stripped)
