import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
AGENT = "adw-test-writer"
TEST_FILE = "tests/test_probe.py"
DENY = "deny"
RULE = "wording_assertion"
BODY_START = 4
HEADER = ("import pytest", "", "", "def test_probe():")
LOWER = " ".join(("the", "cache", "is", "empty"))
TITLED = " ".join(("The", "Cache", "Is", "Empty"))
UNRELATED = " ".join(("the", "store", "is", "full"))
SHORTER = " ".join(("the", "cache", "is"))
ONE_EXTRA = " ".join((LOWER, "now"))
TWO_EXTRA = " ".join((LOWER, "right", "now"))
THREE_EXTRA = " ".join((LOWER, "right", "now", "again"))
PRODUCTION = "src/app.py"


def quoted(text):
    return json.dumps(text)


def probe_module(*body):
    return "\n".join((*HEADER, *("    " + line for line in body))) + "\n"


def titled_check(given):
    return f"assert capitalize_words({given}) == {quoted(TITLED)}"


def lower_check(given):
    return f"assert render_text({quoted(given)}) == {quoted(LOWER)}"


class TextFunctionGuards(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.home.mkdir()
        (self.repo / "tests").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def place(self, relative, text):
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def write_test(self, content):
        payload = {
            "cwd": str(self.repo), "session_id": "guards", "hook_event_name": "PreToolUse",
            "agent_type": AGENT, "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / TEST_FILE), "content": content},
        }
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        self.assertEqual(call.returncode, 0)
        return json.loads(call.stdout)

    def wording_rows(self):
        reports = sorted((self.state / "reports").glob("*.json"))
        rows = [row for report in reports for row in json.loads(report.read_text(encoding="utf-8"))]
        return [row for row in rows if row["rule"] == RULE]

    def verdict(self, content):
        decision = self.write_test(content).get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, tuple(row["line"] for row in self.wording_rows())

    def test_expected_text_derived_from_the_input_is_allowed(self):
        self.assertEqual(self.verdict(probe_module(titled_check(quoted(LOWER)))), (None, ()))

    def test_input_bound_once_to_a_name_is_allowed(self):
        content = probe_module(f"text = {quoted(LOWER)}", titled_check("text"))
        self.assertEqual(self.verdict(content), (None, ()))

    def test_expected_text_found_in_production_code_denies(self):
        self.place(PRODUCTION, f"TITLE = {quoted(TITLED)}\n")
        content = probe_module(titled_check(quoted(LOWER)))
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_production_copy_finding_names_the_production_line(self):
        self.place(PRODUCTION, f"TITLE = {quoted(TITLED)}\n")
        self.write_test(probe_module(titled_check(quoted(LOWER))))
        self.assertEqual([row.get("match") for row in self.wording_rows()], [f"{PRODUCTION}:1"])

    def test_input_that_is_a_near_copy_of_the_expected_text_denies(self):
        content = probe_module(titled_check(quoted(TITLED + "!")))
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_two_calls_under_test_deny(self):
        content = probe_module(titled_check(f"strip_text({quoted(LOWER)})"))
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_second_call_inside_the_arguments_denies(self):
        content = probe_module(f"assert capitalize_words({quoted(LOWER)}, style=pick_style()) == {quoted(TITLED)}")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_input_with_one_extra_word_is_a_near_copy_and_denies(self):
        self.assertEqual(self.verdict(probe_module(lower_check(ONE_EXTRA))), (DENY, (BODY_START + 1,)))

    def test_input_with_two_extra_words_is_a_near_copy_and_denies(self):
        self.assertEqual(self.verdict(probe_module(lower_check(TWO_EXTRA))), (DENY, (BODY_START + 1,)))

    def test_input_with_three_extra_words_is_allowed(self):
        self.assertEqual(self.verdict(probe_module(lower_check(THREE_EXTRA))), (None, ()))

    def test_input_one_word_shorter_than_the_expected_text_denies(self):
        self.assertEqual(self.verdict(probe_module(lower_check(SHORTER))), (DENY, (BODY_START + 1,)))

    def test_input_sharing_few_words_with_the_expected_text_denies(self):
        content = probe_module(titled_check(quoted(UNRELATED)))
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))


if __name__ == "__main__":
    unittest.main()
