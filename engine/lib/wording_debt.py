from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from pathlib import Path
from typing import NamedTuple

try:
    from . import (
        config_roots,
        language_layer,
        language_units,
        payloads,
        session_state,
        test_units,
        tests_policy,
    )
    from .config import contract_strings, effective_config
    from .findings import Finding
    from .regular_file import read_regular_text
    from .test_rules import file_units, wording_assertion
except ImportError:
    import config_roots
    import language_layer
    import language_units
    import payloads
    import session_state
    import test_units
    import tests_policy
    from config import contract_strings, effective_config
    from findings import Finding
    from regular_file import read_regular_text
    from test_rules import file_units, wording_assertion

RULE = "wording_debt_lock"
GAP_RULE = "wording_test_gap"
RULES = frozenset({RULE, GAP_RULE})
FAMILY = "self_protection"
STATE_KEY = "wording_debt"
ENTRIES_KEY = "entries"
CONTRACTS_KEY = "contracts"
MAX_SCAN_BYTES = 1_000_000
MAX_WALKED_ENTRIES = 50_000
UNREADABLE_DEBT = 1
REFRESH_SECONDS = 3.0
START_SECONDS = 6.0
SHOWN_UNITS = 20
PROJECT_MARKER = tests_policy.PROJECT_MARKER
OVERSIZED = "file_too_large"
UNREADABLE = "unreadable"
WALK_LIMIT = "walk_limit"
PARSER_OVERFLOW = test_units.PARSER_OVERFLOW
PARSE_OVERFLOWS = (MemoryError, RecursionError)
STABLE_GAPS = frozenset({OVERSIZED, UNREADABLE})
CACHED_GAPS = STABLE_GAPS | {PARSER_OVERFLOW, test_units.SYNTAX_ERROR}
BUDGET_REASONS = frozenset({language_layer.SIZE_BUDGET, language_layer.TIME_BUDGET})
SKIPPED_DIRS = frozenset({"node_modules", "__pycache__", "venv", "site-packages", "build", "dist", "target"})
ACTION = (
    "Leave unchanged wording tests as they are. Check behavior when you add or change assertions. "
    "The Full report lists every unit under units."
)
UNKNOWN_DETAIL = "The project has existing wording tests. The gate could not read the write to "
DEBT_DETAIL = "The project has {count} existing wording tests in {root}: {units}. Test write in "
OMITTED_UNITS = ", and {count} more listed under units"
GAP_DETAIL = "Test write the gate cannot count because the {language} parser could not read the file ({reason}), in "
SCAN_GAP_DETAIL = "Test file the gate could not scan, left out of the wording test count: "
GAP_ACTION = "Check this file by hand for wording assertions. The next scan counts it once the gate can read it."


class Shift(NamedTuple):
    path: str
    before: int
    after: int
    added: int
    removed: int
    line: int
    readable: bool
    gap: str = ""
    spots: tuple[tuple[int, str], ...] = ()


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
    units: tuple[tuple[int, str], ...] = ()


class Scan(NamedTuple):
    item_count: int
    gap: str
    units: tuple[tuple[int, str], ...] = ()


class Debt(NamedTuple):
    counts: dict[str, int]
    gaps: dict[str, str]
    units: dict[str, tuple[tuple[int, str], ...]]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _oversized(text: str | None) -> bool:
    return text is not None and len(text) > MAX_SCAN_BYTES


def wording_units(path: str, text: str | None, contracts: Collection[str] = ()) -> list[test_units.Unit]:
    if not text or _oversized(text):
        return []
    return [unit for unit in file_units(path, text) if wording_assertion.wording_lines(unit, text, contracts)]


def wording_count(path: str, text: str | None, contracts: Collection[str] = ()) -> int:
    return UNREADABLE_DEBT if _oversized(text) else len(wording_units(path, text, contracts))


def _first_hit(unit: test_units.Unit, text: str, contracts: Collection[str]) -> tuple[int, str] | None:
    hits = wording_assertion.check(unit, text, contracts)
    return (hits[0].line, hits[0].rule) if hits else None


def wording_spots(path: str, text: str | None, contracts: Collection[str] = ()) -> tuple[tuple[int, str], ...]:
    if not text or _oversized(text):
        return ()
    found = (_first_hit(unit, text, contracts) for unit in file_units(path, text))
    return tuple(spot for spot in found if spot is not None)


def _walk_limit() -> int:
    return MAX_WALKED_ENTRIES


def _walked(root: str) -> Walk:
    limit = _walk_limit()
    paths: list[str] = []
    for folder, directories, names in os.walk(root):
        directories[:] = sorted(name for name in directories if not name.startswith(".") and name not in SKIPPED_DIRS)
        for name in names:
            if len(paths) >= limit:
                return Walk(paths, True)
            paths.append(os.path.join(folder, name))
    return Walk(paths, False)


def _counted(path: str, root: str) -> bool:
    known = test_units.language_of(path) is not None or language_layer.language_of(path) is not None
    return known and tests_policy.is_test_path(path, root)


def parse_gap(path: str, text: str | None, deadline: float) -> str:
    language = language_layer.language_of(path)
    if text and test_units.language_of(path) == test_units.PYTHON:
        return test_units.parsed_python(text).gap
    if not text or language in (None, language_layer.PYTHON) or test_units.language_of(path) is not None:
        return ""
    named = language_units.named_test_path(path) or path.lower().endswith(language_units.BATS_SUFFIX)
    if not named and not language_units.MARKER_RE.search(text):
        return ""
    cache_root = config_roots.grammar_cache_root()
    with language_layer.own_budget(MAX_SCAN_BYTES, deadline):
        layer = language_layer.parse(language, text, cache_root)
        if not layer.precise and layer.reason in BUDGET_REASONS:
            language_layer.parse.cache_clear()
            layer = language_layer.parse(language, text, cache_root)
        if not layer.precise:
            return layer.reason or language_layer.PARSER_FAILED
        return "" if language_layer.outline_for(path, language, text) is not None else language_layer.PARSER_FAILED


def _text_debt(
    path: str, text: str | None, counted: bool, deadline: float, contracts: Collection[str],
) -> Scan:
    if _oversized(text):
        return Scan(0, OVERSIZED if counted else "")
    gap = parse_gap(path, text, deadline) if counted else ""
    if gap:
        return Scan(0, gap)
    with language_layer.own_budget(MAX_SCAN_BYTES, deadline):
        spots = wording_spots(path, text, contracts)
    return Scan(len(spots), "", spots)


def _guarded_debt(
    path: str, text: str | None, counted: bool, deadline: float, contracts: Collection[str],
) -> Scan:
    try:
        return _text_debt(path, text, counted, deadline, contracts)
    except PARSE_OVERFLOWS:
        return Scan(0, PARSER_OVERFLOW if counted else "")


def _disk_entry(path: str, counted: bool, deadline: float, contracts: Collection[str]) -> Entry | None:
    lost = Scan(0, UNREADABLE if counted else "")
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return Entry(lost.item_count, -1, -1, -1, -1, lost.gap)
    try:
        scan = _guarded_debt(path, read_regular_text(Path(path)), counted, deadline, contracts)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, RecursionError, MemoryError):
        scan = lost
    return Entry(scan.item_count, *_signature(info), scan.gap, scan.units)


def _disk_count(path: str, unreadable: int, deadline: float, contracts: Collection[str]) -> Scan:
    entry = _disk_entry(path, bool(unreadable), deadline, contracts)
    return Scan(0, "") if entry is None else Scan(entry.item_count, entry.reason, entry.units)


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino


def _unchanged(path: str, entry: Entry | None) -> bool:
    if entry is None or entry.size < 0 or (entry.reason and entry.reason not in CACHED_GAPS):
        return False
    try:
        info = os.stat(path)
    except OSError:
        return False
    return _signature(info) == (entry.size, entry.mtime_ns, entry.ctime_ns, entry.inode)


def _fresh_entry(path: str, cached: Entry | None, deadline: float, contracts: Collection[str]) -> Entry | None:
    if _unchanged(path, cached):
        return cached
    if time.monotonic() >= deadline:
        return Entry(0, -1, -1, -1, -1, language_layer.TIME_BUDGET) if os.path.lexists(path) else None
    return _disk_entry(path, True, deadline, contracts)


def refreshed_entries(
    root: str, cached: dict[str, Entry], deadline: float, contracts: Collection[str] = (),
    *, gone: frozenset[str] = frozenset(),
) -> tuple[dict[str, Entry], bool]:
    walk = _walked(root)
    counted = (path for path in walk.paths if path not in gone and _counted(path, root))
    entries = {path: _fresh_entry(path, cached.get(path), deadline, contracts) for path in counted}
    return {path: entry for path, entry in entries.items() if entry is not None}, walk.truncated


def _debt(root: str, entries: dict[str, Entry], truncated: bool) -> Debt:
    counts = {path: entry.item_count for path, entry in entries.items() if entry.item_count and not entry.reason}
    gaps = {path: entry.reason for path, entry in entries.items() if entry.reason}
    if truncated:
        gaps[root] = WALK_LIMIT
    return Debt(counts, gaps, {path: entry.units for path, entry in entries.items() if entry.units})


def _valid_spot(value: object) -> bool:
    return isinstance(value, list) and len(value) == 2 and type(value[0]) is int and isinstance(value[1], str)


def _valid_entry(value: object) -> bool:
    if not isinstance(value, list) or len(value) != len(Entry._fields):
        return False
    *numbers, reason, units = value
    if not isinstance(reason, str) or not isinstance(units, list) or not all(_valid_spot(spot) for spot in units):
        return False
    return all(type(number) is int for number in numbers) and numbers[0] >= 0


def _loaded_entry(value: list) -> Entry:
    return Entry(*value[:-1], tuple((line, rule) for line, rule in value[-1]))


def _valid_entries(value: object) -> dict[str, Entry] | None:
    if not isinstance(value, dict) or not isinstance(value.get(ENTRIES_KEY), dict):
        return None
    entries = value[ENTRIES_KEY]
    if not all(isinstance(key, str) and _valid_entry(entry) for key, entry in entries.items()):
        return None
    return {path: _loaded_entry(entry) for path, entry in entries.items()}


def _stored(
    session_id: str, root: str, state_root: str | None, contracts: Collection[str],
) -> dict[str, Entry] | None:
    if not session_id:
        return None
    projects = session_state.read_state(session_id, state_root).get(STATE_KEY)
    project = projects.get(root) if isinstance(projects, dict) else None
    if not isinstance(project, dict) or project.get(CONTRACTS_KEY) != sorted(contracts):
        return None
    return _valid_entries(project)


def _store(
    session_id: str, root: str, entries: dict[str, Entry], state_root: str | None, contracts: Collection[str],
) -> None:
    if not session_id:
        return

    def mutate(state: dict) -> dict:
        projects = state.get(STATE_KEY)
        kept = dict(projects) if isinstance(projects, dict) else {}
        kept[root] = {
            CONTRACTS_KEY: sorted(contracts),
            ENTRIES_KEY: {path: [*entry[:-1], [list(spot) for spot in entry.units]] for path, entry in entries.items()},
        }
        return {**state, STATE_KEY: kept}

    session_state.update_state(session_id, mutate, state_root)


def project_debt(
    session_id: str, root: str, state_root: str | None, deadline: float, contracts: Collection[str] = (),
    *, gone: frozenset[str] = frozenset(),
) -> Debt:
    cached = _stored(session_id, root, state_root, contracts)
    entries, truncated = refreshed_entries(root, cached or {}, deadline, contracts, gone=gone)
    if entries != cached:
        _store(session_id, root, entries, state_root, contracts)
    return _debt(root, entries, truncated)


def _deadline() -> float:
    return time.monotonic() + REFRESH_SECONDS


def note_session_start(session_id: str, cwd: str, state_root: str | None) -> None:
    if not session_id or not cwd:
        return
    contracts = contract_strings(effective_config(cwd=cwd))
    project_debt(session_id, tests_policy.project_root(cwd), state_root, time.monotonic() + START_SECONDS, contracts)


def _owning_root(path: str, cwd: str) -> str:
    owner = tests_policy.project_root(os.path.dirname(path))
    if (Path(owner) / PROJECT_MARKER).exists() or not cwd:
        return owner
    base = tests_policy.project_root(cwd)
    return base if Path(os.path.abspath(path)).is_relative_to(base) else owner


def note_written(
    session_id: str, paths: Iterable[str], state_root: str | None, cwd: str = "", *, contracts: Collection[str] = (),
) -> None:
    if not session_id:
        return
    deadline = _deadline()
    for path in paths:
        root = _owning_root(path, cwd)
        entries = _stored(session_id, root, state_root, contracts)
        if entries is None or not _counted(path, root):
            continue
        entry = _disk_entry(path, True, deadline, contracts)
        kept = {key: value for key, value in entries.items() if key != path}
        _store(session_id, root, {**kept, **({path: entry} if entry is not None else {})}, state_root, contracts)


def _deleted(change: tests_policy.PendingChange, deleted: frozenset[Path]) -> bool:
    return change.after is None and Path(change.path) in deleted


def _prior(
    change: tests_policy.PendingChange, counted: bool, deadline: float, contracts: Collection[str],
) -> Scan:
    if change.before is not None:
        return _guarded_debt(change.path, change.before, counted, deadline, contracts)
    return _disk_count(change.path, UNREADABLE_DEBT if counted else 0, deadline, contracts)


def _shift(
    change: tests_policy.PendingChange, deleted: frozenset[Path], root: str, deadline: float, contracts: Collection[str],
) -> Shift:
    gone = _deleted(change, deleted)
    if gone:
        change = change._replace(after="")
    counted = _counted(change.path, root)
    prior = _prior(change, counted, deadline, contracts)
    before = 0 if prior.gap else prior.item_count
    if change.after is None:
        return Shift(change.path, before, before, 0, 0, 1, change.before is None, prior.gap, prior.units)
    if _oversized(change.after) or (change.after != "" and _oversized(change.before)):
        return Shift(change.path, before, before, 0, 0, 1, False)
    if gone and prior.gap:
        return Shift(change.path, before, 0, 0, 0, 1, True)
    try:
        return _written_shift(change, prior, counted, deadline, contracts)
    except PARSE_OVERFLOWS:
        return Shift(change.path, before, before, 0, 0, 1, False, PARSER_OVERFLOW)


def _written_shift(
    change: tests_policy.PendingChange, prior: Scan, counted: bool, deadline: float, contracts: Collection[str],
) -> Shift:
    before = 0 if prior.gap else prior.item_count
    before_gap = "" if prior.gap in STABLE_GAPS else prior.gap
    after_gap = parse_gap(change.path, change.after, deadline) if counted else ""
    if before_gap or after_gap:
        return Shift(change.path, before, before, 0, 0, 1, False, after_gap or before_gap)
    before_wording = Counter(unit.name for unit in wording_units(change.path, change.before, contracts))
    after_units = file_units(change.path, change.after)
    after_wording = wording_units(change.path, change.after, contracts)
    before_names = Counter(unit.name for unit in file_units(change.path, change.before or ""))
    fresh = Counter(unit.name for unit in after_units) - before_names
    removed = (before_wording - Counter(unit.name for unit in after_wording)).total()
    lines = [unit.start for unit in after_units if unit.name in fresh] or [unit.start for unit in after_wording] or [1]
    return Shift(change.path, before, len(after_wording), fresh.total(), removed, lines[0], True, "", prior.units)


def _row(
    path: str, line: int, detail: str, units: tuple[str, ...] | None = None, action: str = ACTION, subject: str = "",
    rule: str = RULE,
) -> dict:
    return Finding(
        family=FAMILY, rule=rule, line=line, detail=detail + (subject or path), force=True,
        snippet=path.strip()[:180], action=action, path=path, severity=None, tool_use_id=None, units=units,
    ).to_dict()


def _unreadable_row(shift: Shift) -> dict:
    if not shift.gap:
        return _row(shift.path, shift.line, UNKNOWN_DETAIL)
    language = language_layer.language_of(shift.path) or "language"
    return _row(shift.path, shift.line, GAP_DETAIL.format(language=language, reason=shift.gap))


def _gap_rows(gaps: dict[str, str]) -> list[dict]:
    return [
        _row(path, 1, SCAN_GAP_DETAIL, action=GAP_ACTION, subject=f"{path}: {reason}", rule=GAP_RULE)
        for path, reason in sorted(gaps.items())
    ]


def _unit_names(root: str, units: dict[str, tuple[tuple[int, str], ...]]) -> tuple[str, ...]:
    return tuple(
        f"{os.path.relpath(path, root)}:{line} {rule}" for path in sorted(units) for line, rule in units[path]
    )


def _debt_detail(root: str, names: tuple[str, ...]) -> str:
    shown = ", ".join(names[:SHOWN_UNITS])
    if len(names) > SHOWN_UNITS:
        shown += OMITTED_UNITS.format(count=len(names) - SHOWN_UNITS)
    return DEBT_DETAIL.format(count=len(names), root=root, units=shown)


def _project_rows(root: str, shifts: list[Shift], debt: Debt, existing: Debt) -> list[dict]:
    old = debt.total
    if not old:
        prior_debt_gaps = {
            shift.path: shift.gap
            for shift in shifts
            if shift.gap and (shift.before or shift.path in existing.counts or shift.path in existing.units)
        }
        return _gap_rows({**existing.gaps, **prior_debt_gaps})
    gaps = _gap_rows(debt.gaps)
    unreadable = [shift for shift in shifts if not shift.readable]
    if unreadable:
        return [_unreadable_row(shift) for shift in unreadable] + gaps
    new = old + sum(shift.after - shift.before for shift in shifts)
    added = sum(shift.added for shift in shifts)
    removed = sum(shift.removed for shift in shifts)
    if added > removed:
        noted = [shift for shift in shifts if shift.added]
    elif new >= old:
        noted = shifts
    else:
        return gaps
    names = _unit_names(root, debt.units)
    detail = _debt_detail(root, names)
    return [_row(shift.path, shift.line, detail, names) for shift in noted if names] + gaps


def _touched_debt(debt: Debt, shifts: list[Shift]) -> Debt:
    touched = {shift.path for shift in shifts}
    counts = {**debt.counts, **{shift.path: 0 if shift.gap else shift.before for shift in shifts}}
    gaps = {path: reason for path, reason in debt.gaps.items() if path not in touched}
    units = {path: spots for path, spots in debt.units.items() if path not in touched}
    return Debt(
        {path: count for path, count in counts.items() if count},
        {**gaps, **{shift.path: shift.gap for shift in shifts if shift.gap}},
        {**units, **{shift.path: shift.spots for shift in shifts if shift.spots}},
    )


def _resolved(change: tests_policy.PendingChange, cwd: Path) -> tests_policy.PendingChange:
    return change._replace(path=str(payloads.resolved_path(change.path, cwd)))


def lock_findings(
    gate: tests_policy.Gate, changes: Iterable[tests_policy.PendingChange], unresolved: Iterable[str] = (),
    deleted: frozenset[Path] = frozenset(),
) -> list[dict]:
    contracts = contract_strings(gate.settings)
    session_id = payloads.session_id(gate.payload)
    state_root = gate.settings.get("state_root")
    cwd = Path(payloads.cwd(gate.payload) or ".")
    deadline = _deadline()
    grouped: dict[str, list[tests_policy.PendingChange]] = {}
    for change in (_resolved(change, cwd) for change in changes):
        root = _owning_root(change.path, str(cwd))
        if tests_policy.touches_tests(change, root):
            grouped.setdefault(root, []).append(change)
    rows: list[dict] = []
    for root, touched in grouped.items():
        shifts = [_shift(change, deleted, root, deadline, contracts) for change in touched]
        gone = frozenset(change.path for change in touched if _deleted(change, deleted))
        debt = project_debt(session_id, root, state_root, deadline, contracts, gone=gone)
        rows.extend(_project_rows(root, shifts, _touched_debt(debt, shifts), debt))
    commands = list(unresolved)
    if commands and project_debt(session_id, tests_policy.project_root(str(cwd)), state_root, deadline, contracts).total:
        rows.extend(_row(command, 1, UNKNOWN_DETAIL) for command in commands)
    return rows


def gate_findings(
    gate: tests_policy.Gate, changes: Iterable[tests_policy.PendingChange], unresolved: Iterable[str] = (),
    deleted: frozenset[Path] = frozenset(), removed: frozenset[Path] = frozenset(),
    *, settings_for: Callable[[str], dict] | None = None,
) -> list[dict]:
    pending = list(changes)
    commands = list(unresolved)
    denied = tests_policy.findings(gate, pending, commands, removed, settings_for=settings_for)
    return denied + lock_findings(gate, pending, commands, deleted)
