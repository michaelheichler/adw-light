from __future__ import annotations

from enum import StrEnum
from pathlib import PurePath

try:
    from . import office_text, scan_input
    from .markup import (
        CONFIG_EXTS,
        MIXED_LANGUAGE_EXTS,
        PROSE_EXTS,
        _sniff_prose,
        is_json_lines,
    )
except ImportError:
    import office_text
    import scan_input
    from markup import (
        CONFIG_EXTS,
        MIXED_LANGUAGE_EXTS,
        PROSE_EXTS,
        _sniff_prose,
        is_json_lines,
    )


CONFIG_BASENAMES = frozenset({
    ".pylintrc", ".editorconfig", ".npmrc", ".yarnrc", ".gitignore", ".gitattributes",
    ".dockerignore", ".flake8", ".coveragerc", ".prettierrc", ".eslintrc", ".babelrc",
})


class ProseBasis(StrEnum):
    SUFFIX = "suffix"
    CONTENT = "content"


def _prose_basis(path: str, text: str | None = None) -> ProseBasis | None:
    if text and text.startswith("#!"):
        return None
    lowered = path.lower()
    pure = PurePath(path)
    path_is_extracted_document = pure.suffix.lower() in office_text.DOCUMENT_SUFFIXES
    if path_is_extracted_document or any(lowered.endswith(ext) for ext in PROSE_EXTS):
        return ProseBasis.SUFFIX
    if text is None or pure.name in CONFIG_BASENAMES or _is_config(path):
        return None
    suffix_allows_guess = pure.suffix.lower() not in scan_input.TEXT_SUFFIXES - {"", ".out", ".log"}
    return ProseBasis.CONTENT if suffix_allows_guess and _sniff_prose(text) else None


def _is_prose(path: str, text: str | None = None) -> bool:
    return _prose_basis(path, text) is not None


def _is_config(path: str) -> bool:
    lowered = path.lower()
    if any(lowered.endswith(ext) for ext in CONFIG_EXTS):
        return True
    return PurePath(lowered).name in CONFIG_BASENAMES


def _code_file(path: str, text: str) -> bool:
    suffix = PurePath(path.lower()).suffix
    if scan_input.is_binary_content(path, text):
        return False
    if suffix == ".jsonl" and is_json_lines(text):
        return False
    return (not _is_prose(path, text) and not _is_config(path)) or suffix in MIXED_LANGUAGE_EXTS
