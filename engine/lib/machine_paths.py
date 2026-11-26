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
    from .temp_mirror import TEMP_ROOTS, literal_prefix, mirrored_temp
except ImportError:
    from findings import finding_row
    from language_layer import language_of, layer_for
    from language_model import COMMENT_KINDS, blanked
    from markup import MIXED_LANGUAGE_EXTS, RegionKind, extract_regions, render_regions
    from temp_mirror import TEMP_ROOTS, literal_prefix, mirrored_temp

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
SEGMENT_SPLIT_RE = re.compile(r"[/\\]+")
HOME_REFERENCE_RE = re.compile(r"\$\{?HOME\b|~|%(?:USERPROFILE|HOMEPATH|HOMEDRIVE)%", re.IGNORECASE)
HOME_ROOTS = frozenset({"Users", "home", "root"})
ROOT_SEGMENTS = frozenset(root.split("/")[0] for root in MACHINE_ROOTS)
TOKEN_SEPARATOR_RE = re.compile(r"[:,\s]+")


def path_findings(path: str, text: str) -> list[dict]:
    if is_deploy_path(path):
        return []
    view = code_view(path, text)
    rows = []
    for number, line in enumerate(view.split("\n"), 1):
        found = _reported_path(path, line)
        if found is not None:
            rows.append(finding_row(
                "code", RULE, number, "Hard-coded machine path in " + path, line, ACTION, match=found,
            ))
    return rows


def _reported_path(path: str, line: str) -> str | None:
    found = machine_path(line)
    while found and not escapes_prefix(placeholder_token(line, found)) and mirrored_temp(path, found):
        line = line.replace(found, " " * len(found))
        found = machine_path(line)
    return found


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
