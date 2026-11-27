from __future__ import annotations

from pathlib import Path

try:
    from .path_policy import anchored_identity
    from .shell_expansion import Expansions
    from .shell_parse import command_expansions
    from .shell_self_protection import _segment_cwds
except ImportError:
    from path_policy import anchored_identity
    from shell_expansion import Expansions
    from shell_parse import command_expansions
    from shell_self_protection import _segment_cwds

QUOTES = "'\""


def named_directories(command: str, cwd: Path) -> tuple[Path, ...]:
    expansions = command_expansions(command, cwd)
    found: list[Path] = []
    for segment, folder in _segment_cwds(command, cwd, None):
        for word in segment:
            value = resolved_prefix(word, expansions)
            if value and _names_directory(value, folder):
                found.append(anchored_identity(value, folder))
    return tuple(dict.fromkeys(found))


def resolved_prefix(word: str, expansions: Expansions) -> str | None:
    text = word.partition("=")[2] if word.startswith("-") else word
    cut = len(text)
    while cut > 0:
        value = expansions.path(_closed(text[:cut]))
        if value:
            return value
        cut = text.rfind("/", 0, cut)
    return None


def _closed(text: str) -> str:
    quote, escaped = "", False
    for char in text:
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            quote = "" if char == quote else quote
        elif char in QUOTES:
            quote = char
    return (text[:-1] if escaped else text) + quote


def _names_directory(value: str, folder: Path) -> bool:
    return "/" in value or value.startswith(("~", ".")) or (folder / value).is_dir()
