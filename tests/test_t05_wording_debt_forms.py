import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView


NO_REPOSITORY = FakeGitView({"roots": {}})

LOCK = "wording_debt_lock"
OBSERVED = "would_block"
NOTED = (None, [(LOCK, 1)], [(LOCK, OBSERVED)])
WRITER = "adw-test-writer"
SESSION = "debt-forms"
OLD_TEST = "tests/test_old.py"
NEW_TEST = "tests/test_new.py"
PRODUCTION = "src/app.py"
CITATION = '    """A test must verify what the code does for its client, never how it does it."""\n'
NEW_BEHAVIOR = "def test_sum():\n" + CITATION + "    assert 1 + 1 == 2\n"
SENTENCE = " ".join(("the", "value", "is", "not", "allowed", "here"))
LOG_SENTENCE = " ".join(("the", "job", "was", "skipped", "because", "the", "queue", "is", "empty"))
TYPED_LOCATION = "/".join(("pkg", "mod.py")) + ":12"
LOWER = " ".join(("the", "cache", "is", "empty"))
TITLED = " ".join(("The", "Cache", "Is", "Empty"))


def module(header, *body):
    return "\n".join((*header, *("    " + line for line in body))) + "\n"


EXCEPTION_MATCH = module(
    ("import pytest", "", "", "def test_err():"),
    f"with pytest.raises(ValueError, match={json.dumps(SENTENCE)}):", "    build()",
)
LOG_ASSERTION = module(("def test_log(caplog):",), "run()", f"assert {json.dumps(LOG_SENTENCE)} in caplog.text")
LOCATION_ASSERTION = module(("def test_where():",), f"assert scan() == {json.dumps(TYPED_LOCATION)}")
TRANSFORMATION = module(
    ("def test_titles():",), f"assert capitalize_words({json.dumps(LOWER)}) == {json.dumps(TITLED)}",
)


PROJECTS = {
    "exception": EXCEPTION_MATCH, "log": LOG_ASSERTION, "location": LOCATION_ASSERTION,
    "derived": TRANSFORMATION, "production": TRANSFORMATION,
}


class WordingDebtForms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        (cls.root / "home").mkdir()
        for name, old_test in PROJECTS.items():
            (cls.root / name / ".git").mkdir(parents=True)
            (cls.root / name / "tests").mkdir()
            (cls.root / name / OLD_TEST).write_text(old_test, encoding="utf-8")
        (cls.root / "production" / "src").mkdir()
        (cls.root / "production" / PRODUCTION).write_text(f"TITLE = {json.dumps(TITLED)}\n", encoding="utf-8")
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)

    def reports(self):
        return set((self.state / "reports").glob("*.json"))

    def ledger(self):
        path = self.state / "ledger" / "ledger.jsonl"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def hook(self, repo, event, payload):
        body = {"session_id": self._testMethodName, "hook_event_name": event, "cwd": str(repo), **payload}
        return run_hook(event, body, state_dir=self.state, git_view=NO_REPOSITORY)

    def new_test_verdict(self, name):
        repo = self.root / name
        self.hook(repo, "SessionStart", {"source": "startup"})
        tool_input = {"file_path": str(repo / NEW_TEST), "content": NEW_BEHAVIOR}
        earlier = self.reports()
        written = len(self.ledger())
        output = self.hook(repo, "PreToolUse", {
            "tool_name": "Write", "tool_input": tool_input, "agent_id": "a1", "agent_type": WRITER,
        })
        rows = [row for report in self.reports() - earlier for row in json.loads(report.read_text(encoding="utf-8"))]
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        noted = [(row["rule"], row["outcome"]) for row in map(json.loads, self.ledger()[written:]) if row.get("rule") == LOCK]
        return decision, [(row["rule"], row["line"]) for row in rows if row["rule"] == LOCK], noted

    def test_exception_match_on_a_sentence_is_noted_without_blocking_a_new_test(self):
        self.assertEqual(self.new_test_verdict("exception"), NOTED)

    def test_log_sentence_assertion_is_noted_without_blocking_a_new_test(self):
        self.assertEqual(self.new_test_verdict("log"), NOTED)

    def test_typed_out_location_assertion_is_noted_without_blocking_a_new_test(self):
        self.assertEqual(self.new_test_verdict("location"), NOTED)

    def test_text_function_test_with_derived_expected_text_is_not_debt(self):
        self.assertEqual(self.new_test_verdict("derived"), (None, [], []))

    def test_text_function_test_whose_expected_text_sits_in_production_code_is_noted(self):
        self.assertEqual(self.new_test_verdict("production"), NOTED)


if __name__ == "__main__":
    unittest.main()
