import unittest

from t2325_fixtures import CODEX_EVENTS, ROOT, Scratch, all_paths, copy_source_repo, hook_commands

MARKER_TEST = """import unittest


class DeployFixtureTests(unittest.TestCase):
    def test_two_plus_two_is_four(self):
        self.assertEqual(2 + 2, 4)
"""


class DeployCase(unittest.TestCase):
    @staticmethod
    def fixture(register_cleanup):
        scratch = Scratch(register_cleanup)
        repo = scratch.root / "repo"
        copy_source_repo(scratch, repo, {"tests/test_fixture.py": MARKER_TEST}, skip_top=("tests",))
        (scratch.home / "Development").mkdir()
        return scratch, repo

    @staticmethod
    def deploy(scratch, repo, *args, **env):
        return scratch.run(["bash", repo / "tools" / "deploy.sh", *args], cwd=repo, timeout=900, **env).returncode


class DeployArgumentTests(DeployCase):
    def test_an_unknown_argument_exits_non_zero_and_installs_nothing(self):
        scratch, repo = self.fixture(self.addCleanup)
        code = self.deploy(scratch, repo, "--everywhere")
        self.assertEqual((code != 0, all_paths(scratch.config_dir, scratch.codex_home, scratch.tmp)),
                         (True, []))


class LocalDeployTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        cls.code = cls.deploy(cls.scratch, cls.repo)

    def test_deploy_exits_zero(self):
        self.assertEqual(self.code, 0)

    def test_deploy_creates_nothing_under_home_development(self):
        self.assertEqual(all_paths(self.scratch.home / "Development"), [])

    def test_deploy_leaves_no_staging_folder_in_the_temp_dir(self):
        self.assertEqual(all_paths(self.scratch.tmp), [])

    def test_codex_runs_every_hook_from_its_plugin_cache(self):
        cache = self.scratch.codex_home / "plugins" / "cache" / "adw-light" / "adw-light"
        runners = {(event, command.split()[1].startswith(str(cache) + "/"))
                   for event, command in hook_commands(self.scratch.codex_home / "hooks.json")}
        self.assertEqual(runners, {(event, True) for event in CODEX_EVENTS})


if __name__ == "__main__":
    unittest.main()
