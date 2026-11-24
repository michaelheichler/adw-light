from __future__ import annotations

import os
import re
import stat
from collections.abc import Sequence

NODE_INTERPRETERS = frozenset({"node", "nodejs"})
NODE_JSON_PATH = r"(?:\.{1,2}/|/)[^'\"\\\n]*\.json"
NODE_JSON_READ = (
    rf"require\(\s*(?:'{NODE_JSON_PATH}'|\"{NODE_JSON_PATH}\")\s*\)"
    r"""(?:\.[A-Za-z_$][\w$]*|\[\s*(?:\d+|'[^'\\\n]*'|"[^"\\\n]*")\s*\])*"""
)
NODE_JSON_WRAPPED = r"(?:console\.log|JSON\.stringify|Object\.keys)\(\s*(?:{inner})(?:\s*,\s*null\s*,\s*\d+)?\s*\)"
NODE_JSON_ONCE = NODE_JSON_WRAPPED.format(inner=NODE_JSON_READ)
NODE_JSON_READ_RE = re.compile(
    rf"\s*(?:{NODE_JSON_READ}|{NODE_JSON_ONCE}|{NODE_JSON_WRAPPED.format(inner=NODE_JSON_ONCE)})\s*;?\s*"
)
JSON_REQUIRE_PATH_RE = re.compile(r"""require\(\s*(['"])([^'"]*)\1""")
JSON_REQUIRE_CALL_RE = re.compile(rf"""require\(\s*(['"])({NODE_JSON_PATH})\1\s*\)""")
JSON_STAND_IN = "0"
NODE_REFLECTION_RE = re.compile(
    r"\b(?:getBuiltinModule|_load|_linkedBinding|binding|dlopen|mainModule|constructor|prototype|getPrototypeOf|"
    r"getOwnProperty\w*|require|import|Function|eval|Reflect|module|global|globalThis|this|arguments|WebAssembly)\b|"
    r"\bprocess\b(?!\s*\.\s*(?:version|versions|platform|arch|pid|argv|env|execPath|release|uptime|cwd|exit)\b)"
)
LITERAL_KEY_RE = re.compile(r"""\[\s*(['"])([A-Za-z_$][\w$]*)\1\s*\]""")
LITERAL_KEY_AT_RE = re.compile(r"""\s*(?:\d+|'[^'\\\n]*'|"[^"\\\n]*")\s*\]""")
ARRAY_OPENERS = frozenset("(,=:[{;")
HTML_COMMENT_MARKS = ("<!--", "-->")
LINE_TERMINATORS = "\n\r" + chr(0x2028) + chr(0x2029)
CONTROL_KEYWORDS = frozenset({"if", "while", "for", "with"})
CONTROL_CLOSE = ")if"
TOKEN_RE = re.compile(r"""\s+|'(?:\\[\s\S]|[^'\\\n\r])*'|"(?:\\[\s\S]|[^"\\\n\r])*"|[\w$]+|\+\+|--|[(){}\[\];,.<>=!+\-*%&|^~?:]""")
WORD_RE = re.compile(r"[\w$]+")
REGEX_LITERAL_RE = re.compile(r"/(?:\\[^\n\r]|\[(?:\\[^\n\r]|[^\]\\\n\r])*\]|[^/\\\[\n\r])+/[A-Za-z]*(?![\w$])")
REGEX_AFTER = frozenset("(,=:[!&|?{;~^%*<>/+-")
EXPRESSION_KEYWORDS = frozenset({
    "await", "break", "case", "continue", "debugger", "default", "delete", "do", "else", "extends",
    "in", "instanceof", "new", "of", "return", "throw", "typeof", "void", "yield",
})
REGEX, DIVISION = "regex", "division"
REGEX_STAND_IN = "0"
FS_READERS = ("readFileSync", "existsSync", "statSync", "readdirSync")
FS_READER = "|".join(FS_READERS)
FS_MODULE = r"""require\(\s*(?P<quote>['"])(?:node:)?fs(?P=quote)\s*\)"""
FS_BINDING_RE = re.compile(rf"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*{FS_MODULE}\s*;?")
FS_DESTRUCTURE_RE = re.compile(rf"\b(?:const|let|var)\s*\{{([\w$\s,]*)\}}\s*=\s*{FS_MODULE}\s*;?")
FS_DIRECT_RE = re.compile(rf"{FS_MODULE}\s*\.\s*({FS_READER})\s*\(")
FS_READER_USE_RE = re.compile(rf"(?<![\w$.])({FS_READER})(?![\w$])(\s*\()?")
ENCODING_RE = re.compile(r"""(['"])[\w-]+\1""")
COMMENT_MARKS = ("/*", "//", *HTML_COMMENT_MARKS)


def node_json_read(name: str, payload: str, directories: Sequence[str]) -> bool:
    if name not in NODE_INTERPRETERS or NODE_JSON_READ_RE.fullmatch(payload) is None:
        return False
    required = JSON_REQUIRE_PATH_RE.search(payload)
    return bool(directories) and required is not None and all(
        _plain_json_file(os.path.join(directory, required.group(2))) for directory in directories
    )


def without_json_requires(payload: str, directories: Sequence[str]) -> str | None:
    found = list(JSON_REQUIRE_CALL_RE.finditer(payload))
    if not found or not directories:
        return None
    paths = {match.group(2) for match in found}
    if not all(_plain_json_file(os.path.join(directory, path)) for directory in directories for path in paths):
        return None
    return JSON_REQUIRE_CALL_RE.sub(JSON_STAND_IN, payload)


def _plain_json_file(path: str) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    real = os.path.realpath(path)
    return stat.S_ISREG(metadata.st_mode) and real.endswith(".json") and os.path.isfile(real)


def without_fs_reads(payload: str) -> str | None:
    if any(mark in payload for mark in COMMENT_MARKS):
        return None
    text = FS_DIRECT_RE.sub(lambda match: match.group(2) + "(", payload)
    names = [match.group(1) for match in FS_BINDING_RE.finditer(text)]
    picked = [name.strip() for match in FS_DESTRUCTURE_RE.finditer(text) for name in match.group(1).split(",")]
    if not set(filter(None, picked)) <= set(FS_READERS):
        return None
    text = FS_DESTRUCTURE_RE.sub(" ", FS_BINDING_RE.sub(" ", text))
    for name in names:
        text = re.sub(rf"(?<![\w$.]){re.escape(name)}\s*\.\s*({FS_READER})(?=\s*\()", r"\1", text)
        if re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text):
            return None
    if text == payload or not all(_plain_read_call(text, match) for match in FS_READER_USE_RE.finditer(text)):
        return None
    return text


def _plain_read_call(text: str, use: re.Match[str]) -> bool:
    if use.group(2) is None:
        return False
    if use.group(1) != "readFileSync":
        return True
    arguments = _call_arguments(text, use.end())
    return arguments is not None and len(arguments) <= 2 and all(ENCODING_RE.fullmatch(part) for part in arguments[1:])


def _call_arguments(text: str, start: int) -> list[str] | None:
    depth, parts, current, position = 1, [], [], start
    while position < len(text):
        match = TOKEN_RE.match(text, position)
        if match is None:
            return None
        token, position = match.group(), match.end()
        depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(token, 0)
        if depth == 0:
            return parts + ["".join(current).strip()] if current or parts else []
        if token == "," and depth == 1:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(token)
    return None


def node_reflection(payload: str) -> bool:
    if _has_unicode_escape(payload):
        return True
    dotted = LITERAL_KEY_RE.sub(lambda match: "." + match.group(2), payload)
    code = _without_regex_literals(payload)
    return NODE_REFLECTION_RE.search(dotted) is not None or any(
        _bracket_hides_name(code, match.start()) for match in re.finditer(r"\[", code)
    )


def _without_regex_literals(payload: str) -> str:
    kept: list[str] = []
    recent: tuple[str | None, str | None] = (None, None)
    parens: list[bool] = []
    position = 0
    while position < len(payload):
        token = _token_at(payload, position, recent[1])
        if token is None:
            return payload
        text, position = token
        kept.append(text)
        if not text.isspace():
            recent = (recent[1], _significant(text, recent, parens))
    return "".join(kept)


def _significant(text: str, recent: tuple[str | None, str | None], parens: list[bool]) -> str:
    if text == "(":
        parens.append(recent[1] in CONTROL_KEYWORDS and recent[0] != ".")
    if text == ")" and parens and parens.pop():
        return CONTROL_CLOSE
    return text


def _token_at(payload: str, position: int, previous: str | None) -> tuple[str, int] | None:
    if payload.startswith(HTML_COMMENT_MARKS, position):
        return None
    if payload.startswith("//", position):
        ends = [index for index in map(lambda end: payload.find(end, position), LINE_TERMINATORS) if index >= 0]
        return " ", min(ends, default=len(payload))
    if payload.startswith("/*", position):
        end = payload.find("*/", position + 2)
        return None if end < 0 else (" ", end + 2)
    if payload[position] != "/":
        match = TOKEN_RE.match(payload, position)
        return None if match is None else (match.group(), match.end())
    role = _slash_role(previous)
    if role == DIVISION:
        return "/", position + 1
    match = REGEX_LITERAL_RE.match(payload, position) if role == REGEX else None
    return None if match is None else (REGEX_STAND_IN, match.end())


def _slash_role(previous: str | None) -> str | None:
    if previous is None or previous in REGEX_AFTER or previous == CONTROL_CLOSE:
        return REGEX
    operand = WORD_RE.fullmatch(previous) is not None and previous not in EXPRESSION_KEYWORDS
    return DIVISION if operand or previous == "]" or previous[0] in "'\"" else None


def _has_unicode_escape(payload: str) -> bool:
    return "\\u" in payload


def _bracket_hides_name(payload: str, start: int) -> bool:
    before = payload[:start].rstrip()
    rest = payload[start + 1:]
    if before and before[-1] not in ARRAY_OPENERS:
        return LITERAL_KEY_AT_RE.match(rest) is None
    return "+" in rest.partition("]")[0]
