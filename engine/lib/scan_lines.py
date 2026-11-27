from __future__ import annotations

from typing import NamedTuple


class _SourceLine(NamedTuple):
    path: str
    number: int
    text: str


def _line_or_blank(lines: list[str], number: int) -> str:
    return lines[number - 1] if number <= len(lines) else ""
