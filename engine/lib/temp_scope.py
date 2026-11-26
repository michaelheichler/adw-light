from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

try:
    from . import config_roots as config_paths
except ImportError:
    import config_roots as config_paths
from typing import NamedTuple

from lib.path_policy import anchored
from lib.shell_syntax import _bare, _basename, _command_word_index, _is_literal_token, _segments, _tokens
from lib.shell_text import shell_tokens

FIXED_TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
DIRECTORY_VERBS = frozenset({"cd", "pushd", "popd"})
LEADING_CD_SEPARATORS = frozenset({"&&", ";"})
EXPANDING_RE = re.compile(r"[*?\[{\\$`]")
MAX_DIRECTORY_CANDIDATES = 16
Directories = tuple[str, ...] | None
Resolve = Callable[[str], "str | None"]


class _Walk(NamedTuple):
    cwd: Path | None
    resolve: Resolve | None


def _forms(path: Path) -> tuple[Path, ...]:
    lexical = Path(os.path.normpath(path))
    try:
        return lexical, Path(os.path.realpath(lexical))
    except (OSError, RuntimeError, ValueError):
        return (lexical,)


def temp_roots() -> tuple[Path, ...]:
    raw = (*FIXED_TEMP_ROOTS, tempfile.gettempdir(), os.environ.get("TMPDIR") or "")
    return tuple({form for item in raw if item for form in _forms(Path(item))})


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def outside_project_temp(path: str | os.PathLike[str], cwd: str | os.PathLike[str] | None) -> bool:
    if not cwd or not Path(cwd).is_absolute():
        return False
    forms = _forms(anchored(path, Path(cwd or ".").absolute()))
    if not all(_within(form, temp_roots()) for form in forms):
        return False
    return not any(_within(form, _forms(Path(cwd))) for form in forms)


TEMP_ASSIGNMENT = re.compile(r'([a-z][a-z0-9_]*)=(.*)', re.ASCII)
TEMP_SPECIAL_NAMES = frozenset({"aliases", "commands", "fignore", "fpath", "functions", "module_path", "options", "path", "status", "watch"})
TEMP_SUBSTITUTION = re.compile(r'\$\((mktemp[ \t][^\n]*)\)')
TEMP_REFERENCE = re.compile(r'"\$(?:([a-z][a-z0-9_]*)|\{([a-z][a-z0-9_]*)\})((?:/[\w.-]+)*)"', re.ASCII)
TEMP_COMPONENT = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*')
TEMP_TEMPLATE = re.compile(r'/[\w./-]+X{3,}', re.ASCII)
TEMP_WORD = re.compile(r'[A-Za-z0-9_.,/][A-Za-z0-9_.,/-]*')
TEMP_TAIL_EXPANSION = re.compile(r"[$\x60'\x22\\*?\[({]")
TMPDIR_DEFAULT = "$" + "{TMPDIR:-/tmp}"


def sealed_temp_tail(command: str, cwd: str | os.PathLike[str] | None) -> str:
    if not cwd or not Path(cwd).is_absolute() or "$(mktemp" not in command:
        return command
    directory = Path(cwd)
    bindings: dict[str, str] = {}
    start = 0
    for end, separator in _temp_statement_ends(command):
        if not _temp_statement(command[start:end].strip(), directory, bindings):
            break
        if separator not in {";", "\n", ""}:
            return command
        start = end + len(separator)
    tail = command[start:]
    return tail if bindings and _sealed_temp_consumer(tail) else command


def _sealed_temp_consumer(tail: str) -> bool:
    return TEMP_TAIL_EXPANSION.search(tail) is None and all(
        not segment or segment[0] in {"git", "bash", "sh"} for segment in _segments(tail)
    )


def _temp_statement_ends(command: str) -> Iterator[tuple[int, str]]:
    for token in shell_tokens(command):
        match = token.match
        if match is not None and match.lastgroup == "separator":
            yield match.start(), match.group()
    yield len(command), ""


def _temp_statement(text: str, cwd: Path, bindings: dict[str, str]) -> bool:
    if (assigned := TEMP_ASSIGNMENT.fullmatch(text)) is not None:
        name, value = assigned.groups()
        if name in TEMP_SPECIAL_NAMES or name in os.environ or name in bindings:
            return False
        known = _temp_reference(value, bindings)
        if known is not None or _fresh_temp_folder(value, cwd):
            bindings[name] = known or ""
            return True
        return False
    if not bindings or not text.startswith(("printf ", "cp ")):
        return False
    match _tokens(text):
        case ["cp", source, destination] if TEMP_WORD.fullmatch(source):
            return _temp_write_path(destination, bindings)
        case ["printf", value, ">" | ">>", destination] if TEMP_WORD.fullmatch(value):
            return _temp_write_path(destination, bindings)
    return False


def _temp_reference(token: str, bindings: dict[str, str]) -> str | None:
    if (match := TEMP_REFERENCE.fullmatch(token)) is None:
        return None
    plain, braced, suffix = match.groups()
    base = bindings.get(plain or braced)
    if base is None or not all(TEMP_COMPONENT.fullmatch(part) for part in suffix.split("/")[1:]):
        return None
    return base + suffix


def _temp_write_path(token: str, bindings: dict[str, str]) -> bool:
    fallback = _temp_reference(token, bindings)
    if not fallback:
        return False
    first = Path("/") / Path(fallback).parts[1]
    return not first.exists() and not first.is_symlink() and not os.access(first.parent, os.W_OK)


def _fresh_temp_folder(value: str, cwd: Path) -> bool:
    call = TEMP_SUBSTITUTION.fullmatch(value[1:-1] if value.startswith('"') and value.endswith('"') else value)
    if call is None:
        return False
    root = next((path for path in (cwd, *cwd.parents) if (path / ".git").exists()), cwd)
    default = os.environ.get("TMPDIR") or "/tmp"
    match _tokens(call[1]):
        case ["mktemp", "-d"] | ["mktemp", "-d", "-t"]:
            folder = Path(default)
        case ["mktemp", "-d", "-t", prefix] if TEMP_COMPONENT.fullmatch(_bare(prefix)):
            folder = Path(default)
        case ["mktemp", "-d", template]:
            folder = _temp_template_parent(template, default)
        case _:
            return False
    return folder is not None and folder.is_absolute() and outside_project_temp(folder / "adw-new-temp", root)


def _temp_template_parent(token: str, default: str) -> Path | None:
    template = _bare(token)
    if token.startswith('"' + TMPDIR_DEFAULT + "/"):
        template = default.rstrip("/") + template[len(TMPDIR_DEFAULT):]
    if not TEMP_TEMPLATE.fullmatch(template) or ".." in Path(template).parts:
        return None
    return Path(template).parent


def leading_directory(command: str) -> str | None:
    lines = command.splitlines()
    tokens = _tokens(lines[0]) if lines else []
    if len(tokens) < 3 or tokens[0] != "cd" or tokens[2] not in LEADING_CD_SEPARATORS or not _plain_path(tokens[1]):
        return None
    changes = [segment for line in lines for segment in _segments(line) if _changes_directory(segment)]
    return _bare(tokens[1]) if len(changes) == 1 else None


def trusted_directory(command: str, cwd: str | os.PathLike[str] | None) -> str | None:
    directory = leading_directory(command)
    if directory is None or _tokens(command.splitlines()[0])[2] != "&&":
        return None
    absolute = anchored(directory, Path(cwd or ".").absolute())
    return directory if absolute.is_dir() and os.access(absolute, os.X_OK) else None


def logical_directory(directory: str, cwd: Path) -> Path | None:
    operand = Path(directory)
    if ".." in operand.parts:
        return None
    path = cwd / operand
    if any(part.is_symlink() for part in (path, *path.parents)):
        return None
    return Path(os.path.normpath(path))


def command_targets(path: str, command: str, cwd: str | os.PathLike[str] | None) -> list[str]:
    directory = leading_directory(command)
    if directory is None or path.startswith("~") or Path(path).is_absolute():
        return [path]
    joined = os.path.join(directory, path)
    return [joined] if trusted_directory(command, cwd) else [joined, path]


def scratch_target(path: str, command: str, cwd: str | os.PathLike[str] | None) -> bool:
    if not _plain_path(path) or path.startswith("~"):
        return False
    if Path(path).is_absolute():
        return outside_project_temp(path, cwd)
    directory = trusted_directory(command, cwd)
    return directory is not None and outside_project_temp(anchored(directory, Path(cwd or ".").absolute()) / path, cwd)


def segment_directories(segments: list[list[str]], start: Directories, resolve: Resolve | None = None) -> list[Directories]:
    return _walked_directories(segments, start, _Walk(None, resolve))


def landing_directories(segments: list[list[str]], cwd: str | os.PathLike[str] | None, resolve: Resolve | None = None) -> list[Directories]:
    return _walked_directories(segments, (".",), _Walk(Path(cwd or ".").absolute(), resolve))


def joined_paths(paths: list[str], folders: Directories) -> list[str]:
    anchored = [path for path in paths if path.startswith("~") or os.path.isabs(path)]
    relative = [path for path in paths if path not in anchored]
    return anchored + [os.path.normpath(os.path.join(folder, path)) for path in relative for folder in folders or ()]


def _walked_directories(segments: list[list[str]], start: Directories, walk: _Walk) -> list[Directories]:
    current = start
    found: list[Directories] = []
    for segment in segments:
        found.append(current)
        current = _after_directory_change(segment, current, walk)
    return found


def _after_directory_change(segment: list[str], current: Directories, walk: _Walk) -> Directories:
    index = _command_word_index(segment)
    if index < len(segment) and _bare(segment[index]) == "builtin":
        return None
    if current is None or not _changes_directory(segment):
        return current
    target = _cd_target(segment[index:], walk.resolve)
    if target is None:
        return None
    moved = {os.path.normpath(os.path.join(folder, target)) for folder in current}
    kept = set() if walk.cwd is not None and all(_searchable(walk.cwd / folder) for folder in moved) else set(current)
    reached = tuple(sorted(kept | moved))
    return reached if len(reached) <= MAX_DIRECTORY_CANDIDATES else None


def _searchable(folder: Path) -> bool:
    return folder.is_dir() and os.access(folder, os.X_OK)


def _cd_target(words: list[str], resolve: Resolve | None) -> str | None:
    operands = [word for word in words[1:] if not _bare(word).startswith("-")]
    if _basename(words[0]) != "cd" or len(operands) > 1 or "-" in map(_bare, words[1:]):
        return None
    if not operands:
        return str(config_paths.home_root())
    folder = resolve(operands[0]) if resolve is not None else (_bare(operands[0]) if _plain_path(operands[0]) else None)
    return None if folder is None else os.path.expanduser(os.fspath(folder))


def _plain_path(token: str) -> bool:
    bare = _bare(token)
    return bool(bare) and not bare.startswith("-") and _is_literal_token(token) and not EXPANDING_RE.search(bare)


def _changes_directory(segment: list[str]) -> bool:
    index = _command_word_index(segment)
    return index < len(segment) and _basename(segment[index]) in DIRECTORY_VERBS
