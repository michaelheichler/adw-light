from __future__ import annotations

import argparse
import ast
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

HOME_TREE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOME_TREE / "engine"))
from lib.test_rules._prose_words import is_prose

MARK = "XX"
KEPT_HIDDEN = frozenset({".claude-plugin"})
FAILURE_LINE = re.compile(r"^(?:FAIL|ERROR): (.+)$", re.MULTILINE)
RAN_LINE = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)
OPENING = re.compile(r"^[A-Za-z]*(\"\"\"|'''|\"|')")
TRIPLES = ('"""', "'''")
NO_RESULT = "timeout"
KIND_CHOICES = ("wrap", "split", "both")


@dataclass(frozen=True, slots=True)
class Mutant:
    path: str
    line: int
    start: int
    end: int
    original: str
    replacement: bytes
    kind: str


@dataclass(frozen=True, slots=True)
class Settings:
    tests_dir: str
    pattern: str
    timeout: int


@dataclass(frozen=True, slots=True)
class Outcome:
    failed: frozenset[str]
    ran: int


def safe_text(mutated: str, expected: str) -> bool:
    try:
        return ast.literal_eval("(" + mutated + ")") == expected
    except (ValueError, SyntaxError):
        return False


def wrap_literal(segment: str, value: str) -> str | None:
    opening = OPENING.match(segment)
    if opening is None:
        return None
    closing = segment[-3:] if segment[-3:] in TRIPLES else segment[-1:]
    tail = len(segment) - len(closing)
    if opening.end() > tail:
        return None
    wrapped = segment[: opening.end()] + MARK + segment[opening.end() : tail] + MARK + closing
    return wrapped if safe_text(wrapped, MARK + value + MARK) else None


def split_literal(segment: str, value: str) -> str | None:
    framed = "(" + segment + ")"
    starts = [0]
    for line in framed.split("\n"):
        starts.append(starts[-1] + len(line) + 1)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(framed).readline))
    except (tokenize.TokenError, SyntaxError):
        return None
    pieces, cursor = [], 0
    for token in (token for token in tokens if token.type == tokenize.STRING):
        begin = starts[token.start[0] - 1] + token.start[1]
        pieces.extend((framed[cursor:begin], token.string.replace(" ", " " + MARK + " ")))
        cursor = begin + len(token.string)
    split = ("".join(pieces) + framed[cursor:])[1:-1]
    return split if split != segment and safe_text(split, value.replace(" ", " " + MARK + " ")) else None


MUTATIONS = {"wrap": wrap_literal, "split": split_literal}


def prose_nodes(tree: ast.AST) -> list[ast.Constant]:
    skipped = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
    for joined in (node for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)):
        skipped.update(id(part) for part in ast.walk(joined))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped and is_prose(node.value)
    ]


def line_starts(data: bytes) -> list[int]:
    starts = [0]
    for line in data.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def mutants_in(path: Path, relative: str, kinds: tuple[str, ...]) -> list[Mutant]:
    data = path.read_bytes()
    try:
        tree = ast.parse(data)
        encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0]
    except (SyntaxError, ValueError):
        return []
    if encoding != "utf-8":
        return []
    starts = line_starts(data)
    found = []
    for node in prose_nodes(tree):
        begin = starts[node.lineno - 1] + node.col_offset
        end = starts[node.end_lineno - 1] + node.end_col_offset
        try:
            segment = data[begin:end].decode("utf-8")
        except UnicodeDecodeError:
            continue
        for kind in kinds:
            mutated = MUTATIONS[kind](segment, node.value)
            if mutated is not None:
                found.append(Mutant(relative, node.lineno, begin, end, node.value, mutated.encode("utf-8"), kind))
    return sorted(found, key=lambda mutant: (mutant.start, mutant.kind))


def is_test_file(relative: Path, tests_dir: str) -> bool:
    name = relative.name
    root = Path(tests_dir).parts
    in_tests = relative.parts[: len(root)] == root or "fixtures" in relative.parts
    return in_tests or name.startswith("test_") or name.endswith("_test.py")


def in_scope(relative: Path, scope: str) -> bool:
    wanted = Path(scope).parts
    return bool(wanted) and relative.parts[: len(wanted)] == wanted


def source_files(project: Path, settings: Settings, scopes: list[str]) -> list[Path]:
    found = []
    for directory, names, files in os.walk(project):
        names[:] = sorted(name for name in names if not name.startswith(".") and name != "__pycache__")
        for name in sorted(files):
            relative = (Path(directory) / name).relative_to(project)
            wanted = not scopes or any(in_scope(relative, scope) for scope in scopes)
            if name.endswith(".py") and wanted and not is_test_file(relative, settings.tests_dir):
                found.append(relative)
    return found


def collect_mutants(project: Path, settings: Settings, scopes: list[str], kinds: tuple[str, ...]) -> list[Mutant]:
    return [
        mutant
        for relative in source_files(project, settings, scopes)
        for mutant in mutants_in(project / relative, relative.as_posix(), kinds)
    ]


def skip_in_copy(directory: str, names: list[str]) -> list[str]:
    skipped = []
    for name in names:
        hidden = name.startswith(".") and name not in KEPT_HIDDEN and os.path.isdir(os.path.join(directory, name))
        if hidden or name in {"__pycache__", ".git"}:
            skipped.append(name)
    return skipped


def run_suite(root: Path, settings: Settings) -> Outcome | None:
    command = [sys.executable, "-m", "unittest", "discover", "-s", settings.tests_dir, "-p", settings.pattern]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        done = subprocess.run(command, cwd=root, capture_output=True, text=True, env=env, timeout=settings.timeout)
    except subprocess.TimeoutExpired:
        return None
    ran = RAN_LINE.search(done.stderr)
    return Outcome(frozenset(FAILURE_LINE.findall(done.stderr)), int(ran.group(1)) if ran else 0)


def try_mutant(root: Path, mutant: Mutant, baseline: frozenset[str], settings: Settings) -> dict:
    target = root / mutant.path
    original = target.read_bytes()
    target.write_bytes(original[: mutant.start] + mutant.replacement + original[mutant.end :])
    try:
        outcome = run_suite(root, settings)
    finally:
        target.write_bytes(original)
    killers = [] if outcome is None else sorted(outcome.failed - baseline)
    status = NO_RESULT if outcome is None else ("killed" if killers else "survived")
    return {"path": mutant.path, "line": mutant.line, "kind": mutant.kind, "original": mutant.original, "status": status, "killers": killers}


def audit(mutants: list[Mutant], baseline: frozenset[str], settings: Settings, copies: list[Path]) -> list[dict]:
    idle: queue.Queue[Path] = queue.Queue()
    for copy in copies:
        idle.put(copy)

    def one(mutant: Mutant) -> dict:
        root = idle.get()
        try:
            return try_mutant(root, mutant, baseline, settings)
        finally:
            idle.put(root)

    with ThreadPoolExecutor(len(copies)) as workers:
        return list(workers.map(one, mutants))


def dying_tests(results: list[dict]) -> dict[str, list[str]]:
    dying: dict[str, list[str]] = defaultdict(list)
    for result in results:
        for test in result["killers"]:
            dying[test].append(f"{result['path']}:{result['line']}")
    return dict(sorted(dying.items()))


def render(report: dict) -> str:
    lines = [f"Baseline failures: {len(report['baseline_failures'])}", f"Mutants run: {len(report['results'])}"]
    for test, places in report["dying_tests"].items():
        lines.append(f"{test} dies on {len(places)} mutants: {', '.join(places[:5])}")
    survived = [r for r in report["results"] if r["status"] == "survived"]
    lines.append(f"Survived: {len(survived)}")
    lines.extend(f"  {r['path']}:{r['line']} {r['kind']}" for r in survived)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wrap prose strings in XX and name the tests that fail only on those changes.")
    parser.add_argument("--project", type=Path, default=HOME_TREE)
    parser.add_argument("--tests-dir", default="tests")
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--kind", choices=KIND_CHOICES, default="both")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--max-mutants", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def listing(mutants: list[Mutant]) -> str:
    return "\n".join(f"{mutant.path}:{mutant.line} {mutant.kind} {mutant.original}" for mutant in mutants)


def run_audit(project: Path, mutants: list[Mutant], settings: Settings, jobs: int) -> dict | None:
    with tempfile.TemporaryDirectory(prefix="adw-mutation-") as work:
        copies = [Path(work) / f"copy{number}" for number in range(max(1, jobs))]
        for copy in copies:
            shutil.copytree(project, copy, ignore=skip_in_copy, symlinks=True)
        baseline = run_suite(copies[0], settings)
        if baseline is None or baseline.ran == 0:
            return None
        results = audit(mutants, baseline.failed, settings, copies)
    return {"baseline_failures": sorted(baseline.failed), "results": results, "dying_tests": dying_tests(results)}


def main() -> int:
    args = build_parser().parse_args()
    project = args.project.resolve()
    if not project.is_dir():
        build_parser().error("The project folder does not exist.")
    settings = Settings(args.tests_dir, args.pattern, args.timeout)
    kinds = tuple(MUTATIONS) if args.kind == "both" else (args.kind,)
    mutants = collect_mutants(project, settings, args.source, kinds)
    mutants = mutants[: args.max_mutants] if args.max_mutants > 0 else mutants
    if args.list:
        print(listing(mutants))
        return 0
    report = run_audit(project, mutants, settings, args.jobs)
    if report is None:
        print("The unmodified copy ran no tests or timed out.", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
