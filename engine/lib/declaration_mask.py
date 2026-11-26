from __future__ import annotations

import re

try:
    from .brace_functions import BRACE_LANGUAGE_EXTS, _masked_end
except ImportError:
    from brace_functions import BRACE_LANGUAGE_EXTS, _masked_end

HASH_COMMENTS = frozenset({".rb", ".sh", ".bash", ".bats", ".ex", ".exs", ".php"})


BLOCK_DELIMITERS = re.compile(r"/\*|\*/")
RUST_LIFETIME = re.compile(r"'[a-zA-Z_]\w*\b(?!')")


RUST_RAW = re.compile(r'r(#*)"')
CS_VERBATIM = re.compile(r'(?:\$@|@\$?)"(?:[^"]|"")*"')


def _block_end(text: str, start: int) -> int:
    depth = 1
    for marker in BLOCK_DELIMITERS.finditer(text, start + 2):
        depth += 1 if marker[0] == "/*" else -1
        if not depth:
            return marker.end()
    return len(text)


def _literal_end(text: str, index: int, suffix: str) -> int | None:
    if suffix == ".cs" and (literal := CS_VERBATIM.match(text, index)):
        return literal.end()
    if suffix == ".rs" and (literal := RUST_RAW.match(text, index)):
        closing = '"' + literal[1]
        end = text.find(closing, literal.end())
        return len(text) if end < 0 else end + len(closing)
    if suffix == ".rs" and RUST_LIFETIME.match(text, index):
        return None
    if suffix in {".kt", ".kts", ".scala", ".rs", ".swift"} and text.startswith("/*", index):
        return _block_end(text, index)
    if suffix in HASH_COMMENTS and text[index] == "#" or suffix == ".lua" and text.startswith("--", index):
        end = text.find("\n", index)
        return len(text) if end < 0 else end
    if suffix in {".kt", ".kts", ".java", ".scala", ".swift"} and text.startswith('"""', index):
        end = text.find('"""', index + 3)
        return len(text) if end < 0 else end + 3
    end = _masked_end(text, index, suffix in BRACE_LANGUAGE_EXTS)
    if end is not None and text[index] in {'"', "'"}:
        newline = text.find("\n", index, end)
        if newline >= 0:
            end = newline
    return end


def declaration_view(text: str, suffix: str) -> str:
    code, index = list(text), 0
    while index < len(text):
        end = _literal_end(text, index, suffix)
        if end is None:
            index += 1
            continue
        start = index + (suffix == ".go" and text[index] == '"')
        code[start:end] = ["\n" if char == "\n" else " " for char in text[start:end]]
        index = end
    return "".join(code)
