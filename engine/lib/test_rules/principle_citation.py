from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

from . import Hit, Rule, RuleSet, module_scopes, module_tree
from .repo_building import named_test_path
try:
    from ..declaration_mask import HASH_COMMENTS, _literal_end
    from ..shell_units import SHELL_SUFFIXES, shell_lines
    from ..brace_functions import _skip_quoted
except ImportError:
    from lib.declaration_mask import HASH_COMMENTS, _literal_end
    from lib.shell_units import SHELL_SUFFIXES, shell_lines
    from lib.brace_functions import _skip_quoted

RULE = "test_principle_citation"
SKILL = Path(__file__).resolve().parents[3] / "skills" / "unit-testing-principles"
DOC_RULES = {"added_docstring", "what_docstring"}


@lru_cache(maxsize=1)
def _book():
    paths = [SKILL / "SKILL.md", *sorted((SKILL / "chapters").glob("*.md"))]
    return tuple(" ".join(path.read_text(encoding="utf-8").split()) for path in paths)


def _quoted(value):
    words = " ".join(value.split())
    if not words or not any(char.isalnum() for char in words):
        return False
    pattern = re.compile(r"(?<!\w)" + re.escape(words) + r"(?!\w)")
    return any(pattern.search(chapter) for chapter in _book())


@lru_cache(maxsize=16)
def _lines(text):
    return text.splitlines()


def citation_comment_markers(path: str, language: str = "") -> tuple[str, ...]:
    try:
        from ..machine_paths import DASH_COMMENT_SUFFIXES
    except ImportError:
        from lib.machine_paths import DASH_COMMENT_SUFFIXES

    suffix = Path(path.lower()).suffix
    if suffix in DASH_COMMENT_SUFFIXES:
        return ("--",)
    if suffix == ".php":
        return ("#", "//")
    return ("#",) if suffix in HASH_COMMENTS | SHELL_SUFFIXES or language in {"ruby", "shell", "bash"} else ("//",)


def match_comment_citation(markers: tuple[str, ...], line: str) -> re.Match[str] | None:
    choices = "|".join(map(re.escape, markers))
    return re.fullmatch(f"(?:{choices})" + r"[ \t]*Principle: (.+)", line.strip())


def verified_comment(markers, line):
    match = match_comment_citation(markers, line)
    return bool(match and _quoted(match[1]))


FRAMEWORK_HEADS = {
    '.kt': re.compile(r'\bfun\s+`[^`]+`\s*\('),
    '.php': re.compile(r'\bfunction\s+test\w*\s*\(|^/\*\*\s*@test\b'),
    '.go': re.compile(r'^func\s*\([^)]*\)\s*Test(?:[A-Z0-9_]\w*)?\s*\('),
    '.groovy': re.compile(r'''^def\s+(["']).+?\1\s*\('''),
    '.exs': re.compile(r'''^property\s*(?:\(|["'])'''),
    '.scala': re.compile(r'''^(?:property\s*\(|(?:"[^"]*"|it)\s+should\s+"[^"]*"\s+in\b)'''),
}
FRAMEWORK_HEADS['.kts'] = FRAMEWORK_HEADS['.kt']
FRAMEWORK_HEADS['.ex'] = FRAMEWORK_HEADS['.exs']


def broken_heads(unit, text):
    try:
        from ..language_units import BATS_TEST_RE, DART_TEST_CALLS, GO_TEST_RE, MARKER_RE, NAMED_TEST_RE, TEST_CALLS
    except ImportError:
        from lib.language_units import BATS_TEST_RE, DART_TEST_CALLS, GO_TEST_RE, MARKER_RE, NAMED_TEST_RE, TEST_CALLS

    framework = FRAMEWORK_HEADS.get(Path(unit.path.lower()).suffix)
    heads = []
    for number, raw in enumerate(_lines(text), 1):
        line = raw.strip()
        function = re.search(r"\b(?:func|fun|def)\s+(\w+)", line)
        call = re.match(r'''(\w+)(?:\.(?:only|skip|todo))?\s*(?:\(|['"])''', line)
        if (framework and framework.search(line) or MARKER_RE.search(line) or BATS_TEST_RE.match(line)
                or function and (GO_TEST_RE.match(function[1]) or NAMED_TEST_RE.match(function[1]))
                or call and call[1] in TEST_CALLS | DART_TEST_CALLS):
            heads.append(unit._replace(start=number, end=number, name=line, body=raw))
    return heads


def comment_citations(unit, text):
    return _verified_lines(Path(unit.path.lower()).suffix, citation_comment_markers(unit.path, unit.language), text)


@lru_cache(maxsize=16)
def _verified_lines(suffix, markers, text):
    if suffix in SHELL_SUFFIXES:
        return frozenset(line.number for line in shell_lines(_lines(text))
                         if line.fresh and verified_comment(markers, line.text))
    found = set()
    index = 0
    number = 1
    while index < len(text):
        if suffix == ".lua" and (opening := re.match(r"--\[(=*)\[", text[index:index + 256])):
            closing = "]" + opening[1] + "]"
            stop = text.find(closing, index + opening.end())
            end = len(text) if stop < 0 else stop + len(closing)
        elif text.startswith(markers, index):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            start = text.rfind("\n", 0, index) + 1
            if not text[start:index].strip() and verified_comment(markers, text[index:end]):
                found.add(number)
        else:
            end = _citation_span_end(text, index, suffix) or index + 1
        number += text.count("\n", index, end)
        index = end
    return frozenset(found)


SIGIL = re.compile(r"~[Ssr](\x22{3}|'{3}|[\x22'/|({\[<])")


def _sigil_end(text, opening):
    delimiter = opening[1]
    closing = {"(": ")", "[": "]", "{": "}", "<": ">"}.get(delimiter, delimiter)
    cursor = opening.end()
    while cursor < len(text):
        if text[cursor] == "\\":
            cursor += 2
        elif text.startswith(closing, cursor):
            return cursor + len(closing)
        else:
            cursor += 1
    return len(text)


def _citation_span_end(text, index, suffix):
    if suffix in {".ex", ".exs"} and (opening := SIGIL.match(text, index)):
        return _sigil_end(text, opening)
    if text[index:index + 3] in {'"""', "'''"}:
        end = text.find(text[index:index + 3], index + 3)
        return len(text) if end < 0 else end + 3
    if suffix in HASH_COMMENTS and text[index] in {"'", '"'}:
        return _skip_quoted(text, index)
    return _literal_end(text, index, suffix)


def _citation(unit, text):
    if unit.language == "python":
        tree = module_tree(text)
        scope = module_scopes(tree).get((unit.start, unit.end)) if tree else None
        if scope:
            first = scope.body[0]
            quote = ast.get_docstring(scope, clean=False)
            if quote and first.lineno == first.end_lineno and len(quote.splitlines()) == 1 and _quoted(quote):
                return first.lineno
        return None
    if unit.start > 1 and unit.start - 1 in comment_citations(unit, text):
        return unit.start - 1
    return None


def check(unit, text):
    if named_test_path(unit.path) and not _citation(unit, text):
        yield Hit(RULE, unit.start, unit.name)


def without_verified_citations(path, text, findings):
    from . import verified_citation_lines

    if not findings or not named_test_path(path):
        return findings
    verified = verified_citation_lines(path, text)
    return [row for row in findings if not (
        row.get("line") in verified and (row.get("end_line") or row["line"]) == row["line"]
        and (row["rule"] in DOC_RULES if path.lower().endswith(".py") else (
            row["family"] != "code" and row["rule"] != "prose_colon"
        ))
    )]


RULE_SET = RuleSet((Rule(RULE, "Test lacks a verified principle citation", "Quote the unit testing skill.", "enforce", True),), check)
