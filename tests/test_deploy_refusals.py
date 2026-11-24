import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "tools" / "deploy.sh"
SCRUBBED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CONFIG_DIR", "ADW_STATE_ROOT", "ADW_CODEX_HOOK", "ADW_PYTHON",
                "ADW_ALLOW_PROTECTED_EDIT", "ADW_RELEASE", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
MARKER_ENV = "ADW_DEPLOY_MARKER"
MARKER_TEST = """import os
import unittest
from pathlib import Path


class DeployRanTests(unittest.TestCase):
    def test_deploy_reached_the_test_run(self):
        Path(os.environ["ADW_DEPLOY_MARKER"]).touch()
"""


class DeployRefusalCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.home = self.scratch / "home"
        self.tmp = self.scratch / "tmp"
        self.repo = self.scratch / "repo"
        self.marker = self.scratch / "tests-ran"
        for folder in (self.home, self.tmp, self.repo / "tools", self.repo / "tests"):
            folder.mkdir(parents=True)
        shutil.copy2(DEPLOY, self.repo / "tools" / "deploy.sh")
        (self.repo / "tests" / "test_marker.py").write_text(MARKER_TEST, encoding="utf-8")
        self.git(self.repo, "init", "-q", "-b", "main")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "baseline")

    def env(self):
        env = {key: value for key, value in os.environ.items() if key not in SCRUBBED_ENV}
        return {**env, "HOME": str(self.home), "TMPDIR": str(self.tmp), "GIT_CONFIG_NOSYSTEM": "1",
                MARKER_ENV: str(self.marker)}

    def git(self, where, *args):
        subprocess.run(["git", "-C", str(where), "-c", "user.name=Probe", "-c", "user.email=probe@example.invalid",
                        *args], check=True, capture_output=True, text=True, env=self.env(), timeout=60)

    def deploy(self, checkout):
        result = subprocess.run(["bash", str(checkout / "tools" / "deploy.sh")], capture_output=True, text=True,
                                check=False, cwd=str(checkout), env=self.env(), timeout=300)
        return result.returncode != 0, self.marker.exists(), sorted(path.name for path in self.tmp.glob("adw-deploy.*"))


class DeployFixtureControlTests(DeployRefusalCase):
    def test_clean_main_checkout_reaches_the_test_run(self):
        self.assertEqual(self.deploy(self.repo), (True, True, []))


class DeployRefusesLinkedWorktreeTests(DeployRefusalCase):
    def test_deploy_from_a_linked_worktree_stops_before_the_test_run(self):
        linked = self.scratch / "linked"
        self.git(self.repo, "worktree", "add", "-q", "-b", "linked", str(linked))
        self.assertEqual(self.deploy(linked), (True, False, []))


class DeployRefusesUncommittedChangesTests(DeployRefusalCase):
    def test_deploy_with_an_untracked_file_stops_before_the_test_run(self):
        (self.repo / "scratch-notes.txt").write_text("draft\n", encoding="utf-8")
        self.assertEqual(self.deploy(self.repo), (True, False, []))

    def test_deploy_with_a_modified_tracked_file_stops_before_the_test_run(self):
        with (self.repo / "tests" / "test_marker.py").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        self.assertEqual(self.deploy(self.repo), (True, False, []))


if __name__ == "__main__":
    unittest.main()
