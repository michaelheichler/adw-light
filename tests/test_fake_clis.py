import os
import unittest

from fake_clis import RealCliReachable
from t2325_fixtures import DEFAULT_REPO, Scratch, github_url

SSH_HOST = "deploy@target.example"


def scratch_with_marker(case):
    scratch = Scratch(case.addCleanup)
    return scratch, scratch.root / "command-ran"


class FakeCliGuardTests(unittest.TestCase):
    def test_a_missing_fake_codex_stops_the_run_before_the_command_starts(self):
        scratch, marker = scratch_with_marker(self)
        (scratch.fakes.bin / "codex").unlink()
        with self.assertRaises(RealCliReachable):
            scratch.run(["touch", marker])
        self.assertFalse(marker.exists())

    def test_a_missing_fake_ssh_stops_the_run_before_the_command_starts(self):
        scratch, marker = scratch_with_marker(self)
        (scratch.fakes.bin / "ssh").unlink()
        with self.assertRaises(RealCliReachable):
            scratch.run(["touch", marker])
        self.assertFalse(marker.exists())

    def test_a_missing_fake_git_stops_the_run_before_the_command_starts(self):
        scratch, marker = scratch_with_marker(self)
        (scratch.fakes.bin / "git").unlink()
        with self.assertRaises(RealCliReachable):
            scratch.run(["touch", marker])
        self.assertFalse(marker.exists())

    def test_a_path_override_that_drops_the_fakes_stops_the_run_before_the_command_starts(self):
        scratch, marker = scratch_with_marker(self)
        with self.assertRaises(RealCliReachable):
            scratch.run(["touch", marker], PATH=os.environ["PATH"])
        self.assertFalse(marker.exists())

    def test_the_fake_claude_records_its_argv_and_lists_an_empty_plugin_table(self):
        scratch = Scratch(self.addCleanup)
        listed = scratch.run(["claude", "plugin", "list", "--json"])
        self.assertEqual((listed.returncode, listed.stdout.strip(), scratch.fakes.calls("claude")),
                         (0, "[]", [["claude", "plugin", "list", "--json"]]))


class StandInGithubTests(unittest.TestCase):
    def test_git_sends_a_github_url_to_the_stand_in_folder(self):
        scratch = Scratch(self.addCleanup)
        resolved = scratch.run(["git", "ls-remote", "--get-url", github_url(DEFAULT_REPO)])
        self.assertEqual(resolved.stdout.strip(), "file://" + str(scratch.fakes.github_repo(DEFAULT_REPO)))

    def test_the_fake_claude_refuses_a_marketplace_tag_the_stand_in_does_not_hold(self):
        scratch = Scratch(self.addCleanup)
        added = scratch.run(["claude", "plugin", "marketplace", "add", DEFAULT_REPO + "#v0.1.2", "--scope", "user"])
        listed = scratch.run(["claude", "plugin", "marketplace", "list", "--json"])
        self.assertEqual((added.returncode != 0, listed.stdout.strip()), (True, "[]"))

    def test_the_fake_codex_refuses_a_marketplace_ref_the_stand_in_does_not_hold(self):
        scratch = Scratch(self.addCleanup)
        added = scratch.run(["codex", "plugin", "marketplace", "add", DEFAULT_REPO, "--ref", "v0.1.2", "--json"])
        self.assertEqual((added.returncode != 0, scratch.fakes.calls("codex")),
                         (True, [["codex", "plugin", "marketplace", "add", DEFAULT_REPO, "--ref", "v0.1.2", "--json"]]))


class FakeSshTests(unittest.TestCase):
    def test_the_fake_ssh_records_its_argv_and_runs_the_command_in_the_remote_scratch_home(self):
        scratch = Scratch(self.addCleanup)
        ran = scratch.run(["ssh", "-o", "BatchMode=yes", SSH_HOST, "pwd"])
        self.assertEqual((ran.stdout.strip(), scratch.fakes.calls("ssh")),
                         (str(scratch.fakes.remote_home), [["ssh", "-o", "BatchMode=yes", SSH_HOST, "pwd"]]))

    def test_the_fake_ssh_passes_no_host_folder_setting_to_the_remote_command(self):
        scratch = Scratch(self.addCleanup)
        ran = scratch.run(["ssh", SSH_HOST, 'printf %s "${CLAUDE_CONFIG_DIR:-none}:${CODEX_HOME:-none}:$HOME"'])
        self.assertEqual(ran.stdout, "none:none:" + str(scratch.fakes.remote_home))

    def test_a_planned_ssh_failure_exits_non_zero_without_running_the_command(self):
        scratch, marker = scratch_with_marker(self)
        scratch.fakes.fail_on("ssh")
        ran = scratch.run(["ssh", SSH_HOST, "touch " + str(marker)])
        self.assertEqual((ran.returncode != 0, marker.exists()), (True, False))


if __name__ == "__main__":
    unittest.main()
