from quiet_git import disable_background_maintenance
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
SHEBANG = "#!/bin/bash\n"
PROSE_COMMIT = 'git commit -m "We utilize this."\n'
CLEAN_LIB = "echo lib\n"
MESSAGE_LOCATION = "commit_message.md:1 "
UTILIZE_TAG = "(utilize)"
SCANNED = (True, True)
HIDDEN_REFERENCE = "xargs " + PROSE_COMMIT
DEEP_CHAIN = 40


def trusting_only(repo):
    return {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": str(repo)}


class CommitGateHome(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.home = Path(workspace.name) / "home"
        self.root = self.home / "repo"
        self.state = Path(workspace.name) / "state"
        self.root.mkdir(parents=True)
        self.trusted_env = trusting_only(self.root.resolve())
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True, env=self.trusted_env)
        disable_background_maintenance(self.root, self.trusted_env)

    def place(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def gate(self, command):
        payload = {"cwd": str(self.root), "session_id": "commit-gate-misreads", "hook_event_name": "PreToolUse",
                   "tool_name": "Bash", "tool_input": {"command": command}}
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True, check=True,
            cwd=self.root, env={**self.trusted_env, "HOME": str(self.home)},
        )
        return json.loads(call.stdout)

    def run_script(self, body, *arguments):
        self.place("s.sh", SHEBANG + body)
        return self.gate(" ".join(["bash", "./s.sh", *arguments]))

    def deny_reason(self, response):
        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])
        return response["hookSpecificOutput"]["permissionDecisionReason"]

    def message_finding(self, response):
        reason = self.deny_reason(response)
        return MESSAGE_LOCATION in reason, UTILIZE_TAG in reason

    def refusal_of_script(self, name, body):
        self.place(name, SHEBANG + body)
        return self.deny_reason(self.gate(f"bash ./{name}"))

    def deep_nesting_refusal(self):
        for level in range(1, DEEP_CHAIN):
            self.place(f"deep{level}.sh", f"{SHEBANG}bash ./deep{level + 1}.sh\n")
        self.place(f"deep{DEEP_CHAIN}.sh", SHEBANG + CLEAN_LIB)
        return self.deny_reason(self.gate("bash ./deep1.sh"))
