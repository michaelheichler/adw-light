from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import PurePath
from typing import TYPE_CHECKING

try:
    from . import scan_input
    from . import shell_units
    from .brace_functions import BRACE_LANGUAGE_EXTS, long_brace_functions
    from .code_units import long_rows, merged_rows, unit_scan
    from .file_kinds import _code_file
    from .findings import finding_row, spanning
except ImportError:
    import scan_input
    import shell_units
    from brace_functions import BRACE_LANGUAGE_EXTS, long_brace_functions
    from code_units import long_rows, merged_rows, unit_scan
    from file_kinds import _code_file
    from findings import finding_row, spanning
if TYPE_CHECKING:
    try:
        from .scanner import _ScanContext
    except ImportError:
        from scanner import _ScanContext

_int_setting = scan_input.int_setting


def _file_length_findings(path: str, count: int) -> list[dict]:
    policy = scan_input.file_length_policy(count)
    if policy is None:
        return []
    rule, action = policy
    return [finding_row("code", rule, 1, f"File has {count} lines in {path}", path, action)]


def file_length_findings(path: str, text: str) -> list[dict]:
    if scan_input.mostly_tool_output(text):
        return []
    return _file_length_findings(path, len(text.splitlines()) or 1) if _code_file(path, text) else []
def _long_functions(tree, func_limit: int) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        span = (getattr(node, "end_lineno", None) or node.lineno) - node.lineno + 1
        if span <= func_limit:
            continue
        yield node

def _long_function_rows(context: _ScanContext) -> list[tuple[int, str, int]]:
    func_limit = _int_setting(context.config, "function_block_lines", "ADW_FUNC_BLOCK_LINES", 80)
    if context.tree is not None:
        return [(node.lineno, node.name, node.end_lineno or node.lineno) for node in _long_functions(context.tree, func_limit)]
    lexer = []
    if PurePath(context.path.lower()).suffix in BRACE_LANGUAGE_EXTS:
        lexer = [(line, name, line + span - 1) for line, name, span in long_brace_functions(context.text, func_limit)]
    scan = unit_scan(context.path, context.text)
    if scan is None:
        return lexer
    rows = long_rows(scan, func_limit)
    if not scan.partial:
        return merged_rows(rows, shell_units.bats_test_rows(context.path, context.text, func_limit))
    return merged_rows(shell_units.closed_rows(context.path, context.text, rows, func_limit, scan.errors), lexer)


def _function_length_findings(context: _ScanContext) -> list[dict]:
    return [
        spanning(finding_row(
            "code", "function_too_long", line,
            "Function is over the length cap in " + context.path,
            name, "Extract helpers until each function does one thing.",
        ), end)
        for line, name, end in _long_function_rows(context)
    ]
