from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path, PurePath
from typing import NamedTuple

try:
    from . import agent_types, declared_tests, git_globs, host, payloads, test_units
    from .findings import Finding
    from .markup import MARKDOWN_EXTS
    from .protected import authorized
    from .regular_file import read_regular_text
except ImportError:
    import agent_types
    import declared_tests
    import git_globs
    import host
    import payloads
    import test_units
    from findings import Finding
    from markup import MARKDOWN_EXTS
    from protected import authorized
    from regular_file import read_regular_text

TEST_WRITER = "adw-test-writer"
TEST_WRITER_TYPES = frozenset({TEST_WRITER, "agent-discipline-watcher:" + TEST_WRITER, "adw-light:" + TEST_WRITER})
RULE = "test_write_gate"
WINDOW_KEY = "tests_allow_until"
WINDOW_HINT = "adw-config tests allow --for 30m"
FIXTURE_DIRS = frozenset({"testdata", "__snapshots__", "__fixtures__", "__mocks__"})
TEST_DIRS = frozenset({"tests", "__tests__", "test", "spec"}) | FIXTURE_DIRS
NESTED_FIXTURE_DIR = "fixtures"
CONFTEST = "conftest.py"
DOC_EXTS = MARKDOWN_EXTS | {".rst", ".txt", ".adoc"}
PROJECT_MARKER = ".git"
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
    f" done here, ask them to run `{WINDOW_HINT}` in a terminal."
)
OMP_ACTION = (
    "OMP names no agent on tool calls, so only a timed window opens this gate."
    f" Ask the user to run `{WINDOW_HINT}` in a terminal."
)


class PendingChange(NamedTuple):
    path: str
    before: str | None
    after: str | None


def project_root(cwd: str) -> str:
    start = Path(os.path.abspath(cwd or "."))
    for directory in (start, *start.parents):
        if (directory / PROJECT_MARKER).exists():
            return str(directory)
    return str(start)


def outside_project(path: str, cwd: str) -> bool:
    base = os.path.abspath(cwd or ".")
    real = Path(os.path.realpath(os.path.join(base, path)))
    return not real.is_relative_to(os.path.realpath(base)) and git_globs.outside_work_trees(real)


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


def touches_tests(change: PendingChange, root: str | None = None, removed: frozenset[Path] = frozenset()) -> bool:
    if change.after is None and is_test_path(change.path, root) and not _plain_module(change, root, removed):
        return True
    if change.before == change.after:
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


def group_unresolved(rows: list[dict]) -> list[dict]:
    dynamic = [row for row in rows if row.get("rule") == "dynamic_write_target"]
    uncertain = [row for row in rows if row.get("rule") == RULE and row.get("detail", "").startswith(UNRESOLVED_DETAIL)]
    uncertain = [row for row in uncertain if any(item["match"] in row["path"] for item in dynamic)]
    if not dynamic or not uncertain:
        return rows
    tokens = list(dict.fromkeys(row["match"] for row in dynamic))
    grouped = {**dynamic[0], "match": ", ".join(tokens), "action": "Use a literal loop list so the write and test gates can check each destination."}
    return [row for row in rows if row not in dynamic and row not in uncertain] + [grouped]


def findings(
    gate: Gate, changes: Iterable[PendingChange], unresolved: Iterable[str] = (), removed: frozenset[Path] = frozenset(),
) -> list[dict]:
    if gate_open(gate):
        return []
    cwd = Path(payloads.cwd(gate.payload) or ".")
    root = project_root(str(cwd))
    untyped = bool(payloads.agent_id(gate.payload)) and not payloads.agent_type(gate.payload)
    detail = UNTYPED_DETAIL if untyped else "Test write in "
    rows = [
        _finding(change.path, detail) for change in changes
        if not outside_project(change.path, str(cwd))
        and touches_tests(change._replace(path=str(payloads.resolved_path(change.path, cwd))), root, removed)
    ]
    unique = {row["path"].rstrip("/"): row for row in rows}
    return [*unique.values(), *(_finding(command, UNRESOLVED_DETAIL) for command in unresolved)]
