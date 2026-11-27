import json
import os
import subprocess
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

from t20_layer_home import BUNDLE, GRAMMARS, LIBRARY_NEEDED, LIBRARY_READY, VENV


LOCK = "wording_debt_lock"
GAP = "wording_test_gap"
REPORT_ONLY = {LOCK, GAP}
OBSERVED = "would_block"
NOTED = (None, [(LOCK, 1)], [(LOCK, OBSERVED)])
GAP_NOTED = (None, [(GAP, 1)], [(GAP, OBSERVED)])
WRITER = "adw-test-writer"
THIS_SESSION = "debt-this"
OTHER_SESSION = "debt-other"
OLD_TEST = "tests/test_old.py"
NEW_TEST = "tests/test_new.py"
ADDED_TEST = "tests/test_added.py"
MJS_TEST = "tests/render.test.mjs"
MISSING_LIBRARY = "library_missing"
PROSE = " ".join(("The", "file", "could", "not", "be", "opened", "today."))
CITATION = '    """A test must verify what the code does for its client, never how it does it."""\n'
SIZE_TEST = "def test_size():\n" + CITATION + "    assert len([1, 2]) == 2\n"
DEBT_FILE = SIZE_TEST + "\n\ndef test_msg():\n" + CITATION + "    assert render() == " + json.dumps(PROSE) + "\n"
BEHAVIOR_FILE = SIZE_TEST + "\n\ndef test_msg():\n" + CITATION + "    assert render() is None\n"
NEW_BEHAVIOR = "def test_sum():\n" + CITATION + "    assert 1 + 1 == 2\n"
MJS_HEADER = "import { test, expect } from 'vitest';\n\n// Principle: A test must verify what the code does for its client, never how it does it.\ntest('message', () => {\n"
MJS_DEBT = MJS_HEADER + "  expect(render()).toBe(" + json.dumps(PROSE) + ");\n});\n"
MJS_BEHAVIOR = MJS_HEADER + "  expect(render()).toBeNull();\n});\n"


PROJECTS = {"cleared": DEBT_FILE, "added": SIZE_TEST, "deleted": DEBT_FILE}


def project(root, name):
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    (repo / "tests").mkdir()
    return repo


def reports(state):
    return {report: json.loads(report.read_text(encoding="utf-8")) for report in (state / "reports").glob("*.json")}


def ledger(state):
    path = state / "ledger" / "ledger.jsonl"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def noted_since(state, written):
    return sorted((row["rule"], row["outcome"]) for row in map(json.loads, ledger(state)[written:]) if row.get("rule") in REPORT_ONLY)


def new_rows(before, after):
    return [row for report, batch in after.items() for row in batch[len(before.get(report, [])):]]


def verdict(response):
    output, rows, noted = response
    decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
    return decision, sorted((row["rule"], row["line"]) for row in rows if row["rule"] in REPORT_ONLY), noted


def lock_details(response):
    return " ".join(row["detail"] for row in response[1] if row["rule"] in REPORT_ONLY)


class DebtChangedByAnotherSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        (cls.root / "home").mkdir()
        for name, old_test in PROJECTS.items():
            (project(cls.root, name) / OLD_TEST).write_text(old_test, encoding="utf-8")
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)

    def hook(self, repo, session, event, payload):
        before = reports(self.state)
        written = len(ledger(self.state))
        body = {"session_id": f"{self._testMethodName}-{session}", "hook_event_name": event, "cwd": str(repo), **payload}
        response = run_hook(event, body, state_dir=self.state, git_view=NO_REPOSITORY)
        return response, new_rows(before, reports(self.state)), noted_since(self.state, written)

    def both_sessions_start(self, repo):
        self.hook(repo, THIS_SESSION, "SessionStart", {"source": "startup"})
        self.hook(repo, OTHER_SESSION, "SessionStart", {"source": "startup"})

    def write(self, repo, relative, content):
        tool_input = {"file_path": str(repo / relative), "content": content}
        return self.hook(repo, THIS_SESSION, "PreToolUse", {"tool_name": "Write", "agent_id": "a1", "agent_type": WRITER, "tool_input": tool_input})

    def other_session_writes(self, repo, relative, content):
        (repo / relative).write_text(content, encoding="utf-8")
        tool_input = {"file_path": str(repo / relative), "content": content}
        self.hook(repo, OTHER_SESSION, "PostToolUse", {
            "tool_name": "Write", "agent_id": "a2", "agent_type": WRITER, "tool_input": tool_input,
            "tool_response": {"filePath": str(repo / relative)},
        })

    def other_session_deletes(self, repo, relative):
        (repo / relative).unlink()
        self.hook(repo, OTHER_SESSION, "PostToolUse", {
            "tool_name": "Bash", "agent_id": "a2", "agent_type": WRITER, "tool_input": {"command": "rm " + relative},
            "tool_response": {"stdout": "", "stderr": ""},
        })

    def test_debt_cleared_by_another_session_lets_this_session_add_a_test(self):
        repo = self.root / "cleared"
        self.both_sessions_start(repo)
        self.other_session_writes(repo, OLD_TEST, BEHAVIOR_FILE)

        self.assertEqual(verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (None, [], []))

    def test_wording_test_added_by_another_session_is_noted_in_this_session(self):
        repo = self.root / "added"
        self.both_sessions_start(repo)
        self.other_session_writes(repo, ADDED_TEST, DEBT_FILE)

        self.assertEqual(verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), NOTED)

    def test_debt_file_deleted_by_another_session_stops_locking_this_session(self):
        repo = self.root / "deleted"
        self.both_sessions_start(repo)
        self.other_session_deletes(repo, OLD_TEST)

        self.assertEqual(verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (None, [], []))


class ParserLayerHome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        cls.home = cls.root / "home"
        cls.home.mkdir()
        cls.repo = project(cls.root, "repo")
        (cls.repo / MJS_TEST).write_text(MJS_DEBT, encoding="utf-8")

    def hook(self, event, payload):
        before = reports(self.state)
        written = len(ledger(self.state))
        call = subprocess.run(
            [sys.executable, "-ISB", str(PROJECT / "adw_light.py"), event, "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps({"session_id": self._testMethodName, "hook_event_name": event, "cwd": str(self.repo), **payload}),
            text=True, capture_output=True, check=True,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        return json.loads(call.stdout), new_rows(before, reports(self.state)), noted_since(self.state, written)

    def start(self):
        self.hook("SessionStart", {"source": "startup"})

    def write(self, relative, content):
        tool_input = {"file_path": str(self.repo / relative), "content": content}
        return self.hook("PreToolUse", {"tool_name": "Write", "agent_id": "a1", "agent_type": WRITER, "tool_input": tool_input})


class TestFileWithoutAParser(ParserLayerHome):
    def test_unparsed_test_file_is_named_as_a_scan_gap_with_its_reason(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.start()

        response = self.write(NEW_TEST, NEW_BEHAVIOR)

        self.assertEqual(verdict(response), GAP_NOTED)
        self.assertIn(f"{self.repo / MJS_TEST}: {MISSING_LIBRARY}", lock_details(response))

    def test_rewrite_of_an_unparsed_test_file_is_noted_as_a_scan_gap_with_the_parser_reason(self):
        """Name the bug. Describe one concrete regression that makes this test fail."""
        self.start()

        response = self.write(MJS_TEST, MJS_BEHAVIOR)

        self.assertEqual(verdict(response), GAP_NOTED)
        self.assertIn(f"{self.repo / MJS_TEST}: {MISSING_LIBRARY}", lock_details(response))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class TestFileAfterTheParserReturns(ParserLayerHome):
    def link_language_layer(self):
        adw = self.home / ".adw-lite"
        (adw / "install").mkdir(parents=True)
        (adw / "install" / "venv").symlink_to(VENV, target_is_directory=True)
        grammars = adw / "cache" / "grammars"
        grammars.mkdir(parents=True)
        (grammars / BUNDLE).symlink_to(GRAMMARS / BUNDLE, target_is_directory=True)

    def test_unparsed_test_file_is_counted_by_its_wording_tests_once_the_parser_works(self):
        self.start()
        self.link_language_layer()

        response = self.write(NEW_TEST, NEW_BEHAVIOR)

        self.assertEqual(verdict(response), NOTED)
        self.assertNotIn(MISSING_LIBRARY, lock_details(response))


if __name__ == "__main__":
    unittest.main()
