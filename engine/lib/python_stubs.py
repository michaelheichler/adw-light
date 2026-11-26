from __future__ import annotations

import ast

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def stub_line_ranges(tree: ast.Module | None) -> list[tuple[int, int]]:
    if tree is None:
        return []
    return [
        (_first_line(node) - 1, node.end_lineno or node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, _FUNCTIONS) and _raises_not_implemented(node.body)
    ]


def _first_line(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])


def _raises_not_implemented(body: list[ast.stmt]) -> bool:
    if len(body) != 1 or not isinstance(body[0], ast.Raise):
        return False
    raised = body[0].exc
    target = raised.func if isinstance(raised, ast.Call) else raised
    return isinstance(target, ast.Name) and target.id == "NotImplementedError"
