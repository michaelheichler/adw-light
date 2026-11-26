from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenSpan:
    start_row: int
    start_col: int
    end_row: int
    end_col: int

    def __post_init__(self) -> None:
        if self.start_row < 1 or self.end_row < self.start_row:
            raise ValueError("token span rows are invalid")
        if self.start_col < 0 or self.end_col < 0:
            raise ValueError("token span columns must be non-negative")
        if self.start_row == self.end_row and self.end_col < self.start_col:
            raise ValueError("single-line token span ends before it starts")


STRING_TOKEN_TYPES = frozenset(
    tok_type for tok_type in (
        tokenize.STRING,
        getattr(tokenize, "FSTRING_START", None),
        getattr(tokenize, "FSTRING_MIDDLE", None),
        getattr(tokenize, "FSTRING_END", None),
    )
    if tok_type is not None
)


def _token_span(token: tokenize.TokenInfo) -> tuple[TokenSpan, ...]:
    if token.type not in STRING_TOKEN_TYPES:
        return ()
    return (TokenSpan(token.start[0], token.start[1], token.end[0], token.end[1]),)


def _python_string_spans(text: str) -> tuple[list[TokenSpan], tuple[int, int] | None]:
    spans: list[TokenSpan] = []
    last_end = (1, 0)
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            spans.extend(_token_span(token))
            last_end = token.end
    except (tokenize.TokenError, SyntaxError, IndentationError, ValueError) as exc:
        return spans, _token_error_offset(exc) or last_end
    return spans, None


def mask_python_strings(text: str) -> str:
    spans, failure_start = _python_string_spans(text)
    if not spans and failure_start is None:
        return text
    lines = text.splitlines(keepends=True)
    for span in spans:
        _blank_token_span(lines, span)
    if failure_start is not None:
        _blank_from(lines, failure_start)
    return "".join(lines)


def _token_error_offset(exc: Exception) -> tuple[int, int] | None:
    if len(exc.args) >= 2 and isinstance(exc.args[1], tuple) and len(exc.args[1]) == 2:
        return exc.args[1]
    lineno = getattr(exc, "lineno", None)
    offset = getattr(exc, "offset", None)
    if lineno is not None and offset is not None:
        return (lineno, max(offset - 1, 0))
    return None


def _blank_from(lines: list[str], start: tuple[int, int]) -> None:
    if not lines:
        return
    row = max(1, min(start[0], len(lines)))
    index = row - 1
    line = lines[index]
    ending = "\n" if line.endswith("\n") else ""
    col = max(0, min(start[1], len(line) - len(ending)))
    lines[index] = line[:col] + " " * (len(line) - col - len(ending)) + ending
    for later in range(index + 1, len(lines)):
        ending = "\n" if lines[later].endswith("\n") else ""
        lines[later] = " " * (len(lines[later]) - len(ending)) + ending


def _blank_token_span(lines: list[str], span: TokenSpan) -> None:
    if span.start_row == span.end_row:
        line = lines[span.start_row - 1]
        lines[span.start_row - 1] = (
            line[:span.start_col]
            + " " * (span.end_col - span.start_col)
            + line[span.end_col:]
        )
        return
    first = lines[span.start_row - 1]
    ending = "\n" if first.endswith("\n") else ""
    lines[span.start_row - 1] = (
        first[:span.start_col]
        + " " * (len(first) - span.start_col - len(ending))
        + ending
    )
    for row in range(span.start_row, span.end_row - 1):
        middle = lines[row]
        ending = "\n" if middle.endswith("\n") else ""
        lines[row] = " " * (len(middle) - len(ending)) + ending
    last = lines[span.end_row - 1]
    lines[span.end_row - 1] = " " * span.end_col + last[span.end_col:]
