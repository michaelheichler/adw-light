from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from pathlib import Path, PurePath
from typing import NamedTuple

try:
    from . import (
        agent_types,
        declared_tests,
        git_globs,
        git_port,
        host,
        payloads,
        temp_scope,
        test_units,
    )
    from .findings import Finding
    from .markup import MARKDOWN_EXTS
    from .path_policy import anchored_identity, exited_project, marked_project
    from .protected import authorized
    from .regular_file import read_regular_text
except ImportError:
    import agent_types
    import declared_tests
    import git_globs
    import git_port
    import host
    import payloads
    import temp_scope
    import test_units
    from findings import Finding
    from markup import MARKDOWN_EXTS
    from path_policy import anchored_identity, exited_project, marked_project
    from protected import authorized
    from regular_file import read_regular_text

TEST_WRITER = "adw-test-writer"
TEST_WRITER_TYPES = frozenset({TEST_WRITER, "agent-discipline-watcher:" + TEST_WRITER, "adw-light:" + TEST_WRITER})
RULE = "test_write_gate"
WINDOW_KEY = "tests_allow_until"
WINDOW_HINT = "/adw-light:config tests allow --for 30m"
FIXTURE_DIRS = frozenset({"testdata", "__snapshots__", "__fixtures__", "__mocks__"})
TEST_DIRS = frozenset({"tests", "__tests__", "test", "spec"}) | FIXTURE_DIRS
NESTED_FIXTURE_DIR = "fixtures"
CONFTEST = "conftest.py"
DOC_EXTS = MARKDOWN_EXTS | {".rst", ".txt", ".adoc"}
PROJECT_MARKER = ".git"
IGNORE_FILE = ".gitignore"
INFO_EXCLUDE_PREFIX = ".git/"
GIT_SECONDS = 5
UNRESOLVED_DETAIL = "Shell command may change tests it does not name: "
UNTYPED_DETAIL = "Test write by a subagent whose hook payload names no agent type, in "
_TEST_NAME_RE = re.compile(
    r"^(?:test_.+\.py|.+_test\.py|conftest\.py|.+_test\.go|.+_spec\.rb"
    r"|.+\.(?:test|spec|cy)\.[cm]?[jt]sx?)$"
)
_PYTHON_TEST_NAME_RE = re.compile(r"^(?:test_.+|.+_test)\.py$")
_JAVA_TEST_RE = re.compile(r"^.+(?:Tests?|IT|Spec)\.(?:java|kt|kts|groovy|scala)$")
AGENT_ACTION = (
    f"Delegate this test change to the {TEST_WRITER} agent. If the user wants it"
    f" done here, ask them to run `{WINDOW_HINT}` in Claude Code or `$adw-config tests allow --for 30m` in Codex."
)
OMP_ACTION = (
    "OMP names no agent on tool calls, so only a timed window opens this gate."
    f" Ask the user to run `{WINDOW_HINT}` in Claude Code or `$adw-config tests allow --for 30m` in Codex."
)


class PendingChange(NamedTuple):
    path: str
    before: str | None
    after: str | None
    after_unknown: bool = False


def project_root(cwd: str) -> str:
    start = Path(os.path.abspath(cwd or "."))
    for directory in (start, *start.parents):
        if (directory / PROJECT_MARKER).exists():
            return str(directory)
    return str(start)


def _marked_test_root(directory: Path) -> Path | None:
    for index, part in enumerate(directory.parts):
        if part.casefold() in TEST_DIRS:
            return marked_project(Path(*directory.parts[:index]))
    return marked_project(directory)


def _cwd_root(cwd: Path) -> Path:
    here = cwd.resolve()
    repository = Path(project_root(str(here)))
    if (repository / PROJECT_MARKER).exists():
        return repository
    return marked_project(here) or repository


def external_target_project(resolved: Path, cwd: Path) -> Path | None:
    here = cwd.resolve()
    if resolved.is_relative_to(here) or (Path(project_root(str(here))) / PROJECT_MARKER).exists():
        return None
    return marked_project(resolved.parent, all_ancestors=True)


def scratch_write(path: Path, cwd: Path) -> bool:
    return temp_scope.outside_project_temp(path, cwd) and external_target_project(anchored_identity(path, cwd), cwd) is None


def target_root(path: Path, cwd: Path) -> str:
    resolved = anchored_identity(path, cwd)
    if (owner := external_target_project(resolved, cwd)) is not None:
        return str(owner)
    cwd_root = _cwd_root(cwd)
    if resolved.is_relative_to(cwd_root):
        return str(cwd_root)
    owner = Path(project_root(str(resolved.parent)))
    if (owner / PROJECT_MARKER).exists():
        return str(owner)
    return str(_marked_test_root(resolved.parent) or project_root(str(cwd)))


def outside_project(path: str, cwd: str) -> bool:
    base = os.path.abspath(cwd or ".")
    found, here = on_disk(os.path.join(base, path)), on_disk(base)
    if found is None or here is None:
        return False
    real = Path(found)
    if temp_scope.scratch_session(Path(base)) and temp_scope.scratch_session(real):
        return True
    return not real.is_relative_to(here) and git_globs.outside_work_trees(real)


def on_disk(path: str) -> str | None:
    real = Path(os.path.realpath(path))
    resolved, parts = Path(real.anchor), real.parts[1:]
    for index, part in enumerate(parts):
        try:
            names = os.listdir(resolved)
        except OSError:
            return str(resolved.joinpath(*parts[index:]))
        if part in names:
            resolved = resolved / part
            continue
        variants = [name for name in names if name.casefold() == part.casefold()]
        if not variants or not (resolved / part).exists():
            return str(resolved.joinpath(*parts[index:]))
        if len(variants) > 1:
            return None
        resolved = Path(os.path.realpath(resolved / variants[0]))
    return str(resolved)


def _judged_parts(path: str, root: str | None) -> tuple[str, ...]:
    pure = PurePath(os.path.normpath(path.lower()))
    base = PurePath(os.path.normpath(root.lower())) if root else None
    if base is not None and pure.is_absolute() and pure.is_relative_to(base):
        return pure.relative_to(base).parts
    return pure.parts


def is_test_path(path: str, root: str | None = None) -> bool:
    name = PurePath(path).name
    if in_test_dir(path, root) and not _document(path, root):
        return True
    return bool(_TEST_NAME_RE.match(name.lower()) or _JAVA_TEST_RE.match(name))


def inline_test_bodies(path: str, text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    bodies = [unit.attributes + unit.body for unit in test_units.extract(path, text)]
    if test_units.language_of(path) == test_units.RUST:
        bodies += test_units.rust_cfg_test_blocks(text)
    return tuple(bodies)


def in_test_dir(path: str, root: str | None = None) -> bool:
    return any(part in TEST_DIRS for part in _judged_parts(path, root))


def in_fixture_dir(path: str, root: str | None = None) -> bool:
    parts = _judged_parts(path, root)
    return any(
        part in FIXTURE_DIRS or (part == NESTED_FIXTURE_DIR and any(above in TEST_DIRS for above in parts[:index]))
        for index, part in enumerate(parts)
    )


def _held_by_suite(change: PendingChange, root: str | None) -> bool:
    name = PurePath(change.path).name
    texts = [side for side in (change.before, change.after) if side and side.strip()]
    suite = in_test_dir(change.path, root) and not _document(change.path, root)
    return bool(texts) and (suite or name == CONFTEST)


def _document(path: str, root: str | None) -> bool:
    return PurePath(path.lower()).suffix in DOC_EXTS and not in_fixture_dir(path, root)


def _plain_module(change: PendingChange, root: str | None, removed: frozenset[Path]) -> bool:
    named = _PYTHON_TEST_NAME_RE.match(PurePath(change.path).name.lower())
    if not named or in_test_dir(change.path, root) or change.before is None or not _deleted(change.path, removed):
        return False
    return not declared_tests.declares_tests(change.path, change.before) and not inline_test_bodies(change.path, change.before)


def _deleted(path: str, removed: frozenset[Path]) -> bool:
    target = Path(path)
    return any(target == folder or folder in target.parents for folder in removed)


def ignored_scratch(path: str, root: str | None) -> bool:
    base = on_disk(root) if root else None
    found = on_disk(os.path.join(base, path)) if base else None
    if base is None or found is None:
        return False
    target = Path(found)
    if target == Path(base) or not target.is_relative_to(base):
        return False
    parts = target.relative_to(base).parts
    held = next((index for index, part in enumerate(parts[:-1]) if part.lower() in TEST_DIRS), None)
    if held == 0 or _borrows_project(target, base):
        return False
    limit = held if held is not None else len(parts) - (0 if target.is_dir() else 1)
    return any(_scratch_folder(base, PurePath(*parts[:depth]).as_posix()) for depth in range(1, limit + 1))


def _scratch_folder(base: str, folder: str) -> bool:
    return _git_ignored(base, folder + "/") and git_port.view().tracked_paths(Path(base), [folder], timeout=GIT_SECONDS) == []


def untracked_scratch(path: str, root: str | None) -> bool:
    return ignored_scratch(path, root)


def foreign_scratch_repo(directory: str, root: str | None) -> bool:
    top = _toplevel(directory)
    if not root or not top or not top.strip():
        return False
    if os.path.realpath(top.strip()) == os.path.realpath(root) or not _separate_repositories(top.strip(), root):
        return False
    return untracked_scratch(top.strip(), root)


def _borrows_project(target: Path, base: str) -> bool:
    directory = target
    while not directory.is_dir() and directory != directory.parent:
        directory = directory.parent
    top = _toplevel(str(directory))
    if not top or not top.strip():
        return False
    return os.path.realpath(top.strip()) != base and not _separate_repositories(top.strip(), base)


def _separate_repositories(first: str, second: str) -> bool:
    shared = [_common_dir(first), _common_dir(second)]
    return None not in shared and shared[0] != shared[1]


def _common_dir(directory: str) -> str | None:
    found = git_port.view().common_dir(Path(directory))
    return None if found is None else str(found)


def temp_scratch_repo(path: str, cwd: str) -> bool:
    resolved_here = on_disk(os.path.abspath(cwd or "."))
    resolved = on_disk(os.path.join(resolved_here, path)) if resolved_here else None
    if resolved_here is None or resolved is None:
        return False
    here, target = Path(resolved_here), Path(resolved)
    roots = temp_scope.temp_roots()
    if not temp_scope._within(target, roots):
        return False
    directory = target
    while not directory.is_dir() and directory != directory.parent:
        directory = directory.parent
    top = _toplevel(str(directory))
    if not top or not top.strip():
        return False
    tree = Path(os.path.realpath(top.strip()))
    project = Path(os.path.realpath(project_root(str(here))))
    in_repository = (project / PROJECT_MARKER).exists()
    if in_repository and (temp_scope._within(tree, (project,)) or not _separate_repositories(str(tree), str(project))):
        return False
    return tree not in roots and temp_scope._within(tree, roots) and not temp_scope._within(here, (tree,))


def _toplevel(directory: str) -> str | None:
    found = git_port.view().toplevel(Path(directory), timeout=GIT_SECONDS, errors="surrogateescape")
    return None if found is None else str(found)


def _git_ignored(root: str, probe: str) -> bool:
    git_view = git_port.view()
    found = git_view.ignore_match(Path(root), probe, excludes_file=True)
    source = found[0] if found else ""
    if PurePath(source).name != IGNORE_FILE or source.startswith(INFO_EXCLUDE_PREFIX):
        return False
    return git_view.is_tracked(Path(root), source) and git_view.is_unchanged(Path(root), source)


def touches_tests(change: PendingChange, root: str | None = None, removed: frozenset[Path] = frozenset()) -> bool:
    if ignored_scratch(change.path, root):
        return False
    if change.after is None and is_test_path(change.path, root) and not _plain_module(change, root, removed):
        return True
    if change.after_unknown or change.before == change.after:
        return False
    if _held_by_suite(change, root):
        return True
    declared = any(declared_tests.declares_tests(change.path, side) for side in (change.before, change.after) if side)
    if declared and (is_test_path(change.path, root) or test_units.language_of(change.path) is None):
        return True
    before = inline_test_bodies(change.path, change.before)
    return before != inline_test_bodies(change.path, change.after)


def window_until(settings: dict) -> int:
    value = settings.get(WINDOW_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def window_seconds_left(settings: dict, now: float) -> int:
    return max(0, int(window_until(settings) - now))


class Gate(NamedTuple):
    payload: object
    settings: dict
    now: float


def gate_open(gate: Gate) -> bool:
    if gate.settings.get("tests") != "deny" or authorized():
        return True
    if agent_types.type_of(gate.payload, gate.settings.get("state_root")) in TEST_WRITER_TYPES:
        return True
    return window_seconds_left(gate.settings, gate.now) > 0


def read_before(path: Path) -> str | None:
    try:
        return read_regular_text(path)
    except (OSError, UnicodeError):
        return None


def _finding(path: str, detail: str = "Test write in ") -> dict:
    action = OMP_ACTION if host.is_omp_host() else AGENT_ACTION
    return Finding(
        family="self_protection", rule=RULE, line=1, detail=detail + path,
        force=True, snippet=path.strip()[:180], action=action,
        path=path, severity=None, tool_use_id=None,
    ).to_dict()


def findings(
    gate: Gate, changes: Iterable[PendingChange], unresolved: Iterable[str] = (), removed: frozenset[Path] = frozenset(),
    *, settings_for: Callable[[str], dict] | None = None,
) -> list[dict]:
    opened = gate_open(gate)
    if opened and settings_for is None:
        return []
    cwd = Path(payloads.cwd(gate.payload) or ".")
    untyped = bool(payloads.agent_id(gate.payload)) and not payloads.agent_type(gate.payload)
    detail = UNTYPED_DETAIL if untyped else "Test write in "
    unique: dict[str, dict] = {}
    for change in changes:
        settings = gate.settings if settings_for is None else settings_for(change.path)
        if (opened if settings is gate.settings else gate_open(gate._replace(settings=settings))):
            continue
        if _project_test_change(change, cwd, removed):
            unique[change.path.rstrip("/")] = _finding(change.path, detail)
    unnamed = [] if opened else [_finding(command, UNRESOLVED_DETAIL) for command in unresolved]
    return [*unique.values(), *unnamed]


def _project_test_change(change: PendingChange, cwd: Path, removed: frozenset[Path]) -> bool:
    path = payloads.resolved_path(change.path, cwd)
    resolved = anchored_identity(change.path, cwd)
    exited = exited_project(cwd.absolute())
    returns_to_project = exited is not None and resolved.is_relative_to(exited)
    if not returns_to_project and external_target_project(resolved, cwd) is None:
        if outside_project(change.path, str(cwd)) or temp_scratch_repo(change.path, str(cwd)):
            return False
    root = target_root(resolved, cwd)
    return touches_tests(change._replace(path=str(path)), root, removed) or (
        resolved != path and not _same_test_shape(path, resolved, root, removed)
        and touches_tests(change._replace(path=str(resolved)), root, removed)
    )


def _same_test_shape(path: Path, resolved: Path, root: str, removed: frozenset[Path]) -> bool:
    return (
        path.name == resolved.name
        and in_test_dir(str(path), root) == in_test_dir(str(resolved), root)
        and in_fixture_dir(str(path), root) == in_fixture_dir(str(resolved), root)
        and _deleted(str(path), removed) == _deleted(str(resolved), removed)
    )
