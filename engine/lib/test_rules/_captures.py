from __future__ import annotations

import ast
from collections.abc import Iterator

EXCEPTION = "exception"
LOG = "log"
LOG_FIXTURE = "caplog"
LOG_RECORD_METHOD = "getMessage"
MAX_PASSES = 4
_EXCEPTION_CONTEXTS = frozenset({
    "raises", "warns", "assertRaises", "assertRaisesRegex", "assertWarns", "assertWarnsRegex",
})
_LOG_CONTEXTS = frozenset({"assertLogs", "assertNoLogs"})
Captures = dict[str, str]


def _called_name(node: ast.expr) -> str:
    if not isinstance(node, ast.Call):
        return ""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return node.func.id if isinstance(node.func, ast.Name) else ""


def _context_kind(node: ast.expr) -> str | None:
    name = _called_name(node)
    if name in _EXCEPTION_CONTEXTS:
        return EXCEPTION
    return LOG if name in _LOG_CONTEXTS else None


def _direct(body: list[ast.stmt]) -> Captures:
    found: Captures = {LOG_FIXTURE: LOG}
    for root in body:
        for node in ast.walk(root):
            if isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
                kind = _context_kind(node.context_expr)
                if kind is not None:
                    found[node.optional_vars.id] = kind
    return found


def _flow(node: ast.AST) -> tuple[ast.expr, ast.expr] | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        return node.targets[0], node.value
    if isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
        return node.target, node.value
    if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        return node.target, node.iter
    return None


def _flows(body: list[ast.stmt]) -> Iterator[tuple[str, ast.expr]]:
    for root in body:
        for node in ast.walk(root):
            pair = _flow(node)
            if pair is not None and isinstance(pair[0], ast.Name):
                yield pair[0].id, pair[1]


def capture_kind(node: ast.AST, captures: Captures) -> str | None:
    kinds = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in captures:
            kinds.add(captures[child.id])
        elif isinstance(child, ast.Attribute) and child.attr == LOG_RECORD_METHOD:
            kinds.add(LOG)
    if LOG in kinds:
        return LOG
    return EXCEPTION if kinds else None


def captures(body: list[ast.stmt]) -> Captures:
    found = _direct(body)
    flows = list(_flows(body))
    for _ in range(MAX_PASSES):
        added = {}
        for name, source in flows:
            kind = None if name in found else capture_kind(source, found)
            if kind is not None:
                added[name] = kind
        if not added:
            break
        found.update(added)
    return found
