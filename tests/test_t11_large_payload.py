import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from t11_language_home import LIBRARY_NEEDED, LIBRARY_READY, HookHome
from t2325_fixtures import small_payload_probe


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tools" / "smoke_probe.py"
FALLBACK_REASON = "library_missing"
HOOK_SECONDS = 10
SHORT_HOOK_SECONDS = 1
PRECISE_CHARS = 262_144
FAKE_HOOK = """import json
import sys
import time
from pathlib import Path

state = Path(sys.argv[sys.argv.index("--state-dir") + 1])
payload = json.loads(sys.stdin.read())
(state / "ledger").mkdir(parents=True, exist_ok=True)
target = str(payload.get("tool_input", {}).get("file_path", ""))
time.sleep(DELAY * target.endswith("big.go"))
print(json.dumps({"decision": "block", "reason": "tracked.md"} if sys.argv[1] == "Stop" else {}))
"""
GO_LINE = "func f() {}\n"
BIG_GO = "package main\n\nvar usage = `\nraw string text\n`\n" + GO_LINE * (PRECISE_CHARS // len(GO_LINE) + 1)


def short_budget_probe():
    return small_payload_probe().replace(f"HOOK_SECONDS = {HOOK_SECONDS}\n", f"HOOK_SECONDS = {SHORT_HOOK_SECONDS}\n")


def probe_with_home(home, root=ROOT, probe=None):
    return subprocess.run([sys.executable, "-c", probe or small_payload_probe(), str(root)], capture_output=True, text=True,
                          timeout=600, env={**os.environ, "HOME": str(home)})


def tree_with_big_write_delay(case, seconds):
    workspace = TemporaryDirectory()
    case.addCleanup(workspace.cleanup)
    (Path(workspace.name) / "adw_light.py").write_text(FAKE_HOOK.replace("DELAY", repr(seconds)), encoding="utf-8")
    return Path(workspace.name)


class SmokeProbeChecksTheInstalledLayer(unittest.TestCase):
    def test_probe_passes_when_no_layer_is_installed(self):
        self.assertEqual(probe_with_home(HookHome(self).home).returncode, 0)

    def test_probe_fails_and_names_the_reason_when_the_installed_layer_falls_back(self):
        call = probe_with_home(HookHome(self).empty_library().home)
        self.assertEqual((call.returncode, "big.go" in call.stderr, FALLBACK_REASON in call.stderr), (1, True, True))

    @unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
    def test_probe_passes_when_the_installed_layer_parses_the_go_writes(self):
        home = HookHome(self).link_library().link_grammars().home
        self.assertEqual(probe_with_home(home).returncode, 0)


class SmokeProbeTimesTheGoWrite(unittest.TestCase):
    def test_probe_passes_a_tree_that_answers_the_go_write_at_once(self):
        call = probe_with_home(HookHome(self).home, tree_with_big_write_delay(self, 0))
        self.assertEqual(call.returncode, 0)

    def test_probe_fails_and_names_big_go_when_the_go_write_takes_the_hook_budget(self):
        """The test fails when a real bug enters the code it exercises."""
        tree = tree_with_big_write_delay(self, SHORT_HOOK_SECONDS)
        call = probe_with_home(HookHome(self).home, tree, short_budget_probe())
        failures = call.stderr.splitlines()[1:]
        self.assertEqual((call.returncode, ["big.go" in failure for failure in failures]), (1, [True]))


class LargePayloads(unittest.TestCase):
    def test_smoke_probe_passes_on_this_tree(self):
        call = subprocess.run([sys.executable, str(PROBE), str(ROOT)], capture_output=True, text=True, timeout=600)
        self.assertEqual(call.returncode, 0)

    @unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
    def test_exact_layer_answers_a_large_go_write_with_json(self):
        """The test runs quickly."""
        call = HookHome(self).link_library().link_grammars().write("big.go", BIG_GO)
        self.assertEqual((call.returncode, type(json.loads(call.stdout))), (0, dict))


if __name__ == "__main__":
    unittest.main()
