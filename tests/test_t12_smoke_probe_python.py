import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from t12_layer_fixtures import layer_folder

TREE = Path(__file__).resolve().parents[1]
PROBE = TREE / "tools" / "smoke_probe.py"
HOOK_EVENTS = {"PostToolUse", "PostToolBatch", "PreToolUse", "Stop"}
LAYER = "layer"
RECORDING_ENTRY = """import json
import os
import sys

with open(os.environ["PROBE_CALLS"], "a", encoding="utf-8") as log:
    log.write(json.dumps([sys.argv[1], os.environ.get("PROBE_INTERPRETER", sys.executable)]) + "\\n")
print("{}")
"""
LAYER_PYTHON = '#!/bin/sh\nPROBE_INTERPRETER={layer} exec "{python}" "$@"\n'


class ProbeRun:
    def __init__(self, register_cleanup, prepare):
        holder = tempfile.TemporaryDirectory()
        register_cleanup(holder.cleanup)
        scratch = Path(holder.name).resolve()
        home, root, calls = scratch / "home", scratch / "release", scratch / "calls.jsonl"
        home.mkdir()
        root.mkdir()
        (root / "adw_light.py").write_text(RECORDING_ENTRY, encoding="utf-8")
        calls.write_text("", encoding="utf-8")
        prepare(home)
        env = {key: value for key, value in os.environ.items() if not key.startswith(("ADW_", "CLAUDE"))}
        subprocess.run([sys.executable, str(PROBE), str(root)], capture_output=True, text=True, check=False,
                       timeout=600, env={**env, "HOME": str(home), "PROBE_CALLS": str(calls)})
        rows = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
        self.events = {event for event, _ in rows}
        self.interpreters = {interpreter for _, interpreter in rows}


def install_layer_python(home):
    python = layer_folder(home) / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(LAYER_PYTHON.format(layer=LAYER, python=sys.executable), encoding="utf-8")
    python.chmod(0o755)


def leave_home_empty(home):
    return home


class InstalledLayerProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = ProbeRun(cls.addClassCleanup, install_layer_python)

    def test_every_hook_case_runs_on_the_environment_python(self):
        self.assertEqual((self.probe.events, self.probe.interpreters), (HOOK_EVENTS, {LAYER}))


class NoLayerProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = ProbeRun(cls.addClassCleanup, leave_home_empty)

    def test_every_hook_case_runs_on_the_python_that_runs_the_probe(self):
        self.assertEqual((self.probe.events, self.probe.interpreters), (HOOK_EVENTS, {sys.executable}))


if __name__ == "__main__":
    unittest.main()
