from __future__ import annotations

from pathlib import PurePath

try:
    from . import scan_input
    from .markup import (
        CONFIG_EXTS,
        MIXED_LANGUAGE_EXTS,
        PROSE_EXTS,
        _sniff_prose,
    )
except ImportError:
    import scan_input
    from markup import (
        CONFIG_EXTS,
        MIXED_LANGUAGE_EXTS,
        PROSE_EXTS,
        _sniff_prose,
    )


CONFIG_BASENAMES = frozenset({
    ".pylintrc", ".editorconfig", ".npmrc", ".yarnrc", ".gitignore", ".gitattributes",
    ".dockerignore", ".flake8", ".coveragerc", ".prettierrc", ".eslintrc", ".babelrc",
})


def _is_prose(path: str, text: str | None = None) -> bool:
    if text and text.startswith("#!"):
        return False
    lowered = path.lower()
    if any(lowered.endswith(ext) for ext in PROSE_EXTS):
        return True
    pure = PurePath(path)
    if text is None or pure.name in CONFIG_BASENAMES or _is_config(path):
        return False
    return pure.suffix.lower() not in scan_input.TEXT_SUFFIXES - {"", ".out", ".log"} and _sniff_prose(text)


def _is_config(path: str) -> bool:
    lowered = path.lower()
    if any(lowered.endswith(ext) for ext in CONFIG_EXTS):
        return True
    return PurePath(lowered).name in CONFIG_BASENAMES


def _code_file(path: str, text: str) -> bool:
    suffix = PurePath(path.lower()).suffix
    if scan_input.is_binary_content(path, text):
        return False
    return (not _is_prose(path, text) and not _is_config(path)) or suffix in MIXED_LANGUAGE_EXTS
