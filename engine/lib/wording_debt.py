from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

try:
    from . import (
        language_layer,
        language_units,
        payloads,
        session_state,
        test_units,
        tests_policy,
    )
    from .findings import Finding
    from .regular_file import read_regular_text
    from .test_rules import file_units, wording_assertion
except ImportError:
    import language_layer
    import language_units
    import payloads
    import session_state
    import test_units
    import tests_policy
    from findings import Finding
    from regular_file import read_regular_text
    from test_rules import file_units, wording_assertion

RULE = "wording_debt_lock"
FAMILY = "self_protection"
STATE_KEY = "wording_debt"
ENTRIES_KEY = "entries"
MAX_SCAN_BYTES = 1_000_000
MAX_WALKED_ENTRIES = 50_000
UNREADABLE_DEBT = 1
REFRESH_SECONDS = 3.0
START_SECONDS = 6.0
NAMED_GAPS = 3
BUDGET_REASONS = frozenset({language_layer.SIZE_BUDGET, language_layer.TIME_BUDGET})
SKIPPED_DIRS = frozenset({"node_modules", "__pycache__", "venv", "site-packages", "build", "dist", "target"})
ACTION = (
    "While the project carries wording debt, a test write must rewrite or delete at least one wording test."
    " Rewrite the wording test named in the block into a behavior test, or delete it when it checks only text,"
    " and add no more new tests than the wording tests you remove."
)
UNKNOWN_DETAIL = "Test write with content the gate cannot read, while the project carries wording debt, in "
RAISE_DETAIL = "Test write that does not lower the wording debt of {count} wording tests in {root}, in "
GROWTH_DETAIL = "Test write that adds {added} new tests but removes only {removed} wording tests in {root}, in "
GAP_DETAIL = "Test write the gate cannot count because the {language} parser could not read the file ({reason}), in "
GAP_NOTE = " plus {count} test files the gate could not scan ({names}), each counted as one wording test until a rescan"


class Shift(NamedTuple):
    path: str
    before: int
    after: int
    added: int
    removed: int
    line: int
    readable: bool
    gap: str = ""


class Walk(NamedTuple):
    paths: list[str]
    truncated: bool


class Entry(NamedTuple):
    item_count: int
    size: int
    mtime_ns: int
    ctime_ns: int
    inode: int
    reason: str


class Debt(NamedTuple):
    counts: dict[str, int]
    gaps: dict[str, str]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _oversized(text: str | None) -> bool:
    return text is not None and len(text) > MAX_SCAN_BYTES


def wording_units(path: str, text: str | None) -> list[test_units.Unit]:
    if not text or _oversized(text):
        return []
    return [unit for unit in file_units(path, text) if wording_assertion.wording_lines(unit, text)]


def wording_count(path: str, text: str | None) -> int:
    return UNREADABLE_DEBT if _oversized(text) else len(wording_units(path, text))


def _walked(root: str) -> Walk:
    paths: list[str] = []
    for folder, directories, names in os.walk(root):
        directories[:] = sorted(name for name in directories if not name.startswith(".") and name not in SKIPPED_DIRS)
        for name in names:
            if len(paths) >= MAX_WALKED_ENTRIES:
                return Walk(paths, True)
            paths.append(os.path.join(folder, name))
    return Walk(paths, False)


def _counted(path: str, root: str) -> bool:
    known = test_units.language_of(path) is not None or language_layer.language_of(path) is not None
    return known and tests_policy.is_test_path(path, root)


def parse_gap(path: str, text: str | None, deadline: float) -> str:
    language = language_layer.language_of(path)
    if not text or language in (None, language_layer.PYTHON) or test_units.language_of(path) is not None:
        return ""
    named = language_units.named_test_path(path) or path.lower().endswith(language_units.BATS_SUFFIX)
    if not named and not language_units.MARKER_RE.search(text):
        return ""
    with language_layer.own_budget(MAX_SCAN_BYTES, deadline):
        layer = language_layer.parse(language, text)
        if not layer.precise and layer.reason in BUDGET_REASONS:
            language_layer.parse.cache_clear()
            layer = language_layer.parse(language, text)
        if not layer.precise:
            return layer.reason or language_layer.PARSER_FAILED
        return "" if language_layer.outline_for(path, language, text) is not None else language_layer.PARSER_FAILED


def _text_debt(path: str, text: str | None, counted: bool, deadline: float) -> tuple[int, str]:
    gap = parse_gap(path, text, deadline) if counted and not _oversized(text) else ""
    if gap:
        return UNREADABLE_DEBT, gap
    with language_layer.own_budget(MAX_SCAN_BYTES, deadline):
        return wording_count(path, text), ""


def _disk_entry(path: str, counted: bool, deadline: float) -> Entry | None:
    unreadable = UNREADABLE_DEBT if counted else 0
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return Entry(unreadable, -1, -1, -1, -1, "")
    try:
        text = read_regular_text(Path(path))
        count, gap = (unreadable, "") if _oversized(text) else _text_debt(path, text, counted, deadline)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, RecursionError, MemoryError):
        count, gap = unreadable, ""
    return Entry(count, *_signature(info), gap)


def _disk_count(path: str, unreadable: int, deadline: float) -> tuple[int, str]:
    entry = _disk_entry(path, bool(unreadable), deadline)
    return (0, "") if entry is None else (entry.item_count, entry.reason)


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino


def _unchanged(path: str, entry: Entry | None) -> bool:
    if entry is None or entry.reason or entry.size < 0:
        return False
    try:
        info = os.stat(path)
    except OSError:
        return False
    return _signature(info) == (entry.size, entry.mtime_ns, entry.ctime_ns, entry.inode)


def _fresh_entry(path: str, cached: Entry | None, deadline: float) -> Entry | None:
    if _unchanged(path, cached):
        return cached
    if time.monotonic() >= deadline:
        return Entry(UNREADABLE_DEBT, -1, -1, -1, -1, language_layer.TIME_BUDGET) if os.path.lexists(path) else None
    return _disk_entry(path, True, deadline)


def refreshed_entries(root: str, cached: dict[str, Entry], deadline: float) -> tuple[dict[str, Entry], bool]:
    walk = _walked(root)
    entries = {path: _fresh_entry(path, cached.get(path), deadline) for path in walk.paths if _counted(path, root)}
    return {path: entry for path, entry in entries.items() if entry is not None}, walk.truncated


def _debt(root: str, entries: dict[str, Entry], truncated: bool) -> Debt:
    counts = {path: entry.item_count for path, entry in entries.items() if entry.item_count}
    if truncated:
        counts[root] = UNREADABLE_DEBT
    return Debt(counts, {path: entry.reason for path, entry in entries.items() if entry.reason})


def _valid_entry(value: object) -> bool:
    if not isinstance(value, list) or len(value) != len(Entry._fields) or not isinstance(value[-1], str):
        return False
    return all(type(number) is int for number in value[:-1]) and value[0] >= 0


def _valid_entries(value: object) -> dict[str, Entry] | None:
    if not isinstance(value, dict) or not isinstance(value.get(ENTRIES_KEY), dict):
        return None
    entries = value[ENTRIES_KEY]
    if not all(isinstance(key, str) and _valid_entry(entry) for key, entry in entries.items()):
        return None
    return {path: Entry(*entry) for path, entry in entries.items()}


def _stored(session_id: str, root: str, state_root: str | None) -> dict[str, Entry] | None:
    if not session_id:
        return None
    projects = session_state.read_state(session_id, state_root).get(STATE_KEY)
    return _valid_entries(projects.get(root)) if isinstance(projects, dict) else None


def _store(session_id: str, root: str, entries: dict[str, Entry], state_root: str | None) -> None:
    if not session_id:
        return

    def mutate(state: dict) -> dict:
        projects = state.get(STATE_KEY)
        kept = dict(projects) if isinstance(projects, dict) else {}
        kept[root] = {ENTRIES_KEY: {path: list(entry) for path, entry in entries.items()}}
        return {**state, STATE_KEY: kept}

    session_state.update_state(session_id, mutate, state_root)


def project_debt(session_id: str, root: str, state_root: str | None, deadline: float) -> Debt:
    cached = _stored(session_id, root, state_root)
    entries, truncated = refreshed_entries(root, cached or {}, deadline)
    if entries != cached:
        _store(session_id, root, entries, state_root)
    return _debt(root, entries, truncated)


def _deadline() -> float:
    return time.monotonic() + REFRESH_SECONDS


def note_session_start(session_id: str, cwd: str, state_root: str | None) -> None:
    if not session_id or not cwd:
        return
    project_debt(session_id, tests_policy.project_root(cwd), state_root, time.monotonic() + START_SECONDS)


def _owning_root(path: str) -> str:
    return tests_policy.project_root(os.path.dirname(path))


def note_written(session_id: str, paths: Iterable[str], state_root: str | None) -> None:
    if not session_id:
        return
    deadline = _deadline()
    for path in paths:
        root = _owning_root(path)
        entries = _stored(session_id, root, state_root)
        if entries is None or not _counted(path, root):
            continue
        entry = _disk_entry(path, True, deadline)
        kept = {key: value for key, value in entries.items() if key != path}
        _store(session_id, root, {**kept, **({path: entry} if entry is not None else {})}, state_root)


def _shift(change: tests_policy.PendingChange, deleted: frozenset[Path], root: str, deadline: float) -> Shift:
    if change.after is None and Path(change.path) in deleted:
        change = change._replace(after="")
    counted = _counted(change.path, root)
    before_wording = Counter(unit.name for unit in wording_units(change.path, change.before))
    if change.before is not None:
        before, before_gap = _text_debt(change.path, change.before, counted, deadline)
    else:
        before, before_gap = _disk_count(change.path, UNREADABLE_DEBT if counted else 0, deadline)
    if change.after is None:
        return Shift(change.path, before, before, 0, 0, 1, change.before is None)
    if _oversized(change.after) or (change.after != "" and _oversized(change.before)):
        return Shift(change.path, before, before, 0, 0, 1, False)
    after_gap = parse_gap(change.path, change.after, deadline) if counted else ""
    if before_gap or after_gap:
        return Shift(change.path, before, before, 0, 0, 1, False, after_gap or before_gap)
    after_units = file_units(change.path, change.after)
    after_wording = wording_units(change.path, change.after)
    before_names = Counter(unit.name for unit in file_units(change.path, change.before or ""))
    fresh = Counter(unit.name for unit in after_units) - before_names
    removed = (before_wording - Counter(unit.name for unit in after_wording)).total()
    lines = [unit.start for unit in after_units if unit.name in fresh] or [unit.start for unit in after_wording] or [1]
    return Shift(change.path, before, len(after_wording), fresh.total(), removed, lines[0], True)


def _row(path: str, line: int, detail: str) -> dict:
    return Finding(
        family=FAMILY, rule=RULE, line=line, detail=detail + path, force=True,
        snippet=path.strip()[:180], action=ACTION, path=path, severity=None, tool_use_id=None,
    ).to_dict()


def _unreadable_row(shift: Shift) -> dict:
    if not shift.gap:
        return _row(shift.path, shift.line, UNKNOWN_DETAIL)
    language = language_layer.language_of(shift.path) or "language"
    return _row(shift.path, shift.line, GAP_DETAIL.format(language=language, reason=shift.gap))


def _gap_note(gaps: dict[str, str]) -> str:
    if not gaps:
        return ""
    named = [f"{path}: {reason}" for path, reason in sorted(gaps.items())[:NAMED_GAPS]]
    if len(gaps) > NAMED_GAPS:
        named.append(f"{len(gaps) - NAMED_GAPS} more")
    return GAP_NOTE.format(count=len(gaps), names=", ".join(named))


def _project_rows(root: str, shifts: list[Shift], debt: Debt) -> list[dict]:
    old = debt.total
    if not old:
        return []
    unreadable = [shift for shift in shifts if not shift.readable]
    if unreadable:
        return [_unreadable_row(shift) for shift in unreadable]
    new = old + sum(shift.after - shift.before for shift in shifts)
    added = sum(shift.added for shift in shifts)
    removed = sum(shift.removed for shift in shifts)
    where = root + _gap_note(debt.gaps)
    if added > removed:
        detail = GROWTH_DETAIL.format(added=added, removed=removed, root=where)
        return [_row(shift.path, shift.line, detail) for shift in shifts if shift.added]
    if new >= old:
        detail = RAISE_DETAIL.format(count=old - len(debt.gaps), root=where)
        return [_row(shift.path, shift.line, detail) for shift in shifts]
    return []


def _resolved(change: tests_policy.PendingChange, cwd: Path) -> tests_policy.PendingChange:
    return change._replace(path=str(payloads.resolved_path(change.path, cwd)))


def lock_findings(
    gate: tests_policy.Gate, changes: Iterable[tests_policy.PendingChange], unresolved: Iterable[str] = (),
    deleted: frozenset[Path] = frozenset(),
) -> list[dict]:
    session_id = payloads.session_id(gate.payload)
    state_root = gate.settings.get("state_root")
    cwd = Path(payloads.cwd(gate.payload) or ".")
    deadline = _deadline()
    grouped: dict[str, list[tests_policy.PendingChange]] = {}
    for change in (_resolved(change, cwd) for change in changes):
        root = _owning_root(change.path)
        if tests_policy.touches_tests(change, root):
            grouped.setdefault(root, []).append(change)
    rows: list[dict] = []
    for root, touched in grouped.items():
        shifts = [_shift(change, deleted, root, deadline) for change in touched]
        debt = project_debt(session_id, root, state_root, deadline)
        touched_paths = {shift.path for shift in shifts}
        merged = {**debt.counts, **{shift.path: shift.before for shift in shifts}}
        gaps = {path: reason for path, reason in debt.gaps.items() if path not in touched_paths}
        rows.extend(_project_rows(root, shifts, Debt({path: count for path, count in merged.items() if count}, gaps)))
    commands = list(unresolved)
    if commands and project_debt(session_id, tests_policy.project_root(str(cwd)), state_root, deadline).total:
        rows.extend(_row(command, 1, UNKNOWN_DETAIL) for command in commands)
    return rows


def gate_findings(
    gate: tests_policy.Gate, changes: Iterable[tests_policy.PendingChange], unresolved: Iterable[str] = (),
    deleted: frozenset[Path] = frozenset(), removed: frozenset[Path] = frozenset(),
) -> list[dict]:
    pending = list(changes)
    commands = list(unresolved)
    return tests_policy.findings(gate, pending, commands, removed) + lock_findings(gate, pending, commands, deleted)
