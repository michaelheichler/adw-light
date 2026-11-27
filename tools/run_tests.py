from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import NamedTuple
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]


def clean_commit() -> str:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()

    return "" if git("status", "--porcelain", "--untracked-files=all") else git("rev-parse", "HEAD")


SHARED_GRAMMAR_MODULES = frozenset((
    "test_gate_false_positives_011.py",
    "test_gate_false_positives_011_k.py",
    "test_qa011_round2_shell.py",
    "test_qa011_scan_bypasses.py",
    "test_t05_wording_debt_refresh.py",
    "test_t11_large_payload.py",
    "test_t11_lexer_defects.py",
    "test_t11_parse_budget.py",
    "test_t11_reduced_precision.py",
    "test_t13_framework_rows.py",
    "test_t13_large_test_edit.py",
    "test_t13_macro_assertions.py",
    "test_t13_ui_locators.py",
    "test_t13_wording_debt_languages.py",
    "test_t13_wording_frameworks.py",
    "test_t13_wording_jest.py",
    "test_t15c_agent_marker_author.py",
    "test_t17_hollow_todo.py",
    "test_t17_stub_todo.py",
    "test_t19_failure_marks.py",
    "test_t19_hollow_tests.py",
    "test_t19_long_functions.py",
    "test_t19_python_unchanged.py",
    "test_t19_test_marks.py",
    "test_t20_java_test_rules.py",
    "test_t20_layer_languages.py",
    "test_t20_python_and_lexer_parity.py",
    "test_t20_script_test_file_names.py",
    "test_t31_citation_markers.py",
    "test_t34_dart_test_units.py",
))


class ModuleResult(NamedTuple):
    name: str
    elapsed: float
    count: int
    failed: bool
    output: str


def run_module(tree: Path, name: str) -> ModuleResult:
    start = monotonic()
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", name, "-v"]
    run = subprocess.run(command, cwd=tree, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = run.stdout.decode(errors="replace")
    counts = re.findall(r"^Ran (\d+) tests? in .+$", output, re.MULTILINE)
    failed = run.returncode != 0 or not counts
    return ModuleResult(name, monotonic() - start, int(counts[-1]) if counts else 0, failed, output if failed else "")


def run_suite(tree: Path, jobs: int, profile: bool) -> tuple[int, int, float]:
    start = monotonic()
    modules = sorted(path.name for path in (tree / "tests").glob("test_*.py"))
    serial = [name for name in modules if name in SHARED_GRAMMAR_MODULES]
    run = partial(run_module, tree)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run, (name for name in modules if name not in SHARED_GRAMMAR_MODULES)))
    print("Serial modules:", ", ".join(serial) or "none", flush=True)
    results.extend(map(run, serial))
    for result in results:
        if result.failed:
            print(result.name, result.output, sep="\n", file=sys.stderr)
    if profile:
        print("Slowest test modules:")
        for result in sorted(results, key=lambda item: item.elapsed, reverse=True):
            print(f"{result.elapsed:.3f}s {result.name}")
    count = sum(result.count for result in results)
    failed = sum(result.failed for result in results)
    print(f"Ran {count} tests in {len(results)} modules ({failed} failed modules).")
    return int(bool(failed)), count, monotonic() - start


def record_pass(commit: str, count: int, elapsed: float) -> None:
    override = os.environ.get("ADW_STATE_ROOT", "").strip()
    state = Path(override).expanduser() if override else Path.home() / ".adw-lite" / "state"
    folder = state / "suite-passes"
    folder.mkdir(parents=True, exist_ok=True)
    record = {"commit": commit, "ran": count, "python_version": platform.python_version(), "wall_seconds": elapsed, "date": datetime.now(UTC).isoformat()}
    with tempfile.NamedTemporaryFile(mode="w", dir=folder, delete=False) as stream:
        json.dump(record, stream)
    Path(stream.name).replace(folder / f"{commit}.json")


@contextmanager
def committed_tree(commit: str):
    with tempfile.TemporaryDirectory() as folder:
        tree = Path(folder) / "source"
        git = ["git", "-C", str(ROOT)]
        subprocess.run([*git, "worktree", "add", "--detach", tree, commit], check=True)
        try:
            names = subprocess.check_output([*git, "ls-tree", "-r", "--name-only", "-z", commit])
            expected = set(os.fsdecode(names).split("\0")[:-1])
            actual = {str(path.relative_to(tree)) for path in tree.rglob("*") if path.is_symlink() or path.is_file()}
            if actual - {".git"} != expected:
                sys.exit("Worktree files differ from the committed tree. No tests ran.")
            yield tree
        finally:
            subprocess.run([*git, "worktree", "remove", "--force", tree], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, 8))
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    commit = clean_commit()
    snapshot = commit or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if not commit:
        print("Testing HEAD, not the uncommitted edits.", flush=True)
    with committed_tree(snapshot) as tree:
        status, count, elapsed = run_suite(tree, args.jobs, args.profile)
    if status:
        return status
    if not count:
        sys.exit("No tests counted. No pass recorded.")
    if commit and clean_commit() == commit:
        record_pass(commit, count, elapsed)
        print(f"Recorded {count} passing tests for {commit} in {elapsed:.2f} seconds.")
    else:
        print("Tests passed without a stamp. The checkout was dirty or changed during the run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
