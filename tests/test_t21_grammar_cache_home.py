import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView
from lib import language_layer
from in_memory_hook import TEST_WRITER, decision_of, language_home
from t19_language_home import LIBRARY_NEEDED, LIBRARY_READY


NO_REPOSITORY = FakeGitView({"roots": {}})
BASH = "bash"
BARE = "bare"
GRAMMAR = "grammar"
PRECISE_ONLY_WITH_GRAMMAR = {BARE: False, GRAMMAR: True}
BATS_PATH = "scripts/suite.bats"
LONG_FUNCTION_REFUSED = ("deny", (("function_too_long", 3),))


def bash_script(tag):
    return f"#!/usr/bin/env bash\n{tag}() {{\n  echo \"$1\"\n}}\n{tag} done\n"


def long_bats_function():
    body = "".join(f'  echo "line {number}"\n' for number in range(1, 86))
    return "#!/usr/bin/env bats\n\nsetup() {\n" + body + "}\n"


def bare_home(home):
    adw = home / ".adw-lite"
    (adw / "install").mkdir(parents=True)
    failed = adw / "cache" / "grammars" / "failed"
    failed.mkdir(parents=True)
    (failed / BASH).touch()
    return home


def environment(home, state):
    return {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "ADW_STATE_ROOT": str(state)}


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class GrammarCachesFollowTheHomeFolder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        cls.homes = {
            name: {BARE: bare_home(cls.root / name / BARE), GRAMMAR: language_home()}
            for name in ("bare_first", "grammar_first")
        }

    def precise_under(self, pair, kind, text):
        with mock.patch.dict(os.environ, environment(self.homes[pair][kind], self.state), clear=True):
            return language_layer.layer_for(str(self.root / "script.sh"), BASH, text).precise

    def scan_in_order(self, pair, order):
        text = bash_script(pair)
        return {kind: self.precise_under(pair, kind, text) for kind in order}

    def test_scan_under_a_home_without_the_grammar_does_not_stop_precise_parsing_under_a_home_with_it(self):
        """Feed input, check the return value."""
        self.assertEqual(self.scan_in_order("bare_first", (BARE, GRAMMAR)), PRECISE_ONLY_WITH_GRAMMAR)

    def test_scan_under_a_home_with_the_grammar_does_not_lend_precise_parsing_to_a_home_without_it(self):
        """Feed input, check the return value."""
        self.assertEqual(self.scan_in_order("grammar_first", (GRAMMAR, BARE)), PRECISE_ONLY_WITH_GRAMMAR)



@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class UnitScansFollowTheHomeFolder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        cls.work = cls.root / "work"
        cls.work.mkdir()
        cls.homes = {BARE: bare_home(cls.root / BARE), GRAMMAR: language_home()}

    def verdict_under(self, kind, text):
        session = "t21-" + kind
        payload = {
            "session_id": session, "hook_event_name": "PreToolUse", "cwd": str(self.work), "tool_name": "Write",
            "tool_input": {"file_path": str(self.work / BATS_PATH), "content": text}, "agent_type": TEST_WRITER,
        }
        environment = {"HOME": str(self.homes[kind]), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, environment, clear=True):
            decision = decision_of(run_hook("PreToolUse", payload, state_dir=self.state, git_view=NO_REPOSITORY))
        reports = sorted((self.state / "reports").glob(session + "-*.json"))
        rows = (row for report in reports for row in json.loads(report.read_text(encoding="utf-8")))
        return decision, tuple(sorted({(row["rule"], row["line"]) for row in rows}))

    def test_long_bats_function_is_refused_under_a_home_with_the_grammar_after_a_scan_under_a_home_without_it(self):
        """Feed input, check the return value."""
        text = long_bats_function()
        self.verdict_under(BARE, text)
        self.assertEqual(self.verdict_under(GRAMMAR, text), LONG_FUNCTION_REFUSED)


if __name__ == "__main__":
    unittest.main()
