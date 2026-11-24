from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import PurePath

try:
    from .findings import finding_row
    from .language_layer import language_of, layer_for
    from .language_model import COMMENT_KINDS, blanked
    from .markup import MIXED_LANGUAGE_EXTS, RegionKind, extract_regions, render_regions
except ImportError:
    from findings import finding_row
    from language_layer import language_of, layer_for
    from language_model import COMMENT_KINDS, blanked
    from markup import MIXED_LANGUAGE_EXTS, RegionKind, extract_regions, render_regions

RULE = "hard_coded_path"
ACTION = "Build the path from a computed root such as the project root, a temp directory, or the home lookup, and join only repo-relative pieces."
MACHINE_ROOTS = (
    "Users", "home", "root", "tmp", "private", "var", "opt", "srv", "mnt", "media",
    "Volumes", "Applications", "Library", "usr/local", "nix/store",
)
ABSOLUTE_RE = re.compile(r"(?<![\w.\-})])/(?:" + "|".join(MACHINE_ROOTS) + r")/[^\s/\"'`)]")
HOME_RE = re.compile(r"(?:^|(?<=[\s\"'`=(,:]))~[\w.\-]*/[\w.]|%(?:USERPROFILE|HOMEPATH|HOMEDRIVE)%", re.IGNORECASE)
DRIVE_RE = re.compile(r"(?<![\w])[A-Za-z]:(?:\\{1,2}|/)[\w.$%~\-]{2}")
GENERIC_USERS = frozenset({
    "root", "user", "users", "admin", "runner", "ubuntu", "nobody", "test", "tests", "dev",
    "build", "ci", "jenkins", "vagrant", "node", "app", "www", "guest", "home", "default",
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
QUOTES = "\"'`"


def path_findings(path: str, text: str) -> list[dict]:
    if is_deploy_path(path):
        return []
    view = code_view(path, text)
    rows = []
    for number, line in enumerate(view.split("\n"), 1):
        found = machine_path(line)
        if found is not None:
            rows.append(finding_row(
                "code", RULE, number, "Hard-coded machine path in " + path, line, ACTION, match=found,
            ))
    return rows


def is_deploy_path(path: str) -> bool:
    pure = PurePath(path.replace("\\", "/").lower())
    if pure.name.startswith(CONTAINER_NAMES) or COMPOSE_RE.match(pure.name):
        return True
    if pure.suffix not in DEPLOY_SUFFIXES:
        return False
    return pure.name.startswith("deploy") or any(part in DEPLOY_DIRS for part in pure.parts[:-1])


def machine_path(line: str) -> str | None:
    for regex in (ABSOLUTE_RE, HOME_RE, DRIVE_RE):
        for found in regex.finditer(line):
            token = _token_at(line, found.start())
            if not _is_url_route(line, found.start(), token):
                return token
    return _user_token(line)


def _is_url_route(line: str, start: int, token: str) -> bool:
    return bool(ROUTE_PARAM_RE.search(token)) or bool(URL_CONTEXT_RE.search(line[:start]))


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
    return blanked(text, layer.spans(COMMENT_KINDS))


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
        end += 1
    return line[start:end]


def _user_token(line: str) -> str | None:
    for name in machine_users():
        found = re.search(r"(?<![\w.\-])" + re.escape(name) + r"(?![\w\-])", line)
        if found:
            return found.group(0)
    return None


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
