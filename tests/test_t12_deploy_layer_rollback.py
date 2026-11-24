import os
import unittest

from t12_layer_fixtures import layer_folder, layer_state, seed_working_layer
from t12_wheelhouse import make_wheelhouse
from t2325_fixtures import ROOT
from test_t2325_deploy import DeployCase

CLAUDE_STUB = """#!/bin/sh
case "$*" in
  "plugin marketplace list --json"|"plugin list --json") echo '[]'; exit 0 ;;
esac
exit 1
"""


class DeployLayerRollbackTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        wheelhouse = make_wheelhouse(cls.scratch.root)
        seed_working_layer(cls.scratch, wheelhouse, ROOT)
        cls.before = layer_state(cls.scratch.home)
        stubs = cls.scratch.root / "stub-bin"
        stubs.mkdir()
        (stubs / "claude").write_text(CLAUDE_STUB, encoding="utf-8")
        (stubs / "claude").chmod(0o755)
        result = cls.scratch.run(["bash", cls.repo / "tools" / "deploy.sh"], cwd=cls.repo, timeout=900,
                                 PATH=str(stubs) + os.pathsep + os.environ["PATH"], PIP_FIND_LINKS=str(wheelhouse))
        cls.code = result.returncode
        cls.after = layer_state(cls.scratch.home)
        cls.install = layer_folder(cls.scratch.home)

    def test_deploy_exits_non_zero_when_the_claude_install_fails(self):
        self.assertNotEqual(self.code, 0)

    def test_setup_ran_during_the_failed_install(self):
        self.assertTrue((self.install / "setup.log").is_file())

    def test_environment_and_setup_record_return_to_their_pre_deploy_content(self):
        self.assertEqual(self.after, self.before)

    def test_the_placed_release_source_is_removed_again(self):
        self.assertFalse((self.install / "source").exists())


if __name__ == "__main__":
    unittest.main()
