import json
import unittest

from t12_layer_fixtures import layer_folder, stage_like_remote
from t12_wheelhouse import make_wheelhouse
from t2325_fixtures import ROOT, Scratch
from test_t2325_install_layers import InstallCase

SMOKE_GRAMMAR = "go"


class RemoteStageInstallTests(InstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scratch = Scratch(cls.addClassCleanup)
        install = layer_folder(cls.scratch.home)
        installer, release = stage_like_remote(cls.release.path, install / ".stage.Probe42", ROOT / "tools")
        wheelhouse = make_wheelhouse(cls.scratch.root)
        result = cls.scratch.run(["bash", installer, "--claude", "--codex", release], timeout=900,
                                 PIP_FIND_LINKS=str(wheelhouse))
        cls.code = result.returncode
        record = install / "setup.json"
        cls.record = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}

    def test_install_from_the_stage_exits_zero(self):
        self.assertEqual(self.code, 0)

    def test_install_from_the_stage_records_a_ready_setup_with_the_smoke_grammar(self):
        self.assertEqual((self.record.get("status"), SMOKE_GRAMMAR in self.record.get("grammars", [])), ("ready", True))


if __name__ == "__main__":
    unittest.main()
