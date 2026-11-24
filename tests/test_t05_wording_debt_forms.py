import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
LOCK = "wording_debt_lock"
DENY = "deny"
WRITER = "adw-test-writer"
SESSION = "debt-forms"
OLD_TEST = "tests/test_old.py"
NEW_TEST = "tests/test_new.py"
PRODUCTION = "src/app.py"
NEW_BEHAVIOR = "def test_sum():\n    assert 1 + 1 == 2\n"
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


class WordingDebtForms(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = self.root / "repo"
        (self.repo / "tests").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def place(self, relative, text):
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def rows(self):
        reports = (self.state / "reports").glob("*.json")
        return [row for report in reports for row in json.loads(report.read_text(encoding="utf-8"))]

    def hook(self, event, payload):
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps({"session_id": SESSION, "hook_event_name": event, "cwd": str(self.repo), **payload}),
            text=True, capture_output=True, check=True,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        return json.loads(call.stdout)

    def new_test_verdict(self, old_test):
        self.place(OLD_TEST, old_test)
        self.hook("SessionStart", {"source": "startup"})
        tool_input = {"file_path": str(self.repo / NEW_TEST), "content": NEW_BEHAVIOR}
        output = self.hook("PreToolUse", {
            "tool_name": "Write", "tool_input": tool_input, "agent_id": "a1", "agent_type": WRITER,
        })
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, [(row["rule"], row["line"]) for row in self.rows() if row["rule"] == LOCK]

    def test_exception_match_on_a_sentence_locks_new_tests(self):
        self.assertEqual(self.new_test_verdict(EXCEPTION_MATCH), (DENY, [(LOCK, 1)]))

    def test_log_sentence_assertion_locks_new_tests(self):
        self.assertEqual(self.new_test_verdict(LOG_ASSERTION), (DENY, [(LOCK, 1)]))

    def test_typed_out_location_assertion_locks_new_tests(self):
        self.assertEqual(self.new_test_verdict(LOCATION_ASSERTION), (DENY, [(LOCK, 1)]))

    def test_text_function_test_with_derived_expected_text_does_not_lock(self):
        self.assertEqual(self.new_test_verdict(TRANSFORMATION), (None, []))

    def test_text_function_test_whose_expected_text_sits_in_production_code_locks(self):
        self.place(PRODUCTION, f"TITLE = {json.dumps(TITLED)}\n")
        self.assertEqual(self.new_test_verdict(TRANSFORMATION), (DENY, [(LOCK, 1)]))


if __name__ == "__main__":
    unittest.main()
