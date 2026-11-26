from __future__ import annotations

import re
from typing import TYPE_CHECKING

try:
    from .code_units import unit_scan
    from .comment_free import comment_free_lines
    from .findings import finding_row, spanning
    from .language_views import merged_findings
    from .scan_lines import _line_or_blank
except ImportError:
    from code_units import unit_scan
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


def _hollow_test_findings(context: _ScanContext, lines: list[str]) -> list[dict]:
    lexer = _scan_hollow_test_blocks(context.path, lines, comment_free_lines(context.path, context.text))
    scan = None if context.tree is not None else unit_scan(context.path, context.text)
    if scan is None:
        return lexer
    rows = [
        spanning(finding_row(
            "code", "hollow_test", row.line, "Test body has no assertion in " + context.path,
            _line_or_blank(context.lines, row.line), "Add an assertion or delete the hollow test.",
        ), row.end)
        for row in scan.hollow
    ]
    return merged_findings(rows, lexer) if scan.partial else rows


def _hollow_test_line_findings(path: str, line_number: int, line: str) -> list[dict]:
    if not _looks_like_empty_test(line):
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
