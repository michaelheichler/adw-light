from __future__ import annotations

import bisect
import io
import re
import tokenize
from typing import NamedTuple

EXTENSIONS = {
    "java": ".java", "python": ".py", "c": ".c", "cpp": ".cpp", "csharp": ".cs", "javascript": ".js",
    "typescript": ".ts", "php": ".php", "go": ".go", "ruby": ".rb", "sql": ".sql",
}
LINE_MARKERS = {"python": "# ", "ruby": "# ", "sql": "-- "}
SLASH_MARKER = "// "
HASH_LANGUAGES = frozenset({"python", "ruby", "php"})
SLASH_LANGUAGES = frozenset(EXTENSIONS) - {"python", "ruby", "sql"}
SLASH_OPEN_RE = re.compile(r"^\s*(?:/\*+|\*+(?!/)|//+)\s?")
HASH_OPEN_RE = re.compile(r"^\s*#+\s?")
DASH_OPEN_RE = re.compile(r"^\s*--\s?")
CLOSE_RE = re.compile(r"\s*\*+/\s*$")


class Row(NamedTuple):
    source: str
    language: str
    label: str
    comment: str
    snippet: str


class Mark(NamedTuple):
    text: str
    only: bool


def comment_lines(language: str, raw: str) -> list[str]:
    openers = [SLASH_OPEN_RE]
    if language in HASH_LANGUAGES:
        openers.append(HASH_OPEN_RE)
    if language == "sql":
        openers.append(DASH_OPEN_RE)
    lines = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        body = line
        for opener in openers:
            body = opener.sub("", body, count=1)
        body = CLOSE_RE.sub("", body).strip()
        if body:
            lines.append(body)
    return lines


def rendered(language: str, lines: list[str], code: str = "") -> str:
    marker = LINE_MARKERS.get(language, SLASH_MARKER)
    body = "".join(marker + line + "\n" for line in lines)
    return body + (code.strip() + "\n" if code.strip() else "")


def text_row(source: str, language: str, label: str, raw: str, code: str = "") -> Row | None:
    lines = comment_lines(language, raw)
    if not lines:
        return None
    return Row(source, language, label, "\n".join(lines), rendered(language, lines, code))


def code_rows(source: str, language: str, label: str, code: str) -> list[Row]:
    lines = code.replace("\r\n", "\n").split("\n")
    marks = _line_marks(code.replace("\r\n", "\n"), language)
    rows = []
    index = 0
    while index < len(lines):
        mark = marks.get(index)
        if mark is None:
            index += 1
            continue
        end = _run_end(marks, index) if mark.only else index
        row = _run_row(source, language, label, lines, marks, index, end)
        if row is not None:
            rows.append(row)
        index = end + 1
    return rows


def _run_end(marks: dict[int, Mark], start: int) -> int:
    end = start
    while end + 1 in marks and marks[end + 1].only:
        end += 1
    return end


def _run_row(source: str, language: str, label: str, lines: list[str], marks: dict[int, Mark], start: int, end: int) -> Row | None:
    texts = [marks[number].text for number in range(start, end + 1)]
    comment = comment_lines(language, "\n".join(texts))
    if not comment:
        return None
    if not marks[start].only:
        return Row(source, language, label, "\n".join(comment), lines[start].strip() + "\n")
    follow = next((number for number in range(end + 1, len(lines)) if lines[number].strip() and number not in marks), None)
    picked = lines[start:end + 1] + ([lines[follow]] if follow is not None else [])
    return Row(source, language, label, "\n".join(comment), "".join(line.strip() + "\n" for line in picked))


def _line_marks(code: str, language: str) -> dict[int, Mark]:
    starts = [0] + [index + 1 for index, char in enumerate(code) if char == "\n"]
    pieces: dict[int, list[tuple[int, int]]] = {}
    for begin, finish in _spans(code, language, starts):
        first = bisect.bisect_right(starts, begin) - 1
        last = bisect.bisect_right(starts, max(begin, finish - 1)) - 1
        for number in range(first, last + 1):
            line_end = starts[number + 1] - 1 if number + 1 < len(starts) else len(code)
            pieces.setdefault(number, []).append((max(begin, starts[number]), min(finish, line_end)))
    return {number: _mark(code, starts, number, spans) for number, spans in pieces.items()}


def _mark(code: str, starts: list[int], number: int, spans: list[tuple[int, int]]) -> Mark:
    line_end = starts[number + 1] - 1 if number + 1 < len(starts) else len(code)
    rest, cursor, text = [], starts[number], []
    for begin, finish in sorted(spans):
        rest.append(code[cursor:begin])
        text.append(code[begin:finish])
        cursor = finish
    rest.append(code[cursor:line_end])
    return Mark(" ".join(text), not "".join(rest).strip())


def _spans(code: str, language: str, starts: list[int]) -> list[tuple[int, int]]:
    if language == "python":
        found = _python_spans(code, starts)
        if found is not None:
            return found
    return _scanned_spans(code, language in SLASH_LANGUAGES, language in HASH_LANGUAGES)


def _python_spans(code: str, starts: list[int]) -> list[tuple[int, int]] | None:
    spans = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(code).readline):
            if token.type == tokenize.COMMENT:
                begin = starts[token.start[0] - 1] + token.start[1]
                spans.append((begin, begin + len(token.string)))
    except (tokenize.TokenError, IndentationError, SyntaxError, IndexError):
        return None
    return spans


def _scanned_spans(code: str, slashes: bool, hashes: bool) -> list[tuple[int, int]]:
    spans, index, quote = [], 0, ""
    while index < len(code):
        char = code[index]
        if quote:
            index, quote = _string_step(code, index, quote)
            continue
        end = _comment_end(code, index, slashes, hashes)
        if end:
            spans.append((index, end))
            index = end
            continue
        quote = char if char in "\"'`" else ""
        index += 1
    return spans


def _string_step(code: str, index: int, quote: str) -> tuple[int, str]:
    char = code[index]
    if char == "\\" and quote != "`":
        return index + 2, quote
    if char == quote or (char == "\n" and quote != "`"):
        return index + 1, ""
    return index + 1, quote


def _comment_end(code: str, index: int, slashes: bool, hashes: bool) -> int:
    if (slashes and code.startswith("//", index)) or (hashes and code[index] == "#"):
        end = code.find("\n", index)
        return len(code) if end < 0 else end
    if slashes and code.startswith("/*", index):
        end = code.find("*/", index + 2)
        return len(code) if end < 0 else end + 2
    return 0
