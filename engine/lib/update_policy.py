from __future__ import annotations

import os
import pwd
from pathlib import Path

from .shell_parse import (
    _basename,
    _command_word_index,
    _words,
)
from .temp_scope import literal_directory

def _segment_contexts(
    segments: list[list[str]],
    cwd: str | os.PathLike[str] | None,
    home: str | os.PathLike[str] | None,
) -> list[tuple[list[str], Path]]:
    context_home = home if home is not None else pwd.getpwuid(os.getuid()).pw_dir
    current = Path(cwd) if cwd is not None else Path.cwd()
    contexts = []
    for segment in segments:
        contexts.append((segment, current))
        current = _next_shell_cwd(segment, current, context_home)
    return contexts


def _next_shell_cwd(
    segment: list[str], current: Path, home: str | os.PathLike[str],
) -> Path:
    words = _words(segment)
    index = _command_word_index(segment)
    if index >= len(words) or _basename(words[index]) != "cd":
        return current
    operand = next((i for i in range(index + 1, len(words)) if words[i] == "--" or not words[i].startswith("-")), len(words))
    if operand < len(words) and words[operand] == "--":
        operand += 1
    if operand >= len(words):
        return Path(os.path.realpath(Path(home))) if words[index + 1:] in ([], ["--"]) else current
    try:
        directory = literal_directory(segment[operand], home)
        return Path(os.path.realpath(current / directory)) if directory else current
    except (OSError, ValueError):
        return current
