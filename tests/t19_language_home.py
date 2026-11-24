import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
PACK = "tree_sitter_language_pack"
BUNDLE = "tree-sitter-language-pack"
SITE = Path("lib") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
VENV = Path(os.environ.get("ADW_TEST_LANGUAGE_VENV") or Path.home() / ".adw-lite" / "install" / "venv")
GRAMMARS = Path(os.environ.get("ADW_TEST_GRAMMAR_CACHE") or Path.home() / ".adw-lite" / "cache" / "grammars")
LIBRARY_READY = (VENV / SITE / PACK).is_dir() and (GRAMMARS / BUNDLE).is_dir()
LIBRARY_NEEDED = "needs the pinned tree-sitter venv and a filled grammar cache (ADW_TEST_LANGUAGE_VENV, ADW_TEST_GRAMMAR_CACHE)"
DENY = "deny"
TEST_WRITER = "adw-test-writer"


class UnitHome:
    def __init__(self, case, exact):
        workspace = TemporaryDirectory()
        case.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.repo.mkdir()
        adw = self.home / ".adw-lite"
        (adw / "install").mkdir(parents=True)
        (adw / "cache" / "grammars").mkdir(parents=True)
        if exact:
            (adw / "install" / "venv").symlink_to(VENV, target_is_directory=True)
            (adw / "cache" / "grammars" / BUNDLE).symlink_to(GRAMMARS / BUNDLE, target_is_directory=True)

    def write(self, relative, content):
        payload = {
            "session_id": "t19", "cwd": str(self.repo), "hook_event_name": "PreToolUse", "tool_name": "Write",
            "agent_type": TEST_WRITER, "tool_input": {"file_path": str(self.repo / relative), "content": content},
        }
        return subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True, timeout=120,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )

    def verdict(self, relative, content):
        call = self.write(relative, content)
        decision = json.loads(call.stdout).get("hookSpecificOutput", {}).get("permissionDecision")
        return call.returncode, decision, self.rule_lines()

    def rule_lines(self):
        rows = [row for report in sorted((self.state / "reports").glob("*.json"))
                for row in json.loads(report.read_text(encoding="utf-8"))]
        return tuple(sorted({(row["rule"], row["line"]) for row in rows}))


def exact_verdict(case, relative, content):
    return UnitHome(case, exact=True).verdict(relative, content)


def fallback_verdict(case, relative, content):
    return UnitHome(case, exact=False).verdict(relative, content)


def blocked(*rows):
    return 0, DENY, tuple(rows)


ALLOWED = (0, None, ())
