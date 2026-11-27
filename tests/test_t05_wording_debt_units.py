import json
import os
import re
import sys
from functools import reduce
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView
from lib.findings import Finding
from lib.test_units import parsed_python
from lib import wording_debt


NO_REPOSITORY = FakeGitView({"roots": {}})

LOCK = "wording_debt_lock"
GAP = "wording_test_gap"
REPORT_ONLY = {LOCK, GAP}
SHOWN_IN_ROW = 5
ASSERTION = "wording_assertion"
LOG = "wording_log_assertion"
EXCEPTION = "wording_exception_match"
LOCATION = "literal_location"
WRITER = "adw-test-writer"
DEBT_KEY = "wording_debt"
SHOWN = 20
MANY = 25
WALK_LIMIT = 12
WALK_REASON = "walk_limit"
SCAN_LIMIT = 1_000_000
OLD_TEST = "tests/test_old.py"
NEW_TEST = "tests/test_new.py"
DEEP = "tests/test_deep.py"
BIG = "tests/test_big.py"
MSGS = "tests/test_msgs.py"
PROSE = " ".join(("The", "file", "could", "not", "be", "opened", "today."))
SENTENCE = " ".join(("the", "value", "is", "not", "allowed", "here"))
LOG_SENTENCE = " ".join(("the", "job", "was", "skipped", "because", "the", "queue", "is", "empty"))
TYPED_LOCATION = "/".join(("pkg", "mod.py")) + ":12"
CITATION = '    """A test must verify what the code does for its client, never how it does it."""\n'
SIZE_TEST = "def test_size():\n" + CITATION + "    assert len([1, 2]) == 2\n"
WORDING_ASSERT = "    assert render() == " + json.dumps(PROSE)
DEBT_FILE = SIZE_TEST + "\n\ndef test_msg():\n" + CITATION + WORDING_ASSERT + "\n"
NEW_BEHAVIOR = "def test_sum():\n" + CITATION + "    assert 1 + 1 == 2\n"
DEEP_TEST = "def test_deep():\n    assert " + "-" * 200_000 + "1\n"
PADDING = 'PAD = "' + "a" * SCAN_LIMIT + '"\n'
MANY_FILE = "".join(f"def test_m{index}():\n{WORDING_ASSERT}\n\n\n" for index in range(MANY))
RAISES_LINE = f"    with pytest.raises(ValueError, match={json.dumps(SENTENCE)}):"
LOG_LINE = f"    assert {json.dumps(LOG_SENTENCE)} in caplog.text"
LOCATION_LINE = f"    assert scan() == {json.dumps(TYPED_LOCATION)}"
FORMS_LINES = (
    "import pytest", "", "", "def test_err():", RAISES_LINE, "        build()", "", "",
    "def test_log(caplog):", "    run()", LOG_LINE, "", "", "def test_where():", LOCATION_LINE,
)
FORMS_FILE = "\n".join(FORMS_LINES) + "\n"
PROJECTS = {
    "many": {OLD_TEST: MANY_FILE},
    "forms": {OLD_TEST: FORMS_FILE},
    "gap_only": {OLD_TEST: SIZE_TEST, DEEP: DEEP_TEST},
    "gap_mixed": {OLD_TEST: DEBT_FILE, DEEP: DEEP_TEST},
    "oversized": {OLD_TEST: DEBT_FILE, BIG: DEBT_FILE + PADDING},
    "filled": {OLD_TEST: SIZE_TEST},
    "at_limit": {OLD_TEST: SIZE_TEST},
    "legacy": {OLD_TEST: DEBT_FILE},
    "plain": {OLD_TEST: SIZE_TEST},
}
LOOSE = {"loose": {MSGS: DEBT_FILE}}


def line_of(lines, text):
    return lines.index(text) + 1


def unit(line, rule, path=OLD_TEST):
    return f"{path}:{line} {rule}"


DEBT_UNIT = unit(line_of(DEBT_FILE.splitlines(), WORDING_ASSERT), ASSERTION)


def build(root, name, files, markers=(".git",)):
    repo = root / name
    repo.mkdir(parents=True)
    for marker in markers:
        (repo / marker).mkdir()
    for relative, content in files.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(content, encoding="utf-8")


def fill(repo, count):
    (repo / "assets").mkdir()
    for index in range(count):
        (repo / "assets" / f"{index}.txt").touch()


def without_units(projects):
    return {root: {"entries": {path: entry[:-1] for path, entry in project["entries"].items()}} for root, project in projects.items()}


class WordingDebtUnits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        (cls.root / "home").mkdir()
        for name, files in PROJECTS.items():
            build(cls.root, name, files)
        for name, files in LOOSE.items():
            build(cls.root, name, files, markers=())
        fill(cls.root / "filled", WALK_LIMIT)
        fill(cls.root / "at_limit", WALK_LIMIT - 1)
        limit = mock.patch.object(wording_debt, "_walk_limit", return_value=WALK_LIMIT)
        limit.start()
        cls.addClassCleanup(limit.stop)
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)

    def reports(self):
        return set((self.state / "reports").glob("*.json"))

    def hook(self, repo, event, payload):
        body = {"session_id": self._testMethodName, "hook_event_name": event, "cwd": str(repo), **payload}
        return run_hook(event, body, state_dir=self.state, git_view=NO_REPOSITORY)

    def start(self, name):
        repo = self.root / name
        self.hook(repo, "SessionStart", {"source": "startup"})
        return repo

    def written_rows(self, repo, relative=NEW_TEST, content=NEW_BEHAVIOR, identity=None):
        earlier = self.reports()
        tool_input = {"file_path": str(repo / relative), "content": content}
        agent = {"agent_id": "a1", "agent_type": WRITER} if identity is None else identity
        output = self.hook(repo, "PreToolUse", {"tool_name": "Write", "tool_input": tool_input, **agent})
        rows = [row for report in self.reports() - earlier for row in json.loads(report.read_text(encoding="utf-8"))]
        return output, [row for row in rows if row["rule"] in REPORT_ONLY]

    def lock_rows(self, repo, relative=NEW_TEST, content=NEW_BEHAVIOR):
        return self.written_rows(repo, relative, content)[1]

    def shape(self, repo, relative=NEW_TEST, content=NEW_BEHAVIOR):
        return sorted((row["rule"], row["path"], tuple(row.get("units", ()))) for row in self.lock_rows(repo, relative, content))

    def refused_debt_row(self, repo):
        output, rows = self.written_rows(repo, identity={})
        reason = output.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        [line] = [text for text in reason.splitlines() if f"({LOCK})" in text]
        [row] = rows
        return line.replace(row["path"], ""), row["units"]

    def drop_stored_units(self):
        path = self.state / "state" / self._testMethodName / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**state, DEBT_KEY: without_units(state[DEBT_KEY])}), encoding="utf-8")

    def test_report_lists_every_unit_and_shows_twenty_in_the_detail_with_the_omitted_count(self):
        """Protection against regressions. The test fails when a real bug enters the code it exercises."""
        repo = self.start("many")
        [row] = self.lock_rows(repo)
        rest = reduce(lambda text, name: text.replace(name, ""), (row["path"], str(repo), *row["units"]), row["detail"])
        shown = sum(name in row["detail"] for name in row["units"])
        self.assertEqual((len(row["units"]), shown, re.findall(r"\d+", rest)), (MANY, SHOWN, [str(MANY), str(MANY - SHOWN)]))

    def test_units_name_the_rule_of_a_log_an_exception_match_and_a_typed_location(self):
        """For a log line, assert on the level, the logger name, and the structured fields, and leave the sentence alone."""
        repo = self.start("forms")
        expected = (
            unit(line_of(FORMS_LINES, RAISES_LINE), EXCEPTION),
            unit(line_of(FORMS_LINES, LOG_LINE), LOG),
            unit(line_of(FORMS_LINES, LOCATION_LINE), LOCATION),
        )
        self.assertEqual(self.shape(repo), [(LOCK, str(repo / NEW_TEST), expected)])

    def test_unparsable_file_alone_gives_one_gap_row_at_that_file_and_no_debt_row(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        repo = self.start("gap_only")
        self.assertEqual(self.shape(repo), [(GAP, str(repo / DEEP), ())])

    def test_wording_tests_beside_an_unparsable_file_give_a_units_row_and_a_gap_row(self):
        """A test must verify what the code does for its client, never how it does it."""
        repo = self.start("gap_mixed")
        self.assertEqual(self.shape(repo), [(LOCK, str(repo / NEW_TEST), (DEBT_UNIT,)), (GAP, str(repo / DEEP), ())])

    def test_oversized_file_gives_a_gap_row_and_adds_no_units(self):
        """Name the bug. Describe one concrete regression that makes this test fail."""
        repo = self.start("oversized")
        self.assertEqual(self.shape(repo), [(LOCK, str(repo / NEW_TEST), (DEBT_UNIT,)), (GAP, str(repo / BIG), ())])

    def test_project_at_the_walk_limit_gives_no_row_and_one_entry_more_gives_one_walk_limit_gap_at_the_root(self):
        """Use a parameterized test for several inputs."""
        repos = {name: self.start(name) for name in ("at_limit", "filled")}
        gaps = {name: [(row["rule"], row["path"], row["detail"].endswith(f"{repo}: {WALK_REASON}")) for row in self.lock_rows(repo)]
                for name, repo in repos.items()}
        self.assertEqual(gaps, {"at_limit": [], "filled": [(GAP, str(repos["filled"]), True)]})

    def test_session_state_stored_without_units_is_rescanned(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        repo = self.start("legacy")
        self.drop_stored_units()
        self.assertEqual(self.shape(repo), [(LOCK, str(repo / NEW_TEST), (DEBT_UNIT,))])

    def test_scan_gap_does_not_raise_the_wording_test_count(self):
        """Name the bug. Describe one concrete regression that makes this test fail."""
        repo = self.start("gap_mixed")
        [row] = [row for row in self.lock_rows(repo) if row["rule"] == LOCK]
        rest = reduce(lambda text, name: text.replace(name, ""), (row["path"], str(repo), *row["units"]), row["detail"])
        self.assertEqual((row["units"], re.findall(r"\d+", rest)), ([DEBT_UNIT], ["1"]))

    def test_new_unparsable_test_file_in_a_project_without_debt_gives_no_row(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        repo = self.start("plain")
        self.assertEqual(self.written_rows(repo, NEW_TEST, DEEP_TEST), ({}, []))

    def test_units_are_relative_to_the_project_root_without_a_git_folder(self):
        """Output based. Feed input, check the return value."""
        repo = self.start("loose")
        expected = unit(line_of(DEBT_FILE.splitlines(), WORDING_ASSERT), ASSERTION, MSGS)
        self.assertEqual(self.shape(repo), [(LOCK, str(repo / NEW_TEST), (expected,))])

    def test_refusal_row_shows_the_unit_count_the_first_five_units_and_the_omitted_count(self):
        """Protection against regressions. The test fails when a real bug enters the code it exercises."""
        repo = self.start("many")
        line, units = self.refused_debt_row(repo)
        rest = reduce(lambda text, name: text.replace(name, ""), units, line)
        shown = [name in line for name in units]
        expected = [True] * SHOWN_IN_ROW + [False] * (MANY - SHOWN_IN_ROW)
        self.assertEqual((shown, re.findall(r"\d+", rest)[-2:]), (expected, [str(MANY), str(MANY - SHOWN_IN_ROW)]))


def finding(**extra):
    return Finding(
        family="self_protection", rule=LOCK, line=1, detail="d", force=True, snippet="s", action="a",
        path="p", severity=None, tool_use_id=None, **extra,
    )


class PythonParse(unittest.TestCase):
    def test_parser_overflow_flag_is_set_for_deep_nesting_and_clear_for_a_syntax_error(self):
        """Use a parameterized test for several inputs."""
        texts = {"deep": DEEP_TEST, "syntax": "def test_broken(:\n    pass\n"}
        flags = {name: parsed_python(text).overflow for name, text in texts.items()}
        self.assertEqual(flags, {"deep": True, "syntax": False})


class FindingUnits(unittest.TestCase):
    def test_finding_with_units_survives_a_round_trip_through_its_dict(self):
        """Output based. Feed input, check the return value."""
        original = finding(units=(unit(3, ASSERTION), unit(9, LOG)))
        self.assertEqual(Finding.from_dict(original.to_dict()), original)

    def test_finding_without_units_writes_no_units_key(self):
        """Prefer output based tests."""
        self.assertNotIn("units", finding().to_dict())


if __name__ == "__main__":
    unittest.main()
