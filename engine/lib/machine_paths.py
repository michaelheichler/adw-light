from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import PurePath
from posixpath import normpath
from typing import Iterator

try:
    from .command_words import SHELL_BUILTINS, SHELL_TOOLS
    from .language_units import named_test_path
    from .findings import finding_row
    from .language_layer import language_of, layer_for
    from .language_model import COMMENT_KINDS, Kind, blanked
    from .markup import MIXED_LANGUAGE_EXTS, RegionKind, extract_regions, render_regions
    from .temp_mirror import TEMP_ROOTS, literal_prefix, mirrored_temp
except ImportError:
    from command_words import SHELL_BUILTINS, SHELL_TOOLS
    from language_units import named_test_path
    from findings import finding_row
    from language_layer import language_of, layer_for
    from language_model import COMMENT_KINDS, Kind, blanked
    from markup import MIXED_LANGUAGE_EXTS, RegionKind, extract_regions, render_regions
    from temp_mirror import TEMP_ROOTS, literal_prefix, mirrored_temp

RULE = "hard_coded_path"
SYSTEM_RULE = "hard_coded_system_path"
ACTION = "Build the path from a computed root such as the project root, a temp directory, or the home lookup, and join only repo-relative pieces."
MACHINE_ROOTS = (
    "Users", "home", "root", "tmp", "private", "var", "opt", "srv", "mnt", "media",
    "Volumes", "Applications", "Library", "usr", "etc", "nix/store",
)
HOME_FOLDERS = frozenset({"users", "home"})
ROOTS_PATTERN = "|".join("(?i:" + root + ")" if root.casefold() in HOME_FOLDERS else root for root in MACHINE_ROOTS)
ABSOLUTE_RE = re.compile(r"(?<![\w.\-})])/+(?:\.{1,2}/+)*(?:" + ROOTS_PATTERN + r")/+[^\s/\"'`)]")
HOME_RE = re.compile(
    r"(?:^|(?<=[\s\"'`=(,:]))~[\w.\-]*/[\w.]|%(?:USERPROFILE|HOMEPATH|HOMEDRIVE)%"
    r"|(?<=[\"'`])~[\w.\-]+/?(?=[\"'`])",
    re.IGNORECASE,
)
DRIVE_RE = re.compile(r"(?<![\w])[A-Za-z]:(?:\\{1,2}|/)[\w.$%~\-]{2}")
GENERIC_USERS = frozenset({
    "root", "user", "users", "admin", "runner", "ubuntu", "nobody", "test", "tests", "dev",
    "build", "ci", "jenkins", "vagrant", "node", "app", "www", "guest", "home", "default",
    "sample", "someone", "example", "alice", "bob", "me", "username", "placeholder", "yourname", "your-user",
})
USER_VARIABLES = ("USER", "LOGNAME", "USERNAME")
DEPLOY_DIRS = frozenset({
    "deploy", "deployment", "deployments", ".github", ".gitlab", "ansible", "terraform",
    "k8s", "kubernetes", "helm",
})
DEPLOY_SUFFIXES = frozenset({".sh", ".bash", ".zsh", ".ps1", ".yml", ".yaml", ".tf", ".tfvars", ".hcl"})
CONTAINER_NAMES = ("dockerfile", "containerfile")
COMPOSE_RE = re.compile(r"^(?:docker-)?compose(?:[.\-][\w.\-]*)?\.ya?ml$")
DASH_COMMENT_SUFFIXES = frozenset({".sql", ".lua", ".hs", ".lhs", ".elm", ".ada", ".adb", ".ads", ".vhd", ".vhdl"})
LINE_COMMENT_RE = re.compile(r"(?:^|(?<=\s))(?://|#)|/\*")
DASH_LINE_COMMENT_RE = re.compile(r"(?:^|(?<=\s))(?://|#|--\s)|/\*")
BLOCK_LINE_RE = re.compile(r"^\s*(?:\*|/\*|//|#|;)")
DASH_BLOCK_LINE_RE = re.compile(r"^\s*(?:\*|/\*|//|#|--\s|;)")
ROUTE_PARAM_RE = re.compile(r"<[\w:.\-]+>|/:\w")
URL_CONTEXT_RE = re.compile(
    r"(?:\b\w*(?:route|fetch|href|url|uri|endpoint|redirect|navigate)\w*"
    r"|\.(?:get|post|put|patch|delete|head|options|all|use))\s*[(=:]\s*\{?\s*[\"'`]$",
    re.IGNORECASE,
)
SOURCE_CONTEXT_RE = re.compile(r"(?<![\w.\-])(?:src|to|action)\s*=\s*\{?\s*[\"'`]$", re.IGNORECASE)
URL_AUTHORITY_RE = re.compile(r"[A-Za-z][\w+.\-]*:$")
ESCAPE_RE = re.compile(r"\\(?:u\{([0-9a-fA-F]{1,6})\}|u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8})|x([0-9a-fA-F]{2})|([0-7]{1,3})|\\)")
ESCAPED_CHARACTERS = frozenset("/._-~:")
HOME_PIECE_RE = re.compile(
    r"""["'`](?:/+(?i:users|home)|[A-Za-z]:(?:\\{1,2}|/)(?i:users)|~)(?:/+|\\{1,2})?["'`]"""
    r"""\s*\)?\s*[/,]\s*["'`][/\\]*([\w.-]+)(?=[/\\"'`])"""
)
ROOT_HOMES = (("root",), ("var", "root"), ("private", "var", "root"))
VENV_DIRS = frozenset({".venv", "venv", "virtualenv", "site-packages", "dist-packages"})
QUOTES = "\"'`"
SEGMENT_SPLIT_RE = re.compile(r"[/\\]+")
HOME_REFERENCE_RE = re.compile(r"\$\{?HOME\b|~|%(?:USERPROFILE|HOMEPATH|HOMEDRIVE)%", re.IGNORECASE)
HOME_ROOTS = frozenset({"Users", "home", "root"})
ROOT_SEGMENTS = frozenset(root.split("/")[0] for root in MACHINE_ROOTS)
TOKEN_SEPARATOR_RE = re.compile(r"[:,\s]+")


def path_findings(path: str, text: str) -> list[dict]:
    if is_deploy_path(path) or _venv_path(path):
        return []
    view = code_view(path, text)
    rows = []
    for number, line in enumerate(view.split("\n"), 1):
        for rule, token in _reported_paths(path, decoded_escapes(line)).items():
            detail = "Hard-coded system path" if rule == SYSTEM_RULE else "Hard-coded machine path"
            rows.append(finding_row("code", rule, number, detail + " in " + path, line, ACTION, match=token))
    return rows


def _reported_paths(path: str, line: str) -> dict[str, str]:
    rows = {}
    for token, rule in _path_tokens(line):
        if rule == SYSTEM_RULE and not escapes_prefix(placeholder_token(line, token)) and mirrored_temp(path, token):
            rule = None
        if rule is not None:
            rows.setdefault(rule, token)
    if RULE not in rows and (name := _home_piece_user(line)):
        rows[RULE] = name
    return rows


def decoded_escapes(line: str) -> str:
    return ESCAPE_RE.sub(_decoded_escape, line) if "\\" in line else line


def _decoded_escape(match: re.Match[str]) -> str:
    digits = next((group for group in match.groups() if group), None)
    if digits is None:
        return match.group(0)
    base = 8 if match.group(5) else 16
    code = int(digits, base)
    character = chr(code) if code < 0x110000 else ""
    return character if character.isalnum() or character in ESCAPED_CHARACTERS else match.group(0)


def _home_piece_user(line: str) -> str | None:
    matches = tuple(HOME_PIECE_RE.finditer(line))
    if not matches:
        return None
    names = {name.casefold() for name in machine_users()}
    return next((hit[1] for hit in matches if hit[1].casefold() in names), None)


def placeholder_token(line: str, token: str) -> str:
    index = line.find(token)
    if index < 0:
        return token
    pieces: list[str] = []
    while index < len(line):
        character = line[index]
        if character == "{" or line.startswith("${", index):
            close = _placeholder_end(line, index + (2 if character == "$" else 1))
            pieces.append("{}")
            index = close + 1
        elif character.isspace() or character in QUOTES + "),;":
            break
        else:
            pieces.append(character)
            index += 1
    return "".join(pieces)


def _placeholder_end(line: str, index: int) -> int:
    depth = 1
    while index < len(line):
        depth += {"{": 1, "}": -1}.get(line[index], 0)
        if depth == 0:
            return index
        index += 1
    return len(line)


def escapes_prefix(token: str) -> bool:
    first, *others = TOKEN_SEPARATOR_RE.split(token)
    root = next((item for item in TEMP_ROOTS if first.startswith(item + "/")), None)
    prefix = literal_prefix(first)
    if root is None or prefix is None or any(_absolute_part(part) for part in others):
        return True
    rest = first[len(root):]
    if HOME_REFERENCE_RE.search(rest) or DRIVE_RE.search(rest):
        return True
    users = {name.lower() for name in machine_users()}
    segments = [segment for segment in SEGMENT_SPLIT_RE.split(rest) if segment]
    if any(segment == ".." or segment in HOME_ROOTS or segment.lower() in users for segment in segments):
        return True
    return any(segment in ROOT_SEGMENTS for segment in SEGMENT_SPLIT_RE.split(first[len(prefix):]) if segment)


def _absolute_part(part: str) -> bool:
    return part.startswith(("/", "~", "\\")) or bool(HOME_REFERENCE_RE.search(part) or DRIVE_RE.match(part))


def is_deploy_path(path: str) -> bool:
    pure = PurePath(path.replace("\\", "/").lower())
    if pure.name.startswith(CONTAINER_NAMES) or COMPOSE_RE.match(pure.name):
        return True
    if pure.suffix not in DEPLOY_SUFFIXES:
        return False
    return pure.name.startswith("deploy") or any(part in DEPLOY_DIRS for part in pure.parts[:-1])


def _path_tokens(line: str) -> Iterator[tuple[str, str | None]]:
    finish = 0
    homes, home_re = _user_homes(os.path.expanduser("~"))
    matches = sorted((hit.start(), kind) for kind, regex in enumerate((ABSOLUTE_RE, HOME_RE, DRIVE_RE, home_re))
                     for hit in regex.finditer(line))
    for start, kind in matches:
        if start < finish:
            continue
        token = _token_at(line, start)
        finish = start + len(token)
        if kind == 1 and token.startswith("~") and "/" not in token:
            if _account_home(token):
                yield token, RULE
            continue
        normalized = _normalize_token(token)
        if _url_authority(line, start, token):
            continue
        if _is_url_route(line, start, normalized):
            if _home_prefix(normalized, homes) or _account_home(token):
                yield token, RULE
        elif _real_home(normalized, homes) or _account_home(token):
            yield token, RULE
        elif not _venv_path(normalized):
            yield token, _path_rule(normalized, kind)


def _venv_path(path: str) -> bool:
    return bool(VENV_DIRS.intersection(part.lower() for part in SEGMENT_SPLIT_RE.split(path)))


def _normalize_token(token: str) -> str:
    if token.startswith("~"):
        home, _, rest = token.partition("/")
        return home + "/" + normpath(rest)
    return "/" + normpath(token).lstrip("/") if token.startswith("/") else token


def _url_authority(line: str, start: int, token: str) -> bool:
    return token.startswith("//") and not token.startswith("///") and bool(URL_AUTHORITY_RE.search(line[:start]))


def _home_prefix(token: str, homes: tuple[str, ...]) -> bool:
    return any(token == home or token.startswith(home + "/") for home in homes)


def _account_home(token: str) -> bool:
    names = {name.casefold() for name in machine_users()}
    parts = [part.casefold() for part in SEGMENT_SPLIT_RE.split(token) if part]
    if token.startswith("~"):
        return parts[0][1:] in names or (parts[0] == "~" and any(part in names for part in parts[1:]))
    return any(folder in HOME_FOLDERS and user in names for folder, user in zip(parts, parts[1:]))


def _real_home(token: str, homes: tuple[str, ...]) -> bool:
    if _home_prefix(token, homes):
        return True
    if token.startswith("~"):
        user = token[1:].split("/", 1)[0]
        return bool(user) and user.casefold() not in GENERIC_USERS
    if not token.startswith("/"):
        return False
    parts = token.strip("/").split("/")
    return any(tuple(parts[:len(home)]) == home for home in ROOT_HOMES) or any(
        _home_user(folder, user) for folder, user in zip(parts, parts[1:])
    )


def _home_user(folder: str, user: str) -> bool:
    if folder.casefold() not in HOME_FOLDERS or user.casefold() in GENERIC_USERS:
        return False
    pattern = r"[\w.-]+" if folder in {"Users", "home"} else r"[A-Za-z][\w.-]*"
    return bool(re.fullmatch(pattern, user))


def _path_rule(token: str, kind: int) -> str | None:
    if kind == 1 and token.startswith("~"):
        user, _, rest = token[1:].partition("/")
        first = rest.split("/", 1)[0].casefold()
        if user.casefold() in GENERIC_USERS or (not user and (len(first) == 1 or first in GENERIC_USERS)):
            return None
        return RULE if user else SYSTEM_RULE
    if kind:
        return RULE if kind != 3 else None
    return None if token.lstrip("/").split("/", 1)[0].casefold() in HOME_FOLDERS else SYSTEM_RULE


@lru_cache(maxsize=8)
def _user_homes(home: str) -> tuple[tuple[str, ...], re.Pattern[str]]:
    paths = tuple(sorted({path.rstrip("/") for path in (home, os.path.realpath(home)) if path.startswith("/") and path != "/"}))
    if not paths:
        return (), re.compile(r"(?!)")
    prefix = "|".join(map(re.escape, paths))
    return paths, re.compile(r"(?<![\w./-])(?:" + prefix + r")(?=$|[^\w.-])")


def _is_url_route(line: str, start: int, token: str) -> bool:
    if URL_CONTEXT_RE.search(line[:start]) or SOURCE_CONTEXT_RE.search(line[:start]):
        return True
    system_data = token.startswith("/") and token.split("/", 3)[1:3] == ["var", "lib"]
    return not system_data and bool(ROUTE_PARAM_RE.search(token))


def code_view(path: str, text: str) -> str:
    suffix = PurePath(path.lower()).suffix
    if suffix in MIXED_LANGUAGE_EXTS:
        return _lexer_view(render_regions(text, extract_regions(path, text), {RegionKind.SCRIPT}))
    dashes = suffix in DASH_COMMENT_SUFFIXES
    language = language_of(path)
    if language is None:
        return _lexer_view(text, dashes)
    layer = layer_for(path, language, text)
    if not layer.precise:
        return _lexer_view(text, dashes)
    spans = layer.spans(COMMENT_KINDS)
    if "~/" in text and named_test_path(path):
        for start, end in layer.spans(frozenset({Kind.STRING})):
            literal = text[start:end]
            head = re.match(r'''[rRuU]*["']{1,3}([a-z][\w-]*)\s''', literal)
            if head and (head[1] in SHELL_BUILTINS or head[1] in SHELL_TOOLS):
                spans.extend((start + hit.start(), start + hit.end()) for hit in re.finditer(r'''(?<=[\s"'=<>])~(?=/)''', literal))
    return blanked(text, spans)


def _lexer_view(text: str, dashes: bool = False) -> str:
    return "\n".join(_lexer_line(line, dashes) for line in text.split("\n"))


def _lexer_line(line: str, dashes: bool) -> str:
    block_line, line_comment = (DASH_BLOCK_LINE_RE, DASH_LINE_COMMENT_RE) if dashes else (BLOCK_LINE_RE, LINE_COMMENT_RE)
    if block_line.match(line):
        return ""
    quote = ""
    for index, character in enumerate(line):
        if quote:
            quote = "" if character == quote and line[index - 1] != "\\" else quote
        elif character in QUOTES:
            quote = character
        elif line_comment.match(line, index):
            return line[:index]
    return line


def _token_at(line: str, start: int) -> str:
    end = start
    while end < len(line) and not line[end].isspace() and line[end] not in QUOTES + "),;":
        if end != start + 1 and line[end] == ":" and line[end + 1:end + 2] in {"/", "~"}:
            break
        end += 1
    return line[start:end]


@lru_cache(maxsize=1)
def machine_users() -> tuple[str, ...]:
    names = {os.environ.get(variable, "") for variable in USER_VARIABLES}
    names.add(_login_name())
    return tuple(sorted(name for name in names if len(name) >= 3 and name.lower() not in GENERIC_USERS))


def _login_name() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, AttributeError, KeyError, OSError):
        return ""
