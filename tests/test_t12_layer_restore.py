import unittest

from t12_layer_fixtures import layer_backups, layer_folder, layer_state, seed_layer
from t12_wheelhouse import make_wheelhouse
from test_t2325_install_layers import SMALL_PROBE_RELEASE, InstallCase


def leave_empty(home):
    return home


class UnreachableGrammarInstall:
    def __init__(self, register_cleanup, release, prepare):
        self.scratch = release.host(register_cleanup)
        home = self.scratch.home
        prepare(home)
        self.before = layer_state(home)
        wheelhouse = make_wheelhouse(self.scratch.root, reachable=False)
        result = self.scratch.run(["bash", release.installer, "--claude", "--codex", "--tag", release.tag], timeout=900,
                                  PIP_FIND_LINKS=str(wheelhouse))
        self.code = result.returncode
        self.after = layer_state(home)
        self.install = layer_folder(home)
        self.backups = layer_backups(home)
        self.codex_calls = self.scratch.fakes.calls("codex")


class SeededLayerSmokeFailureTests(InstallCase):
    release_fixture = SMALL_PROBE_RELEASE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.outcome = UnreachableGrammarInstall(cls.addClassCleanup, cls.release, seed_layer)

    def test_install_exits_non_zero(self):
        self.assertNotEqual(self.outcome.code, 0)

    def test_setup_ran_before_the_smoke_test_failed(self):
        self.assertTrue((self.outcome.install / "setup.log").is_file())

    def test_environment_and_setup_record_match_the_seed_byte_for_byte(self):
        self.assertEqual(self.outcome.after, self.outcome.before)

    def test_the_failed_claude_smoke_stops_the_install_before_codex(self):
        self.assertEqual(self.outcome.codex_calls, [])

    def test_no_layer_backup_folder_remains(self):
        self.assertEqual(self.outcome.backups, [])


class FreshLayerSmokeFailureTests(InstallCase):
    release_fixture = SMALL_PROBE_RELEASE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.outcome = UnreachableGrammarInstall(cls.addClassCleanup, cls.release, leave_empty)

    def test_install_exits_non_zero(self):
        self.assertNotEqual(self.outcome.code, 0)

    def test_neither_an_environment_nor_a_setup_record_is_left(self):
        self.assertEqual(((self.outcome.install / "venv").exists(), (self.outcome.install / "setup.json").exists()),
                         (False, False))

    def test_the_failed_claude_smoke_stops_the_install_before_codex(self):
        self.assertEqual(self.outcome.codex_calls, [])

    def test_no_layer_backup_folder_remains(self):
        self.assertEqual(self.outcome.backups, [])


if __name__ == "__main__":
    unittest.main()
