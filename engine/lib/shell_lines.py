from __future__ import annotations

from typing import NamedTuple

from .shell_text import ShellToken, _normalize_ansi_c_quotes, _substitutions, shell_tokens

SKIPPED = frozenset({"continuation", "comment"})
SUBSTITUTING = frozenset({"double", "backtick"})


class CommandLine(NamedTuple):

    text: str
    bodies: tuple[str, ...]


class ShellPass(NamedTuple):
    lines: list[CommandLine]
    substitutions: list[str]


def shell_pass(command: str) -> ShellPass:
    text = _normalize_ansi_c_quotes(command)
    found = ShellPass([], [])
    parts: list[str] = []
    for token in shell_tokens(text):
        if token.match is None:
            continue
        if token.match.lastgroup == "separator" and token.match.group() == "\n":
            _ended(found, parts, token)
            parts = []
        else:
            _taken(found, parts, token)
    if parts:
        found.lines.append(CommandLine("".join(parts), ()))
    return found


def command_lines(command: str) -> list[CommandLine]:
    return shell_pass(command).lines


def _taken(found: ShellPass, parts: list[str], token: ShellToken) -> None:
    match = token.match
    source = match.string[match.start():min(token.end, len(match.string))]
    if match.lastgroup in SUBSTITUTING:
        found.substitutions.extend(_substitutions(source))
    if match.lastgroup not in SKIPPED:
        parts.append(source)


def _ended(found: ShellPass, parts: list[str], token: ShellToken) -> None:
    for body in token.bodies:
        found.substitutions.extend(_substitutions(body.text) if body.expands else [])
    found.lines.append(CommandLine("".join(parts), tuple(body.text for body in token.bodies)))
