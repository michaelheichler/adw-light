from __future__ import annotations

import ast
import os
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path, PurePath

try:
    from . import git_port
    from .command_words import COMMAND_WORDS
    from .markup import LOG_EXTS, PLAIN_TEXT_EXTS
except ImportError:
    import git_port
    from command_words import COMMAND_WORDS
    from markup import LOG_EXTS, PLAIN_TEXT_EXTS

OUTPUT_EXTS = LOG_EXTS | frozenset({".out", ".tap"})
TAP_SUFFIXES = PLAIN_TEXT_EXTS | OUTPUT_EXTS
GIT_SECONDS = 5
TAP_VERSION_RE = re.compile(r"TAP version \d+[ \t]*")
TAP_PLAN_RE = re.compile(r"1\.\.\d+(?:[ \t]+#[ \t].*)?")
TAP_TEST_RE = re.compile(r"(?:not )?ok[ \t]+\d+(?:[ \t].*)?")
TAP_BAIL_RE = re.compile(r"Bail out!.*")
TAP_DIAGNOSTIC_RE = re.compile(r"#(?:[ \t].*)?")
YAML_OPEN_RE = re.compile(r"[ \t]+---[ \t]*")
YAML_CLOSE_RE = re.compile(r"[ \t]+\.\.\.[ \t]*")
SHELL_FUNCTION_RE = re.compile(r"^[ \t]*(?:function[ \t]+[\w:.-]+|[\w:.-]+[ \t]*\([ \t]*\))", re.MULTILINE)
PYTHON_DEFINITION_RE = re.compile(
    r"^[ \t]*(?:(?:async[ \t]+)?def[ \t]+\w|class[ \t]+\w|import[ \t]+\w|from[ \t]+[\w.]+[ \t]+import\b)", re.MULTILINE,
)
SHELL_KEYWORDS = frozenset(("if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "select", "function"))
ASSIGNMENT_RE = re.compile(r"[A-Za-z_]\w*(?:\[[^]]*\])?\+?=")
TAP_TEST_PREFIX_RE = re.compile(r"(?:not )?ok[ \t]+\d+(?:[ \t]+-)?[ \t]*")
YAML_KEY_RE = re.compile(r"[ \t]+[\w-]+:[ \t]*")
LINE_SUFFIX = ".tap-line.txt"
Scan = Callable[[str, str], list[dict]]


def exempt_tap(path: str, text: str) -> bool:
    if PurePath(path.lower()).suffix not in TAP_SUFFIXES:
        return False
    return strict_tap(text) and ignored_output(path)


def exempt_output(path: str, text: str) -> bool:
    if PurePath(path.lower()).suffix not in OUTPUT_EXTS:
        return False
    if text.startswith("#!") or reads_as_code(text):
        return False
    return ignored_output(path)


def reads_as_code(text: str) -> bool:
    if SHELL_FUNCTION_RE.search(text) or PYTHON_DEFINITION_RE.search(text):
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    statements = sum(_shell_statement(line) or _python_statement(line) for line in lines)
    return statements * 2 > len(lines)


def _shell_statement(line: str) -> bool:
    words = line.split()
    while words and ASSIGNMENT_RE.match(words[0]):
        words = words[1:]
    return not words or words[0] in COMMAND_WORDS or words[0] in SHELL_KEYWORDS


def _python_statement(line: str) -> bool:
    try:
        body = ast.parse(line).body
    except (SyntaxError, ValueError):
        return False
    bare = len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, (ast.Name, ast.Constant))
    return bool(body) and not bare


def tap_findings(path: str, text: str, scan: Scan) -> list[dict]:
    rows = []
    unit_path = path + LINE_SUFFIX
    for number, line in enumerate(text.split("\n"), 1):
        payload = tap_payload(line)
        if not payload.strip():
            continue
        for row in scan(unit_path, payload + "\n"):
            detail = str(row.get("detail", "")).replace(unit_path, path)
            rows.append({**row, "line": number, "detail": detail, "snippet": line.strip()[:180]})
    return rows


def tap_payload(line: str) -> str:
    text = line.rstrip("\r")
    if TAP_PLAN_RE.fullmatch(text) or TAP_VERSION_RE.fullmatch(text):
        return ""
    if YAML_OPEN_RE.fullmatch(text) or YAML_CLOSE_RE.fullmatch(text):
        return ""
    for pattern in (TAP_TEST_PREFIX_RE, YAML_KEY_RE):
        found = pattern.match(text)
        if found is not None:
            return text[found.end():]
    if text.startswith("Bail out!"):
        return text[len("Bail out!"):]
    return text.lstrip("# \t")


def strict_tap(text: str) -> bool:
    lines = [line.rstrip("\r") for line in text.split("\n") if line.strip()]
    if lines and TAP_VERSION_RE.fullmatch(lines[0]):
        lines = lines[1:]
    plans = [index for index, line in enumerate(lines) if TAP_PLAN_RE.fullmatch(line)]
    if len(plans) != 1 or plans[0] not in (0, len(lines) - 1):
        return False
    return _tap_body(lines[:plans[0]] + lines[plans[0] + 1:])


def _tap_body(lines: list[str]) -> bool:
    tested = yaml = after_test = False
    for line in lines:
        if yaml:
            yaml = not YAML_CLOSE_RE.fullmatch(line)
            if not line[:1].isspace():
                return False
            continue
        if TAP_TEST_RE.fullmatch(line):
            tested = after_test = True
        elif after_test and YAML_OPEN_RE.fullmatch(line):
            yaml, after_test = True, False
        elif not (TAP_BAIL_RE.fullmatch(line) or (tested and TAP_DIAGNOSTIC_RE.fullmatch(line))):
            return False
    return tested and not yaml


def ignored_output(path: str) -> bool:
    return _ignored_output(git_port.view(), path)


@lru_cache(maxsize=16)
def _ignored_output(git_view: git_port.GitView, path: str) -> bool:
    target = Path(os.path.realpath(os.path.abspath(path)))
    directory = target.parent
    while not directory.is_dir() and directory != directory.parent:
        directory = directory.parent
    top = git_view.toplevel(directory, timeout=GIT_SECONDS, errors="surrogateescape")
    if top is None:
        return False
    root = str(top)
    relative = os.path.relpath(target, os.path.realpath(root))
    if relative.startswith(".."):
        return False
    if git_view.is_tracked(top, relative):
        return False
    verdict = git_view.ignore_match(top, relative)
    if verdict is None:
        return False
    return _committed_rule(git_view, root, *verdict)


def _committed_rule(git_view: git_port.GitView, root: str, source: str, pattern: str) -> bool:
    source_path = Path(source if os.path.isabs(source) else os.path.join(root, source))
    relative = os.path.relpath(os.path.realpath(source_path), os.path.realpath(root))
    if relative.startswith("..") or PurePath(relative).name != ".gitignore":
        return False
    committed = git_view.head_text(Path(root), PurePath(relative).as_posix(), timeout=GIT_SECONDS, errors="surrogateescape")
    return committed is not None and pattern in committed.splitlines()
