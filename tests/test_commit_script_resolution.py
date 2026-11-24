from quiet_git import disable_background_maintenance
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
SHEBANG = "#!/bin/bash\n"
PROSE_COMMIT = 'git commit -m "We utilize this."\n'
MESSAGE_LOCATION = "commit_message.md:1 "
UTILIZE_TAG = "(utilize)"
ROOT_FINDER = 'dev=$(cd "$(dirname "$0")/.." && pwd -P)\nbash "$dev/tools/build.sh"\n'
FILLER_LINE = "echo filler\n"


def trusting_only(repo):
    return {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": str(repo)}


def script_with_filler(total_bytes):
    count = (total_bytes - len(SHEBANG) - len(PROSE_COMMIT)) // len(FILLER_LINE)
    return SHEBANG + FILLER_LINE * count + PROSE_COMMIT


class CommitGateHarness(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name) / "repo"
        self.home = Path(workspace.name) / "home"
        self.state = Path(workspace.name) / "state"
        self.root.mkdir()
        self.home.mkdir()
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
        payload = {"cwd": str(self.root), "session_id": "commit-script-resolution", "hook_event_name": "PreToolUse",
                   "tool_name": "Bash", "tool_input": {"command": command}}
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True, check=True,
            cwd=self.root, env={**self.trusted_env, "HOME": str(self.home)},
        )
        return json.loads(call.stdout)

    def timed_gate(self, command):
        start = time.monotonic()
        response = self.gate(command)
        return response, time.monotonic() - start

    def deny_reason(self, response):
        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])
        return response["hookSpecificOutput"]["permissionDecisionReason"]

    def message_finding(self, response):
        reason = self.deny_reason(response)
        return MESSAGE_LOCATION in reason, UTILIZE_TAG in reason


class ScriptsThatFindTheirRoot(CommitGateHarness):
    def test_sibling_script_without_a_commit_passes(self):
        self.place("tools/run.sh", SHEBANG + ROOT_FINDER)
        self.place("tools/build.sh", SHEBANG + "echo built\n")

        self.assertEqual({}, self.gate("bash tools/run.sh"))

    def test_sibling_script_with_a_commit_is_scanned_for_the_message(self):
        self.place("tools/run.sh", SHEBANG + ROOT_FINDER)
        self.place("tools/build.sh", SHEBANG + PROSE_COMMIT)

        self.assertEqual((True, True), self.message_finding(self.gate("bash tools/run.sh")))


class MissingScripts(CommitGateHarness):
    def test_script_unpacked_by_an_untracked_step_is_denied_as_missing(self):
        archive = io.BytesIO()
        body = (SHEBANG + PROSE_COMMIT).encode("utf-8")
        with tarfile.open(fileobj=archive, mode="w") as bundle:
            member = tarfile.TarInfo("t1.sh")
            member.size = len(body)
            member.mode = 0o755
            bundle.addfile(member, io.BytesIO(body))
        (self.root / "t.tar").write_bytes(archive.getvalue())

        self.assertEqual((False, False), self.message_finding(self.gate("tar -xf t.tar && bash t1.sh")))

    def test_existence_check_before_running_a_missing_script_passes(self):
        self.assertEqual({}, self.gate("[ -f ./missing.sh ] && bash ./missing.sh"))

    def test_if_block_guarding_a_missing_script_passes(self):
        self.assertEqual({}, self.gate("if [ -f x ]; then bash x; fi"))

    def test_sourcing_an_absent_rc_file_passes(self):
        self.assertEqual({}, self.gate("source ~/.rc 2>/dev/null"))


class UnreadableScripts(CommitGateHarness):
    def test_script_inside_a_locked_directory_is_denied_as_unreadable(self):
        self.place("locked/run.sh", SHEBANG + PROSE_COMMIT)
        (self.root / "locked").chmod(0o000)
        self.addCleanup((self.root / "locked").chmod, 0o755)

        self.assertEqual((False, False), self.message_finding(self.gate("bash locked/run.sh")))


class LargeScripts(CommitGateHarness):
    def test_script_just_under_the_size_cap_is_scanned_in_time(self):
        self.place("big.sh", script_with_filler(500 * 1024))

        response, seconds = self.timed_gate("bash ./big.sh")

        self.assertEqual((True, True), self.message_finding(response))
        self.assertLess(seconds, 5.0)

    def test_script_over_the_size_cap_is_denied_by_name_without_a_full_parse(self):
        self.place("huge.sh", script_with_filler(5_000_000))

        response, seconds = self.timed_gate("bash ./huge.sh")

        self.assertEqual((False, False), self.message_finding(response))
        self.assertLess(seconds, 2.0)


if __name__ == "__main__":
    unittest.main()
