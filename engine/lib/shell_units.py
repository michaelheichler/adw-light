from __future__ import annotations

import bisect
import re
from collections.abc import Iterator
from pathlib import PurePath
from typing import NamedTuple

SHELL_SUFFIXES = frozenset({".sh", ".bash", ".zsh", ".ksh", ".bats"})
HEREDOC_RE = re.compile(
    r"(?<!<)<<(?P<tabs>-?)(?!<)[ \t]*(?:\\(?P<bare>[A-Za-z_][\w.-]*)|(?P<quote>['\"]?)(?P<word>[A-Za-z_][\w.-]*)(?P=quote))"
)
CLOSE_RE = re.compile(r"\}[ \t]*(?:#.*)?$")
HEAD_RE = re.compile(
    r"""^(?:function[ \t]+(?P<keyword>[\w:.-]+)(?:[ \t]*\(\))?|(?P<name>[\w:.-]+)[ \t]*\(\)"""
    r"""|@test[ \t]+(?P<test>"[^"\n]*"|'[^'\n]*'|\S+))[ \t]*\{[ \t]*(?:#.*)?$"""
)
ANSI_QUOTE = "$'"
COMMAND_SEPARATORS = frozenset(";&|")
CLOSER_FOLLOWERS = frozenset(";&|#")


class ShellLine(NamedTuple):
    number: int
    text: str
    fresh: bool
    depth: int


class _OpenHead(NamedTuple):
    line: int
    name: str
    depth: int


def closed_rows(
    path: str, text: str, rows: list[tuple[int, str, int]], limit: int,
    errors: tuple[tuple[int, int], ...] = (),
) -> list[tuple[int, str, int]]:
    if PurePath(path.lower()).suffix not in SHELL_SUFFIXES:
        return rows
    lines = text.split("\n")
    closes = column_zero_closes(lines)
    parser = {line: (name, end) for line, name, end in rows}
    settled = [
        (line, name, settled_end(parser[line][1] if line in parser else end, end, errors))
        for line, name, end in brace_rows(lines)
    ]
    starts = {row[0] for row in settled}
    clamped = [
        (line, name, settled_end(end, _clamped(line, end, lines, closes), errors))
        for line, name, end in rows if line not in starts
    ]
    return sorted(row for row in settled + clamped if row[2] - row[0] + 1 > limit)


def settled_end(parser_end: int, own_end: int, errors: tuple[tuple[int, int], ...]) -> int:
    if parser_end <= own_end:
        return own_end
    recovered = any(start <= parser_end and end > own_end for start, end in errors)
    return own_end if recovered else parser_end


def bats_test_rows(path: str, text: str, limit: int) -> list[tuple[int, str, int]]:
    if PurePath(path.lower()).suffix != ".bats":
        return []
    lines = text.split("\n")
    return [
        row for row in brace_rows(lines)
        if lines[row[0] - 1].startswith("@test") and row[2] - row[0] + 1 > limit
    ]


def brace_rows(lines: list[str]) -> list[tuple[int, str, int]]:
    rows: list[tuple[int, str, int]] = []
    open_head: _OpenHead | None = None
    last = 0
    for line in shell_lines(lines):
        last = line.number if line.text.strip() else last
        head = HEAD_RE.match(line.text) if line.fresh else None
        if head is not None:
            if open_head is not None:
                rows.append((open_head.line, open_head.name, line.number - 1))
            open_head = _OpenHead(line.number, _head_name(head), 1 + line.depth)
        elif open_head is not None:
            depth = open_head.depth + line.depth
            if depth <= 0:
                rows.append((open_head.line, open_head.name, line.number))
                open_head = None
            else:
                open_head = open_head._replace(depth=depth)
    if open_head is not None:
        rows.append((open_head.line, open_head.name, max(last, open_head.line)))
    return rows


def _head_name(head: re.Match[str]) -> str:
    return (head.group("keyword") or head.group("name") or head.group("test")).strip("\"'")


def column_zero_closes(lines: list[str]) -> list[int]:
    return [line.number for line in shell_lines(lines) if line.fresh and CLOSE_RE.match(line.text)]


def shell_lines(lines: list[str]) -> Iterator[ShellLine]:
    pending: list[tuple[str, bool]] = []
    quote = ""
    for number, line in enumerate(lines, 1):
        if pending:
            word, tabs = pending[0]
            if (line.lstrip("\t") if tabs else line) == word:
                pending.pop(0)
            continue
        fresh = not quote
        quote, depth = scan_shell_line(line, quote)
        yield ShellLine(number, line, fresh, depth)
        if not quote:
            pending.extend(
                (found.group("bare") or found.group("word"), bool(found.group("tabs")))
                for found in HEREDOC_RE.finditer(line)
            )


def scan_shell_line(line: str, quote: str) -> tuple[str, int]:
    depth = 0
    index = 0
    previous = ""
    while index < len(line):
        character = line[index]
        step = 1
        if quote:
            if character == "\\" and quote != "'":
                step = 2
            elif character == quote[-1]:
                quote = ""
        elif character == "\\":
            step = 2
        elif line.startswith(ANSI_QUOTE, index):
            quote, step = ANSI_QUOTE, 2
        elif character in "'\"":
            quote = character
        elif character == "#" and (index == 0 or line[index - 1].isspace()):
            break
        elif character in "{}" and _command_word(line, index, previous):
            depth += 1 if character == "{" else -1
        if not character.isspace():
            previous = "\\" if step == 2 and not quote else character
        index += step
    return quote, depth


def _command_word(line: str, index: int, previous: str) -> bool:
    if previous and previous not in COMMAND_SEPARATORS and not (previous == "{" and line[index] == "{"):
        return False
    following = line[index + 1:index + 2]
    if line[index] == "{":
        return following == "" or following.isspace()
    return following == "" or following.isspace() or following in CLOSER_FOLLOWERS


def _clamped(line: int, end: int, lines: list[str], closes: list[int]) -> int:
    if line > len(lines) or lines[line - 1][:1].isspace():
        return end
    index = bisect.bisect_right(closes, line)
    return closes[index] if index < len(closes) and closes[index] < end else end
