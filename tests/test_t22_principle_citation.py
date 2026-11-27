import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
NO_REPOSITORY_VIEW = Path(__file__).resolve().parent / "fixtures" / "no_repository_view.json"
TEST_WRITER = "adw-test-writer"
DENY = "deny"
CONTEXT = "context"
NONE = "none"
CITATION = ("test_principle_citation", "block")
DOCSTRING_BAN = {("added_docstring", "block"), ("what_docstring", "block")}
GATE = ("test_write_gate", "block")
PY_TEST = "tests/test_sample.py"
JS_TEST = "tests/sample.test.js"
SKILL_QUOTE = "A test must verify what the code does for its client, never how it does it."
FORMULA_QUOTE = "The value of a test is the product of the four scores."
CHAPTER_QUOTE = "A zero on any one makes the whole test worthless."
MADE_UP_QUOTE = "A test should always be written in green ink before lunch."
PY_HEADER = ("import unittest", "", "", "class Sample(unittest.TestCase):")
JS_HEADER = ("const { add } = require('../src/add');", "")


def python_method(name, quote):
    docstring = ('        """' + quote + '"""',) if quote else ()
    return ("    def " + name + "(self):", *docstring, "        self.assertEqual(add(2, 3), 5)", "")


def python_test(*methods):
    lines = (*PY_HEADER, *(line for name, quote in methods for line in python_method(name, quote)))
    return "\n".join(lines) + "\n"


def python_with_quoted_helper():
    helper = ("def numbers():", '    """' + FORMULA_QUOTE + '"""', "    return [2, 3]", "", "")
    return "\n".join(helper) + python_test(("test_adds", CHAPTER_QUOTE))


def js_block(call, quote):
    citation = ("// Principle: " + quote,) if quote else ()
    return (*citation, call + "('adds', () => {", "  expect(add(2, 3)).toBe(5);", "});", "")


def js_test(*blocks):
    lines = (*JS_HEADER, *(line for call, quote in blocks for line in js_block(call, quote)))
    return "\n".join(lines) + "\n"


def decision_of(response):
    output = response.get("hookSpecificOutput", {})
    if output.get("permissionDecision") == DENY:
        return DENY
    return CONTEXT if output.get("additionalContext") else NONE


class TestWriterCitesTheBook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = TemporaryDirectory()
        cls.root = Path(cls.workspace.name)
        cls.home = cls.root / "home"
        cls.state = cls.root / "state"
        cls.home.mkdir()
        (cls.root / "pyproject.toml").write_text("[project]\nname = \"fixture\"\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def verdict(self, relative, content, agent_type=TEST_WRITER):
        typed = {"agent_type": agent_type} if agent_type else {}
        payload = {
            "cwd": str(self.root), "session_id": self.id(), "hook_event_name": "PreToolUse", **typed,
            "tool_name": "Write", "tool_input": {"file_path": str(self.root / relative), "content": content},
        }
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state), "--git-view", str(NO_REPOSITORY_VIEW)],
            input=json.dumps(payload), text=True, capture_output=True, check=True,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        return decision_of(json.loads(call.stdout)), self.ledger_rules()

    def ledger_rules(self):
        ledger = self.state / "ledger" / "ledger.jsonl"
        lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
        rows = (json.loads(line) for line in lines if line.strip())
        return {(row["rule"], row["outcome"]) for row in rows if row.get("session_id") == self.id() and row.get("rule")}

    def test_python_tests_quoting_the_skill_and_a_chapter_are_allowed(self):
        content = python_test(("test_adds", SKILL_QUOTE), ("test_adds_again", CHAPTER_QUOTE))
        self.assertEqual(self.verdict(PY_TEST, content), (NONE, set()))

    def test_python_test_without_a_quote_is_denied_for_citation(self):
        content = python_test(("test_adds", SKILL_QUOTE), ("test_adds_again", None))
        self.assertEqual(self.verdict(PY_TEST, content), (DENY, {CITATION}))

    def test_python_test_quoting_a_sentence_outside_the_skill_is_denied_for_citation(self):
        content = python_test(("test_adds", MADE_UP_QUOTE))
        self.assertEqual(self.verdict(PY_TEST, content), (DENY, {CITATION} | DOCSTRING_BAN))

    def test_quote_on_a_helper_function_keeps_the_docstring_ban(self):
        self.assertEqual(self.verdict(PY_TEST, python_with_quoted_helper()), (DENY, DOCSTRING_BAN))

    def test_javascript_tests_with_a_principle_line_above_each_are_allowed(self):
        content = js_test(("test", SKILL_QUOTE), ("it", FORMULA_QUOTE))
        self.assertEqual(self.verdict(JS_TEST, content), (NONE, set()))

    def test_javascript_test_without_a_principle_line_is_denied_for_citation(self):
        content = js_test(("test", SKILL_QUOTE), ("it", None))
        self.assertEqual(self.verdict(JS_TEST, content), (DENY, {CITATION}))

    def test_quoted_python_test_from_a_normal_agent_meets_the_test_write_gate(self):
        decision, rules = self.verdict(PY_TEST, python_test(("test_adds", SKILL_QUOTE)), agent_type=None)
        self.assertEqual((decision, GATE in rules, CITATION in rules), (DENY, True, False))


if __name__ == "__main__":
    unittest.main()
