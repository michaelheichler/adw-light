from __future__ import annotations

import re
from collections.abc import Iterator
from typing import NamedTuple

from .commit_positional import script_named, substituted, tokenizable
from .shell_text import shell_tokens

NAME_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
SCRIPT_NAME_RE = re.compile(r"\$(?:0|\{0)")


class Function(NamedTuple):
    name: str
    body: str
    original: str


class _Token(NamedTuple):
    start: int
    end: int
    word: str


def extract_functions(text: str) -> tuple[str, dict[str, Function]]:
    tokens = _words(tokenizable(text))
    prefix = "__adw_function_"
    while prefix in text:
        prefix += "_"
    declarations: dict[str, Function] = {}
    replacements = []
    for name, start_index, opening, closing in _function_spans(tokens):
        start, end = tokens[start_index].start, tokens[closing].end
        marker = f"{prefix}{len(declarations)}"
        declarations[marker] = Function(name, text[tokens[opening].end:tokens[closing].start], text[start:end])
        replacements.append((start, end, marker))
    for start, end, marker in reversed(replacements):
        text = text[:start] + marker + text[end:]
    return text, declarations


def _words(scanned: str) -> list[_Token]:
    words: list[_Token] = []
    glued = False
    for token in shell_tokens(scanned):
        if token.match is None or token.match.lastgroup == "comment" or not token.match.group().strip():
            glued = False
            continue
        start, end = token.match.start(), token.match.end() if token.match.lastgroup == "separator" else token.end
        separator = token.match.lastgroup == "separator"
        if glued and not separator and words and words[-1].end == start:
            words[-1] = _Token(words[-1].start, end, scanned[words[-1].start:end])
        else:
            words.append(_Token(start, end, scanned[start:end]))
        glued = not separator
    return words


def _function_spans(tokens: list[_Token]) -> Iterator[tuple[str, int, int, int]]:
    index = 0
    while index < len(tokens):
        header = _function_header(tokens, index)
        closing = _closing_brace(tokens, header[1]) if header is not None else None
        if header is None or closing is None:
            index += 1
            continue
        yield header[0], index, header[1], closing
        index = closing + 1


def _function_header(tokens: list[_Token], index: int) -> tuple[str, int] | None:
    cursor = index + 1 if tokens[index].word == "function" else index
    if cursor >= len(tokens) or NAME_RE.fullmatch(tokens[cursor].word) is None:
        return None
    name = tokens[cursor].word
    cursor += 1
    parentheses = [token.word for token in tokens[cursor:cursor + 2]] == ["(", ")"]
    cursor += 2 if parentheses else 0
    if cursor >= len(tokens) or tokens[cursor].word != "{":
        return None
    return (name, cursor) if parentheses or tokens[index].word == "function" else None


def _closing_brace(tokens: list[_Token], opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(tokens)):
        depth += {"{": 1, "}": -1}.get(tokens[index].word, 0)
        if depth == 0:
            return index
    return None


def with_positional(text: str, positional: list[str], known: bool = False) -> str:
    if not positional and not known:
        return text
    prepared, declarations = extract_functions(text)
    return substituted(prepared, positional, {marker: function.original for marker, function in declarations.items()})


def with_script_name(text: str, operand: str, inside_functions: bool) -> str:
    if inside_functions:
        return script_named(text, operand, {})
    prepared, declarations = extract_functions(text)
    return script_named(prepared, operand, {marker: function.original for marker, function in declarations.items()})


def functions_use_script_name(text: str) -> bool:
    _prepared, declarations = extract_functions(text)
    return any(SCRIPT_NAME_RE.search(function.body) for function in declarations.values())
