from __future__ import annotations

import csv
import os
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from comment_rows import EXTENSIONS, Row
from comment_sources import WHY_WHAT_SOURCES

HOME_TREE = Path(__file__).resolve().parents[1]
LABELS_FILE = HOME_TREE / "measure" / "comment" / "labels.tsv"
SHAPE_RULES = frozenset({"comment_shape", "deferred_work_comment"})
WHAT, WHY, NONE = "what", "why", "none"
PASS_ROLES = frozenset({WHY, "directive", "human"})
PRIMARY_LANGUAGE = "java"
ALL = "all"
CHUNK = 64
STATE_ENV = "ADW_STATE_ROOT"
ENGINE: dict[str, object] = {}


def expectations(path: Path = LABELS_FILE) -> dict[tuple[str, str], str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {(record["source"], record["label"]): record["role"] for record in csv.DictReader(handle, delimiter="\t")}


def role(row: Row, table: dict[tuple[str, str], str]) -> str:
    found = {table.get((row.source, part), NONE) for part in row.label.split("+")} - {NONE}
    return found.pop() if len(found) == 1 else NONE


def start_engine(state_root: str) -> None:
    os.environ[STATE_ENV] = state_root
    engine = str(HOME_TREE / "engine")
    if engine not in sys.path:
        sys.path.insert(0, engine)
    from lib import config, scanner
    ENGINE["scan"] = scanner.scan_all
    ENGINE["config"] = config.effective_config(None, cwd=state_root)


def blocked(item: tuple[str, str]) -> bool:
    language, snippet = item
    findings = ENGINE["scan"]("bench/comment" + EXTENSIONS[language], snippet, ENGINE["config"])
    return any(finding.get("rule") in SHAPE_RULES for finding in findings)


def verdicts(rows: list[Row], workers: int) -> list[bool]:
    items = [(row.language, row.snippet) for row in rows]
    with tempfile.TemporaryDirectory() as state:
        if workers <= 1:
            start_engine(state)
            return [blocked(item) for item in items]
        with ProcessPoolExecutor(workers, initializer=start_engine, initargs=(state,)) as pool:
            return list(pool.map(blocked, items, chunksize=CHUNK))


def summary(rows: list[Row], hits: list[bool], table: dict[tuple[str, str], str]) -> dict:
    wants = [role(row, table) for row in rows]
    return {
        "rows": len(rows),
        "java_precision": _precision(rows, hits, wants),
        "wrong_block": _wrong_blocks(rows, hits, wants),
    }


def _precision(rows: list[Row], hits: list[bool], wants: list[str]) -> dict:
    groups: dict[str, Counter] = defaultdict(Counter)
    for row, hit, want in zip(rows, hits, wants, strict=True):
        if row.language == PRIMARY_LANGUAGE and row.source in WHY_WHAT_SOURCES and want in (WHAT, WHY):
            for key in (row.source, ALL):
                groups[key][(want, hit)] += 1
    return {key: _block_counts(counts) for key, counts in sorted(groups.items())}


def _block_counts(counts: Counter) -> dict:
    caught, wrong = counts[(WHAT, True)], counts[(WHY, True)]
    what_rows = caught + counts[(WHAT, False)]
    return {
        "what_rows": what_rows, "why_rows": wrong + counts[(WHY, False)],
        "blocked_what": caught, "blocked_why": wrong,
        "precision": rate(caught, caught + wrong), "recall": rate(caught, what_rows),
    }


def _wrong_blocks(rows: list[Row], hits: list[bool], wants: list[str]) -> dict:
    groups: dict[str, Counter] = defaultdict(Counter)
    for row, hit, want in zip(rows, hits, wants, strict=True):
        if want in PASS_ROLES:
            groups[row.language][hit] += 1
    return {
        language: {"pass_rows": counts[True] + counts[False], "blocked": counts[True], "rate": rate(counts[True], counts[True] + counts[False])}
        for language, counts in sorted(groups.items())
    }


def rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def report(result: dict) -> str:
    lines = [f"Rows scanned: {result['rows']}", "", "Java blocking precision on the why and what labels"]
    lines.append(f"{'source':<14}{'blocked':>9}{'what':>8}{'why':>8}{'precision':>11}{'recall':>9}")
    for source, numbers in result["java_precision"].items():
        lines.append(
            f"{source:<14}{numbers['blocked_what'] + numbers['blocked_why']:>9}{numbers['blocked_what']:>8}"
            f"{numbers['blocked_why']:>8}{_shown(numbers['precision']):>11}{_shown(numbers['recall']):>9}"
        )
    lines += ["", "Wrong-block rate per language on rows that should pass"]
    lines.append(f"{'language':<14}{'pass rows':>11}{'blocked':>9}{'rate':>9}")
    for language, numbers in result["wrong_block"].items():
        lines.append(f"{language:<14}{numbers['pass_rows']:>11}{numbers['blocked']:>9}{_shown(numbers['rate']):>9}")
    return "\n".join(lines)


def _shown(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"
