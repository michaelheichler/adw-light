import json
import unittest

from t12_layer_fixtures import layer_backups
from t12_wheelhouse import make_wheelhouse
from t2325_fixtures import Scratch, all_paths
from test_t2325_install_layers import INSTALL, InstallCase

PASS_MARK = "smoke probe passed"
LAYER_MARK = "tree-sitter"
GRAMMAR = "go"


class SetupInstall:
    def __init__(self, register_cleanup, release, **env):
        self.scratch = Scratch(register_cleanup)
        result = self.scratch.run(["bash", INSTALL, "--claude", "--codex", release], timeout=900, **env)
        self.code = result.returncode
        self.pass_lines = [line for line in result.stdout.splitlines() if PASS_MARK in line]
        self.install = self.scratch.home / ".adw-lite" / "install"

    def record(self):
        return json.loads((self.install / "setup.json").read_text(encoding="utf-8"))

    def tree_sitter_passes(self):
        return [LAYER_MARK in line for line in self.pass_lines]


class WorkingSetupInstallTests(InstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        wheelhouse = make_wheelhouse(cls.release.scratch.root)
        cls.installed = SetupInstall(cls.addClassCleanup, cls.release.path, PIP_FIND_LINKS=str(wheelhouse))

    def test_install_exits_zero(self):
        self.assertEqual(self.installed.code, 0)

    def test_setup_is_ready_with_the_smoke_grammar_and_an_environment_python(self):
        record = self.installed.record()
        self.assertEqual((record["status"], GRAMMAR in record["grammars"],
                          (self.installed.install / "venv" / "bin" / "python").exists()), ("ready", True, True))

    def test_only_the_smoke_test_before_setup_runs_on_the_lexer(self):
        self.assertEqual(self.installed.tree_sitter_passes(), [False, True, True, True])

    def test_install_leaves_the_temp_dir_empty(self):
        self.assertEqual(all_paths(self.installed.scratch.tmp), [])

    def test_install_leaves_no_layer_backup_folder(self):
        self.assertEqual(layer_backups(self.installed.scratch.home), [])


class FailingSetupInstallTests(InstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.installed = SetupInstall(cls.addClassCleanup, cls.release.path)

    def test_install_still_exits_zero(self):
        self.assertEqual(self.installed.code, 0)

    def test_setup_is_recorded_as_failed_and_leaves_no_environment(self):
        self.assertEqual((self.installed.record()["status"], (self.installed.install / "venv").exists()), ("failed", False))

    def test_both_plugin_caches_are_installed(self):
        claude = self.installed.scratch.config_dir / "plugins" / "cache" / "adw-light" / "adw-light" / self.release.version
        self.assertEqual((self.claude_install_path(self.installed.scratch),
                          (self.codex_cache_root(self.installed.scratch) / self.release.version / "hooks" / "run.sh").is_file()),
                         ([str(claude)], True))

    def test_every_smoke_test_runs_on_the_lexer(self):
        self.assertEqual(self.installed.tree_sitter_passes(), [False, False, False, False])


if __name__ == "__main__":
    unittest.main()
