from __future__ import annotations

import re
import shlex

from .commit_inputs import PLAIN_COMMIT, Input, UnreadableCommit
from .commit_shapes import COMMAND_LEADERS, LiteralWord, Piece, shaped
from .shell_operators import SEPARATORS
from .shell_text import (
    INLINE_INPUT,
    SUBSTITUTED,
    Redirect,
    ShellBody,
    ShellToken,
    _closing_paren,
    _normalize_ansi_c_quotes,
    _quote_end,
    _quoted_substitutions,
    _Scanner,
    _substitution_spans,
)

RESERVED_WORDS = COMMAND_LEADERS
QUOTE_MARK = "\ue010"
HOIST_MARK = "\ue011"
QUOTE_START_RE = re.compile(r"[\\'\"]")
PUNCTUATION_RUN_RE = re.compile(r"[;&|()\n]+")
SHELL_OPERATOR_RE = re.compile(r"&&|\|\||\|&|[;&|()]|\n")
UNREADABLE_QUOTING = (
    "The commit gate cannot read the quoting in this command, so it cannot see what the commit records. "
    f"{PLAIN_COMMIT}"
)


class _CommitScanner(_Scanner):
    plain_scans = False

    def segments(self) -> list[list[str]]:
        text = self.text()
        readable = not self.plain_scans and not any(mark in self.command for mark in (QUOTE_MARK, HOIST_MARK))
        try:
            lexer = shlex.shlex(_marked_quotes(text) if readable else text, posix=True, punctuation_chars=";&|()\n")
            lexer.whitespace, lexer.whitespace_split, lexer.commenters = " \t\r", True, ""
            pieces = _hoisted_groups([piece for token in lexer for piece in _pieces(token, readable)], readable)
        except ValueError:
            if "commit" in text:
                raise UnreadableCommit(UNREADABLE_QUOTING) from None
            return []
        return _split_segments(shaped(pieces))

    def _nested_text(self, body: str) -> str:
        nested = _CommitScanner(body)
        text = nested.text()
        self.plain_scans = self.plain_scans or nested.plain_scans
        return f"{HOIST_MARK} {text}"

    def _herestring(self, match: re.Match) -> None:
        self.plain_scans = self.plain_scans or self._shell_reads(match.start())
        super()._herestring(match)

    def _bodies(self, bodies: tuple[ShellBody, ...]) -> None:
        plain = any(heredoc.read_by_shell or not body.closed for heredoc, body in zip(self.pending, bodies))
        self.plain_scans = self.plain_scans or plain
        super()._bodies(bodies)

    def _take(self, token: ShellToken) -> None:
        match = token.match
        if match is None or match.lastgroup != "double":
            super()._take(token)
            return
        word = self.command[match.start():token.end]
        self._lead_with(_command_bodies(word))
        self.parts.append(_quoted_substitutions(word))

    def _lead_with(self, bodies: list[str]) -> None:
        for body in bodies:
            self.parts.insert(self.command_part, f"( {self._nested_text(body)}\n)\n")
            self.command_part += 1

    def _replace(self, token: ShellToken) -> None:
        match = token.match
        if match is None:
            super()._replace(token)
            return
        body = self.command[match.end():token.end - 1]
        if match.lastgroup == "subst" and _is_arithmetic(body):
            self._lead_with(_command_bodies(body[1:-1]))
            self.parts.append(SUBSTITUTED)
            return
        if match.lastgroup != "procsub":
            super()._replace(token)
            return
        self.parts.insert(self.command_part, f"( {self._nested_text(body)}\n) | ")
        self.command_part += 1
        self._process_file()


def segments(command: str | list[str]) -> list[list[str]]:
    if isinstance(command, list):
        return _split_segments(command)
    return _CommitScanner(_normalize_ansi_c_quotes(command.split("\0", 1)[0])).segments()


def command_words(segment: list[str]) -> list[str]:
    index = 0
    while index < len(segment) and (segment[index] in RESERVED_WORDS or segment[index] == "function"):
        index += 2 if segment[index] == "function" else 1
    return segment[index:]


def bind_input(inputs: dict[int, Input], redirect: Redirect) -> None:
    if redirect.operator in {"<", "<>"}:
        inputs[redirect.fd] = Input(path=redirect.target)
        return
    if redirect.operator == INLINE_INPUT:
        inputs[redirect.fd] = Input(text=redirect.target)
        return
    if redirect.operator == "<&" and redirect.target.isdigit() and int(redirect.target) in inputs:
        inputs[redirect.fd] = inputs[int(redirect.target)]


def is_assignment(token: str) -> bool:
    name, separator, _value = token.partition("=")
    return bool(separator and name and name.isidentifier())


def alias_definitions(args: list[str]) -> dict[str, str]:
    return {name: value for name, separator, value in (arg.partition("=") for arg in args) if separator}


def _pieces(token: str, readable: bool) -> list[Piece]:
    if token == HOIST_MARK:
        return [Piece(token, False)]
    quoted = not readable or QUOTE_MARK in token
    token = token.replace(QUOTE_MARK, "").replace(HOIST_MARK, "")
    if readable and quoted or not PUNCTUATION_RUN_RE.fullmatch(token):
        return [Piece(token, False, quoted=quoted)]
    operators = SHELL_OPERATOR_RE.findall(token)
    return [Piece(operator, True, index > 0, quoted) for index, operator in enumerate(operators)]


def _hoisted_groups(pieces: list[Piece], readable: bool) -> list[Piece]:
    folded: list[Piece] = []
    for piece in pieces:
        if piece.text != HOIST_MARK or piece.operator:
            folded.append(piece)
            continue
        if readable and folded and folded[-1].operator and folded[-1].text == "(" and not folded[-1].quoted:
            folded[-1] = folded[-1]._replace(hoisted=True)
    return folded


def _marked_quotes(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    while (match := QUOTE_START_RE.search(text, cursor)) is not None:
        start = match.start()
        if match.group() == "\\":
            parts.extend([text[cursor:start], QUOTE_MARK, text[start:start + 2]])
            cursor = start + 2
            continue
        end = _quote_end(text, start + 1, match.group())
        parts.extend([text[cursor:start + 1], QUOTE_MARK, text[start + 1:end]])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _is_arithmetic(body: str) -> bool:
    return body.startswith("(") and _closing_paren(body, 1) == len(body) - 1


def _command_bodies(text: str) -> list[str]:
    bodies: list[str] = []
    for start, _end, body in _substitution_spans(text):
        if text.startswith("$((", start) and _is_arithmetic(body):
            bodies.extend(_command_bodies(body[1:-1]))
        else:
            bodies.append(body)
    return bodies


def _split_segments(tokens: list[str]) -> list[list[str]]:
    result: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in SEPARATORS and not isinstance(token, LiteralWord):
            result.extend([current, [token]])
            current = []
            continue
        current.append(token)
    result.append(current)
    return result
