from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BIG_IMAGE = "A" * 2_500_000
LANGUAGE_PARTS = (("install", "venv"), ("cache", "grammars"))
CODE_HEAD = "package main\n\nvar usage = `\n// raw string text\n`\n"
BIG_CODE = CODE_HEAD + "func f() {}\n" * 210_000
SMALL_CODE = CODE_HEAD + "func f() {}\n"
HOOK_SECONDS = 10
TEST_WRITER = "adw-test-writer"
JEST_CASE = (
    "test('case {n}', () => {{\n"
    "  const label = 'item-{n}';\n"
    "  expect(format(label)).toBe('item-{n}');\n"
    "}});\n\n"
)
BIG_TEST = "".join(JEST_CASE.format(n=n) for n in range(3_500))
SIZE_BUDGET = "size_budget"
RUN_SECONDS = 60
STOP_SECONDS = 8
CASES = (
    ("PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "pwd"},
                     "tool_response": {"stdout": BIG_IMAGE}}),
    ("PostToolBatch", {"tool_calls": [{"tool_name": "Read", "tool_input": {"file_path": "/tmp/shot.png"},
                                       "tool_response": BIG_IMAGE}]}),
    ("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}),
    ("Stop", {"last_assistant_message": "Done. The tests pass.", "stop_hook_active": False}),
)


def state_dir(home: Path) -> Path:
    return home / ".adw-lite" / "state"


def run_case(root: Path, home: Path, event: str, body: dict, *, tracked: str = "", python: str = "") -> str | None:
    project = home / "project"
    project.mkdir(exist_ok=True)
    payload = {"session_id": "smoke-probe", "cwd": str(project), "hook_event_name": event, **body}
    limit = STOP_SECONDS if event == "Stop" else RUN_SECONDS
    try:
        result = subprocess.run(
            [python or sys.executable, "-I", str(root / "adw_light.py"), event, "--host", "claude",
             "--state-dir", str(state_dir(home))],
            input=json.dumps(payload), capture_output=True, text=True, timeout=limit,
            env={**os.environ, "HOME": str(home)},
        )
    except subprocess.TimeoutExpired:
        return f"{event} ran past the {limit} s probe limit"
    if result.returncode != 0:
        return f"{event} exit {result.returncode}: {result.stderr.strip()[:300]}"
    try:
        response = json.loads(result.stdout)
    except ValueError:
        return f"{event} printed non-JSON: {result.stdout[:200]}"
    if not isinstance(response, dict):
        return f"{event} did not return an object"
    if event in {"PostToolUse", "PostToolBatch"} and response.get("decision") == "block":
        return f"{event} returned a block"
    if tracked and (response.get("decision") != "block" or tracked not in response.get("reason", "")):
        return f"Stop did not report {tracked}: {response}"
    return None


def tracked_stop(root: Path, home: Path, python: str) -> str | None:
    path = home / "project" / "tracked.md"
    path.write_text("Short text.\n", encoding="utf-8")
    write = {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "Short text.\n"}}
    problem = run_case(root, home, "PostToolUse", write, python=python)
    if problem:
        return problem
    path.write_bytes(b"x\n" * 1_100_000)
    return run_case(root, home, "Stop", {}, tracked=path.name, python=python)


def installed_layer() -> bool:
    return (Path.home() / ".adw-lite").joinpath(*LANGUAGE_PARTS[0]).is_dir()


def linked_language_layer(home: Path) -> str:
    if not installed_layer():
        return ""
    installed = Path.home() / ".adw-lite"
    for parts in LANGUAGE_PARTS:
        source = installed.joinpath(*parts)
        if source.is_dir():
            target = home / ".adw-lite" / Path(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=True)
    environment_python = home.joinpath(".adw-lite", *LANGUAGE_PARTS[0], "bin", "python")
    return str(environment_python) if environment_python.exists() else sys.executable


def fallback_reasons(home: Path, path: Path) -> list[str]:
    ledger = state_dir(home) / "ledger" / "ledger.jsonl"
    if not ledger.is_file():
        return []
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [str(row.get("reason")) for row in rows
            if row.get("event") == "reduced_precision" and Path(str(row.get("path"))).name == path.name]


def timed_write(root: Path, home: Path, path: Path, content: str, agent: dict, python: str = "") -> str | None:
    write = {**agent, "tool_name": "Write", "tool_input": {"file_path": str(path), "content": content}}
    started = time.monotonic()
    problem = run_case(root, home, "PreToolUse", write, python=python)
    elapsed = time.monotonic() - started
    if problem:
        return problem
    if elapsed >= HOOK_SECONDS:
        return f"{path.name} took {elapsed:.1f} s, past the {HOOK_SECONDS} s hook budget"
    return None


def code_write(root: Path, home: Path, name: str, content: str, allowed: set[str] | None,
               python: str = "") -> str | None:
    path = home / "project" / name
    if problem := timed_write(root, home, path, content, {}, python):
        return problem
    if allowed is None:
        return None
    reasons = set(fallback_reasons(home, path)) - allowed
    return f"{name} fell back to the lexer although the pinned layer is installed: {sorted(reasons)}" if reasons else None


def big_code_write(root: Path, home: Path, python: str) -> str | None:
    if not python:
        return code_write(root, home, "big.go", BIG_CODE, None)
    return (code_write(root, home, "big.go", BIG_CODE, {SIZE_BUDGET}, python)
            or code_write(root, home, "small.go", SMALL_CODE, set(), python))


def legacy_test_write(root: Path, home: Path, python: str) -> str | None:
    path = home / "project" / "src" / "__tests__" / "legacy.test.js"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BIG_TEST, encoding="utf-8")
    edited = BIG_TEST.replace("item-0'", "item-zero'", 1)
    return timed_write(root, home, path, edited, {"agent_type": TEST_WRITER}, python)


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as scratch:
        home = Path(scratch)
        python = linked_language_layer(home)
        failures = [problem for event, body in CASES
                    if (problem := run_case(root, home, event, body, python=python))]
        if problem := tracked_stop(root, home, python):
            failures.append(problem)
        if problem := big_code_write(root, home, python):
            failures.append(problem)
        if problem := legacy_test_write(root, home, python):
            failures.append(problem)
        if not (state_dir(home) / "ledger").is_dir():
            failures.append("no ledger under the .adw-lite state folder")
    if failures:
        sys.exit("smoke probe failed:\n" + "\n".join(failures))
    path = "the pinned tree-sitter layer" if installed_layer() else "the lexer fallback"
    print(f"smoke probe passed {len(CASES) + 3} cases on {root}, with code writes on {path}")


if __name__ == "__main__":
    main()
