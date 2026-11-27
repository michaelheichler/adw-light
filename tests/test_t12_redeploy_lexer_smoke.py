import json
import shutil
import unittest
from pathlib import Path

from t12_layer_fixtures import layer_folder, seed_working_layer
from t12_wheelhouse import FAKE_PACK, PACK, build_wheel, make_wheelhouse
from t2325_fixtures import ROOT, all_paths
from test_t2325_install_layers import SMALL_PROBE_RELEASE, InstallCase

RECORDING_PROBE = """import json
import os
import sys
from pathlib import Path

row = {"isolated": sys.prefix == sys.base_prefix, "executable": sys.executable, "home": os.environ["HOME"]}
with open(os.environ["SMOKE_RECORD"], "a", encoding="utf-8") as log:
    log.write(json.dumps(row) + "\\n")
sys.exit(1 if (Path.home() / ".adw-lite").exists() else 0)
"""
LOGGED_DOWNLOAD = """

_LOG = {log!r}
_unlogged_download = download


def download(languages):
    with open(_LOG, "a", encoding="utf-8") as log:
        log.write(str(_state["cache"].resolve()) + "\\n")
    _unlogged_download(languages)
"""


def build_user_venv(scratch):
    venv = layer_folder(scratch.home) / "venv"
    result = scratch.run(["python3", "-m", "venv", "--without-pip", venv], timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    return venv / "bin" / "python"


def stage_recording_tools(scratch, installer):
    tools = scratch.root / "staged" / "tools"
    tools.mkdir(parents=True)
    shutil.copy2(installer, tools / "install_layers.sh")
    (tools / "smoke_probe.py").write_text(RECORDING_PROBE, encoding="utf-8")
    return tools / "install_layers.sh"


def logging_wheelhouse(scratch, log):
    wheelhouse = make_wheelhouse(scratch.root)
    build_wheel(wheelhouse, PACK, {"tree_sitter_language_pack/__init__.py": FAKE_PACK + LOGGED_DOWNLOAD.format(log=str(log))})
    return wheelhouse


def smoke_homes(tmp):
    return sorted(path.name for path in tmp.glob("adw-smoke-home.*"))


class RecordedRedeploySmokeTests(InstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scratch = cls.fresh_scratch()
        cls.venv_python = build_user_venv(cls.scratch)
        record = cls.scratch.root / "smoke-record.jsonl"
        installer = stage_recording_tools(cls.scratch, cls.release.installer)
        cls.code = cls.scratch.run(["bash", installer, "--claude", "--codex", "--tag", cls.release.tag], timeout=900,
                                   SMOKE_RECORD=str(record)).returncode
        cls.runs = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]

    def test_the_lexer_smoke_runs_on_a_python_outside_any_environment(self):
        self.assertEqual((self.runs[0]["isolated"], self.runs[0]["executable"] != str(self.venv_python)), (True, True))

    def test_the_lexer_smoke_runs_under_a_throwaway_home(self):
        self.assertNotEqual(Path(self.runs[0]["home"]).resolve(), self.scratch.home)

    def test_the_install_goes_on_to_the_release_smoke_under_the_user_home(self):
        self.assertEqual((len(self.runs), Path(self.runs[1]["home"]).resolve()), (2, self.scratch.home))

    def test_the_throwaway_home_is_removed(self):
        self.assertEqual(smoke_homes(self.scratch.tmp), [])


class WorkingLayerRedeployTests(InstallCase):
    release_fixture = SMALL_PROBE_RELEASE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scratch = cls.fresh_scratch()
        log = cls.scratch.root / "grammar-downloads.log"
        wheelhouse = logging_wheelhouse(cls.scratch, log)
        seed_working_layer(cls.scratch, wheelhouse, ROOT)
        result = cls.scratch.run(["bash", cls.release.installer, "--claude", "--codex", "--tag", cls.release.tag],
                                 timeout=900,
                                 PIP_FIND_LINKS=str(wheelhouse))
        cls.code = result.returncode
        user_layer = cls.scratch.home / ".adw-lite"
        caches = [Path(line) for line in log.read_text(encoding="utf-8").splitlines()]
        cls.user_caches = [cache for cache in caches if cache.is_relative_to(user_layer)]
        cls.foreign_caches = [str(cache) for cache in caches if not cache.is_relative_to(user_layer)]

    def test_redeploy_over_a_working_layer_exits_zero(self):
        self.assertEqual(self.code, 0)

    def test_no_grammar_is_fetched_outside_the_user_layer(self):
        self.assertEqual((len(self.user_caches) > 0, self.foreign_caches), (True, []))

    def test_redeploy_leaves_the_temp_dir_empty(self):
        self.assertEqual(all_paths(self.scratch.tmp), [])


if __name__ == "__main__":
    unittest.main()
