from __future__ import annotations

import re

from lib.inplace_targets import inplace_edit
from lib.shell_parse import (
    _bare, _basename, _command_word_index, _interpreter_code_flags, _is_stdin_program,
    _logical_lines, _payload_command_index, _segments,
)


INPLACE_VERBS = frozenset({"sed", "perl", "ruby"})
AWK_VERBS = frozenset({"awk", "gawk"})
VALUE_CONSUMING_FLAGS: dict[str, frozenset[str]] = {
    "sed": frozenset({"e", "f"}),
    "perl": frozenset({"I", "e", "E"}),
    "ruby": frozenset({"I", "e", "E"}),
}
MUTATING_VERB_RE = re.compile(
    r"\b(?:tee|cp|mv|ln|rm|truncate|chmod|chown|dd|shred|unlink)\b|\bsed\s+-i", re.IGNORECASE
)


def _is_positional_argument(token: str) -> bool:
    bare = _bare(token)
    return not bare.startswith(("-", "<", ">"))


def _bare_interpreter_name(segment: list[str]) -> str | None:
    index = _payload_command_index(segment)
    if index >= len(segment):
        return None
    name = _basename(segment[index])
    if _interpreter_code_flags(name) is None:
        return None
    trailing = segment[index + 1:]
    has_operand = any(_is_positional_argument(token) for token in trailing)
    if has_operand and not _is_stdin_program(name, trailing):
        return None
    return name


def _cluster_has_inplace(verb: str, letters: str) -> bool:
    value_consuming = VALUE_CONSUMING_FLAGS.get(verb, frozenset())
    for letter in letters:
        if letter == "i":
            return True
        if letter in value_consuming:
            return False
    return False


def _awk_has_inplace(tokens: list[str]) -> bool:
    expecting_value = False
    for token in tokens:
        bare = _bare(token)
        if expecting_value and bare.startswith("inplace"):
            return True
        if expecting_value:
            expecting_value = False
            continue
        if bare == "--inplace" or bare.startswith("--inplace="):
            return True
        if bare in ("-i", "--include"):
            expecting_value = True
            continue
        if bare.startswith("-i") and not bare.startswith("--") and bare[2:].startswith("inplace"):
            return True
    return False


def _has_inplace_flag(segment: list[str]) -> bool:
    index = _command_word_index(segment)
    if index >= len(segment):
        return False
    verb = _basename(segment[index])
    if verb in AWK_VERBS:
        return _awk_has_inplace(segment[index + 1:])
    if verb not in INPLACE_VERBS:
        return False
    for token in segment[index + 1:]:
        bare = _bare(token)
        if bare == "--in-place" or bare.startswith("--in-place="):
            return True
        if bare.startswith("-") and not bare.startswith("--") and _cluster_has_inplace(verb, bare[1:]):
            return True
    return False


def inplace_edit_paths(command: str) -> list[str]:
    edits = (inplace_edit(segment) for line, _, _ in _logical_lines(command) for segment in _segments(line) if _has_inplace_flag(segment))
    return [target for edit in edits if edit is not None for target in edit.targets]
