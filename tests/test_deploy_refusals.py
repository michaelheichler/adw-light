import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fake_clis import FakeClis
from t2325_fixtures import DEFAULT_REPO, ModuleFixture, github_url, write_suite_stamp

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "tools" / "deploy.sh"
SCRUBBED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CONFIG_DIR", "ADW_STATE_ROOT", "ADW_CODEX_HOOK", "ADW_PYTHON",
                "ADW_ALLOW_PROTECTED_EDIT", "ADW_RELEASE", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
MARKER_ENV = "ADW_DEPLOY_MARKER"
PUBLISH_ENV = "ADW_DEPLOY_PUBLISH_RECORD"
HOOK_HELPERS = ("hooks/merge_hooks.py", "hooks/merge-codex-hooks.py")
OTHER_COMMIT = "0123456789abcdef0123456789abcdef01234567"
RECORDING_PUBLISH = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$ADW_DEPLOY_PUBLISH_RECORD"
exit 1
"""
MARKER_TEST = """import os
import unittest
from pathlib import Path


class DeployRanTests(unittest.TestCase):
    def test_deploy_reached_the_test_run(self):
        Path(os.environ["ADW_DEPLOY_MARKER"]).touch()
"""


class DeployCheckout:
    def __init__(self, register_cleanup):
        scratch = tempfile.TemporaryDirectory()
        register_cleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.home = self.scratch / "home"
        self.tmp = self.scratch / "tmp"
        self.repo = self.scratch / "repo"
        self.marker = self.scratch / "tests-ran"
        self.publish_record = self.scratch / "publish-args"
        self.fakes = FakeClis(self.scratch / "fake-cli")
        for folder in (self.home, self.tmp, self.repo / "tools", self.repo / "tests", self.repo / "hooks"):
            folder.mkdir(parents=True)
        (self.home / ".gitconfig").write_text("[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n", encoding="utf-8")
        shutil.copy2(DEPLOY, self.repo / "tools" / "deploy.sh")
        (self.repo / "tools" / "release_branch.sh").write_text(RECORDING_PUBLISH, encoding="utf-8")
        (self.repo / "VERSION").write_text((ROOT / "VERSION").read_text(encoding="utf-8"), encoding="utf-8")
        (self.repo / "tests" / "test_marker.py").write_text(MARKER_TEST, encoding="utf-8")
        (self.repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        for helper in HOOK_HELPERS:
            (self.repo / helper).write_text("", encoding="utf-8")
        self.git(self.repo, "init", "-q", "-b", "main")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "baseline")
        self.git(self.repo, "remote", "add", "origin", github_url(DEFAULT_REPO))
        self.git(self.fakes.github, "init", "-q", "--bare", str(self.fakes.github_repo(DEFAULT_REPO)))
        self.head = self.git(self.repo, "rev-parse", "HEAD")

    def restore(self):
        self.git(self.repo, "reset", "-q", "--hard")
        self.git(self.repo, "clean", "-q", "-f", "-d", "-x")
        self.marker.unlink(missing_ok=True)
        self.publish_record.unlink(missing_ok=True)
        shutil.rmtree(self.home / ".adw-lite", ignore_errors=True)
        for leftover in self.tmp.iterdir():
            shutil.rmtree(leftover)

    def env(self):
        env = {key: value for key, value in os.environ.items() if key not in SCRUBBED_ENV}
        return {**env, "HOME": str(self.home), "TMPDIR": str(self.tmp), "GIT_CONFIG_NOSYSTEM": "1",
                "PATH": self.fakes.search_path(), MARKER_ENV: str(self.marker), PUBLISH_ENV: str(self.publish_record)}

    def git(self, where, *args):
        return subprocess.run(["git", "-C", str(where), "-c", "user.name=Probe", "-c", "user.email=probe@example.invalid",
                        *args], check=True, capture_output=True, text=True, env=self.env(), timeout=60).stdout.strip()

    def deploy(self, checkout):
        result = self.run_deploy(checkout)
        published = self.publish_record.read_text(encoding="utf-8").split() if self.publish_record.exists() else None
        return result.returncode != 0, self.marker.exists(), published

    def run_deploy(self, checkout):
        return subprocess.run(["bash", str(checkout / "tools" / "deploy.sh")], capture_output=True, text=True,
                                check=False, cwd=str(checkout), env=self.fakes.require_on(self.env()),
                                timeout=300)

    def stamp(self, commit):
        write_suite_stamp(self.home, commit)


CHECKOUT = ModuleFixture(DeployCheckout)


class DeployRefusalCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = CHECKOUT.get()
        cls.scratch, cls.repo = cls.checkout.scratch, cls.checkout.repo

    def setUp(self):
        self.checkout.restore()

    def deploy(self, checkout):
        return self.checkout.deploy(checkout)


class DeployFixtureControlTests(DeployRefusalCase):
    def test_a_stamped_clean_main_checkout_reaches_the_publish_step_without_running_tests(self):
        self.checkout.stamp(self.checkout.head)
        _, tests_ran, published = self.deploy(self.repo)
        self.assertEqual((tests_ran, published is not None), (False, True))


class DeployPublishesStampedCommitTests(DeployRefusalCase):
    def test_deploy_hands_the_stamped_head_commit_to_the_publish_step_with_the_push_remote(self):
        self.checkout.stamp(self.checkout.head)
        self.assertEqual(self.deploy(self.repo)[2],
                         ["--push", "--remote", "origin", "--source-commit", self.checkout.head])


class DeployRefusesMissingStampTests(DeployRefusalCase):
    def test_deploy_without_a_suite_stamp_stops_before_publishing(self):
        self.assertEqual(self.deploy(self.repo), (True, False, None))

    def test_deploy_with_only_a_stamp_for_another_commit_stops_before_publishing(self):
        self.checkout.stamp(OTHER_COMMIT)
        self.assertEqual(self.deploy(self.repo), (True, False, None))


class DeployRefusesLinkedWorktreeTests(DeployRefusalCase):
    def test_deploy_from_a_stamped_linked_worktree_stops_before_publishing(self):
        self.checkout.stamp(self.checkout.head)
        linked = self.scratch / "linked"
        self.checkout.git(self.repo, "worktree", "add", "-q", "-b", "linked", str(linked))
        self.addCleanup(self.checkout.git, self.repo, "branch", "-q", "-D", "linked")
        self.addCleanup(self.checkout.git, self.repo, "worktree", "remove", "--force", str(linked))
        self.assertEqual(self.deploy(linked), (True, False, None))


class DeployRefusesUncommittedChangesTests(DeployRefusalCase):
    def test_a_stamped_checkout_with_an_untracked_file_stops_before_publishing(self):
        self.checkout.stamp(self.checkout.head)
        (self.repo / "scratch-notes.txt").write_text("draft\n", encoding="utf-8")
        self.assertEqual(self.deploy(self.repo), (True, False, None))

    def test_a_stamped_checkout_with_a_modified_tracked_file_stops_before_publishing(self):
        self.checkout.stamp(self.checkout.head)
        with (self.repo / "tests" / "test_marker.py").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        self.assertEqual(self.deploy(self.repo), (True, False, None))



class DeployRefusesHiddenIndexFlagsTests(DeployRefusalCase):
    def flag(self, flag, path):
        self.checkout.stamp(self.checkout.head)
        self.checkout.git(self.repo, "update-index", flag, path)
        self.addCleanup(self.checkout.git, self.repo, "update-index", flag.replace("--", "--no-", 1), path)

    def test_a_skip_worktree_flag_on_the_hook_merge_helper_stops_before_publishing(self):
        self.flag("--skip-worktree", "hooks/merge_hooks.py")
        self.assertEqual(self.deploy(self.repo), (True, False, None))

    def test_an_assume_unchanged_flag_on_the_codex_hook_merge_script_stops_before_publishing(self):
        self.flag("--assume-unchanged", "hooks/merge-codex-hooks.py")
        self.assertEqual(self.deploy(self.repo), (True, False, None))

    def test_a_skip_worktree_flag_on_a_tool_script_stops_before_publishing(self):
        self.flag("--skip-worktree", "tools/release_branch.sh")
        self.assertEqual(self.deploy(self.repo), (True, False, None))


class DeployRefusesIgnoredLoadableFilesTests(DeployRefusalCase):
    def plant(self, path):
        self.checkout.stamp(self.checkout.head)
        planted = self.repo / path
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_bytes(b"\0")
        return path

    def test_an_ignored_bytecode_file_under_tools_stops_before_publishing(self):
        self.plant("tools/__pycache__/release_manifest.cpython-311.pyc")
        self.assertEqual(self.deploy(self.repo), (True, False, None))

    def test_the_refusal_names_the_ignored_bytecode_file_under_tools(self):
        planted = self.plant("tools/__pycache__/release_manifest.cpython-311.pyc")
        self.assertIn(planted, self.checkout.run_deploy(self.repo).stderr)

    def test_an_ignored_bytecode_file_under_hooks_stops_before_publishing(self):
        self.plant("hooks/__pycache__/merge_hooks.cpython-311.pyc")
        self.assertEqual(self.deploy(self.repo), (True, False, None))


if __name__ == "__main__":
    unittest.main()
