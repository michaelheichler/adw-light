from __future__ import annotations

import re
import shlex
from pathlib import Path

try:
    from .gated_targets import GatedTargets, NO_TARGETS, UNRESOLVED
    from . import git_port
    from .git_globs import tracked_paths
    from .tests_policy import is_test_path
except ImportError:
    from gated_targets import GatedTargets, NO_TARGETS, UNRESOLVED
    import git_port
    from git_globs import tracked_paths
    from tests_policy import is_test_path


LITERAL = re.compile(r"[a-zA-Z0-9_./ -]+", re.ASCII)
REMOVAL_FLAGS = {"--cached", "--ignore-unmatch", "--force", "--recursive", "--quiet"}


def _removed(body: str) -> list[str] | None:
    if not re.fullmatch(r"[a-zA-Z0-9_./ '-]+", body, re.ASCII):
        return None
    try:
        words = shlex.split(body)
    except ValueError:
        return None
    start = 2 if words[:2] == ["git", "rm"] else 1 if words[:1] == ["rm"] else 0
    if not start:
        return None
    paths = []
    options = True
    for word in words[start:]:
        if options and word == "--":
            options = False
        elif options and word.startswith("-"):
            if word not in REMOVAL_FLAGS and not re.fullmatch(r"-[rfqR]+", word):
                return None
        else:
            paths.append(word)
    return paths or None


def filter_targets(rest: list[str], base: str, cwd: str | None) -> GatedTargets:
    paths = []
    directory = Path(cwd or ".") / base
    args = iter(rest)
    for word in args:
        flag, sep, value = word.partition("=")
        if flag == "--":
            break
        if not flag.endswith("-filter"):
            continue
        body = value if sep else next(args, "")
        if flag == "--msg-filter":
            continue
        if flag == "--subdirectory-filter":
            listed = tracked_paths(directory)
            if not body or listed is None or any(is_test_path(p) and not Path(p).is_relative_to(body) for p in listed):
                return UNRESOLVED
        else:
            names = _removed(body) if flag in {"--tree-filter", "--index-filter"} else None
            if names is None:
                return UNRESOLVED
            listed = tracked_paths(directory)
            if listed is None:
                return UNRESOLVED
            paths.extend(str(Path(base) / p) for p in names)
            paths.extend(str(Path(base) / p) for p in listed if any(Path(p).is_relative_to(n) for n in names))
    return GatedTargets(tuple(paths), False)


def submodule_targets(rest: list[str], base: str, cwd: str | None) -> GatedTargets:
    while rest and rest[0] in {"-q", "--quiet", "--cached"}:
        rest = rest[1:]
    if rest[:1] != ["update"]:
        return NO_TARGETS
    directory = Path(cwd or ".") / base
    flags = {"--init", "--checkout", "--remote", "--merge", "--rebase", "--force", "-f", "--quiet", "-q"}
    args = iter(rest[1:])
    specs = []
    for word in args:
        if word == "--":
            specs.extend(args)
            break
        if word.startswith("-") and word not in flags:
            return UNRESOLVED
        if word not in flags:
            specs.append(word)
    if any(not LITERAL.fullmatch(s) for s in specs):
        return UNRESOLVED
    records = git_port.view().config_entries(directory, r"^submodule[.].*[.]path$", file=".gitmodules")
    if records is None:
        return UNRESOLVED
    paths = []
    for record in records:
        _, sep, name = record.partition(chr(10))
        if not sep or not name or Path(name).is_absolute() or ".." in Path(name).parts:
            return UNRESOLVED
        if specs and not any(Path(name).is_relative_to(spec) for spec in specs):
            continue
        paths.append(str(Path(base) / name))
        if (directory / name / ".git").exists():
            tracked = tracked_paths(directory / name)
            if tracked is None:
                return UNRESOLVED
            paths.extend(str(Path(base) / name / path) for path in tracked if is_test_path(path))
    return GatedTargets(tuple(paths), False)
