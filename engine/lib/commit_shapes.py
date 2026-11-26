from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple

COMMAND_LEADERS = frozenset({"!", "{", "}", "if", "then", "else", "elif", "do", "while", "until", "coproc"})
DECLARERS = frozenset({"local", "declare", "typeset", "readonly", "export"})
CASE_TERMINATORS = ((";", ";", "&"), (";", ";"), (";", "&"))
CONDITION_OPERATORS = frozenset({"(", ")", "|", "&&", "||", "\n"})
ARRAY_TARGET_RE = re.compile(r"[A-Za-z_]\w*\+?=")
ARITHMETIC_STAND_IN = ":"


class LiteralWord(str):
    __slots__ = ()


class Piece(NamedTuple):
    text: str
    operator: bool
    joined: bool = False
    quoted: bool = False
    hoisted: bool = False


class _Span(NamedTuple):
    groups: list[str]
    top: list[Piece]
    end: int


class _Abandoned(Exception):
    pass


def _word_text(piece: Piece) -> str:
    return LiteralWord(piece.text) if piece.quoted else piece.text


def shaped(pieces: list[Piece]) -> list[str]:
    shaper = _Shaper(pieces)
    shaper.commands(in_case=False)
    return shaper.out


@dataclass(slots=True)
class _Shaper:
    pieces: list[Piece]
    cursor: int = 0
    out: list[str] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    command_start: int = 0

    def commands(self, in_case: bool) -> bool:
        while self.cursor < len(self.pieces):
            if in_case and self._branch_end():
                return True
            self._step()
        return False

    def _step(self) -> None:
        piece = self.pieces[self.cursor]
        if not self.words and self._special(piece):
            return
        if piece.operator:
            self._emit_break(piece.text)
            self.cursor += 1
            return
        if self._array_allowed() and self._attempt(self._array):
            return
        self.out.append(_word_text(piece))
        if piece.text not in COMMAND_LEADERS or piece.quoted or self.words:
            self.words.append(piece.text)
        self.cursor += 1

    def _special(self, piece: Piece) -> bool:
        if piece.operator:
            return self._opens_arithmetic(self.cursor) and self._attempt(lambda: self._arithmetic(self.cursor))
        keyword = self._keyword_at(self.cursor)
        if keyword == "for" and self._opens_arithmetic(self.cursor + 1):
            return self._attempt(lambda: self._arithmetic(self.cursor + 1))
        if keyword == "[[":
            return self._attempt(self._condition)
        return keyword == "case" and self._attempt(self._case)

    def _attempt(self, parse) -> bool:
        saved = (self.cursor, len(self.out), list(self.words), self.command_start)
        try:
            parse()
        except _Abandoned:
            self.cursor, length, self.words, self.command_start = saved
            del self.out[length:]
            return False
        return True

    def _emit_break(self, text: str) -> None:
        self.out.append(";" if text == "\n" else text)
        self.words = []
        self.command_start = len(self.out)

    def _lead_with(self, groups: list[str]) -> None:
        self.out[self.command_start:self.command_start] = groups
        self.command_start += len(groups)

    def _opens_arithmetic(self, index: int) -> bool:
        return self._op(index) == "(" and self._op(index + 1) == "(" and self.pieces[index + 1].joined

    def _arithmetic(self, start: int) -> None:
        span = self._span(start + 2, self._arithmetic_closer)
        self._lead_with(span.groups)
        self.out.append(ARITHMETIC_STAND_IN)
        self.words = [ARITHMETIC_STAND_IN]
        self.cursor = span.end

    def _arithmetic_closer(self, index: int) -> int:
        if self._op(index) != ")":
            return 0
        if self._op(index + 1) == ")" and self.pieces[index + 1].joined:
            return 2
        raise _Abandoned

    def _condition(self) -> None:
        span = self._span(self.cursor + 1, lambda index: int(self._keyword_at(index) == "]]"))
        if any(piece.operator and not piece.quoted and piece.text not in CONDITION_OPERATORS for piece in span.top):
            raise _Abandoned
        self._lead_with(span.groups)
        words = ["[[", *(_word_text(piece) for piece in span.top if piece.quoted or not piece.operator), "]]"]
        self.out.extend(words)
        self.words = words
        self.cursor = span.end

    def _array_allowed(self) -> bool:
        target = self._keyword_at(self.cursor)
        if target is None or not ARRAY_TARGET_RE.fullmatch(target) or self._op(self.cursor + 1) != "(":
            return False
        return all(ARRAY_TARGET_RE.match(word) for word in self.words) or self.words[0] in DECLARERS

    def _array(self) -> None:
        target = self.pieces[self.cursor].text
        span = self._span(self.cursor + 2, lambda index: int(self._op(index) == ")"))
        if any(piece.operator and not piece.quoted and piece.text != "\n" for piece in span.top):
            raise _Abandoned
        self._lead_with(span.groups)
        elements = [piece.text for piece in span.top if piece.quoted or not piece.operator]
        value = f"{target}({' '.join(elements)})"
        self.out.append(value)
        self.words.append(value)
        self.cursor = span.end

    def _span(self, start: int, closer) -> _Span:
        groups: list[str] = []
        top: list[Piece] = []
        depth, index = 0, start
        while index < len(self.pieces):
            if self.pieces[index].hoisted and self._op(index) == "(":
                end = self._group_end(index)
                groups.extend(["(", *shaped(self.pieces[index + 1:end]), ")"])
                index = end + 1
                continue
            width = closer(index) if depth == 0 else 0
            if width:
                return _Span(groups, top, index + width)
            depth += self._depth_change(index)
            if depth < 0:
                raise _Abandoned
            top.append(self.pieces[index])
            index += 1
        raise _Abandoned

    def _group_end(self, start: int) -> int:
        depth = 0
        for index in range(start, len(self.pieces)):
            depth += self._depth_change(index)
            if depth == 0:
                return index
        raise _Abandoned

    def _depth_change(self, index: int) -> int:
        return {"(": 1, ")": -1}.get(self._op(index) or "", 0)

    def _case(self) -> None:
        self._case_head()
        while True:
            self._skip_newlines()
            if self._keyword_at(self.cursor) == "esac":
                self._close_case()
                return
            self._pattern()
            self._emit_break(";")
            if not self.commands(in_case=True):
                raise _Abandoned
            if not self._terminator():
                self._close_case()
                return
            self.cursor += len(self._terminator())
            self._emit_break(";")

    def _case_head(self) -> None:
        if self._word_at(self.cursor + 1) is None:
            raise _Abandoned
        self.cursor += 2
        self._skip_newlines()
        if self._keyword_at(self.cursor) != "in":
            raise _Abandoned
        self.cursor += 1

    def _pattern(self) -> None:
        if self._op(self.cursor) == "(":
            self.cursor += 1
        while True:
            if self._word_at(self.cursor) is None:
                raise _Abandoned
            operator = self._op(self.cursor + 1)
            self.cursor += 2
            if operator == ")":
                return
            if operator != "|":
                raise _Abandoned

    def _close_case(self) -> None:
        self.cursor += 1
        self._emit_break(";")
        self.out.append("esac")
        self.words = ["esac"]

    def _branch_end(self) -> bool:
        return bool(self._terminator()) or (not self.words and self._keyword_at(self.cursor) == "esac")

    def _terminator(self) -> tuple[str, ...]:
        for terminator in CASE_TERMINATORS:
            indexes = range(self.cursor, self.cursor + len(terminator))
            if tuple(self._op(index) for index in indexes) != terminator:
                continue
            if all(self.pieces[index].joined for index in indexes[1:]):
                return terminator
        return ()

    def _skip_newlines(self) -> None:
        while self._op(self.cursor) == "\n":
            self.cursor += 1

    def _op(self, index: int) -> str | None:
        if index >= len(self.pieces) or not self.pieces[index].operator or self.pieces[index].quoted:
            return None
        return self.pieces[index].text

    def _word_at(self, index: int) -> str | None:
        return self.pieces[index].text if index < len(self.pieces) and not self.pieces[index].operator else None

    def _keyword_at(self, index: int) -> str | None:
        word = self._word_at(index)
        return None if word is None or self.pieces[index].quoted else word
