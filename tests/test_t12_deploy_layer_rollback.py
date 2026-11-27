import unittest

from t12_layer_fixtures import layer_folder, layer_state, seed_working_layer
from t12_wheelhouse import make_wheelhouse
from t2325_fixtures import DEFAULT_REPO, PLUGIN_ID, ROOT, installed_plugins, marketplaces, source_version
from test_t2325_deploy import DeployCase

ROLLBACK_CALLS = [["claude", "plugin", "uninstall", PLUGIN_ID, "--scope", "user"],
                  ["claude", "plugin", "marketplace", "remove", "adw-light"]]
CODEX_CALLS = [["codex", "plugin", "marketplace", "list", "--json"],
               ["codex", "plugin", "marketplace", "list", "--json"],
               ["codex", "plugin", "marketplace", "add", DEFAULT_REPO, "--ref", "v{version}", "--json"],
               ["codex", "plugin", "remove", PLUGIN_ID], ["codex", "plugin", "marketplace", "remove", "adw-light"]]


class DeployLayerRollbackTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        wheelhouse = make_wheelhouse(cls.scratch.root)
        seed_working_layer(cls.scratch, wheelhouse, ROOT)
        cls.before = layer_state(cls.scratch.home)
        cls.scratch.fakes.fail_on("codex", "plugin", "marketplace", "add")
        result = cls.scratch.run(["bash", cls.repo / "tools" / "deploy.sh"], cwd=cls.repo, timeout=900,
                                 PIP_FIND_LINKS=str(wheelhouse))
        cls.code = result.returncode
        cls.after = layer_state(cls.scratch.home)
        cls.install = layer_folder(cls.scratch.home)
        cls.claude_calls = cls.scratch.fakes.calls("claude")

    def test_deploy_exits_non_zero_when_the_codex_install_fails(self):
        self.assertNotEqual(self.code, 0)

    def test_setup_ran_during_the_failed_install(self):
        self.assertTrue((self.install / "setup.log").is_file())

    def test_environment_and_setup_record_return_to_their_pre_deploy_content(self):
        self.assertEqual(self.after, self.before)

    def test_with_no_earlier_marketplace_the_rollback_uninstalls_the_plugin_and_removes_the_marketplace(self):
        codex = [[word.format(version=source_version(self.repo)) for word in call] for call in CODEX_CALLS]
        self.assertEqual((self.claude_calls[-2:], self.scratch.fakes.calls("codex")), (ROLLBACK_CALLS, codex))

    def test_the_rollback_leaves_no_claude_plugin_and_no_claude_marketplace(self):
        self.assertEqual((installed_plugins(self.scratch, "claude"), marketplaces(self.scratch, "claude")), ({}, {}))


if __name__ == "__main__":
    unittest.main()
