from __future__ import annotations

from pathlib import PurePath

try:
    from .comment_rules import python_comment_tokens
    from .declaration_mask import declaration_view
except ImportError:
    from comment_rules import python_comment_tokens
    from declaration_mask import declaration_view

SPLITLINES_BREAKS = frozenset("\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


def comment_free_lines(path: str, text: str) -> list[str]:
    suffix = PurePath(path.lower()).suffix
    if suffix != ".py":
        return _with_line_breaks(text, declaration_view(text, suffix)).splitlines()
    rows = text.split("\n")
    for number, comment in python_comment_tokens(text).items():
        row = rows[number - 1]
        start = row.rfind(comment)
        if start >= 0:
            rows[number - 1] = row[:start] + _line_breaks(row[start:])
    return "\n".join(rows).splitlines()


def _with_line_breaks(text: str, view: str) -> str:
    return "".join(original if original in SPLITLINES_BREAKS else masked for original, masked in zip(text, view))


def _line_breaks(span: str) -> str:
    return "".join(char for char in span if char in SPLITLINES_BREAKS)
