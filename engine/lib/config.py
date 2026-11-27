from __future__ import annotations

import copy
import hashlib
import json
import operator
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

try:
    from . import families, german_rules, prose_language, test_rules
    from .findings import Outcome, VerdictKind
    from .payloads import cwd as payload_cwd, exact_string_dict, session_id as payload_session_id
except ImportError:
    import families
    import german_rules
    import prose_language
    import test_rules
    from findings import Outcome, VerdictKind
    from payloads import cwd as payload_cwd, exact_string_dict, session_id as payload_session_id


SCANNER_ALWAYS_BLOCKING_RULES = frozenset({
    "suppression_escape_hatch",
    "file_too_long",
    "unscannable_file",
})
REPORT_ONLY_RULES = frozenset({"wording_debt_lock", "wording_test_gap", "hard_coded_system_path"})
FIXED_OBSERVE_RULES = frozenset({"file_length_warning", "file_length_critical"}) | REPORT_ONLY_RULES
STRICT_HARD_BLOCK_RULES = frozenset({
    "wording_assertion", "wording_exception_match", "wording_log_assertion", "wording_ui_locator",
    "added_comment",
    "added_docstring",
    "unresolved_doc_reference",
    "what_comment",
    "what_docstring",
    "weak_why_comment",
    "prose_comment_block",
    "docstring_narration",
    "agent_marker_author",
})
SELF_PROTECTION_RULES = frozenset({
    "watcher_install_surface", "watcher_wiring_removal", "config_seal",
    "state_deletion", "state_mutation",
    "test_write_gate", "test_writer_definition",
})
ALWAYS_BLOCKING_RULES = (
    SCANNER_ALWAYS_BLOCKING_RULES | STRICT_HARD_BLOCK_RULES | SELF_PROTECTION_RULES
)

GATE_STATES = ("off", "observe", "enforce")
JUDGED_STATE = "judged"
SURFACE_PROSE = "prose"
SURFACE_COMMIT = "commit"
SURFACES = (SURFACE_PROSE, SURFACE_COMMIT)
SURFACE_ALL = "all"
RULE_GATE_STATES = (*GATE_STATES, JUDGED_STATE)

GATE_FAMILIES = families.NAMES
RETIRED_RULES = frozenset({"long_sentence", "de_sentence_length"})
RETIRED_SETTINGS = frozenset({"sentence_word_cap"})

MAX_LISTED_ROWS = 5
CONTRACT_STRINGS_KEY = "contract_strings"

DEFAULTS = {
    "prose": True,
    "comment": True,
    "code": True,
    "punctuation": True,
    "english": True,
    "clean_code": True,
    "max_rows": MAX_LISTED_ROWS,
    "list_item_cap": 8,
    "prose_colon_limit": 3,
    "adw_model": "",
    "exempt_paths": [],
    CONTRACT_STRINGS_KEY: [],
    "exempt_families": {},
    "baseline": "report",
    "gates": {},
    "rule_gates": {
        "apology_comment": "observe",
        "dead_metaphor": "observe",
        "ai_tell": "observe",
        "inflated_diction": "observe",
        "vague_quantity": "observe",
        "filler": "observe",
        "throat_clearing_idiom": "observe",
        "meta_commentary_idiom": "observe",
        "unresolved_doc_reference": "enforce",
        "ai_closer": "observe",
        "greeting_opener": "observe",
        "hedge_stack": "observe",
        "corporate_idiom": "observe",
        "oversized_list": "observe",
        "weighted_slop_marker": "observe",
        "formulaic_opener": "observe",
        "formulaic_filler": "observe",
        "low_sentence_variance": "observe",
        "uniform_paragraph_endings": "observe",
        "de_uniform_rhythm": "off",
        "de_uniform_paragraphs": "off",
        "de_repeated_connector": "observe",
        "three_item_list": "judged",
        "choppy_sentences": "judged",
        "de_parataxis": "judged",
        "throat_clearing_opener": "enforce",
        "emphasis_crutch": "observe",
        "meta_commentary": "enforce",
        "telling_not_showing": "observe",
        "vague_declarative": "observe",
        "performative_emphasis": "observe",
        "business_jargon": "enforce",
        "filler_phrase": "observe",
        "binary_contrast": "enforce",
        "negative_listing": "enforce",
        "dramatic_fragmentation": "enforce",
        "formulaic_construction": "enforce",
        "false_agency": "observe",
        "narrator_distance": "enforce",
        "rhetorical_setup": "observe",
        "lazy_extreme": "enforce",
        "ste_phrasal_verb": "observe",
        "ste_marketing_adjective": "observe",
        "ste_nominalization": "enforce",
        "ste_compound_tense": "observe",
        "ste_sentence_length": "observe",
        "ste_synonym_rotation": "observe",
        "ste_dangling_conjunction": "observe",
        "passive_voice": "enforce",
        "banned_adverb": "enforce",
        "weak_sentence_starter": "enforce",
        "file_length_warning": "observe",
        "file_length_critical": "observe",
        "deferred_work_comment": "observe",
        "comment_shape": "observe",
        "agent_marker_author": "enforce",
        "wording_debt_lock": "observe",
        "wording_test_gap": "observe",
        "hard_coded_path": "enforce",
        "hard_coded_system_path": "observe",
        "dash_cluster": "off",
        "quote_marks": "off",
        "genitive_apostrophe": "off",
        "typed_ellipsis": "off",
        **test_rules.default_gates(test_rules.RULE_SETS),
        **german_rules.default_gates(german_rules.RULE_SETS),
    },
    "kill_switches": {},
    "data_boundary": {"enabled": False},
    "tests": "deny",
    "prose_languages": list(prose_language.LANGUAGES),
}
TESTS_POLICIES = ("allow", "deny")


class RuleCalibration(NamedTuple):
    corpus: str
    sample_size: int
    true_positive: int
    precision: float | None
    sample_kind: str


RULE_CALIBRATIONS: dict[str, RuleCalibration] = {
    "weighted_slop_marker": RuleCalibration("AI Generated Essays Dataset.csv", 40, 2, 1.0000, "held-out"),
    "formulaic_opener": RuleCalibration("AI Generated Essays Dataset.csv", 40, 1, 1.0000, "held-out"),
    "formulaic_filler": RuleCalibration("AI Generated Essays Dataset.csv", 40, 1, 1.0000, "held-out"),
    "low_sentence_variance": RuleCalibration("local markdown p05 of 709 paragraphs", 709, 0, 0.0, "unmeasurable"),
    "three_item_list": RuleCalibration("benchmark_patterns.jsonl", 121, 90, None, JUDGED_STATE),
    "choppy_sentences": RuleCalibration("parataxis_sample.jsonl", 10, 5, 0.5, JUDGED_STATE),
    "de_parataxis": RuleCalibration("parataxis_sample.jsonl", 8, 5, 0.625, JUDGED_STATE),
}


CONFIG_NAME = ".agent-discipline.json"
MAX_PROJECT_CONFIG_BYTES = 256 * 1024
MAX_PROJECT_CONFIG_DEPTH = 32


@dataclass(frozen=True, slots=True)
class StorageRoots:
    state: str | os.PathLike[str] | None
    ledger: str | os.PathLike[str] | None


@dataclass(frozen=True, slots=True)
class ProjectConfig:

    path: Path
    data: dict[str, object]
    settings: dict[str, object]
    digest: str | None
    exists: bool


class ConfigLoadError(ValueError):
    pass


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-standard JSON constant {value}")


def _reject_duplicate_keys(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _check_json_depth(raw: bytes) -> None:
    stack: list[int] = []
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                in_string = False
            continue
        if byte == 34:
            in_string = True
        elif byte in (91, 123):
            stack.append(byte)
            if len(stack) > MAX_PROJECT_CONFIG_DEPTH:
                raise ConfigLoadError("project config nesting exceeds the limit")
        elif byte in (93, 125):
            if not stack or (byte == 93 and stack[-1] != 91) or (byte == 125 and stack[-1] != 123):
                raise ConfigLoadError("project config has mismatched delimiters")
            stack.pop()


def _read_project_bytes(path: Path) -> bytes | None:
    descriptor = -1
    try:
        if path.stat().st_size > MAX_PROJECT_CONFIG_BYTES:
            raise ConfigLoadError("project config exceeds the size limit")
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigLoadError("project config is not a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(MAX_PROJECT_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigLoadError(f"project config is unreadable: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > MAX_PROJECT_CONFIG_BYTES:
        raise ConfigLoadError("project config exceeds the size limit")
    return raw


def _validate_legacy_family_values(fields: dict[str, object]) -> None:
    namespaces = [fields]
    checks = fields.get("checks")
    if operator.is_(type(checks), dict):
        namespaces.append(checks)
    for namespace in namespaces:
        for family in GATE_FAMILIES:
            if family in namespace and not operator.is_(type(namespace[family]), bool):
                raise ConfigLoadError(f"legacy family {family} must be a JSON boolean")


def _validate_tests_policy(fields: dict[str, object]) -> None:
    if "tests" in fields and fields["tests"] not in TESTS_POLICIES:
        raise ConfigLoadError("tests must be allow or deny")


def _validate_structured_policy(settings: dict[str, object]) -> None:
    try:
        from .configure_policy import MAPPING_MERGE_KEYS, known_values
    except ImportError:
        from configure_policy import MAPPING_MERGE_KEYS, known_values
    keys = MAPPING_MERGE_KEYS | {"exempt_paths", CONTRACT_STRINGS_KEY}
    structured = {key: value for key, value in settings.items() if key in keys}
    try:
        known_values(structured)
    except ValueError as exc:
        raise ConfigLoadError(str(exc)) from exc


def _parse_project_config(path: Path, raw: bytes) -> ProjectConfig:
    _check_json_depth(raw)
    try:
        text = raw.decode("utf-8")
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ConfigLoadError(f"project config is not valid JSON: {exc}") from exc
    if not operator.is_(type(data), dict):
        raise ConfigLoadError("project config must contain a JSON object")
    fields = exact_string_dict(data)
    _validate_legacy_family_values(fields)
    _validate_tests_policy(fields)
    settings = flatten_settings(fields)
    _validate_structured_policy(settings)
    return ProjectConfig(path, fields, settings, hashlib.sha256(raw).hexdigest(), True)


def load_project_config(path: str | os.PathLike[str]) -> ProjectConfig:
    target = Path(path)
    raw = _read_project_bytes(target)
    if raw is None:
        return ProjectConfig(target, {}, {}, None, False)
    return _parse_project_config(target, raw)


def _safe_settings(data: object) -> dict[str, object]:
    if data is not None and not operator.is_(type(data), dict):
        raise ValueError("configuration must be a mapping")
    settings = flatten_settings(data)
    for family in GATE_FAMILIES:
        if family in settings and not operator.is_(type(settings[family]), bool):
            settings.pop(family)
    return settings

def flatten_settings(data: object) -> dict:
    fields = exact_string_dict(data)
    settings = exact_string_dict(fields.get("checks"))
    settings.update({key: value for key, value in fields.items() if key != "checks"})
    return settings


def contract_strings(settings: dict) -> frozenset[str]:
    values = settings.get(CONTRACT_STRINGS_KEY)
    if type(values) is list and all(type(value) is str for value in values):
        return frozenset(values)
    return frozenset()


def _project_settings(cwd: str | os.PathLike[str]) -> dict:
    path = _find_project_config(Path(cwd))
    try:
        return load_project_config(path).settings
    except (OSError, ValueError, TypeError) as exc:
        sys.stderr.write(f"agent-discipline-watcher: could not read project config at {path}: {exc}\n")
        return {}


NESTED_SETTING_MAPS = frozenset({"rule_gates", "gates", "kill_switches", "exempt_families", "data_boundary"})
CALLER_OWNED_MAPS = frozenset({"data_boundary"})


def _merge_settings_keeping_nested_defaults(merged: dict, overrides: dict, nested: frozenset[str]) -> None:
    for key, value in overrides.items():
        base = merged.get(key)
        if key in nested and operator.is_(type(base), dict) and operator.is_(type(value), dict):
            merged[key] = {**base, **copy.deepcopy(value)}
        else:
            merged[key] = value


def effective_config(config: dict | None = None, cwd: str | os.PathLike[str] | None = None) -> dict:
    merged = copy.deepcopy(DEFAULTS)
    if cwd is not None:
        _merge_settings_keeping_nested_defaults(merged, _project_settings(cwd), NESTED_SETTING_MAPS)
    if config is not None:
        _merge_settings_keeping_nested_defaults(
            merged, _safe_settings(config), NESTED_SETTING_MAPS - CALLER_OWNED_MAPS,
        )
    return merged


def effective_hook_config(config: object, cwd: str | os.PathLike[str] | None) -> dict:
    caller = exact_string_dict(config)
    scan_config = {key: value for key, value in caller.items() if key not in {"state_root", "ledger_root"}}
    merged = effective_config(scan_config, cwd)
    for key in ("state_root", "ledger_root"):
        value = caller.get(key)
        merged[key] = value if isinstance(value, str) else None
    return merged


def payload_hook_config(config: object, payload: object) -> dict:
    merged = effective_hook_config(config, payload_cwd(payload) or None)
    merged["session_id"] = payload_session_id(payload)
    if merged["session_id"] and payload_cwd(payload):
        _note_linked_config(Path(payload_cwd(payload)), merged)
    return merged


LINKED_CONFIG_RULE = "linked_config_ignored"
LINKED_CONFIG_KEY = "linked_configs"


def _note_linked_config(cwd: Path, merged: dict) -> None:
    try:
        skipped = _locate_project_config(cwd)[1]
        if skipped is None:
            return
        reporting, session_state = _ledger_modules()
        fresh: list[str] = []

        def remember(state: dict) -> dict:
            noted = state.get(LINKED_CONFIG_KEY, [])
            if str(skipped) in noted:
                return state
            fresh.append(str(skipped))
            return {**state, LINKED_CONFIG_KEY: [*noted, str(skipped)][-32:]}

        session_state.update_state(merged["session_id"], remember, merged.get("state_root"))
        for path in fresh:
            reporting.append_row({
                "ts": reporting.now_iso(), "session_id": merged["session_id"], "hook": "config", "event": "load",
                "family": "self_protection", "rule": LINKED_CONFIG_RULE, "path": path, "outcome": "release",
            }, merged.get("ledger_root"))
    except (OSError, ValueError, TypeError):
        return


def _project_markers() -> frozenset[str]:
    try:
        from .shell_snapshot import PROJECT_MARKERS
    except ImportError:
        from shell_snapshot import PROJECT_MARKERS
    return PROJECT_MARKERS


def _link_project(folder: Path) -> Path | None:
    home = Path.home().resolve()
    markers = _project_markers()
    for directory in (folder, *folder.parents):
        if directory == home or directory == directory.parent:
            return None
        if (directory / ".git").exists() or any((directory / marker).is_file() for marker in markers):
            return directory
    return None


def _link_escape(logical: Path) -> tuple[Path, Path] | None:
    for link in reversed((logical, *logical.parents)):
        if not link.is_symlink():
            continue
        container = link.parent.resolve()
        root = _link_project(container)
        if root is not None and not link.resolve().is_relative_to(root):
            return link.resolve(), container
    return None


def _first_config(folders) -> Path | None:
    return next((parent / CONFIG_NAME for parent in folders if (parent / CONFIG_NAME).exists()), None)


def _locate_project_config(cwd: Path) -> tuple[Path, Path | None]:
    logical = Path(os.path.abspath(cwd))
    current = logical.resolve()
    if current.is_file():
        current, logical = current.parent, logical.parent
    found = _first_config((current, *current.parents))
    escape = _link_escape(logical)
    if escape is None:
        return found or current / CONFIG_NAME, None
    container = escape[1]
    chosen = _first_config((container, *container.parents))
    return chosen or container / CONFIG_NAME, (found if found is not None and found != chosen else None)


def _find_project_config(cwd: Path) -> Path:
    return _locate_project_config(cwd)[0]


def project_config_path(cwd: str | os.PathLike[str]) -> Path:
    return _find_project_config(Path(cwd))


def gate_map(cfg: dict, key: str) -> dict:
    return exact_string_dict(cfg.get(key))


def family_enabled(cfg: dict, family: str) -> bool:
    return all(cfg.get(name, True) for name in families.scope(family))


def _gate_state_from(cfg: dict, family: str) -> str:
    names = families.scope(family)
    switches = gate_map(cfg, "kill_switches")
    if any(switches.get(name) is True for name in names):
        return "off"
    gates = gate_map(cfg, "gates")
    for name in names:
        if gates.get(name) in GATE_STATES:
            return gates[name]
    return "enforce" if family_enabled(cfg, family) else "off"


def gate_state(family: str, config: dict | None = None) -> str:
    return _gate_state_from(effective_config(config), family)


def _surface_state(gate: dict, surface: str | None) -> str | None:
    chosen = gate.get(surface or SURFACE_PROSE)
    if chosen is None:
        chosen = gate.get(SURFACE_ALL)
    return chosen if chosen in RULE_GATE_STATES else None


def _rule_state_from(cfg: dict, rule: str, surface: str | None = None) -> str | None:
    if not rule:
        return None
    gate = gate_map(cfg, "rule_gates").get(rule)
    if isinstance(gate, dict):
        return _surface_state(exact_string_dict(gate), surface)
    return gate if gate in RULE_GATE_STATES else None


def rule_state(rule: str, config: dict | None = None, surface: str | None = None) -> str | None:
    return _rule_state_from(effective_config(config), rule, surface)


def calibration_detail(rule: str) -> str | None:
    calibration = RULE_CALIBRATIONS.get(rule)
    if calibration is None:
        return None
    precision = "unmeasured" if calibration.precision is None else f"{calibration.precision:.4f}"
    return (
        "Calibration: "
        f"{precision} precision, {calibration.true_positive} true positives, "
        f"{calibration.corpus}, n={calibration.sample_size}, {calibration.sample_kind}."
    )


def calibrated_findings(findings: list[dict]) -> list[dict]:
    calibrated: list[dict] = []
    for finding in findings:
        detail = calibration_detail(finding["rule"])
        calibrated.append(finding if detail is None else {**finding, "detail": finding["detail"] + " " + detail})
    return calibrated


def _outcome_for(state: str) -> Outcome:
    if state == "enforce":
        return Outcome.BLOCK
    return Outcome.WOULD_BLOCK if state == "observe" else Outcome.RELEASE


def resolve_outcome(finding: dict, config: dict | None = None) -> Outcome:
    rule = finding.get("rule", "") if isinstance(finding, dict) else ""
    if rule in ALWAYS_BLOCKING_RULES:
        return Outcome.BLOCK
    if rule in FIXED_OBSERVE_RULES:
        return Outcome.WOULD_BLOCK
    outcome = _configured_outcome(finding, effective_config(config), rule)
    severity = finding.get("severity") if isinstance(finding, dict) else None
    severity_is_observe = severity == VerdictKind.OBSERVE.value
    return Outcome.WOULD_BLOCK if severity_is_observe and outcome is Outcome.BLOCK else outcome


def _configured_outcome(finding: dict, cfg: dict, rule: str) -> Outcome:
    surface = finding.get("surface") if isinstance(finding, dict) else None
    own = _rule_state_from(cfg, rule, surface if isinstance(surface, str) else None)
    if own is not None:
        return _outcome_for(own)
    family = finding.get("family", "") if isinstance(finding, dict) else ""
    return _outcome_for(_gate_state_from(cfg, family))


def _storage_roots(values: tuple[object, ...], named: dict[str, object]) -> StorageRoots:
    if len(values) == 1 and isinstance(values[0], StorageRoots) and not named:
        return values[0]
    if len(values) > 2:
        raise TypeError("record_state_transitions accepts at most two root values")
    roots: dict[str, object] = dict(zip(("state_root", "ledger_root"), values, strict=False))
    for name, value in named.items():
        if name not in {"state_root", "ledger_root"} or name in roots:
            raise TypeError(f"record_state_transitions got an invalid keyword: {name}")
        roots[name] = value
    state = roots.get("state_root")
    ledger = roots.get("ledger_root")
    if state is not None and not isinstance(state, (str, os.PathLike)):
        raise TypeError("state_root must be a path or None")
    if ledger is not None and not isinstance(ledger, (str, os.PathLike)):
        raise TypeError("ledger_root must be a path or None")
    return StorageRoots(state, ledger)


def record_state_transitions(
    session_id: str,
    config: dict | None,
    *roots: object,
    **named_roots: object,
) -> list[dict]:
    if not session_id:
        return []
    storage = _storage_roots(roots, named_roots)
    try:
        return _record_transitions(session_id, config, storage)
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"agent-discipline-watcher: state-transition log failed: {exc}\n")
        return []


def _ledger_modules() -> tuple[ModuleType, ModuleType]:
    try:
        from . import reporting, session_state
    except ImportError:
        import reporting
        import session_state
    return reporting, session_state


def _record_transitions(
    session_id: str,
    config: dict | None,
    roots: StorageRoots,
) -> list[dict]:
    reporting, session_state = _ledger_modules()
    cfg = effective_config(config)
    current = {family: gate_state(family, cfg) for family in families.LEAVES}
    captured: list[dict] = []

    def diff_and_snapshot(state: dict) -> dict:
        previous = state.get("gate_states") or {}
        captured.extend(_transition_rows(session_id, previous, current))
        return {**state, "gate_states": current}

    session_state.update_state_strict(session_id, diff_and_snapshot, roots.state)
    for row in captured:
        reporting.append_row(row, roots.ledger)
    return captured


def _transition_rows(session_id: str, previous: dict, current: dict) -> list[dict]:
    reporting, _ = _ledger_modules()
    rows: list[dict] = []
    for family in current:
        old = previous.get(family)
        if old is None or old == current[family]:
            continue
        rows.append({
            "ts": reporting.now_iso(),
            "session_id": session_id,
            "hook": "config",
            "event": "state_transition",
            "family": family,
            "outcome": "",
            "from_state": old,
            "to_state": current[family],
        })
    return rows
