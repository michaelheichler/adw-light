import hashlib
import json
import os
import sys
import time
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


NO_REPOSITORY = FakeGitView({"roots": {}})

LOCK = "wording_debt_lock"
GAP = "wording_test_gap"
REPORT_ONLY = {LOCK, GAP}
GATE = "test_write_gate"
WORDING = "wording_assertion"
DENY = "deny"
OBSERVED = "would_block"
NOTED = [(LOCK, OBSERVED)]
GAP_NOTED = [(GAP, OBSERVED)]
WRITER = "adw-test-writer"
SESSION = "debt-session"
CONFIG = ".agent-discipline.json"
OLD_TEST = "tests/test_old.py"
NEW_TEST = "tests/test_new.py"
PROSE = " ".join(("The", "file", "could", "not", "be", "opened", "today."))
NEW_PROSE = " ".join(("The", "disk", "could", "not", "be", "read", "at", "all."))
CITATION = '    """A test must verify what the code does for its client, never how it does it."""\n'
SIZE_TEST = "def test_size():\n" + CITATION + "    assert len([1, 2]) == 2\n"
WORDING_ASSERT = "    assert render() == " + json.dumps(PROSE) + "\n"
WORDING_TEST = "def test_msg():\n" + CITATION + WORDING_ASSERT
BEHAVIOR_ASSERT = "    assert render() is None\n"
WORDING_LINE = 6
ADDED_LINE = 7
SIZE_ASSERT_LINE = 3
WORDING_ASSERT_LINE = 8
NEW_ASSERT_LINE = 3
DEBT_FILE = SIZE_TEST + "\n\n" + WORDING_TEST
NEW_BEHAVIOR = "def test_sum():\n" + CITATION + "    assert 1 + 1 == 2\n"
SHORT_PROSE = " ".join(("The", "file", "is", "gone."))
SHORT_WORDING = "def test_a():\n assert f()==" + json.dumps(SHORT_PROSE) + "\n"
HEREDOC_THEN_PRINTF = "cat >notes.txt <<E\nx\nE\nprintf '" + SHORT_WORDING + "' >tests/test_z.py"
QUOTED_MARKER_PRINTF = "printf '# a << b\n" + SHORT_WORDING + "' >tests/test_z.py"
NEW_WORDING = "def test_notice():\n" + CITATION + "    assert render() == " + json.dumps(NEW_PROSE) + "\n"
CLEAN_FILE = SIZE_TEST
CLEAN_TEST = "tests/test_clean.py"
DEEP = "tests/test_deep.py"
OVERFLOW = "parser_overflow"
DEEP_TEST = "def test_deep():\n    assert " + "-" * 200_000 + "1\n"
SCAN_LIMIT = 1_000_000
PADDING = 'PAD = "' + "a" * SCAN_LIMIT + '"\n'
WALK_LIMIT = 12
PASSING_DELETES = (
    "rm " + OLD_TEST,
    "unlink " + OLD_TEST,
    "rm -f " + OLD_TEST + " " + CLEAN_TEST,
)
LOCKED_DELETES = (
    "rm " + OLD_TEST + " > log",
    "rm " + OLD_TEST + " && printf x > " + OLD_TEST,
    "bash -c " + json.dumps("rm " + OLD_TEST),
    "rm " + CLEAN_TEST,
)
PRODUCT_TEST = "def test_product():\n" + CITATION + "    assert 2 * 3 == 6\n"
PROJECTS = {
    "debt": DEBT_FILE, "clean": CLEAN_FILE, "window": DEBT_FILE, "allowed": DEBT_FILE, "rewritten": DEBT_FILE,
    "deleted": DEBT_FILE, "tracked": DEBT_FILE, "deep": DEEP_TEST, "big": DEBT_FILE + PADDING,
    "big_clean": SIZE_TEST + PADDING, "filled": CLEAN_FILE, "below": CLEAN_FILE, "gap_only": CLEAN_FILE,
}
UNSCANNABLE_DELETES = (("gap_only", "rm " + DEEP), ("big", "rm " + OLD_TEST))
GIT_PASSING_DELETES = (
    "git rm " + OLD_TEST,
    "git rm -q " + OLD_TEST,
    "git -C tests rm test_old.py",
)
GIT_LOCKED_DELETES = (
    "git rm --cached " + OLD_TEST,
    "git rm -n " + OLD_TEST,
)
OVERSIZED_LOCKED_DELETES = (
    "rm " + OLD_TEST + " > log",
    "rm " + OLD_TEST + " && printf x > " + OLD_TEST,
)


def project(root, name, old_test):
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / OLD_TEST).write_text(old_test, encoding="utf-8")
    return repo


def fill(repo, count):
    (repo / "assets").mkdir()
    for index in range(count):
        (repo / "assets" / f"{index}.txt").touch()


class WordingDebtLock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        (cls.root / "home").mkdir()
        for name, old_test in PROJECTS.items():
            project(cls.root, name, old_test)
        (cls.root / "tracked" / CLEAN_TEST).write_text(CLEAN_FILE, encoding="utf-8")
        (cls.root / "gap_only" / DEEP).write_text(DEEP_TEST, encoding="utf-8")
        (cls.root / "window" / CONFIG).write_text(json.dumps({"tests_allow_until": int(time.time()) + 3600}), encoding="utf-8")
        (cls.root / "allowed" / CONFIG).write_text(json.dumps({"tests": "allow"}), encoding="utf-8")
        fill(cls.root / "filled", WALK_LIMIT)
        fill(cls.root / "below", WALK_LIMIT - 1)
        limit = mock.patch.object(wording_debt, "_walk_limit", return_value=WALK_LIMIT)
        limit.start()
        cls.addClassCleanup(limit.stop)
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)

    def repo(self, name):
        return self.root / name

    def reports(self):
        return {report: json.loads(report.read_text(encoding="utf-8")) for report in (self.state / "reports").glob("*.json")}

    def ledger(self):
        path = self.state / "ledger" / "ledger.jsonl"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def hook(self, event, payload):
        before = self.reports()
        written = len(self.ledger())
        body = {"session_id": self._testMethodName, "hook_event_name": event, **payload}
        response = run_hook(event, body, state_dir=self.state, git_view=NO_REPOSITORY)
        rows = [row for report, batch in self.reports().items() for row in batch[len(before.get(report, [])):]]
        noted = [(row["rule"], row["outcome"]) for row in map(json.loads, self.ledger()[written:]) if row.get("rule") in REPORT_ONLY]
        return response, rows, sorted(noted)

    def start(self, repo):
        self.hook("SessionStart", {"cwd": str(repo), "source": "startup"})

    def started(self, name):
        repo = self.repo(name)
        self.start(repo)
        return repo

    def tool(self, repo, tool_name, tool_input, agent=WRITER):
        identity = {"agent_id": "a1", "agent_type": agent} if agent else {}
        return self.hook("PreToolUse", {"cwd": str(repo), "tool_name": tool_name, "tool_input": tool_input, **identity})

    def write(self, repo, relative, content, agent=WRITER):
        return self.tool(repo, "Write", {"file_path": str(repo / relative), "content": content}, agent)

    def edit(self, repo, old, new, agent=WRITER, relative=OLD_TEST):
        tool_input = {"file_path": str(repo / relative), "old_string": old, "new_string": new}
        return self.tool(repo, "Edit", tool_input, agent)

    def verdict(self, response):
        output, rows, noted = response
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, sorted((row["rule"], row["line"]) for row in rows if row["rule"] in {LOCK, GATE, GAP}), noted

    def denial_and_debt_outcome(self, response):
        output, _, noted = response
        return output.get("hookSpecificOutput", {}).get("permissionDecision"), noted

    def refusal(self, response):
        output, rows, noted = response
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, sorted((row["rule"], row["line"]) for row in rows), noted

    def bash(self, repo, command):
        return self.verdict(self.tool(repo, "Bash", {"command": command}))

    def overflow_gaps(self, response, repo):
        output, rows, noted = response
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        reason = f"{repo / DEEP}: {OVERFLOW}"
        gaps = [(row["rule"], row["detail"].endswith(reason)) for row in rows if row["rule"] in {LOCK, GATE, GAP}]
        return decision, gaps, noted

    def debt_entries(self, repo):
        path = self.state / "state" / self._testMethodName / "state.json"
        return set(json.loads(path.read_text(encoding="utf-8"))["wording_debt"][str(repo)]["entries"])

    def test_new_behavior_test_passes_and_the_debt_is_only_noted(self):
        repo = self.started("debt")
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (None, [(LOCK, 1)], NOTED))

    def test_shell_append_to_a_test_file_passes_and_the_debt_is_only_noted(self):
        """The test fails when a real bug enters the code it exercises."""
        repo = self.started("debt")
        command = "printf '\\n' >> " + OLD_TEST
        self.assertEqual(self.verdict(self.tool(repo, "Bash", {"command": command})), (None, [(LOCK, WORDING_LINE)], NOTED))

    def test_in_place_shell_edit_of_a_test_passes_and_the_debt_is_only_noted(self):
        """A test must verify what the code does for its client, never how it does it."""
        repo = self.started("debt")
        command = "sed -i.bak 's/2/3/' " + OLD_TEST
        self.assertEqual(self.refusal(self.tool(repo, "Bash", {"command": command})), (None, [(LOCK, 1)], NOTED))

    def test_main_agent_is_refused_by_the_test_write_gate_while_the_debt_is_only_noted(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        repo = self.started("debt")
        response = self.write(repo, NEW_TEST, NEW_BEHAVIOR, agent=None)
        self.assertEqual(self.verdict(response), (DENY, [(GATE, 1), (LOCK, 1)], NOTED))

    def test_new_wording_assertion_next_to_old_debt_is_denied(self):
        """Protection against regressions. The test fails when a real bug enters the code it exercises."""
        repo = self.started("debt")
        expected = (DENY, [(WORDING, NEW_ASSERT_LINE), (LOCK, 1)], NOTED)
        self.assertEqual(self.refusal(self.write(repo, NEW_TEST, NEW_WORDING)), expected)

    def test_multiline_printf_of_a_wording_test_after_a_heredoc_is_denied_but_not_for_debt(self):
        """Name the bug. Describe one concrete regression that makes this test fail."""
        repo = self.started("debt")
        response = self.tool(repo, "Bash", {"command": HEREDOC_THEN_PRINTF})
        self.assertEqual(self.denial_and_debt_outcome(response), (DENY, NOTED))

    def test_multiline_printf_with_a_quoted_heredoc_marker_is_denied_but_not_for_debt(self):
        """Write a unit test for domain logic and algorithms."""
        repo = self.started("debt")
        response = self.tool(repo, "Bash", {"command": QUOTED_MARKER_PRINTF})
        self.assertEqual(self.denial_and_debt_outcome(response), (DENY, NOTED))

    def test_test_writer_changing_an_old_wording_assertion_to_new_prose_is_denied(self):
        """Feed input, check the return value."""
        repo = self.started("debt")
        response = self.edit(repo, json.dumps(PROSE), json.dumps(NEW_PROSE))
        self.assertEqual(self.refusal(response), (DENY, [(WORDING, WORDING_ASSERT_LINE), (LOCK, WORDING_LINE)], NOTED))

    def test_test_writer_changing_an_old_behavior_assertion_to_prose_is_denied(self):
        """The test stays green when you restructure the code without changing its behavior."""
        repo = self.started("debt")
        response = self.edit(repo, "len([1, 2]) == 2", "len([1, 2]) == " + json.dumps(NEW_PROSE))
        self.assertEqual(self.refusal(response), (DENY, [(WORDING, SIZE_ASSERT_LINE), (LOCK, 1)], NOTED))

    def test_rewriting_the_wording_test_into_a_behavior_test_passes(self):
        repo = self.started("debt")
        decision, rows, noted = self.verdict(self.edit(repo, WORDING_ASSERT, BEHAVIOR_ASSERT))
        self.assertEqual((decision == DENY, rows, noted), (False, [], []))

    def test_deleting_the_wording_test_passes(self):
        repo = self.started("debt")
        decision, rows, noted = self.verdict(self.write(repo, OLD_TEST, SIZE_TEST))
        self.assertEqual((decision == DENY, rows, noted), (False, [], []))

    def test_replacing_one_wording_test_with_one_new_test_passes(self):
        repo = self.started("debt")
        decision, rows, noted = self.verdict(self.write(repo, OLD_TEST, SIZE_TEST + "\n\n" + NEW_BEHAVIOR))
        self.assertEqual((decision == DENY, rows, noted), (False, [], []))

    def test_rewrite_that_adds_a_second_new_test_passes_and_is_noted_at_the_first_added_test(self):
        repo = self.started("debt")
        content = SIZE_TEST + "\n\n\n" + NEW_BEHAVIOR + "\n\n" + PRODUCT_TEST
        self.assertEqual(self.verdict(self.write(repo, OLD_TEST, content)), (None, [(LOCK, ADDED_LINE)], NOTED))

    def test_edit_that_keeps_the_debt_passes_and_is_noted_at_the_remaining_wording_test(self):
        repo = self.started("debt")
        response = self.edit(repo, "len([1, 2]) == 2", "len([1, 2, 3]) == 3")
        self.assertEqual(self.verdict(response), (None, [(LOCK, WORDING_LINE)], NOTED))

    def test_open_timed_window_lets_the_main_agent_write_a_test_while_the_debt_is_noted(self):
        repo = self.started("window")
        response = self.write(repo, NEW_TEST, NEW_BEHAVIOR, agent=None)
        self.assertEqual(self.verdict(response), (None, [(LOCK, 1)], NOTED))

    def test_allow_tests_policy_lets_the_main_agent_write_a_test_while_the_debt_is_noted(self):
        repo = self.started("allowed")
        response = self.write(repo, NEW_TEST, NEW_BEHAVIOR, agent=None)
        self.assertEqual(self.verdict(response), (None, [(LOCK, 1)], NOTED))

    def test_project_without_debt_lets_the_test_writer_add_a_test(self):
        repo = self.started("clean")
        self.assertEqual(self.write(repo, NEW_TEST, NEW_BEHAVIOR), ({}, [], []))

    def test_project_without_debt_gives_the_main_agent_only_the_test_write_gate(self):
        repo = self.started("clean")
        response = self.write(repo, NEW_TEST, NEW_BEHAVIOR, agent=None)
        self.assertEqual(self.verdict(response), (DENY, [(GATE, 1)], []))

    def test_debt_in_one_project_does_not_lock_a_clean_project_in_the_same_session(self):
        self.started("debt")
        clean = self.repo("clean")
        self.assertEqual(self.write(clean, NEW_TEST, NEW_BEHAVIOR), ({}, [], []))

    def test_debt_project_is_noted_after_a_session_started_in_a_clean_one(self):
        self.started("clean")
        repo = self.repo("debt")
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (None, [(LOCK, 1)], NOTED))

    def test_write_into_a_clean_project_from_a_debt_project_cwd_is_not_locked(self):
        repo = self.started("debt")
        clean = self.repo("clean")
        tool_input = {"file_path": str(clean / NEW_TEST), "content": NEW_BEHAVIOR}
        self.assertEqual(self.verdict(self.tool(repo, "Write", tool_input)), (None, [], []))

    def test_recorded_rewrite_clears_the_debt_for_the_next_new_test(self):
        repo = self.started("rewritten")
        rewritten = DEBT_FILE.replace(WORDING_ASSERT, BEHAVIOR_ASSERT)
        (repo / OLD_TEST).write_text(rewritten, encoding="utf-8")
        edit_input = {"file_path": str(repo / OLD_TEST), "old_string": WORDING_ASSERT, "new_string": BEHAVIOR_ASSERT}
        posted = {"cwd": str(repo), "tool_name": "Edit", "tool_input": edit_input, "agent_id": "a1", "agent_type": WRITER}
        self.hook("PostToolUse", {**posted, "tool_response": {"filePath": str(repo / OLD_TEST)}})
        self.assertEqual(self.write(repo, NEW_TEST, NEW_BEHAVIOR), ({}, [], []))

    def test_shell_delete_of_the_wording_test_file_passes_the_lock(self):
        repo = self.started("tracked")
        for command in PASSING_DELETES:
            with self.subTest(command=command):
                decision, rows, noted = self.bash(repo, command)
                self.assertEqual((decision == DENY, rows, noted), (False, [], []))

    def test_shell_command_that_keeps_or_hides_the_file_passes_and_is_noted(self):
        repo = self.started("tracked")
        for command in LOCKED_DELETES:
            with self.subTest(command=command):
                self.assertEqual(self.bash(repo, command), (None, [(LOCK, 1)], NOTED))

    def test_recorded_shell_delete_clears_the_debt_for_the_next_new_test(self):
        repo = self.started("deleted")
        command = {"command": "rm " + OLD_TEST}
        self.tool(repo, "Bash", command)
        (repo / OLD_TEST).unlink()
        posted = {"cwd": str(repo), "tool_name": "Bash", "tool_input": command, "agent_id": "a1", "agent_type": WRITER}
        self.hook("PostToolUse", {**posted, "tool_response": {"stdout": "", "stderr": ""}})
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (None, [], []))

    def test_unparsable_test_file_still_gets_the_session_contract(self):
        clean = self.repo("clean")
        deep = self.repo("deep")
        contract = self.hook("SessionStart", {"cwd": str(clean), "source": "startup"})[0]
        self.assertEqual(self.hook("SessionStart", {"cwd": str(deep), "source": "startup"})[0], contract)

    def test_unparsable_test_file_is_noted_as_a_scan_gap(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        repo = self.started("deep")
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (None, [(GAP, 1)], GAP_NOTED))

    def test_padded_rewrite_that_keeps_the_wording_test_passes_and_is_noted(self):
        repo = self.started("debt")
        self.assertEqual(self.verdict(self.write(repo, OLD_TEST, DEBT_FILE + PADDING)), (None, [(LOCK, 1)], NOTED))

    def test_padded_new_test_file_passes_and_is_noted_while_the_project_has_debt(self):
        repo = self.started("debt")
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR + PADDING)), (None, [(LOCK, 1)], NOTED))

    def test_shrinking_an_oversized_wording_test_file_passes_and_is_noted_as_a_scan_gap(self):
        """Name the bug. Describe one concrete regression that makes this test fail."""
        repo = self.started("big")
        self.assertEqual(self.verdict(self.write(repo, OLD_TEST, SIZE_TEST)), (None, [(GAP, 1)], GAP_NOTED))

    def test_rewriting_the_wording_test_inside_an_oversized_file_passes_and_is_noted_as_a_scan_gap(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        repo = self.started("big")
        self.assertEqual(self.verdict(self.edit(repo, WORDING_ASSERT, BEHAVIOR_ASSERT)), (None, [(GAP, 1)], GAP_NOTED))

    def test_shell_delete_of_an_unscannable_test_file_passes_and_drops_its_scan_gap_row(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        verdicts = {(name, command): self.bash(self.started(name), command) for name, command in UNSCANNABLE_DELETES}
        self.assertEqual(verdicts, dict.fromkeys(UNSCANNABLE_DELETES, (None, [], [])))

    def test_shell_delete_of_an_unparsable_test_file_drops_it_from_the_session_debt(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        repo = self.started("gap_only")
        self.bash(repo, "rm " + DEEP)
        self.assertEqual(self.debt_entries(repo), {str(repo / OLD_TEST)})

    def test_test_writer_rewriting_an_unparsable_test_file_passes_with_one_parser_overflow_gap(self):
        """Name the bug. Describe one concrete regression that makes this test fail."""
        repo = self.started("gap_only")
        response = self.write(repo, DEEP, NEW_BEHAVIOR)
        self.assertEqual(self.overflow_gaps(response, repo), (None, [(GAP, True)], GAP_NOTED))

    def test_test_writer_editing_an_unparsable_test_file_passes_with_one_parser_overflow_gap(self):
        """Protection against regressions. The test fails when a real bug enters the code it exercises."""
        repo = self.started("gap_only")
        response = self.edit(repo, "def test_deep():", "def test_deeper():", relative=DEEP)
        self.assertEqual(self.overflow_gaps(response, repo), (None, [(GAP, True)], GAP_NOTED))

    def test_unparsable_test_file_on_disk_is_noted_with_the_parser_overflow_reason(self):
        """Output based. Feed input, check the return value."""
        repo = self.started("gap_only")
        response = self.write(repo, NEW_TEST, NEW_BEHAVIOR)
        self.assertEqual(self.overflow_gaps(response, repo), (None, [(GAP, True)], GAP_NOTED))

    def test_second_write_reuses_the_cached_parser_overflow_entry_without_reading_the_file(self):
        """Name the bug. Describe one concrete regression that makes this test fail."""
        repo = self.started("gap_only")
        self.write(repo, NEW_TEST, NEW_BEHAVIOR)
        with mock.patch.object(wording_debt, "read_regular_text", wraps=wording_debt.read_regular_text) as reads:
            response = self.write(repo, NEW_TEST, NEW_BEHAVIOR)
        read_paths = [str(call.args[0]) for call in reads.call_args_list]
        self.assertEqual((self.overflow_gaps(response, repo), read_paths), ((None, [(GAP, True)], GAP_NOTED), []))

    def test_new_unparsable_test_file_in_a_project_with_debt_passes_with_a_parser_overflow_gap(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        repo = self.started("debt")
        output, rows, noted = self.write(repo, DEEP, DEEP_TEST)
        reasons = sorted((row["rule"], row["detail"].endswith(f"{repo / DEEP}: {OVERFLOW}")) for row in rows)
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual((decision, reasons), (None, [(LOCK, False), (GAP, True)]))

    def test_main_agent_writing_unparsable_test_content_gets_the_test_write_gate_refusal(self):
        """A test must verify what the code does for its client, never how it does it."""
        repo = self.started("clean")
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, DEEP_TEST, agent=None)), (DENY, [(GATE, 1)], []))

    def test_shell_command_that_keeps_an_oversized_wording_test_file_passes_and_is_noted_as_a_scan_gap(self):
        """Use a parameterized test for several inputs."""
        repo = self.started("big")
        verdicts = {command: self.bash(repo, command) for command in OVERSIZED_LOCKED_DELETES}
        self.assertEqual(verdicts, dict.fromkeys(OVERSIZED_LOCKED_DELETES, (None, [(GAP, 1)], GAP_NOTED)))

    def test_emptying_an_oversized_wording_test_file_passes_and_keeps_its_scan_gap_row(self):
        """Feed input, check the return value."""
        repo = self.started("big")
        self.assertEqual(self.verdict(self.write(repo, OLD_TEST, "")), (None, [(GAP, 1)], GAP_NOTED))

    def test_oversized_test_file_without_wording_tests_is_noted_as_a_scan_gap(self):
        """Protection against regressions. The test fails when a real bug enters the code it exercises."""
        repo = self.started("big_clean")
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (None, [(GAP, 1)], GAP_NOTED))

    def test_project_past_the_walk_limit_is_noted_as_a_scan_gap(self):
        """Write a unit test for domain logic and algorithms."""
        repo = self.started("filled")
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (None, [(GAP, 1)], GAP_NOTED))

    def test_project_at_the_walk_limit_without_wording_tests_is_not_locked(self):
        """Feed input, check the return value."""
        repo = self.started("below")
        self.assertEqual(self.write(repo, NEW_TEST, NEW_BEHAVIOR), ({}, [], []))

class GitIndexDeletes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        cls.repo = cls.root / "tracked"
        (cls.root / "home").mkdir()
        files = {OLD_TEST: DEBT_FILE, CLEAN_TEST: CLEAN_FILE}
        for relative, content in files.items():
            (cls.repo / relative).parent.mkdir(parents=True, exist_ok=True)
            (cls.repo / relative).write_text(content, encoding="utf-8")
        cls.git = FakeGitView({"roots": {str(cls.repo): {"index": files, "disk": files}}})
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home"), "PATH": os.environ.get("PATH", "")}, clear=True)
        environment.start()
        cls.addClassCleanup(environment.stop)

    def reports(self):
        return {report: json.loads(report.read_text(encoding="utf-8")) for report in (self.state / "reports").glob("*.json")}

    def hook(self, session, event, payload):
        before = self.reports()
        body = {"session_id": session, "hook_event_name": event, **payload}
        written = len(self.ledger())
        response = run_hook(event, body, state_dir=self.state, git_view=self.git)
        rows = [row for report, batch in self.reports().items() for row in batch[len(before.get(report, [])):]]
        noted = [(row["rule"], row["outcome"]) for row in map(json.loads, self.ledger()[written:]) if row.get("rule") == LOCK]
        return response, rows, sorted(noted)

    def ledger(self):
        path = self.state / "ledger" / "ledger.jsonl"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def bash(self, command):
        session = "s" + hashlib.sha256(f"{self.id()}-{command}".encode()).hexdigest()[:20]
        self.hook(session, "SessionStart", {"cwd": str(self.repo), "source": "startup"})
        tool_input = {"cwd": str(self.repo), "tool_name": "Bash", "tool_input": {"command": command}, "agent_id": "a1", "agent_type": WRITER}
        output, rows, noted = self.hook(session, "PreToolUse", tool_input)
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision == DENY, sorted((row["rule"], row["line"]) for row in rows if row["rule"] in {LOCK, GATE}), noted

    def test_git_delete_of_the_wording_test_file_passes_the_lock(self):
        verdicts = {command: self.bash(command) for command in GIT_PASSING_DELETES}
        self.assertEqual(dict.fromkeys(GIT_PASSING_DELETES, (False, [], [])), verdicts)

    def test_git_command_that_keeps_or_hides_the_file_passes_and_is_noted(self):
        verdicts = {command: self.bash(command) for command in GIT_LOCKED_DELETES}
        self.assertEqual(dict.fromkeys(GIT_LOCKED_DELETES, (False, [(LOCK, 1)], NOTED)), verdicts)


if __name__ == "__main__":
    unittest.main()
