from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import NamedTuple

from .shell_text import ShellToken, _substitution_spans, shell_tokens

SCRIPT_SOURCE = r'(?:\$0|\$\{0\}|\$BASH_SOURCE|\$\{BASH_SOURCE(?:\[0\])?\})'
QUOTED_SOURCE = rf'(?:"{SCRIPT_SOURCE}"|{SCRIPT_SOURCE})'
DIRNAME = rf'\$\(dirname[ \t]+(?:--[ \t]+)?{QUOTED_SOURCE}\)'
DIRNAME_PATH = rf'{DIRNAME}(?:/\.\.)?'
QUOTED_DIRNAME = rf'(?:"{DIRNAME_PATH}"|{DIRNAME_PATH})'
SOURCE_PREFIX = r'"\$\{(?:BASH_SOURCE(?:\[0\])?|0)%/\*\}"'
CHANGE_DIRECTORY = r"(?:CDPATH=(?:''|\"\")[ \t]+)?cd[ \t]+(?:--[ \t]+)?"
QUIET = r'(?:[ \t]+2>/dev/null)?'
PWD_FALLBACK = rf'(?:[ \t]*\|\|[ \t]*(?:pwd(?:[ \t]+-P)?|dirname[ \t]+(?:--[ \t]+)?{QUOTED_SOURCE}))?'
CHANGE_SUBSTITUTION = (rf'\$\({CHANGE_DIRECTORY}(?:{QUOTED_DIRNAME}|{SOURCE_PREFIX}){QUIET}'
                       rf'[ \t]*&&[ \t]*pwd(?:[ \t]+-P)?{QUIET}{PWD_FALLBACK}\)')
CHANGE_SUBSTITUTION_RE = re.compile(CHANGE_SUBSTITUTION)
DIRECTORY_RE = re.compile(
    rf'(?:{DIRNAME}|\$\{{(?:BASH_SOURCE(?:\[0\])?|0)%/\*\}}|{CHANGE_SUBSTITUTION})'
)
LITERAL_CHANGE_RE = re.compile(
    rf'\$\({CHANGE_DIRECTORY}"(?P<directory>/[^"$`\\]*)"{QUIET}[ \t]*&&[ \t]*pwd(?P<physical>[ \t]+-P)?\)'
)
TRAILING_GUARD_RE = re.compile(r'(?P<value>"?\$\(.*\)"?)[ \t]*\|\|[ \t]*(?:return|exit)(?:[ \t]+[0-9]+)?')
GUARDED_RE = re.compile(
    r'^[ \t]*(?P<start>if)[ \t]+![ \t]+(?P<name>[A-Za-z_][A-Za-z_0-9]*)="(?P<expression>\$\([^\n]*\))"'
    r'[ \t]*;[ \t]*then[ \t]*\n[ \t]*(?P=name)=(?:"\$\(pwd(?:[ \t]+-P)?\)"|\$\(pwd(?:[ \t]+-P)?\))[ \t]*\n'
    r'[ \t]*fi(?=[ \t]*(?:\n|;|$))',
    re.MULTILINE,
)
PHYSICAL_RE = re.compile(r'\bset\b[^\n;&|]*?[ \t](?:[-+][A-Za-z]*P|-o[ \t]+physical\b)')
BASH_SOURCE_RE = re.compile(r'\$\{?BASH_SOURCE\b')
ASSIGNMENT_RE = re.compile(
    r'(?m)^[ \t]*(?:(?:export|readonly|local)[ \t]+)?'
    r'(?P<name>[A-Za-z_][A-Za-z_0-9]*)=(?P<value>[^\n]+?)(?:[ \t]+#[^\n]*)?[ \t]*$'
)

MIGRATION_RE = re.compile(
    r"""
    ^[ \t]*(?P<start>printf)[ \t]+-v[ \t]+(?P<name>[A-Za-z_][A-Za-z_0-9]*)
    [ \t]+'%s'[ \t]+"\$\{BASH_SOURCE\[0\]\}"[ \t]*(?:\n|;)[ \t]*
    case[ \t]+"\$(?P=name)"[ \t]+in\s*
    \*"/"\*\)[ \t]*printf[ \t]+-v[ \t]+(?P=name)
    [ \t]+'%s'[ \t]+"\$\{(?P=name)%"/"\*\}"[ \t]*;;\s*
    \*\)[ \t]*printf[ \t]+-v[ \t]+(?P=name)[ \t]+'%s'[ \t]+\.[ \t]*;;\s*
    esac(?=[ \t]*(?:\n|;|$))
    """,
    re.MULTILINE | re.VERBOSE,
)


class ScriptLocation(NamedTuple):
    path: Path
    operand: str
    executed: bool
    bash_source: bool = False


def with_script_directory(text: str, location: ScriptLocation) -> str:
    if PHYSICAL_RE.search(text):
        return text
    tokens = [token for token in shell_tokens(text) if token.match is not None]
    starts = {token.match.start() for token in tokens if token.match is not None}
    changes_directory = _changes_directory(tokens)
    replacements = _migration_assignments(text, location, starts) if location.bash_source else []
    replacements.extend(_guarded_assignments(text, location, starts, changes_directory))
    for assignment in ASSIGNMENT_RE.finditer(text):
        if assignment.start("name") not in starts:
            continue
        value = assignment.group("value")
        guard = TRAILING_GUARD_RE.fullmatch(value)
        replaced = guard.group("value") if guard is not None else value
        expression = replaced[1:-1] if replaced.startswith('"') and replaced.endswith('"') else replaced
        if not DIRECTORY_RE.fullmatch(expression) or not _resolvable(expression, location, changes_directory):
            continue
        directory = _directory_value(expression, location)
        start = assignment.start("value")
        replacements.append((start, start + len(replaced), shlex.quote(directory)))
    protected = tuple(replacements)
    replacements.extend(item for item in _dirname_words(text, location, tokens, changes_directory)
                        if not any(start <= item[0] < end for start, end, _value in protected))
    for start, end, value in sorted(replacements, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def _resolvable(expression: str, location: ScriptLocation, changes_directory: bool) -> bool:
    if not location.executed and re.search(r'\$(?:0|\{0(?:\}|%))', expression):
        return False
    if BASH_SOURCE_RE.search(expression) and not location.bash_source:
        return False
    if "&&" not in expression:
        return True
    if changes_directory and not os.path.isabs(location.operand):
        return False
    return "%/*" not in expression or "/" in location.operand


def _guarded_assignments(text: str, location: ScriptLocation, starts: set[int],
                         changes_directory: bool) -> list[tuple[int, int, str]]:
    replacements = []
    for match in GUARDED_RE.finditer(text):
        expression = match.group("expression")
        if match.start("start") not in starts or not CHANGE_SUBSTITUTION_RE.fullmatch(expression):
            continue
        if not _resolvable(expression, location, changes_directory):
            continue
        directory = shlex.quote(_directory_value(expression, location))
        assignment = f"{match.group('name')}={directory}" + "\n" * match.group().count("\n")
        replacements.append((match.start("start"), match.end(), assignment))
    return replacements


def _escaped(directory: str) -> str:
    return directory.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def _dirname_words(text: str, location: ScriptLocation, tokens: list[ShellToken],
                   changes_directory: bool) -> list[tuple[int, int, str]]:
    replacements = []
    escaped = _escaped(os.path.dirname(location.operand) or ".")
    for token in tokens:
        match = token.match
        if match is None or match.lastgroup != "double":
            continue
        offset = match.start()
        word = text[offset:token.end]
        changes = _change_spans(word, location, changes_directory)
        replacements.extend((offset + start, offset + end, value) for start, end, value in changes)
        replacements.extend((offset + start, offset + end, escaped) for start, end in _dirname_spans(word, location)
                            if not any(outer <= start < stop for outer, stop, _value in changes))
    return replacements


def _change_spans(word: str, location: ScriptLocation, changes_directory: bool) -> list[tuple[int, int, str]]:
    spans = []
    for start, end, _body in _substitution_spans(word):
        expression = word[start:end]
        if CHANGE_SUBSTITUTION_RE.fullmatch(expression) and _resolvable(expression, location, changes_directory):
            spans.append((start, end, _escaped(_directory_value(expression, location))))
        elif (literal := literal_directory(expression)) is not None:
            spans.append((start, end, _escaped(literal)))
    return spans


def literal_directory(expression: str) -> str | None:
    match = LITERAL_CHANGE_RE.fullmatch(expression)
    if match is None:
        return None
    logical = os.path.normpath(match.group("directory"))
    if not os.path.isdir(logical):
        return None
    return os.path.realpath(logical) if match.group("physical") else logical


def resolve_literal_directories(word: str, context: str = "") -> str:
    if PHYSICAL_RE.search(word) or PHYSICAL_RE.search(context):
        return word
    for start, end, _body in sorted(_substitution_spans(word), reverse=True):
        literal = literal_directory(word[start:end])
        if literal is not None:
            word = word[:start] + _escaped(literal) + word[end:]
    return word


def _dirname_spans(word: str, location: ScriptLocation) -> list[tuple[int, int]]:
    return [(start, end) for start, end, _body in _substitution_spans(word)
            if re.fullmatch(DIRNAME, word[start:end])
            and (location.executed or not re.search(r'\$(?:0|\{0\})', word[start:end]))
            and (location.bash_source or not BASH_SOURCE_RE.search(word[start:end]))]


def _changes_directory(tokens: list[ShellToken]) -> bool:
    depth = 0
    for token in tokens:
        word = token.match.group().strip() if token.match is not None else ""
        depth = max(0, depth + {"(": 1, ")": -1}.get(word, 0))
        if depth == 0 and word in {"cd", "pushd", "popd"}:
            return True
    return False


def _migration_assignments(text: str, location: ScriptLocation, starts: set[int]) -> list[tuple[int, int, str]]:
    replacements = []
    directory = shlex.quote(os.path.dirname(location.operand) or ".")
    for match in MIGRATION_RE.finditer(text):
        if match.start("start") not in starts:
            continue
        assignment = f"{match.group('name')}={directory}"
        replacement = assignment + "\n" * match.group().count("\n")
        replacements.append((match.start(), match.end(), replacement))
    return replacements


def _directory_value(expression: str, location: ScriptLocation) -> str:
    if "&&" in expression:
        directory = location.path.parent.parent if re.search(r'/\.\."?[ \t]*(?:2>/dev/null[ \t]*)?&&', expression) else location.path.parent
        parent = directory.resolve() if re.search(r'&&[ \t]*pwd[ \t]+-P', expression) else directory
        return str(parent)
    if "%/*" in expression:
        return location.operand.rsplit("/", 1)[0] if "/" in location.operand else location.operand
    return os.path.dirname(location.operand) or "."
