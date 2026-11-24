from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import NamedTuple

from .shell_text import _closing_paren, _quote_end, shell_tokens

CLOSER_OF = {"if": "fi", "case": "esac", "while": "done", "until": "done", "for": "done", "select": "done", "{": "}"}
BLOCK_MIDDLES = frozenset({"then", "elif", "else", "do"})
PARENTHESES = frozenset({"(", ")"})
PIPELINE_PREFIXES = frozenset({"!", "time"})
JOINED_OPERATORS = frozenset({"&&", "||", ";;", "|&", ";&", ";;&"})
SHIFT_LEADERS = frozenset({"start", ";", "\n", "then", "else", "{", "arm"})
SUBSHELL_FOLLOWERS = frozenset({"|", "|&", "&"})
WORD_PARTS = frozenset({"single", "double", "backtick", "subst", "procsub", "escape", "other"})
LITERAL_QUOTE_PROGRAMS = frozenset({
    "awk", "gawk", "mawk", "nawk", "sed", "gsed", "jq", "grep", "egrep", "fgrep", "rg", "perl", "python", "python3",
    "node", "cut", "tr",
})
SIMPLE_RE = re.compile(r"\$([1-9@*])|\$\{([1-9@*])\}")
SCRIPT_NAME_RE = re.compile(r"\$0|\$\{0\}")
ASCII_COUNT_RE = re.compile(r"[0-9]+")
OPERATOR_RE = re.compile(r"\$\{([1-9@*])(:?)([-=?+])")
COMPLEX_WORD_CHARS = frozenset("$`{}'\"\\")
DOUBLE_QUOTE_SPECIAL_RE = re.compile(r'["\\`]|\$(?=\()')
COMMENT_LEADERS = frozenset(" \t\n;&|()")
INFINITY = float("inf")
ANSI_C_RE = re.compile(r"\$'(?:\\.|[^'\\])*'?", re.DOTALL)
NUMERIC_NOOP_RE = re.compile(r"-[0-9]+")
SLICE_RE = re.compile(r"\$\{([@*]):([0-9]+)(?::([0-9]+))?\}")
ARITHMETIC_COUNT_RE = re.compile(r"\$\(\([ \t]*([0-9]+)[ \t]*\)\)")
MIXED_HEREDOC_RE = re.compile(r"(?P<operator><<-?[ \t]*)(?P<word>(?=[^\s;&|()<>]*['\"\\])[^\s;&|()<>]+)")
SCOPED_PHASES = frozenset({("if", "branch"), ("case", "arm"), ("{", "body")})
OPENING_PHASE = {"if": "cond", "case": "head", "{": "body"}
BRANCHING_OPENERS = frozenset({"if", "case"})
LEAVING_COMMANDS = frozenset({"return", "exit"})
CASE_HEADS = frozenset({"head", "patterns"})
CASE_ARM_ENDS = frozenset({";;", ";&", ";;&"})
SHELL_NAMES = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
SHELL_PREFIXES = frozenset({"command", "exec", "builtin", "nohup"})
SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O"})
ASSIGNMENT_RE = re.compile(r"[A-Za-z_0-9]+(?:\[[^\]]*\])?\+?=")
POSITIONAL_ASSIGNMENT_RE = re.compile(r"(?:argv(?:\[[^\]]*\])?|[0-9]+)\+?=")
EVAL_SETTER_RE = re.compile(r"\bset\b|\bshift\b|\bargv\b|(?:^|[\s;&|])[0-9]+\+?=")


class _Item(NamedTuple):
    operator: bool
    text: str
    start: int
    end: int
    literal: bool
    quotes: tuple[int, ...] = ()
    bare: str | None = None


class _Shift(NamedTuple):
    end: int
    shift_count: int
    limit: float = INFINITY
    replacement: tuple[str, ...] | None = None


@dataclass(slots=True)
class _Block:
    opener: str
    phase: str
    start: int
    pending: list[int] = field(default_factory=list)
    transparent: bool = False
    changed: bool = False
    leaves: bool = False

    @property
    def scoped(self) -> bool:
        return (self.opener, self.phase) in SCOPED_PHASES


@dataclass(slots=True)
class _Structure:
    stack: list[_Block] = field(default_factory=list)
    poisoned: bool = False
    before: str = "start"
    command: list[_Item] = field(default_factory=list)
    shifts: list[_Shift] = field(default_factory=list)
    literal_quotes: set[int] = field(default_factory=set)
    unknown_from: float = INFINITY
    skipping: str = ""
    skip_depth: int = 0
    closing: tuple[_Block, int] | None = None

    def operator(self, item: _Item) -> None:
        if self.skipping:
            self.skip_operator(item)
            return
        opens_inside_command = item.text == "(" and bool(self.command)
        if opens_inside_command and self.command[-1].text.endswith("="):
            self.skipping, self.skip_depth = "array", 1
            return
        top = self.stack[-1] if self.stack else None
        if top is not None and top.opener == "case":
            self.case_operator(top, item)
            return
        self.finish(item.text)
        if item.text == "(":
            self.poisoned = self.poisoned or opens_inside_command
            self.stack.append(_Block("(", "body", item.start))
        elif item.text == ")":
            self.close("(", item.start)

    def case_operator(self, top: _Block, item: _Item) -> None:
        if top.phase in CASE_HEADS:
            if item.text == ")":
                top.phase, self.command, self.before = "arm", [], "arm"
            elif item.text not in PARENTHESES:
                self.command, self.before = [], item.text
            return
        self.finish(item.text)
        if item.text in CASE_ARM_ENDS:
            self.end_segment(top, item.start)
            top.phase = "patterns"
        elif item.text in PARENTHESES:
            self.poisoned = True

    def skip_operator(self, item: _Item) -> None:
        if self.skipping == "array" and item.text in PARENTHESES:
            self.skip_depth += 1 if item.text == "(" else -1
            self.skipping = self.skipping if self.skip_depth > 0 else ""

    def word(self, item: _Item) -> None:
        if self.skipping:
            if self.skipping == "test":
                self.command.append(item)
                self.skipping = "" if item.text == "]]" else self.skipping
            return
        if not self.command and item.literal and item.text == "[[":
            self.skipping = "test"
        top = self.stack[-1] if self.stack else None
        if top is not None and top.opener == "case" and top.phase in CASE_HEADS:
            self.case_word(top, item)
        elif self.command or not item.literal:
            self.command.append(item)
        elif item.text in CLOSER_OF:
            transparent = item.text == "{" and self.before in SHIFT_LEADERS and all(block.scoped for block in self.stack)
            self.stack.append(_Block(item.text, OPENING_PHASE.get(item.text, "loop"), item.start, transparent=transparent))
            self.before = item.text
        elif item.text in CLOSER_OF.values():
            self.close(item.text, item.start)
            self.command.append(item)
        elif item.text in BLOCK_MIDDLES or item.text in PIPELINE_PREFIXES:
            self.middle(item)
        else:
            self.command.append(item)

    def case_word(self, top: _Block, item: _Item) -> None:
        if item.literal and item.text == "esac" and not self.command:
            self.close("esac", item.start)
            self.command.append(item)
            return
        if top.phase == "head" and item.literal and item.text == "in":
            top.phase = "patterns"
        self.command.append(item)

    def middle(self, item: _Item) -> None:
        top = self.stack[-1] if self.stack else None
        self.before = item.text
        if item.text in PIPELINE_PREFIXES:
            return
        if top is None or (item.text == "do") != (top.phase == "loop"):
            self.poisoned = True
            return
        if top.opener == "if":
            self.end_segment(top, item.start)
            top.phase = "cond" if item.text == "elif" else "branch"

    def close(self, closer: str, position: int) -> None:
        block = self.stack.pop() if self.stack else None
        opener = block.opener if block is not None else ""
        if block is not None and block.transparent and closer == "}":
            self.closing = (block, position)
        elif block is not None:
            self.end_segment(block, position)
            if block.changed and opener in BRANCHING_OPENERS:
                self.unknown_from = min(self.unknown_from, position)
        if opener != closer and CLOSER_OF.get(opener) != closer:
            self.poisoned = True

    def release(self, follower: str) -> None:
        if self.closing is None:
            return
        block, position = self.closing
        self.closing = None
        if follower in SUBSHELL_FOLLOWERS:
            self.end_segment(block, position)
        elif self.stack:
            self.stack[-1].pending.extend(block.pending)

    def end_segment(self, block: _Block, position: int) -> None:
        block.changed = block.changed or not block.leaves and any(self.shifts[index].shift_count or self.shifts[index].replacement is not None
                                             for index in block.pending)
        for index in block.pending:
            self.shifts[index] = self.shifts[index]._replace(limit=position)
        block.pending.clear()
        block.leaves = False

    def finish(self, follower: str) -> None:
        if self.closing is not None:
            self.release(follower)
        command, self.command = self.command, []
        if command:
            self._read_command(command, follower)
        self.before = follower

    def _read_command(self, command: list[_Item], follower: str) -> None:
        if command[0].literal and command[0].text in LITERAL_QUOTE_PROGRAMS:
            self.literal_quotes.update(start for item in command[1:] for start in item.quotes)
        self.literal_quotes.update(_inner_shell_quotes(command))
        core = _core(command)
        if self.stack and core and core[0].bare in LEAVING_COMMANDS and self._unconditional(follower):
            self.stack[-1].leaves = True
        count = self._shift_count(command, follower)
        replacement = _literal_reset(command) if self._unconditional(follower) else None
        if count is not None or replacement is not None:
            if self.stack:
                self.stack[-1].pending.append(len(self.shifts))
            self.shifts.append(_Shift(command[-1].end, count or 0, replacement=replacement))
        elif _is_shift(command) or _sets_positional(command):
            self.unknown_from = min(self.unknown_from, self._unknown_start(command[0].start))

    def _unknown_start(self, position: int) -> int:
        loops = [block.start for block in self.stack if block.phase == "loop"]
        return min(loops, default=position)

    def _unconditional(self, follower: str) -> bool:
        if self.poisoned or follower in SUBSHELL_FOLLOWERS or self.before not in SHIFT_LEADERS:
            return False
        return all(block.scoped for block in self.stack)

    def _shift_count(self, command: list[_Item], follower: str) -> int | None:
        if not _is_shift(command) or not self._unconditional(follower):
            return None
        arguments = _core(command)[1:]
        if not arguments:
            return 1
        return _known_count(arguments[0]) if len(arguments) == 1 else None


def _core(command: list[_Item]) -> list[_Item]:
    return command[len(_leading_assignments(command)):]


def _is_shift(command: list[_Item]) -> bool:
    core = _core(command)
    return bool(core) and core[0].bare == "shift"


def _literal_reset(command: list[_Item]) -> tuple[str, ...] | None:
    core = _core(command)
    if len(core) < 2 or core[0].bare != "set" or core[1].bare != "--" or len(core) != len(command):
        return None
    values = [item.bare for item in core[2:]]
    return None if None in values else tuple(value for value in values if value is not None)


def _known_count(argument: _Item) -> int | None:
    arithmetic = ARITHMETIC_COUNT_RE.fullmatch(argument.text)
    if arithmetic is not None:
        return int(arithmetic.group(1))
    text = argument.bare
    if text is None:
        return None
    if ASCII_COUNT_RE.fullmatch(text):
        return int(text)
    if NUMERIC_NOOP_RE.fullmatch(text) or (text.isdigit() and not text.isascii()):
        return 0
    return None


def _sets_positional(command: list[_Item]) -> bool:
    if any(POSITIONAL_ASSIGNMENT_RE.match(item.text) for item in _leading_assignments(command)):
        return True
    core = _core(command)
    if not core:
        return False
    if core[0].bare == "eval":
        return any(EVAL_SETTER_RE.search(item.text) for item in core[1:])
    return core[0].bare == "set" and _set_operands(core[1:])


def _leading_assignments(command: list[_Item]) -> list[_Item]:
    leading = []
    for item in command:
        if not ASSIGNMENT_RE.match(item.text):
            break
        leading.append(item)
    return leading


def _set_operands(arguments: list[_Item]) -> bool:
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item.bare is None or item.bare in {"-", "--"} or item.bare[:1] not in "-+":
            return True
        index += 2 if "o" in item.bare[1:] else 1
    return False


def _inner_shell_quotes(command: list[_Item]) -> list[int]:
    index = next((position for position, item in enumerate(command)
                  if not (item.literal and (item.text in SHELL_PREFIXES or ASSIGNMENT_RE.match(item.text)))), len(command))
    if index >= len(command) or not command[index].literal or command[index].text.rsplit("/", 1)[-1] not in SHELL_NAMES:
        return []
    reads_command, cursor = False, index + 1
    while cursor < len(command) and command[cursor].literal and command[cursor].text[:1] in "-+":
        option = command[cursor].text
        if option == "--":
            cursor += 1
            break
        reads_command = reads_command or (option[:1] == "-" and "c" in option[1:])
        cursor += 2 if option in SHELL_VALUE_OPTIONS else 1
    if not reads_command or cursor >= len(command):
        return []
    return list(command[cursor].quotes)


def _items(text: str) -> list[_Item]:
    items: list[_Item] = []
    parts: list[tuple[str, str, int, int]] = []
    for token in shell_tokens(text):
        if token.match is None:
            continue
        group, start = token.match.lastgroup, token.match.start()
        raw = text[start:token.end]
        if group in WORD_PARTS and not raw.isspace():
            parts.append((group, raw, start, token.end))
            continue
        _flush(items, parts)
        if group == "separator":
            _add_operator(items, token.match.group(), start, token.match.end())
    _flush(items, parts)
    return items


def _flush(items: list[_Item], parts: list[tuple[str, str, int, int]]) -> None:
    if not parts:
        return
    literal = all(group == "other" for group, _raw, _start, _end in parts)
    quotes = tuple(start for group, _raw, start, _end in parts if group == "single")
    bare_parts = [_bare_part(group, raw) for group, raw, _start, _end in parts]
    bare = None if None in bare_parts else "".join(part for part in bare_parts if part is not None)
    text = "".join(raw for _group, raw, _start, _end in parts)
    items.append(_Item(False, text, parts[0][2], parts[-1][3], literal, quotes, bare))
    parts.clear()


def _bare_part(group: str, raw: str) -> str | None:
    if group == "other":
        return None if "$" in raw else raw
    if group == "escape":
        return raw[1:]
    if group == "single" and len(raw) > 1 and raw.endswith("'"):
        return raw[1:-1]
    if group == "double" and len(raw) > 1 and raw.endswith('"') and not any(char in raw[1:-1] for char in "$`\\"):
        return raw[1:-1]
    return None


def _add_operator(items: list[_Item], raw: str, start: int, end: int) -> None:
    last = items[-1] if items else None
    if last is not None and last.operator and last.end == start and last.text + raw in JOINED_OPERATORS:
        items[-1] = last._replace(text=last.text + raw, end=end)
        return
    items.append(_Item(True, raw, start, end, True))


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
