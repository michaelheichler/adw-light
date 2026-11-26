from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import NamedTuple

from lib.opaque_write import _bare_interpreter_name, _execution_contexts, _interpreter_run
from lib.shell_expansion import Expansions
from lib.shell_parse import _bare, _basename, _command_word_index, line_segments, write_paths
from lib.write_targets import _without_redirects, mutation_targets, unknown_targets

try:
    from .captured_output import _pristine
    from .commit_inputs import _shell_args
    from .script_directory import resolve_literal_directories
    from .script_files import script_text
    from .shell_parse import command_expansions, interpreter_invocation
    from .shell_self_protection import _segment_cwds
    from .shell_syntax import _payload_command_index
    from .shell_text import shell_tokens
except ImportError:
    from captured_output import _pristine
    from commit_inputs import _shell_args
    from script_directory import resolve_literal_directories
    from script_files import script_text
    from shell_parse import command_expansions, interpreter_invocation
    from shell_self_protection import _segment_cwds
    from shell_syntax import _payload_command_index
    from shell_text import shell_tokens

AWK_NAMES = frozenset({"awk", "gawk", "mawk", "nawk"})
AWK_VALUE_FLAGS = frozenset({"-f", "-v", "-F", "-i", "-l", "-E", "--file", "--assign", "--field-separator", "--include", "--load"})
AWK_PROGRAM_FILE_FLAGS = frozenset({"-f", "--file", "-E"})
AWK_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')
AWK_RUN_RE = re.compile(r"\bsystem\s*\(|\|\s*getline|\|&")
AWK_OUTPUT_RE = re.compile(r"\b(?:print|printf)\b[^;{}\n]*?(>>?|\|)\s*(\"\d+\"|[^\s;{}]*)")
AWK_STANDARD_STREAMS = frozenset({'"/dev/stdout"', '"/dev/stderr"'})
HERESTRING = "<<<"
EXISTENCE_TEST_RE = r"""(?:\[\[?\s+-[efrsx]\s+{quoted}\s+\]\]?|\btest\s+-[efrsx]\s+{quoted}(?=[\s;&)]|$))"""
GUARD_LEAD = r"(?:^|[;\n&|(]|\bthen\b|\bdo\b)\s*"
GUARDED_CALL = r"(?:\w+=\S*\s+)*{name}\s(?:[^;&|\n]*\s)?{quoted}(?=[\s;&|)]|$)"
GUARD_FORMS = (
    GUARD_LEAD + r"{test}\s*&&\s*" + GUARDED_CALL,
    GUARD_LEAD + r"if\s+{test}\s*;\s*(?=then\b)(?:(?!\b(?:else|elif|fi)\b)[\s\S])*?" + GUARD_LEAD + GUARDED_CALL,
)
QUOTED_KINDS = frozenset({"single", "double"})
HIDDEN_KINDS = frozenset({"escape", "backtick", "subst", "procsub"})
DYNAMIC_WRITE_MARKS = ("$", "`", "/proc/", "/dev/fd/")


class Script(NamedTuple):
    interpreter: str
    body: str | None
    segment: tuple[str, ...]


def herestring_scripts(command: str, expansions: Expansions) -> list[Script]:
    scripts = []
    for segment in line_segments(command):
        word, rest = _herestring(segment)
        name = _bare_interpreter_name(rest) if word is not None else None
        scripts.extend([Script(name, expansions.assigned(word), tuple(segment))] if name and word is not None else [])
    return scripts


def _herestring(segment: list[str]) -> tuple[str | None, list[str]]:
    for index, token in enumerate(segment):
        if token == HERESTRING and index + 1 < len(segment):
            return segment[index + 1], segment[:index] + segment[index + 2:]
        if token.startswith(HERESTRING) and len(token) > len(HERESTRING):
            return token[len(HERESTRING):], segment[:index] + segment[index + 1:]
    return None, segment


def eval_scripts(command: str, expansions: Expansions) -> list[Script]:
    scripts = []
    for segment in line_segments(command):
        args = _eval_args(segment)
        if args:
            parts = [expansions.assigned(token) for token in _without_redirects(args)]
            body = None if None in parts else " ".join(part for part in parts if part is not None)
            scripts.append(Script("eval", body, tuple(segment)))
    return scripts


def _eval_args(segment: list[str]) -> list[str]:
    index = _command_word_index(segment)
    index += 1 if index < len(segment) and _bare(segment[index]) == "builtin" else 0
    return segment[index + 1:] if index < len(segment) and _bare(segment[index]) == "eval" else []


def awk_writes(command: str, expansions: Expansions) -> bool:
    for segment in line_segments(command):
        index = _command_word_index(segment)
        if index < len(segment) and _basename(segment[index]) in AWK_NAMES and _awk_args_write(segment[index + 1:], expansions):
            return True
    return False


def changed_script_inputs(command: str, cwd: Path):
    expansions = command_expansions(command, cwd)
    for segment, directory in _segment_cwds(command, cwd, None):
        index = _payload_command_index(segment)
        if index >= len(segment):
            continue
        name = _basename(segment[index])
        if name not in {"sh", "bash", "python3", "node"}:
            continue
        args = _without_redirects(segment[index + 1:])
        word = _file_operand(name, args)
        if word is None or word == "-":
            continue
        options = args[:args.index(word)]
        if interpreter_invocation([segment[index], *options, word]):
            continue
        value = expansions.path(resolve_literal_directories(word, command))
        path = (directory / value).resolve() if value else None
        if path is not None and not any(c in str(path) for c in "*?[") and _pristine(path, directory):
            continue
        if path is not None and not path.exists() and _guarded(command, name, _bare(word)) and not _may_write(command, path, directory):
            continue
        prefix = [*segment[:index + 1], *options, "-c"]
        contexts = _execution_contexts(command, cwd).get(tuple(segment), [])
        roots = [*contexts, str(path.parent)] if contexts and path else contexts
        run = _interpreter_run(name, script_text(path) if path is not None else None, prefix, {tuple(prefix): roots})
        yield run, directory


def unresolved_shell_script(command: str, cwd: Path) -> bool:
    expansions = command_expansions(command, cwd)
    for segment, _directory in _segment_cwds(command, cwd, None):
        index = _payload_command_index(segment)
        if index >= len(segment) or _basename(segment[index]) not in {"sh", "bash"}:
            continue
        args = _without_redirects(segment[index + 1:])
        word = _file_operand(_basename(segment[index]), args)
        if word is None or word == "-" or interpreter_invocation([segment[index], *args[:args.index(word)], word]):
            continue
        if not expansions.path(resolve_literal_directories(word, command)):
            return True
    return False


def _guarded(command: str, name: str, word: str) -> bool:
    quoted = f"""["']?{re.escape(word)}["']?"""
    test = EXISTENCE_TEST_RE.format(quoted=quoted)
    visible = _visible_text(command, word)
    if visible is None:
        return False
    forms = [form.format(test=test, name=re.escape(name), quoted=quoted) for form in GUARD_FORMS]
    return any(re.search(form, visible) for form in forms)


def _visible_text(command: str, word: str) -> str | None:
    parts, start = [], 0
    for token in shell_tokens(command):
        source, start = command[start:token.end], token.end
        if any(not body.closed for body in token.bodies):
            return None
        if token.match is None:
            continue
        kind = token.match.lastgroup
        if kind in QUOTED_KINDS and (len(source) < 2 or source[-1] != source[0]):
            return None
        if kind in QUOTED_KINDS:
            parts.append(source if source[1:-1] == word else "''")
        elif kind in HIDDEN_KINDS:
            parts.append("''")
        elif kind != "comment":
            parts.append(token.match.group())
    return "".join(parts) if start == len(command) else None


def _may_write(command: str, path: Path, directory: Path) -> bool:
    expansions = command_expansions(command, directory)
    named = list(write_paths(command))
    for segment in line_segments(command):
        if unknown_targets(segment, expansions):
            return True
        named += mutation_targets(segment, expansions)
    return any(_could_name(target, path, directory) for target in named)


def _could_name(target: str, path: Path, directory: Path) -> bool:
    if any(mark in target for mark in DYNAMIC_WRITE_MARKS) or target.startswith("~"):
        return True
    written, run = _folded_parts((directory / target).resolve()), _folded_parts(path)
    return written == run or written[-1:] == run[-1:] or run[:len(written)] == written


def _folded_parts(path: Path) -> tuple[str, ...]:
    return tuple(unicodedata.normalize("NFC", unicodedata.normalize("NFC", part).casefold()) for part in path.parts)


def _file_operand(name: str, args: list[str]) -> str | None:
    words = [_bare(arg) for arg in args]
    if name in {"sh", "bash"}:
        shell = _shell_args(words)
        if shell.from_stdin or shell.reads_command or shell.parse_only or shell.operand is None:
            return None
        return args[len(args) - len(shell.rest) - 1]
    index = 0
    while index < len(words) and words[index].startswith("-"):
        flag = words[index]
        if flag == "--":
            index += 1
            break
        if flag in {"-", "--version", "--help"} or (name == "python3" and flag.startswith(("-m", "-c"))):
            return None
        index += 2 if flag in {"-W", "-X", "-r", "--require", "--import", "--loader"} else 1
    return args[index] if index < len(args) else None


def _awk_args_write(args: list[str], expansions: Expansions) -> bool:
    words = [_bare(token) for token in args]
    position = 0
    while position < len(words) and words[position].startswith("-") and words[position] != "--":
        if words[position] in AWK_PROGRAM_FILE_FLAGS:
            return False
        if words[position:position + 2] in (["-i", "inplace"], ["--include", "inplace"]) or words[position] in {"-iinplace", "--include=inplace"}:
            return True
        position += 2 if words[position] in AWK_VALUE_FLAGS else 1
    position += 1 if words[position:position + 1] == ["--"] else 0
    program = expansions.assigned(args[position]) if position < len(args) else ""
    return program is None or _program_writes(program)


def _program_writes(program: str) -> bool:
    strings = AWK_STRING_RE.findall(program)
    numbers = iter(range(len(strings)))
    stripped = AWK_STRING_RE.sub(lambda _match: f'"{next(numbers)}"', program)
    if AWK_RUN_RE.search(stripped):
        return True
    for match in AWK_OUTPUT_RE.finditer(stripped):
        target = match.group(2)
        named = strings[int(target[1:-1])] if target.startswith('"') else ""
        if match.group(1) == "|" or named not in AWK_STANDARD_STREAMS:
            return True
    return False
