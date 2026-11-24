from quiet_git import disable_background_maintenance
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from t20_layer_home import BUNDLE, GRAMMARS, LIBRARY_NEEDED, LIBRARY_READY, VENV


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
LOCK = "wording_debt_lock"
DENY = "deny"
WRITER = "adw-test-writer"
THIS_SESSION = "debt-this"
OTHER_SESSION = "debt-other"
OLD_TEST = "tests/test_old.py"
NEW_TEST = "tests/test_new.py"
ADDED_TEST = "tests/test_added.py"
MJS_TEST = "tests/render.test.mjs"
MISSING_LIBRARY = "library_missing"
PROSE = " ".join(("The", "file", "could", "not", "be", "opened", "today."))
SIZE_TEST = "def test_size():\n    assert len([1, 2]) == 2\n"
DEBT_FILE = SIZE_TEST + "\n\ndef test_msg():\n    assert render() == " + json.dumps(PROSE) + "\n"
BEHAVIOR_FILE = SIZE_TEST + "\n\ndef test_msg():\n    assert render() is None\n"
NEW_BEHAVIOR = "def test_sum():\n    assert 1 + 1 == 2\n"
MJS_HEADER = "import { test, expect } from 'vitest';\n\ntest('message', () => {\n"
MJS_DEBT = MJS_HEADER + "  expect(render()).toBe(" + json.dumps(PROSE) + ");\n});\n"
MJS_BEHAVIOR = MJS_HEADER + "  expect(render()).toBeNull();\n});\n"


class DebtHome(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.repo = self.root / "repo"
        self.home.mkdir()
        (self.repo / "tests").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        disable_background_maintenance(str(self.repo))

    def place(self, relative, content):
        (self.repo / relative).write_text(content, encoding="utf-8")

    def link_language_layer(self):
        adw = self.home / ".adw-lite"
        (adw / "install").mkdir(parents=True)
        (adw / "install" / "venv").symlink_to(VENV, target_is_directory=True)
        grammars = adw / "cache" / "grammars"
        grammars.mkdir(parents=True)
        (grammars / BUNDLE).symlink_to(GRAMMARS / BUNDLE, target_is_directory=True)

    def reports(self):
        return {report: json.loads(report.read_text(encoding="utf-8")) for report in (self.state / "reports").glob("*.json")}

    def hook(self, session, event, payload):
        before = self.reports()
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps({"session_id": session, "hook_event_name": event, "cwd": str(self.repo), **payload}),
            text=True, capture_output=True, check=True,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        rows = [row for report, batch in self.reports().items() for row in batch[len(before.get(report, [])):]]
        return json.loads(call.stdout), rows

    def start(self, session):
        self.hook(session, "SessionStart", {"source": "startup"})

    def write(self, session, relative, content):
        tool_input = {"file_path": str(self.repo / relative), "content": content}
        return self.hook(session, "PreToolUse", {"tool_name": "Write", "agent_id": "a1", "agent_type": WRITER, "tool_input": tool_input})

    def other_session_writes(self, relative, content):
        self.place(relative, content)
        tool_input = {"file_path": str(self.repo / relative), "content": content}
        self.hook(OTHER_SESSION, "PostToolUse", {
            "tool_name": "Write", "agent_id": "a2", "agent_type": WRITER, "tool_input": tool_input,
            "tool_response": {"filePath": str(self.repo / relative)},
        })

    def other_session_deletes(self, relative):
        (self.repo / relative).unlink()
        self.hook(OTHER_SESSION, "PostToolUse", {
            "tool_name": "Bash", "agent_id": "a2", "agent_type": WRITER, "tool_input": {"command": "rm " + relative},
            "tool_response": {"stdout": "", "stderr": ""},
        })

    def both_sessions_start(self):
        self.start(THIS_SESSION)
        self.start(OTHER_SESSION)

    def verdict(self, response):
        output, rows = response
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, sorted((row["rule"], row["line"]) for row in rows if row["rule"] == LOCK)

    def lock_details(self, response):
        return " ".join(row["detail"] for row in response[1] if row["rule"] == LOCK)


class DebtChangedByAnotherSession(DebtHome):
    def test_debt_cleared_by_another_session_lets_this_session_add_a_test(self):
        self.place(OLD_TEST, DEBT_FILE)
        self.both_sessions_start()
        self.other_session_writes(OLD_TEST, BEHAVIOR_FILE)

        self.assertEqual(self.verdict(self.write(THIS_SESSION, NEW_TEST, NEW_BEHAVIOR)), (None, []))

    def test_wording_test_added_by_another_session_locks_this_session(self):
        self.place(OLD_TEST, SIZE_TEST)
        self.both_sessions_start()
        self.other_session_writes(ADDED_TEST, DEBT_FILE)

        self.assertEqual(self.verdict(self.write(THIS_SESSION, NEW_TEST, NEW_BEHAVIOR)), (DENY, [(LOCK, 1)]))

    def test_debt_file_deleted_by_another_session_stops_locking_this_session(self):
        self.place(OLD_TEST, DEBT_FILE)
        self.both_sessions_start()
        self.other_session_deletes(OLD_TEST)

        self.assertEqual(self.verdict(self.write(THIS_SESSION, NEW_TEST, NEW_BEHAVIOR)), (None, []))


class TestFileWithoutAParser(DebtHome):
    def test_unparsed_test_file_is_named_as_unscanned_debt_with_its_reason(self):
        self.place(MJS_TEST, MJS_DEBT)
        self.start(THIS_SESSION)

        response = self.write(THIS_SESSION, NEW_TEST, NEW_BEHAVIOR)

        self.assertEqual(self.verdict(response), (DENY, [(LOCK, 1)]))
        self.assertIn(f"{self.repo / MJS_TEST}: {MISSING_LIBRARY}", self.lock_details(response))

    def test_rewrite_of_an_unparsed_test_file_is_refused_with_the_parser_reason(self):
        self.place(MJS_TEST, MJS_DEBT)
        self.start(THIS_SESSION)

        response = self.write(THIS_SESSION, MJS_TEST, MJS_BEHAVIOR)

        self.assertEqual(self.verdict(response), (DENY, [(LOCK, 1)]))
        self.assertIn(f"({MISSING_LIBRARY})", self.lock_details(response))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class TestFileAfterTheParserReturns(DebtHome):
    def test_unparsed_test_file_is_counted_by_its_wording_tests_once_the_parser_works(self):
        self.place(MJS_TEST, MJS_DEBT)
        self.start(THIS_SESSION)
        self.link_language_layer()

        response = self.write(THIS_SESSION, NEW_TEST, NEW_BEHAVIOR)

        self.assertEqual(self.verdict(response), (DENY, [(LOCK, 1)]))
        self.assertNotIn(MISSING_LIBRARY, self.lock_details(response))


if __name__ == "__main__":
    unittest.main()
