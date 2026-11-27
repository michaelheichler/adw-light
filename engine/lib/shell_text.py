from __future__ import annotations

import re
import shlex
from functools import lru_cache
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import NamedTuple

WORD_FRAGMENT_RE = re.compile(r'''\\.|'[^']*'|"(?:\\.|[^"\\])*"''')
ESCAPED_WORD_SPACE = frozenset({"\\ ", "\\\t"})
SUBSTITUTION_RE = re.compile(r"\\.|\$\(|`", re.DOTALL)
BACKTICK_BODY_RE = re.compile(r"(?:\\.|[^`\\])*", re.DOTALL)
SINGLE_QUOTE = r"'[^']*'"
DOUBLE_QUOTE = r'"(?:\\.|[^"\\])*"'
BACKTICK = r"`(?:\\.|[^`\\])*`?"
COMMENT = r"(?<![^\s;&|()])\#[^\n]*"
ANSI_C_QUOTE = r"\$'(?:\\.|[^'\\])*'"


def _scan_pattern(*, physical: bool) -> str:
    optional = "" if physical else "?"
    disabled = "(?!)" if physical else ""
    bare_escape = "" if physical else r"\\?"
    literal = r"""[^\s;&|()<>'"$\x60\\]*"""
    quoted = r"""(?:'[^'$\x60\\\n]*'|"[^"$\x60\\\n]*")"""
    joined = rf"{literal}(?:{quoted}{literal})+(?=$|[\s;&|()<>])"
    redirect_word = r"""(?:\\[ \t\\]|\\(?![ \t\\])|[^\s;&|()<>'"\\])+"""
    return rf"""
        (?P<continuation>\\\n)
      | (?P<escape>\\.)
      | (?P<ansi>{ANSI_C_QUOTE}{optional})
      | (?P<single>{SINGLE_QUOTE}{optional})
      | (?P<double>{DOUBLE_QUOTE}{optional})
      | (?P<backtick>{disabled}{BACKTICK})
      | (?P<subst>\$\()
      | (?P<procsub>[<>]\()
      | (?P<herestring><<<[ \t]*(?P<word>{SINGLE_QUOTE}|{DOUBLE_QUOTE}|[^\s;&|()<>'"]+))
      | (?P<heredoc><<(?P<strip>-?)[ \t]*(?:'(?P<quoted>[^'\n]*)'|"(?P<dquoted>[^"\n]*)"|{bare_escape}(?P<bare>[^\s;&|()<>'"]+)))
      | (?P<dup>\d*[<>]&(?:\d+|-)?)
      | (?P<redirect>\d*(?:>>|>\||<>|>|<)[ \t]*(?:{joined}|{SINGLE_QUOTE}|{DOUBLE_QUOTE}|{redirect_word}))
      | (?P<comment>{disabled}{COMMENT})
      | (?P<separator>[;&|()\n])
      | (?P<other>(?:[^\\'"`#;&|()<>\s$]|\$(?![(']))+|[^\S\n]+|.)
    """


SCAN_RE = re.compile(_scan_pattern(physical=False), re.VERBOSE | re.DOTALL)
PHYSICAL_SCAN_RE = re.compile(_scan_pattern(physical=True), SCAN_RE.flags)
PLAIN_TEXT_RE = re.compile(r"(?P<other><<-?|#|\n)")
ANSI_C_RE = re.compile(ANSI_C_QUOTE + "?", re.DOTALL)
REDIRECT_PARTS_RE = re.compile(r"(\d*)(>>|>\||<>|<&|>&|>|<)[ \t]*(.*)", re.DOTALL)
MARKED_RE = re.compile(r"\x00(\d+)(<<|>>|>\||<>|<&|>&|>|<)(.*)", re.DOTALL)
ANSI_C_START_RE = re.compile(r"\$'|\\.|'[^']*'?|\"(?:\\.|[^\"\\])*\"?", re.DOTALL)
ANSI_C_ESCAPE_RE = re.compile(
    r"\\(?:([abeEfnrtv\\'\"?])|([0-7]{1,3})|x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})|c(.)|(.))", re.DOTALL,
)
ANSI_C_ESCAPES = {
    "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
    "\\": "\\", "'": "'", '"': '"', "?": "?",
}
PASSED_THROUGH = frozenset({"escape", "ansi", "single", "double", "other"})
QUOTED_WORDS = frozenset({"double", "ansi"})
ARITHMETIC_OPENERS = frozenset({"separator", "subst"})
ARITHMETIC_PLAIN = frozenset({"heredoc", "comment"})
SHELL_MARKS = ARITHMETIC_PLAIN | {"separator"}
LINE_BREAK = ("separator", "\n")
REPLACED = frozenset({"backtick", "subst", "procsub"})
MARKED = frozenset({"redirect", "dup"})
SCRIPT_READERS = frozenset({"source", "."})
SUBSTITUTED = "$SUBSTITUTED"
PROCESS_FILE = "/dev/fd/63"
REDIRECT_MARK = "\x00"
INLINE_INPUT = "<<"


class _Reading(NamedTuple):
    heredocs: frozenset[int]
    breaks: frozenset[int]


class _Heredoc(NamedTuple):
    delimiter: str
    strip_tabs: bool
    read_by_shell: bool
    slot: str = ""


class Redirect(NamedTuple):
    fd: int
    operator: str
    target: str


class ShellBody(NamedTuple):
    text: str
    closed: bool
    expands: bool
    strip_tabs: bool = False


class ShellToken(NamedTuple):
    match: re.Match[str] | None
    end: int
    bodies: tuple[ShellBody, ...] = ()


def _drain_heredocs(
    text: str, position: int, pending: list[tuple[_Heredoc, bool]], *, physical: bool,
) -> tuple[tuple[ShellBody, ...], int]:
    bodies = []
    for heredoc, expands in pending:
        body, position, closed = _heredoc_body(text, position, heredoc, physical=physical)
        bodies.append(ShellBody(body, closed, expands, heredoc.strip_tabs))
    pending.clear()
    return tuple(bodies), position


def shell_tokens(text: str, *, physical: bool = False) -> Iterator[ShellToken]:
    position = 0
    pending: list[tuple[_Heredoc, bool]] = []
    pattern = PHYSICAL_SCAN_RE if physical else SCAN_RE
    arithmetic_stop = 0
    reading = _shell_reading(text) if physical and "<<" in text else None
    while position < len(text):
        newline = text.find("\n", position) if physical else -1
        limit = len(text) if newline < 0 else max(position + 1, newline)
        match = pattern.match(text, position, limit)
        if match is None:
            break
        match = _shell_match(text, match, arithmetic_stop, reading) if match.lastgroup in SHELL_MARKS else match
        position = match.end()
        if match.lastgroup in ARITHMETIC_OPENERS and 0 <= arithmetic_stop <= match.start() and text.startswith("((", position - 1):
            arithmetic_stop = _arithmetic_reach(text, position - 1)
        if not physical and match.lastgroup in {"subst", "procsub"}:
            position = _closing_paren(text, position) + 1
        if not physical and match.lastgroup == "double":
            position = _quote_end(text, match.start() + 1, '"')
        if match.lastgroup == "heredoc":
            expands = match.group("bare") is not None and (physical or "\\" not in match.group())
            pending.append((_Heredoc(_delimiter(match), bool(match.group("strip")), False), expands))
        bodies: tuple[ShellBody, ...] = ()
        if match.lastgroup == "separator" and match.group() == "\n":
            bodies, position = _drain_heredocs(text, position, pending, physical=physical)
        yield ShellToken(match, position, bodies)
    if pending:
        bodies, position = _drain_heredocs(text, position, pending, physical=physical)
        yield ShellToken(None, position, bodies)


def _shell_match(text: str, match: re.Match[str], arithmetic_stop: int, reading: _Reading | None) -> re.Match[str]:
    kind = match.lastgroup
    line_break = (kind, match.group()) == LINE_BREAK
    if match.start() < arithmetic_stop and (kind in ARITHMETIC_PLAIN or line_break):
        return PLAIN_TEXT_RE.match(text, match.start())
    if reading is None or match.start() in (reading.heredocs if kind == "heredoc" else reading.breaks):
        return match
    return PLAIN_TEXT_RE.match(text, match.start()) if kind == "heredoc" or line_break else match


@lru_cache(maxsize=16)
def _shell_reading(text: str) -> _Reading:
    marks = list(_read_marks(text, 0))
    return _Reading(
        frozenset(start for kind, start in marks if kind == "heredoc"),
        frozenset(start for kind, start in marks if kind == "separator"),
    )


def _read_marks(text: str, offset: int) -> Iterator[tuple[str, int]]:
    for token in shell_tokens(text):
        if token.match is None:
            continue
        if token.match.lastgroup == "heredoc" or (token.match.lastgroup, token.match.group()) == LINE_BREAK:
            yield token.match.lastgroup, offset + token.match.start()
        for start, body in _code_bodies(text, token):
            yield from _read_marks(body, offset + start)


def _code_bodies(text: str, token: ShellToken) -> list[tuple[int, str]]:
    match = token.match
    if match.lastgroup in {"subst", "procsub"}:
        return [_substitution_body(text, match.end(), token.end - 1)]
    if match.lastgroup == "backtick":
        return [(match.start() + 1, match.group()[1:].removesuffix("`"))]
    if match.lastgroup == "double":
        word = text[match.start():token.end]
        return [(match.start() + start, body) for _, _, start, body in _substitution_parts(word)]
    return []


def _substitution_body(text: str, opened: int, closed: int) -> tuple[int, str]:
    if text.startswith("$((", opened - 2) and _arithmetic_end(text, opened - 1) == closed + 1:
        return opened - 1, text[opened - 1:closed + 1]
    return opened, text[opened:closed]


@lru_cache(maxsize=16)
def shell_words(command: str) -> tuple[str, ...]:
    fragments: list[str] = []
    saved: dict[str, str] = {}
    marker = "\0"
    while marker in command:
        marker += "\0"

    def stash(text: str) -> str:
        slot = f"{marker}{len(saved)}{marker}"
        saved[slot] = text
        return slot

    start = 0
    for token in shell_tokens(command):
        source = command[start:token.end]
        start = token.end
        if token.match is not None and token.match.lastgroup in QUOTED_WORDS:
            fragments.append(f" {stash(source)} ")
            continue
        fragments.append(WORD_FRAGMENT_RE.sub(
            lambda match: _word_fragment(match.group(), stash),
            source,
        ))
    try:
        lexer = shlex.shlex("".join(fragments), posix=False, punctuation_chars=";&|()>")
        lexer.whitespace_split = True
        slots = re.compile(rf"{marker}[0-9]+{marker}")
        return tuple(slots.sub(lambda match: saved.get(match.group(), match.group()), word) for word in lexer)
    except ValueError:
        return tuple(command.split())


def _word_fragment(text: str, stash: Callable[[str], str]) -> str:
    if text in ESCAPED_WORD_SPACE:
        return stash(text)
    return f" {text} " if text[0] in "\"'" else text


def shell_text(command: str) -> str:
    return _scan(_normalize_ansi_c_quotes(command.split("\0", 1)[0]))


def heredoc_value(word: str) -> str | None:
    if not word.startswith("$(") or _closing_paren(word, 2) != len(word) - 1:
        return None
    tokens = list(shell_tokens(word[2:-1]))
    syntax = [
        token.match for token in tokens
        if token.match is not None and token.match.lastgroup != "comment" and token.match.group().strip()
    ]
    bodies = [body for token in tokens for body in token.bodies]
    if len(syntax) != 2 or syntax[0].group() != "cat" or syntax[1].lastgroup != "heredoc":
        return None
    if len(bodies) != 1 or not bodies[0].closed:
        return None
    body = bodies[0]
    value = "\n".join(line.lstrip("\t") for line in body.text.split("\n")) if body.strip_tabs else body.text
    return value.rstrip("\n")


def split_redirects(words: list[str]) -> tuple[list[str], list[Redirect]]:
    marked = [(word, MARKED_RE.fullmatch(word)) for word in words]
    plain = [word for word, match in marked if match is None]
    return plain, [Redirect(int(match.group(1)), match.group(2), match.group(3)) for _word, match in marked if match]


def decode_ansi_c_value(value: str) -> str:
    if not value.startswith("$'"):
        return value
    end, decoded = _ansi_c_quote(value, 2)
    return decoded if end == len(value) - 1 else value


def _scan(text: str) -> str:
    return _Scanner(text).text()


def _marker(fd: str, operator: str, target: str) -> str:
    default = "0" if operator.startswith("<") else "1"
    return f" {shlex.quote(f'{REDIRECT_MARK}{fd or default}{operator}{target}')} "


@dataclass(slots=True)
class _Scanner:
    command: str
    parts: list[str] = field(default_factory=list)
    pending: list[_Heredoc] = field(default_factory=list)
    start: int = 0
    command_part: int = 0

    def text(self) -> str:
        for token in shell_tokens(self.command):
            if token.match is not None:
                self._take(token)
            self._bodies(token.bodies)
        return "".join(self.parts)

    def _take(self, token: ShellToken) -> None:
        match = token.match
        kind = match.lastgroup
        if kind == "separator":
            self._separator(match)
            return
        if kind in REPLACED:
            self._replace(token)
            return
        if kind in MARKED:
            fd, operator, target = REDIRECT_PARTS_RE.fullmatch(match.group()).groups()
            self.parts.append(_marker(fd, operator, _unquoted(target)))
        if kind == "heredoc":
            self._heredoc(match)
        if kind == "herestring":
            self._herestring(match)
        if kind == "double":
            word = self.command[match.start():token.end]
            self._lead_with(_substitutions(word))
            self.parts.append(_quoted_substitutions(word))
        elif kind in PASSED_THROUGH:
            self.parts.append(match.group())

    def _heredoc(self, match: re.Match) -> None:
        slot = f"\x01{len(self.pending)}:{match.start()}\x01"
        self.pending.append(_Heredoc(_delimiter(match), bool(match.group("strip")), self._shell_reads(match.start()), slot))
        self.parts.append(slot)

    def _herestring(self, match: re.Match) -> None:
        word = _unquoted(match.group("word"))
        self.parts.append(f"\n{_scan(word)}\n" if self._shell_reads(match.start()) else _marker("", INLINE_INPUT, word))

    def _replace(self, token: ShellToken) -> None:
        match = token.match
        if match.lastgroup == "backtick":
            body = match.group()[1:].removesuffix("`")
        else:
            _, body = _substitution_body(self.command, match.end(), token.end - 1)
        if match.lastgroup == "procsub":
            self.parts.insert(self.command_part, f"( {_scan(body)}\n) | ")
            self.command_part += 1
            self._process_file()
        else:
            self._lead_with([body])
            self.parts.append(SUBSTITUTED)

    def _process_file(self) -> None:
        index = len(self.parts) - 1
        while index >= self.command_part and not self.parts[index].strip():
            index -= 1
        if index >= self.command_part and self.parts[index] == "<":
            self.parts[index] = _marker("", "<", PROCESS_FILE)
            return
        self.parts.append(PROCESS_FILE)

    def _separator(self, match: re.Match) -> None:
        self.parts.append(match.group())
        self.start, self.command_part = match.end(), len(self.parts)

    def _lead_with(self, bodies: list[str]) -> None:
        for body in bodies:
            self.parts.insert(self.command_part, f"{_scan(body)}\n")
            self.command_part += 1

    def _shell_reads(self, position: int) -> bool:
        return _reads_script(self.command[self.start:position])

    def _bodies(self, bodies: tuple[ShellBody, ...]) -> None:
        for heredoc, body in zip(self.pending, bodies):
            if heredoc.read_by_shell or not body.closed:
                self.parts.append(_scan(body.text))
            self.parts[self.parts.index(heredoc.slot)] = "" if heredoc.read_by_shell else _marker("", INLINE_INPUT, body.text)
        if bodies:
            self.pending = []


def _substitutions(text: str) -> list[str]:
    return [body for _, _, body in _substitution_spans(text)]


def _substitution_spans(text: str) -> Iterator[tuple[int, int, str]]:
    for start, end, _, body in _substitution_parts(text):
        yield start, end, body


def _substitution_parts(text: str) -> Iterator[tuple[int, int, int, str]]:
    cursor = 0
    while (match := SUBSTITUTION_RE.search(text, cursor)) is not None:
        cursor = match.end()
        if match.group().startswith("\\"):
            continue
        if match.group() == "`":
            end = BACKTICK_BODY_RE.match(text, cursor).end()
            yield match.start(), end + 1, cursor, text[cursor:end]
        else:
            end = _closing_paren(text, cursor)
            yield match.start(), end + 1, *_substitution_body(text, cursor, end)
        cursor = end + 1


def _quoted_substitutions(word: str) -> str:
    parts: list[str] = []
    cursor = 0
    for start, end, _ in _substitution_spans(word):
        if end > len(word):
            return word
        parts.extend([word[cursor:start], word[start:end].replace("\\", "\\\\").replace('"', '\\"')])
        cursor = end
    return "".join([*parts, word[cursor:]])


def _closing_paren(text: str, cursor: int) -> int:
    depth = 1
    pending: list[_Heredoc] = []
    arithmetic = _arithmetic_around(text, cursor)
    while cursor < len(text):
        char = text[cursor]
        if char == "\n" and pending and cursor >= arithmetic:
            cursor += 1
            for heredoc in pending:
                _, cursor, _ = _heredoc_body(text, cursor, heredoc)
            pending.clear()
            continue
        if text.startswith("<<", cursor) and cursor >= arithmetic:
            match = SCAN_RE.match(text, cursor)
            if match is not None and match.lastgroup == "heredoc":
                pending.append(_Heredoc(_delimiter(match), bool(match.group("strip")), False))
                cursor = match.end()
                continue
        if char == "\\":
            cursor += 2
            continue
        if text.startswith("$'", cursor):
            cursor = ANSI_C_RE.match(text, cursor).end()
            continue
        if char in {"'", '"', "`"}:
            cursor = _quote_end(text, cursor + 1, char)
            continue
        if char == "#" and cursor >= arithmetic and (cursor == 0 or text[cursor - 1] in " \t\n;&|()"):
            newline = text.find("\n", cursor)
            cursor = len(text) if newline < 0 else newline
            continue
        if text.startswith("((", cursor) and 0 <= arithmetic <= cursor:
            arithmetic = _arithmetic_reach(text, cursor)
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0:
            return cursor
        cursor += 1
    return len(text)


def _arithmetic_around(text: str, cursor: int) -> int:
    opened = cursor >= 1 and text.startswith("((", cursor - 1) and text[cursor - 2:cursor - 1] not in ("<", ">")
    return _arithmetic_reach(text, cursor - 1) if opened else 0


def _arithmetic_end(text: str, start: int) -> int:
    return max(_arithmetic_reach(text, start), 0)


def _arithmetic_reach(text: str, start: int) -> int:
    depth = 1
    cursor = start + 2
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "$" and text.startswith(("$(", "$'"), cursor):
            cursor = _closing_paren(text, cursor + 2) + 1 if text[cursor + 1] == "(" else ANSI_C_RE.match(text, cursor).end()
            continue
        if char in {"'", '"', "`"}:
            cursor = _quote_end(text, cursor + 1, char)
            continue
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0:
            return cursor + 2 if text.startswith("))", cursor) else 0
        cursor += 1
    return -1


def _quote_end(text: str, cursor: int, quote: str) -> int:
    while cursor < len(text):
        if text[cursor] == quote:
            return cursor + 1
        if quote != "'" and text[cursor] == "\\":
            cursor += 2
        elif quote == '"' and text.startswith("$(", cursor):
            cursor = _closing_paren(text, cursor + 2) + 1
        elif quote == '"' and text[cursor] == "`":
            cursor = _quote_end(text, cursor + 1, "`")
        else:
            cursor += 1
    return len(text)


def _delimiter(match: re.Match) -> str:
    return next(value for value in (match.group("quoted"), match.group("dquoted"), match.group("bare")) if value is not None)


def _unquoted(word: str) -> str:
    try:
        return " ".join(shlex.split(word))
    except ValueError:
        return word


def _reads_script(prefix: str) -> bool:
    from .shell_syntax import SHELL_C_INTERPRETERS, _basename, _payload_command_index

    try:
        words = shlex.split(prefix)
    except ValueError:
        return True
    index = _payload_command_index(words)
    return index >= len(words) or words[index] in SCRIPT_READERS or _basename(words[index]) in SHELL_C_INTERPRETERS


def _heredoc_body(command: str, start: int, heredoc: _Heredoc, *, physical: bool = False) -> tuple[str, int, bool]:
    cursor = start
    while cursor < len(command):
        newline = command.find("\n", cursor)
        stop = len(command) if newline < 0 else newline
        line = command[cursor:stop]
        line = line.lstrip("\t") if heredoc.strip_tabs else line
        if (line.rstrip() if physical else line) == heredoc.delimiter:
            return command[start:cursor], min(stop + 1, len(command)), True
        cursor = stop + 1
    return command[start:], len(command), False


def _normalize_ansi_c_quotes(command: str) -> str:
    parts: list[str] = []
    cursor = position = 0
    while (match := ANSI_C_START_RE.search(command, position)) is not None:
        position = match.end()
        if match.group() != "$'":
            continue
        end, value = _ansi_c_quote(command, match.end())
        if end < 0:
            return command
        parts.extend([command[cursor:match.start()], shlex.quote(value)])
        cursor = position = end + 1
    parts.append(command[cursor:])
    return "".join(parts)


def _ansi_c_quote(command: str, cursor: int) -> tuple[int, str]:
    chars: list[str] = []
    while cursor < len(command) and command[cursor] != "'":
        match = ANSI_C_ESCAPE_RE.match(command, cursor) if command[cursor] == "\\" else None
        if command[cursor] == "\\" and match is None:
            return -1, ""
        chars.append(_escape_value(match) if match else command[cursor])
        cursor = match.end() if match else cursor + 1
    return (cursor, "".join(chars).split("\0", 1)[0]) if cursor < len(command) else (-1, "")


def _escape_value(match: re.Match) -> str:
    simple, octal, hexa, short, wide, control, other = match.groups()
    if simple:
        return ANSI_C_ESCAPES[simple]
    if octal or hexa:
        return chr(int(octal, 8) & 0xFF) if octal else chr(int(hexa, 16))
    if short or wide:
        code = int(short or wide, 16)
        return chr(code) if code <= 0x10FFFF else match.group()
    if control:
        return chr(ord(control) & 0x1F)
    return f"\\{other}"
