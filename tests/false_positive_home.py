from quiet_git import disable_background_maintenance
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
DENY = "deny"
GATE = "test_write_gate"
LOCK = "wording_debt_lock"
PASSED = (None, [])
REFUSED = (DENY, [GATE])
OLD_TEST = "tests/test_old.py"
SIZE_TEST = "def test_size():\n    assert len([1, 2]) == 2\n"
WORDING_TEST = "def test_msg():\n    assert render() == " + json.dumps("The file could not be opened today.") + "\n"
DEBT_FILE = SIZE_TEST + "\n\n" + WORDING_TEST
PLAIN_MODULE = "def helper():\n    return 1\n"
IDENTITY = ("-c", "user.name=Probe", "-c", "user.email=probe@example.invalid")


class FalsePositiveHome(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.home.mkdir()

    def committed_repo(self, files):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        disable_background_maintenance(str(repo))
        self.place(repo, files)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), *IDENTITY, "commit", "-qm", "base"], check=True)
        subprocess.run(["git", "-C", str(repo), "branch", "other"], check=True)
        self.hook("SessionStart", {"cwd": str(repo), "source": "startup"})
        return repo

    def place(self, folder, files):
        for relative, content in files.items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def commit_on_other(self, repo, files):
        git = ["git", "-C", str(repo)]
        subprocess.run([*git, "checkout", "-q", "other"], check=True)
        self.place(repo, files)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, *IDENTITY, "commit", "-qm", "other"], check=True)
        subprocess.run([*git, "checkout", "-q", "-"], check=True)
        return repo

    def reports(self):
        return {report: json.loads(report.read_text(encoding="utf-8")) for report in (self.state / "reports").glob("*.json")}

    def hook(self, event, payload):
        before = self.reports()
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps({"session_id": "false-positive", "hook_event_name": event, **payload}),
            text=True, capture_output=True, check=True,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        rows = [row for report, batch in self.reports().items() for row in batch[len(before.get(report, [])):]]
        decision = json.loads(call.stdout).get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, sorted({row["rule"] for row in rows})

    def bash(self, repo, command):
        return self.hook("PreToolUse", {"cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": command}})

    def write(self, cwd, path, content):
        tool_input = {"file_path": str(path), "content": content}
        return self.hook("PreToolUse", {"cwd": str(cwd), "tool_name": "Write", "tool_input": tool_input})
