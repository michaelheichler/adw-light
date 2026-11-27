from __future__ import annotations

import ast
import io
import keyword
import re
import tokenize
from enum import StrEnum
from typing import NamedTuple

try:
    from .comment_rules import DIRECTIVE_COMMENT_RE, HEADER_COMMENT_RE
except ImportError:
    from comment_rules import DIRECTIVE_COMMENT_RE, HEADER_COMMENT_RE


class Kind(StrEnum):
    CODE = "code"
    IDENTIFIER = "identifier"
    STRING = "string"
    STRING_TEXT = "string_text"
    TEMPLATE_TEXT = "template_text"
    LINE_COMMENT = "line_comment"
    BLOCK_COMMENT = "block_comment"
    DIRECTIVE = "directive"
    HEREDOC = "heredoc"
    REGEX = "regex"
    ERROR = "error"


class UnitMark(StrEnum):
    NONE = ""
    MARKED = "marked"
    NAMED = "named"


TEXT_KINDS = frozenset({Kind.STRING_TEXT, Kind.TEMPLATE_TEXT, Kind.HEREDOC, Kind.REGEX})
COMMENT_KINDS = frozenset({Kind.LINE_COMMENT, Kind.BLOCK_COMMENT, Kind.DIRECTIVE})
PYTHON = "python"
BLOCK_OPENERS = ("/*", "<!--", "{-", "(*", "--[[", "=begin", '"""', "'''")
COMMENT_MARKER_RE = re.compile(r"^(?:/\*+|//+|#+|--+|;+|%+|<!--)\s*")
BLANK_RE = re.compile(r"[^\n]")


class Node(NamedTuple):
    kind: Kind
    start: int
    end: int


class CodeUnit(NamedTuple):
    name: str
    start: int
    end: int
    body: int
    test: UnitMark
    expects_failure: bool = False


class Layer(NamedTuple):
    language: str
    precise: bool
    nodes: tuple[Node, ...]
    reason: str = ""
    units: tuple[CodeUnit, ...] = ()

    @property
    def errors(self) -> tuple[Node, ...]:
        return tuple(node for node in self.nodes if node.kind is Kind.ERROR)

    def spans(self, kinds: frozenset[Kind]) -> list[tuple[int, int]]:
        return [(node.start, node.end) for node in self.nodes if node.kind in kinds]


def lexer_layer(language: str, reason: str) -> Layer:
    return Layer(language, False, (), reason)


def comment_kind(text: str, start: int) -> Kind:
    stripped = text.strip()
    body = COMMENT_MARKER_RE.sub("", stripped)
    if start == 0 and stripped.startswith("#!"):
        return Kind.DIRECTIVE
    if DIRECTIVE_COMMENT_RE.match(stripped) or HEADER_COMMENT_RE.search(body):
        return Kind.DIRECTIVE
    if stripped.startswith(BLOCK_OPENERS) or "\n" in stripped:
        return Kind.BLOCK_COMMENT
    return Kind.LINE_COMMENT


def blanked(text: str, spans: list[tuple[int, int]]) -> str:
    parts: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        start = max(start, cursor)
        if end <= start:
            continue
        parts.append(text[cursor:start])
        parts.append(BLANK_RE.sub(" ", text[start:end]))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def shifted(layer: Layer, offset: int) -> Layer:
    nodes = tuple(Node(node.kind, node.start + offset, node.end + offset) for node in layer.nodes)
    return layer._replace(nodes=nodes)


def _line_starts(text: str) -> list[int]:
    starts = [0]
    starts.extend(index + 1 for index, character in enumerate(text) if character == "\n")
    return starts


def _token_kinds() -> dict[int, Kind]:
    kinds = {tokenize.NAME: Kind.IDENTIFIER, tokenize.ERRORTOKEN: Kind.ERROR}
    for name in ("FSTRING_MIDDLE", "TSTRING_MIDDLE"):
        if hasattr(tokenize, name):
            kinds[getattr(tokenize, name)] = Kind.STRING_TEXT
    return kinds


TOKEN_KINDS = _token_kinds()
OPENING_TOKENS = frozenset(getattr(tokenize, name) for name in ("FSTRING_START", "TSTRING_START") if hasattr(tokenize, name))
CLOSING_TOKENS = frozenset(getattr(tokenize, name) for name in ("FSTRING_END", "TSTRING_END") if hasattr(tokenize, name))


class _PythonWalk:
    def __init__(self, text: str) -> None:
        self.text = text
        self.starts = _line_starts(text)
        self.nodes: list[Node] = []
        self.openings: list[int] = []

    def offset(self, point: tuple[int, int]) -> int:
        row, column = point
        return min(self.starts[min(row, len(self.starts)) - 1] + column, len(self.text))

    def token(self, token: tokenize.TokenInfo) -> None:
        start, end = self.offset(token.start), self.offset(token.end)
        if token.type == tokenize.COMMENT:
            self.nodes.append(Node(comment_kind(token.string, start), start, end))
        elif token.type == tokenize.STRING:
            self.nodes.extend((Node(Kind.STRING, start, end), Node(Kind.STRING_TEXT, start, end)))
        elif token.type in OPENING_TOKENS:
            self.openings.append(start)
        elif token.type in CLOSING_TOKENS and self.openings:
            self.nodes.append(Node(Kind.STRING, self.openings.pop(), end))
        elif token.type in TOKEN_KINDS and not keyword.iskeyword(token.string):
            self.nodes.append(Node(TOKEN_KINDS[token.type], start, end))

    def failure(self, error: Exception) -> None:
        line = getattr(error, "lineno", None)
        if line is None and len(error.args) > 1 and isinstance(error.args[1], tuple):
            line = error.args[1][0]
        start = self.starts[max(1, min(int(line or 1), len(self.starts))) - 1]
        self.nodes.append(Node(Kind.ERROR, start, len(self.text)))


def python_layer(text: str) -> Layer:
    walk = _PythonWalk(text)
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            walk.token(token)
    except (tokenize.TokenError, IndentationError, SyntaxError) as error:
        walk.failure(error)
    try:
        ast.parse(text)
    except (SyntaxError, ValueError, MemoryError, RecursionError) as error:
        walk.failure(error)
    return Layer(PYTHON, True, tuple(sorted(walk.nodes, key=lambda node: (node.start, -node.end))))
