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
from lib import wording_debt
from lib.test_units import parsed_python


NO_REPOSITORY = FakeGitView({"roots": {}})

LOCK = "wording_debt_lock"
GAP = "wording_test_gap"
GATE = "test_write_gate"
REPORTED = {LOCK, GAP, GATE}
DENY = "deny"
OBSERVED = "would_block"
BOTH_NOTED = sorted([(GAP, OBSERVED), (LOCK, OBSERVED)])
WRITER = "adw-test-writer"
SYNTAX = "syntax_error"
OVERFLOW = "parser_overflow"
OLD_TEST = "tests/test_old.py"
NEW_TEST = "tests/test_new.py"
CLEAN_TEST = "tests/test_clean.py"
BROKEN = "tests/test_broken.py"
UNMARKED = "tests/test_shape.py"
NULLED = "tests/test_nulled.py"
PROSE = " ".join(("The", "file", "could", "not", "be", "opened", "today."))
CITATION = '    """A test must verify what the code does for its client, never how it does it."""\n'
SIZE_TEST = "def test_size():\n" + CITATION + "    assert len([1, 2]) == 2\n"
WORDING_ASSERT = "    assert render() == " + json.dumps(PROSE)
DEBT_FILE = SIZE_TEST + "\n\ndef test_msg():\n" + CITATION + WORDING_ASSERT + "\n"
NEW_BEHAVIOR = "def test_sum():\n" + CITATION + "    assert 1 + 1 == 2\n"
MALFORMED = "def test_broken(:\n    pass\n"
REPAIRED = "def test_broken():\n    pass\n"
UNMARKED_MALFORMED = "shape = (\n"
NULL_BYTES = "def test_nulled():\n    assert 1 == 1\x00\n"
DEEP_TEST = "def test_deep():\n    assert " + "-" * 200_000 + "1\n"
DEBT_UNIT = f"{OLD_TEST}:{DEBT_FILE.splitlines().index(WORDING_ASSERT) + 1} wording_assertion"
BROKEN_FILES = {BROKEN: MALFORMED, UNMARKED: UNMARKED_MALFORMED, NULLED: NULL_BYTES}
PROJECTS = {
    "debt_and_broken": {OLD_TEST: DEBT_FILE, **BROKEN_FILES},
    "debt": {OLD_TEST: DEBT_FILE, CLEAN_TEST: SIZE_TEST},
    "only_debt": {OLD_TEST: DEBT_FILE},
    "plain": {OLD_TEST: SIZE_TEST},
    "cached": {OLD_TEST: SIZE_TEST, BROKEN: MALFORMED},
    "repaired": {OLD_TEST: SIZE_TEST, BROKEN: MALFORMED},
    "removed": {OLD_TEST: SIZE_TEST, BROKEN: MALFORMED},
}


def build(root, name, files):
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    for relative, content in files.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(content, encoding="utf-8")


class SyntaxErrorGap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        (cls.root / "home").mkdir()
        for name, files in PROJECTS.items():
            build(cls.root, name, files)
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)

    def reports(self):
        return {report: json.loads(report.read_text(encoding="utf-8")) for report in (self.state / "reports").glob("*.json")}

    def ledger(self):
        path = self.state / "ledger" / "ledger.jsonl"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def hook(self, repo, event, payload):
        before = self.reports()
        written = len(self.ledger())
        body = {"session_id": self._testMethodName, "hook_event_name": event, "cwd": str(repo), **payload}
        output = run_hook(event, body, state_dir=self.state, git_view=NO_REPOSITORY)
        rows = [row for report, batch in self.reports().items() for row in batch[len(before.get(report, [])):]]
        noted = [(row["rule"], row["outcome"]) for row in map(json.loads, self.ledger()[written:]) if row.get("rule") in REPORTED]
        return output, [row for row in rows if row["rule"] in REPORTED], sorted(noted)

    def started(self, name):
        repo = self.root / name
        self.hook(repo, "SessionStart", {"source": "startup"})
        return repo

    def tool(self, repo, tool_name, tool_input, agent=WRITER):
        identity = {"agent_id": "a1", "agent_type": agent} if agent else {}
        return self.hook(repo, "PreToolUse", {"tool_name": tool_name, "tool_input": tool_input, **identity})

    def write(self, repo, relative, content, agent=WRITER):
        return self.tool(repo, "Write", {"file_path": str(repo / relative), "content": content}, agent)

    def edit(self, repo, relative, old, new):
        return self.tool(repo, "Edit", {"file_path": str(repo / relative), "old_string": old, "new_string": new})

    def gaps(self, response, repo, relative, reason=SYNTAX):
        output, rows, noted = response
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        subject = f"{repo / relative}: {reason}"
        return decision, sorted((row["rule"], row["detail"].endswith(subject)) for row in rows), noted

    def gap_paths(self, response):
        _, rows, _ = response
        return sorted((row["path"], row["detail"].endswith(f"{row['path']}: {SYNTAX}")) for row in rows if row["rule"] == GAP)

    def test_malformed_test_files_on_disk_are_each_reported_with_the_syntax_error_reason(self):
        """Use a parameterized test for several inputs."""
        repo = self.started("debt_and_broken")
        response = self.write(repo, NEW_TEST, NEW_BEHAVIOR)
        self.assertEqual(self.gap_paths(response), sorted((str(repo / path), True) for path in BROKEN_FILES))

    def test_malformed_test_files_do_not_raise_the_wording_test_count(self):
        """Name the bug. Describe one concrete regression that makes this test fail."""
        repo = self.started("debt_and_broken")
        _, rows, _ = self.write(repo, NEW_TEST, NEW_BEHAVIOR)
        [row] = [row for row in rows if row["rule"] == LOCK]
        self.assertEqual(row["units"], [DEBT_UNIT])

    def test_test_writer_writing_a_malformed_test_in_a_project_with_debt_passes_with_a_syntax_error_gap(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        repo = self.started("debt")
        response = self.write(repo, NEW_TEST, MALFORMED)
        self.assertEqual(self.gaps(response, repo, NEW_TEST), (None, [(LOCK, False), (GAP, True)], BOTH_NOTED))

    def test_test_writer_editing_a_test_into_malformed_content_in_a_project_with_debt_passes_with_a_syntax_error_gap(self):
        """Protection against regressions. The test fails when a real bug enters the code it exercises."""
        repo = self.started("debt")
        response = self.edit(repo, CLEAN_TEST, "def test_size():", "def test_size(:")
        self.assertEqual(self.gaps(response, repo, CLEAN_TEST), (None, [(LOCK, False), (GAP, True)], BOTH_NOTED))

    def test_null_byte_test_content_written_by_the_test_writer_reports_the_syntax_error_reason(self):
        """Output based. Feed input, check the return value."""
        repo = self.started("debt")
        response = self.write(repo, NULLED, NULL_BYTES)
        self.assertEqual(self.gaps(response, repo, NULLED), (None, [(LOCK, False), (GAP, True)], BOTH_NOTED))

    def test_replacing_the_only_wording_debt_file_with_malformed_python_gives_one_observed_syntax_error_gap(self):
        """Name the bug. Describe one concrete regression that makes this test fail."""
        repo = self.started("only_debt")
        response = self.write(repo, OLD_TEST, MALFORMED)
        self.assertEqual(self.gaps(response, repo, OLD_TEST), (None, [(GAP, True)], [(GAP, OBSERVED)]))

    def test_replacing_the_only_wording_debt_file_with_parser_overflow_content_gives_one_observed_overflow_gap(self):
        """Protection against regressions. The test fails when a real bug enters the code it exercises."""
        repo = self.started("only_debt")
        response = self.write(repo, OLD_TEST, DEEP_TEST)
        self.assertEqual(self.gaps(response, repo, OLD_TEST, OVERFLOW), (None, [(GAP, True)], [(GAP, OBSERVED)]))

    def test_parser_names_overflow_and_syntax_errors_as_distinct_gap_reasons(self):
        """Use a parameterized test for several inputs."""
        texts = {"deep": DEEP_TEST, "syntax": MALFORMED, "unmarked": UNMARKED_MALFORMED, "null": NULL_BYTES, "valid": SIZE_TEST}
        reasons = {name: parsed_python(text).gap for name, text in texts.items()}
        self.assertEqual(reasons, {"deep": OVERFLOW, "syntax": SYNTAX, "unmarked": SYNTAX, "null": SYNTAX, "valid": ""})

    def test_new_malformed_test_file_in_a_project_without_debt_gives_no_row(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        repo = self.started("plain")
        self.assertEqual(self.write(repo, NEW_TEST, MALFORMED), ({}, [], []))

    def test_second_write_reuses_the_cached_syntax_error_entry_without_reading_the_file(self):
        """Name the bug. Describe one concrete regression that makes this test fail."""
        repo = self.started("cached")
        self.write(repo, NEW_TEST, NEW_BEHAVIOR)
        with mock.patch.object(wording_debt, "read_regular_text", wraps=wording_debt.read_regular_text) as reads:
            response = self.write(repo, NEW_TEST, NEW_BEHAVIOR)
        read_paths = [str(call.args[0]) for call in reads.call_args_list]
        self.assertEqual((self.gaps(response, repo, BROKEN), read_paths), ((None, [(GAP, True)], [(GAP, OBSERVED)]), []))

    def test_repairing_a_malformed_test_file_on_disk_clears_its_gap_on_the_next_scan(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        repo = self.started("repaired")
        (repo / BROKEN).write_text(REPAIRED, encoding="utf-8")
        self.assertEqual(self.write(repo, NEW_TEST, NEW_BEHAVIOR), ({}, [], []))

    def test_deleting_a_malformed_test_file_on_disk_clears_its_gap_on_the_next_scan(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        repo = self.started("removed")
        (repo / BROKEN).unlink()
        self.assertEqual(self.write(repo, NEW_TEST, NEW_BEHAVIOR), ({}, [], []))

    def test_main_agent_writing_malformed_test_content_gets_the_test_write_gate_refusal(self):
        """A test must verify what the code does for its client, never how it does it."""
        repo = self.started("plain")
        output, rows, noted = self.write(repo, NEW_TEST, MALFORMED, agent=None)
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual((decision, sorted((row["rule"], row["line"]) for row in rows)), (DENY, [(GATE, 1)]))


if __name__ == "__main__":
    unittest.main()
