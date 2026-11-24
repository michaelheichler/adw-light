import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from t19_language_home import BUNDLE, GRAMMARS, LIBRARY_NEEDED, LIBRARY_READY, VENV


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
DENY = "deny"
TEST_WRITER = "adw-test-writer"
WELL_FORMED_TODO = "TODO: Add CSV export to the report view to let users share results"
VAGUE_TODO = "TODO: fix this"

__all__ = ["DENY", "LIBRARY_NEEDED", "LIBRARY_READY", "TEST_WRITER", "VAGUE_TODO", "WELL_FORMED_TODO", "source", "verdict"]


def source(*lines):
    return "\n".join(lines) + "\n"


def _home(case, exact):
    workspace = TemporaryDirectory()
    case.addCleanup(workspace.cleanup)
    root = Path(workspace.name)
    (root / "repo").mkdir()
    adw = root / "home" / ".adw-lite"
    (adw / "install").mkdir(parents=True)
    (adw / "cache" / "grammars").mkdir(parents=True)
    if exact:
        (adw / "install" / "venv").symlink_to(VENV, target_is_directory=True)
        (adw / "cache" / "grammars" / BUNDLE).symlink_to(GRAMMARS / BUNDLE, target_is_directory=True)
    return root


def verdict(case, relative, content, agent=None, exact=False):
    root = _home(case, exact)
    payload = {
        "session_id": "t17", "cwd": str(root / "repo"), "hook_event_name": "PreToolUse", "tool_name": "Write",
        "tool_input": {"file_path": str(root / "repo" / relative), "content": content},
    }
    payload.update({"agent_type": agent} if agent else {})
    call = subprocess.run(
        [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(root / "state")],
        input=json.dumps(payload), text=True, capture_output=True, timeout=120, check=False,
        env={"HOME": str(root / "home"), "PATH": os.environ.get("PATH", "")},
    )
    response = json.loads(call.stdout) if call.stdout.strip() else {}
    decision = response.get("hookSpecificOutput", {}).get("permissionDecision")
    reports = sorted((root / "state" / "reports").glob("*.json"))
    rows = {(row["rule"], row["line"]) for report in reports for row in json.loads(report.read_text(encoding="utf-8"))}
    return call.returncode, decision, tuple(sorted(rows))
