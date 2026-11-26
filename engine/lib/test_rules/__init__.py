from __future__ import annotations

import ast
import functools
import importlib
import pkgutil
from collections.abc import Callable, Iterable
from pathlib import PurePath
from typing import NamedTuple

try:
    from ..findings import Finding
    from ..test_units import Unit, extract
except ImportError:
    from findings import Finding
    from test_units import Unit, extract

FAMILY = "code"
UNMEASURED_STATE = "observe"
EXPORT = "RULE_SET"


class Rule(NamedTuple):
    name: str
    detail: str
    action: str
    state: str = UNMEASURED_STATE
    spans_test: bool = False


class Hit(NamedTuple):
    rule: str
    line: int
    snippet: str
    location: str | None = None


class RuleSet(NamedTuple):
    rules: tuple[Rule, ...]
    check: Callable[[Unit, str], Iterable[Hit]]


@functools.lru_cache(maxsize=32)
def module_tree(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


@functools.lru_cache(maxsize=32)
def module_scopes(tree: ast.Module) -> dict[tuple[int, int], ast.FunctionDef | ast.AsyncFunctionDef]:
    scopes = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.setdefault((node.lineno, node.end_lineno), node)
    return scopes


@functools.lru_cache(maxsize=32)
def enclosing_classes(tree: ast.Module) -> dict[int, ast.ClassDef]:
    owners = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owners.setdefault(child.lineno, node)
    return owners


def _exported(package: str, name: str) -> RuleSet:
    rule_set = getattr(importlib.import_module(package + "." + name), EXPORT, None)
    if not isinstance(rule_set, RuleSet):
        raise TypeError(f"Code Check module {name} must export {EXPORT} as a RuleSet")
    return rule_set


def discover(paths: Iterable[str], package: str) -> tuple[RuleSet, ...]:
    found = tuple(
        _exported(package, module.name)
        for module in pkgutil.iter_modules(list(paths))
        if not module.name.startswith("_")
    )
    names = [rule.name for rule_set in found for rule in rule_set.rules]
    if len(names) != len(set(names)):
        raise ValueError("two Code Check modules declare the same rule")
    return found


RULE_SETS: tuple[RuleSet, ...] = discover(__path__, __name__)


def default_gates(rule_sets: Iterable[RuleSet]) -> dict[str, str]:
    return {rule.name: rule.state for rule_set in rule_sets for rule in rule_set.rules}


def _finding(unit: Unit, rule: Rule, hit: Hit) -> dict:
    return Finding(
        family=FAMILY, rule=rule.name, line=hit.line,
        detail=rule.detail + " in " + unit.name + " in " + unit.path, force=False,
        snippet=hit.snippet.strip()[:180] or unit.name, action=rule.action,
        path=None, severity=None, tool_use_id=None,
        end_line=max(unit.end, hit.line) if rule.spans_test else None, match=hit.location,
    ).to_dict()


def _set_findings(rule_set: RuleSet, unit: Unit, text: str) -> list[dict]:
    declared = {rule.name: rule for rule in rule_set.rules}
    rows: list[dict] = []
    for hit in rule_set.check(unit, text):
        if hit.rule not in declared:
            raise ValueError("Code Check hit names an undeclared rule: " + hit.rule)
        rows.append(_finding(unit, declared[hit.rule], hit))
    return rows


def unit_findings(unit: Unit, text: str, rule_sets: Iterable[RuleSet] | None = None) -> list[dict]:
    chosen = RULE_SETS if rule_sets is None else rule_sets
    return [row for rule_set in chosen for row in _set_findings(rule_set, unit, text)]


def check_file(path: str, text: str, rule_sets: Iterable[RuleSet] | None = None) -> list[dict]:
    chosen = RULE_SETS if rule_sets is None else tuple(rule_sets)
    if not chosen:
        return []
    from . import repo_building

    file_rows = repo_building.file_rows(path, text) if repo_building.RULE_SET in chosen else []
    chosen = tuple(group for group in chosen if group is not repo_building.RULE_SET)
    layer_units, merged_findings = _layer_tools()
    layered = layer_units(path, text)
    lexed = None if layered is not None and not layered.broken else extract(path, text)
    if layered is None:
        return file_rows + _unit_rows(lexed, text, chosen)
    rows = file_rows + _unit_rows(layered.units, text, chosen)
    if layered.broken and not layered.units and not lexed:
        return rows + _broken_file_rows(path, text, chosen)
    return merged_findings(rows, _unit_rows(lexed, text, chosen)) if layered.broken else rows


def _broken_file_rows(path: str, text: str, chosen: tuple[RuleSet, ...]) -> list[dict]:
    from . import principle_citation

    if principle_citation.RULE_SET not in chosen:
        return []
    whole = _whole_file_unit(path, text)
    heads = principle_citation.broken_heads(whole, text)
    return _unit_rows(heads or (whole,), text, (principle_citation.RULE_SET,))


def _whole_file_unit(path: str, text: str) -> Unit:
    return Unit(path, PurePath(path).name, 1, max(1, len(text.splitlines())), text, PurePath(path).suffix.lstrip("."))


def verified_citation_lines(path: str, text: str) -> set[int]:
    from . import principle_citation

    layer_units, _ = _layer_tools()
    layered = layer_units(path, text)
    if layered is not None and layered.broken and not layered.units and not extract(path, text):
        whole = _whole_file_unit(path, text)
        comments = principle_citation.comment_citations(whole, text)
        heads = principle_citation.broken_heads(whole, text)
        return {unit.start - 1 for unit in heads if unit.start - 1 in comments}
    units = layered.units if layered is not None else extract(path, text)
    return {line for unit in units if (line := principle_citation._citation(unit, text))}


def file_units(path: str, text: str) -> list[Unit]:
    layer_units, _ = _layer_tools()
    layered = layer_units(path, text)
    return list(layered.units) if layered is not None else extract(path, text)


def _layer_tools():
    try:
        from ..language_units import layer_units
        from ..language_views import merged_findings
    except ImportError:
        from language_units import layer_units
        from language_views import merged_findings
    return layer_units, merged_findings


def _unit_rows(units: Iterable[Unit], text: str, chosen: tuple[RuleSet, ...]) -> list[dict]:
    return [row for unit in units for row in unit_findings(unit, text, chosen)]
