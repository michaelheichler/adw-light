from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple

try:
    from .shell_text import shell_tokens
except ImportError:
    from shell_text import shell_tokens

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
ASCII_COUNT_RE = re.compile(r"[0-9]+")
INFINITY = float("inf")
NUMERIC_NOOP_RE = re.compile(r"-[0-9]+")
ARITHMETIC_COUNT_RE = re.compile(r"\$\(\([ \t]*([0-9]+)[ \t]*\)\)")
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
