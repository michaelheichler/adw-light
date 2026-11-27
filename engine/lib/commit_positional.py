from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

try:
    from .commit_shell_structure import _items, _Structure
    from .shell_text import _closing_paren, _quote_end
except ImportError:
    from commit_shell_structure import _items, _Structure
    from shell_text import _closing_paren, _quote_end

SIMPLE_RE = re.compile(r"\$([1-9@*])|\$\{([1-9@*])\}")
SCRIPT_NAME_RE = re.compile(r"\$0|\$\{0\}")
OPERATOR_RE = re.compile(r"\$\{([1-9@*])(:?)([-=?+])")
COMPLEX_WORD_CHARS = frozenset("$`{}'\"\\")
DOUBLE_QUOTE_SPECIAL_RE = re.compile(r'["\\`]|\$(?=\()')
COMMENT_LEADERS = frozenset(" \t\n;&|()")
ANSI_C_RE = re.compile(r"\$'(?:\\.|[^'\\])*'?", re.DOTALL)
SLICE_RE = re.compile(r"\$\{([@*]):([0-9]+)(?::([0-9]+))?\}")
MIXED_HEREDOC_RE = re.compile(r"(?P<operator><<-?[ \t]*)(?P<word>(?=[^\s;&|()<>]*['\"\\])[^\s;&|()<>]+)")


def tokenizable(text: str) -> str:
    text = ANSI_C_RE.sub(lambda match: "_" * len(match.group()), text)
    return MIXED_HEREDOC_RE.sub(_quoted_delimiter, text)


def _quoted_delimiter(match: re.Match[str]) -> str:
    word = match.group("word")
    plain = re.sub(r"[\\'\"]", "", word)
    quoted = f"'{plain}'"
    return match.group("operator") + quoted + " " * (len(word) - len(quoted)) if len(quoted) <= len(word) else match.group()


def _structure(text: str) -> _Structure:
    structure = _Structure()
    for item in _items(tokenizable(text)):
        if item.operator:
            structure.operator(item)
        else:
            structure.word(item)
    structure.finish("end")
    return structure


def _double_escaped(value: str) -> str:
    return DOUBLE_QUOTE_SPECIAL_RE.sub(r"\\\g<0>", value)


def _single_escaped(value: str) -> str:
    return value.replace("'", "'\"'\"'")


def _rendered(name: str, positional: list[str], context: str) -> str:
    if name in "@*":
        return _rendered_all(name, positional, context)
    index = int(name) - 1
    return _rendered_value(positional[index], context) if index < len(positional) else ""


def _rendered_value(value: str, context: str) -> str:
    if context == "double":
        return _double_escaped(value)
    if context == "single":
        return _single_escaped(value)
    if context == "single_double":
        return _single_escaped(_double_escaped(value))
    return " ".join(shlex.quote(word) for word in value.split())


def _rendered_all(name: str, positional: list[str], context: str) -> str:
    if context == "plain":
        return shlex.join(word for value in positional for word in value.split())
    if context == "single":
        return _single_escaped(" ".join(positional))
    if name == "*":
        return _rendered_value(" ".join(positional), context)
    joined = '" "'.join(_double_escaped(value) for value in positional)
    return joined if context == "double" else _single_escaped(joined)


def _brace_end(text: str, cursor: int) -> int | None:
    depth = 1
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
        elif char == "'":
            close = text.find("'", cursor + 1)
            cursor = len(text) if close < 0 else close + 1
        elif char in "\"`":
            cursor = _quote_end(text, cursor + 1, char)
        elif text.startswith("$(", cursor):
            cursor = _closing_paren(text, cursor + 2) + 1
        elif text.startswith("${", cursor):
            depth, cursor = depth + 1, cursor + 2
        else:
            depth -= char == "}"
            if depth == 0:
                return cursor
            cursor += 1
    return None


def _state(name: str, positional: list[str]) -> tuple[bool, bool]:
    if name in "@*":
        return bool(positional), bool(" ".join(positional))
    index = int(name) - 1
    present = index < len(positional)
    return present, present and positional[index] != ""


def _operator_expansion(match: re.Match[str], close: int, positional: list[str], context: str) -> str:
    text = match.string
    name, colon, operator = match.groups()
    word = text[match.end():close]
    complex_word = any(char in COMPLEX_WORD_CHARS for char in word)
    present, filled = _state(name, positional)
    decided = filled if colon else present
    if operator == "?":
        return _rendered(name, positional, context) if decided else ""
    if operator == "+" and not decided:
        return ""
    if operator != "+" and decided:
        return _rendered(name, positional, context)
    if not complex_word:
        return word
    literal = _quoted_literal(word)
    if literal is None:
        return text[match.start():close + 1]
    return shlex.quote(literal) if context == "plain" else _rendered_value(literal, context)


def _sliced(match: re.Match[str], positional: list[str], context: str) -> tuple[str, int] | None:
    name, offset, length = match.group(1), int(match.group(2)), match.group(3)
    if offset == 0:
        return None
    selected = positional[offset - 1:]
    selected = selected[:int(length)] if length is not None else selected
    return _rendered_all(name, selected, context), match.end()


def _quoted_literal(word: str) -> str | None:
    if any(char in word for char in "$`{}"):
        return None
    try:
        parts = shlex.split(word)
    except ValueError:
        return None
    return parts[0] if len(parts) == 1 else None


@dataclass(slots=True)
class _Substitution:
    text: str
    positional: list[str]
    structure: _Structure
    markers: dict[int, tuple[int, str]]
    zero: str | None = None

    def positional_at(self, position: int) -> list[str] | None:
        if position >= self.structure.unknown_from:
            return None
        current = self.positional
        for shift in self.structure.shifts:
            if not shift.end <= position < shift.limit:
                continue
            if shift.replacement is not None:
                current = list(shift.replacement)
            elif shift.shift_count <= len(current):
                current = current[shift.shift_count:]
        return current

    def parameter(self, cursor: int, context: str, end: int) -> tuple[str, int] | None:
        if self.zero is not None:
            named = SCRIPT_NAME_RE.match(self.text, cursor, end)
            return (_rendered_value(self.zero, context), named.end()) if named is not None else None
        positional = self.positional_at(cursor)
        if positional is None:
            return None
        simple = SIMPLE_RE.match(self.text, cursor, end)
        if simple is not None:
            return _rendered(simple.group(1) or simple.group(2), positional, context), simple.end()
        sliced = SLICE_RE.match(self.text, cursor, end)
        if sliced is not None:
            return _sliced(sliced, positional, context)
        match = OPERATOR_RE.match(self.text, cursor, end)
        close = _brace_end(self.text, match.end()) if match is not None else None
        if match is None or close is None or close >= end:
            return None
        return _operator_expansion(match, close, positional, context), close + 1

    def plain(self, cursor: int, end: int) -> str:
        pieces: list[str] = []
        while cursor < end:
            piece, cursor = self.plain_step(cursor, end)
            pieces.append(piece)
        return "".join(pieces)

    def plain_step(self, cursor: int, end: int) -> tuple[str, int]:
        text, char = self.text, self.text[cursor]
        if cursor in self.markers:
            marker_end, original = self.markers[cursor]
            return original, marker_end
        if char == "\\":
            return text[cursor:cursor + 2], cursor + 2
        if text.startswith("$'", cursor):
            close = _ansi_end(text, cursor + 2, end)
            return text[cursor:close], close
        if char == "'":
            close = text.find("'", cursor + 1, end)
            close = end if close < 0 else close + 1
            return self.single(cursor, close), close
        if char == '"':
            close = _double_end(text, cursor + 1, end) or min(_quote_end(text, cursor + 1, '"'), end)
            return self.double(cursor, close), close
        if char == "#" and (cursor == 0 or text[cursor - 1] in COMMENT_LEADERS):
            newline = text.find("\n", cursor, end)
            close = end if newline < 0 else newline
            return text[cursor:close], close
        return self.nested_step(cursor, end, "plain")

    def nested_step(self, cursor: int, end: int, context: str) -> tuple[str, int]:
        text = self.text
        if text[cursor] == "`":
            close = min(_quote_end(text, cursor + 1, "`"), end)
            closed = close > cursor + 1 and text[close - 1] == "`"
            return "`" + self.plain(cursor + 1, close - closed) + ("`" if closed else ""), close
        if text.startswith("$(", cursor):
            close = min(_closing_paren(text, cursor + 2), end)
            closed = close < end
            return "$(" + self.plain(cursor + 2, close) + (")" if closed else ""), close + closed
        if text[cursor] == "$":
            found = self.parameter(cursor, context, end)
            if found is not None:
                return found
        return text[cursor], cursor + 1

    def double(self, start: int, close: int) -> str:
        text = self.text
        positional = self.positional_at(start)
        if self.zero is None and positional is not None and text[start:close] in ('"$@"', '"${@}"'):
            return shlex.join(positional)
        pieces, cursor = [text[start]], start + 1
        inner_end = close - 1 if close - 1 > start and text[close - 1] == '"' else close
        while cursor < inner_end:
            if text[cursor] == "\\":
                pieces.append(text[cursor:cursor + 2])
                cursor += 2
                continue
            piece, cursor = self.nested_step(cursor, inner_end, "double")
            pieces.append(piece)
        pieces.append(text[inner_end:close])
        return "".join(pieces)

    def single(self, start: int, close: int) -> str:
        text = self.text
        if start in self.structure.literal_quotes:
            return text[start:close]
        pieces, cursor, quoted = [], start, False
        while cursor < close:
            char = text[cursor]
            quoted = quoted != (char == '"' and cursor > start)
            found = self.parameter(cursor, "single_double" if quoted else "single", close) if char == "$" else None
            step = min(2, close - cursor) if char == "\\" and cursor > start else 1
            piece, cursor = found if found is not None else (text[cursor:cursor + step], cursor + step)
            pieces.append(piece)
        return "".join(pieces)


def _double_end(text: str, cursor: int, end: int) -> int | None:
    while cursor < end:
        char = text[cursor]
        if char == '"':
            return cursor + 1
        if text.startswith("${", cursor):
            close = _brace_end(text, cursor + 2)
            if close is None:
                return None
            cursor = close + 1
        elif text.startswith("$(", cursor):
            cursor = _closing_paren(text, cursor + 2) + 1
        elif char in "\\`":
            cursor = cursor + 2 if char == "\\" else _quote_end(text, cursor + 1, "`")
        else:
            cursor += 1
    return None


def _ansi_end(text: str, cursor: int, end: int) -> int:
    while cursor < end:
        if text[cursor] == "\\":
            cursor += 2
        elif text[cursor] == "'":
            return cursor + 1
        else:
            cursor += 1
    return end


def _marker_spans(prepared: str, originals: dict[str, str]) -> dict[int, tuple[int, str]]:
    if not originals:
        return {}
    pattern = "|".join(re.escape(marker) for marker in sorted(originals, key=len, reverse=True))
    return {match.start(): (match.end(), originals[match.group()])
            for match in re.finditer(rf"(?<![\w])(?:{pattern})(?![\w])", prepared)}


def substituted(prepared: str, positional: list[str], originals: dict[str, str]) -> str:
    substitution = _Substitution(prepared, positional, _structure(prepared), _marker_spans(prepared, originals))
    return substitution.plain(0, len(prepared))


def script_named(prepared: str, operand: str, originals: dict[str, str]) -> str:
    substitution = _Substitution(prepared, [], _structure(prepared), _marker_spans(prepared, originals), zero=operand)
    return substitution.plain(0, len(prepared))
