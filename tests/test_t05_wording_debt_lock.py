from quiet_git import disable_background_maintenance
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
LOCK = "wording_debt_lock"
GATE = "test_write_gate"
DENY = "deny"
WRITER = "adw-test-writer"
SESSION = "debt-session"
CONFIG = ".agent-discipline.json"
OLD_TEST = "tests/test_old.py"
NEW_TEST = "tests/test_new.py"
PROSE = " ".join(("The", "file", "could", "not", "be", "opened", "today."))
SIZE_TEST = "def test_size():\n    assert len([1, 2]) == 2\n"
WORDING_ASSERT = "    assert render() == " + json.dumps(PROSE) + "\n"
WORDING_TEST = "def test_msg():\n" + WORDING_ASSERT
BEHAVIOR_ASSERT = "    assert render() is None\n"
WORDING_LINE = 5
ADDED_LINE = 6
DEBT_FILE = SIZE_TEST + "\n\n" + WORDING_TEST
NEW_BEHAVIOR = "def test_sum():\n    assert 1 + 1 == 2\n"
CLEAN_FILE = SIZE_TEST
CLEAN_TEST = "tests/test_clean.py"
DEEP_TEST = "def test_deep():\n    assert " + "-" * 200_000 + "1\n"
SCAN_LIMIT = 1_000_000
PADDING = 'PAD = "' + "a" * SCAN_LIMIT + '"\n'
WALK_LIMIT = 50_000
PASSING_DELETES = (
    "rm " + OLD_TEST,
    "git rm " + OLD_TEST,
    "git rm -q " + OLD_TEST,
    "git -C tests rm test_old.py",
    "unlink " + OLD_TEST,
    "rm -f " + OLD_TEST + " " + CLEAN_TEST,
)
LOCKED_DELETES = (
    "git rm --cached " + OLD_TEST,
    "git rm -n " + OLD_TEST,
    "rm " + OLD_TEST + " > log",
    "rm " + OLD_TEST + " && printf x > " + OLD_TEST,
    "bash -c " + json.dumps("rm " + OLD_TEST),
    "rm " + CLEAN_TEST,
)
OVERSIZED_LOCKED_DELETES = (
    "rm " + OLD_TEST + " > log",
    "rm " + OLD_TEST + " && printf x > " + OLD_TEST,
)


class WordingDebtLock(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.home.mkdir()

    def project(self, name, old_test):
        repo = self.root / name
        (repo / "tests").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        disable_background_maintenance(str(repo))
        (repo / OLD_TEST).write_text(old_test, encoding="utf-8")
        return repo

    def reports(self):
        return {report: json.loads(report.read_text(encoding="utf-8")) for report in (self.state / "reports").glob("*.json")}

    def hook(self, event, payload):
        before = self.reports()
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps({"session_id": SESSION, "hook_event_name": event, **payload}),
            text=True, capture_output=True, check=True,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        rows = [row for report, batch in self.reports().items() for row in batch[len(before.get(report, [])):]]
        return json.loads(call.stdout), rows

    def start(self, repo):
        self.hook("SessionStart", {"cwd": str(repo), "source": "startup"})

    def tool(self, repo, tool_name, tool_input, agent=WRITER):
        identity = {"agent_id": "a1", "agent_type": agent} if agent else {}
        return self.hook("PreToolUse", {"cwd": str(repo), "tool_name": tool_name, "tool_input": tool_input, **identity})

    def write(self, repo, relative, content, agent=WRITER):
        return self.tool(repo, "Write", {"file_path": str(repo / relative), "content": content}, agent)

    def edit(self, repo, old, new, agent=WRITER):
        tool_input = {"file_path": str(repo / OLD_TEST), "old_string": old, "new_string": new}
        return self.tool(repo, "Edit", tool_input, agent)

    def verdict(self, response):
        output, rows = response
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, sorted((row["rule"], row["line"]) for row in rows if row["rule"] in {LOCK, GATE})

    def debt_project(self, name="debt"):
        repo = self.project(name, DEBT_FILE)
        self.start(repo)
        return repo

    def tracked_debt_project(self, name):
        repo = self.project(name, DEBT_FILE)
        (repo / CLEAN_TEST).write_text(CLEAN_FILE, encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        self.start(repo)
        return repo

    def bash(self, repo, command):
        return self.verdict(self.tool(repo, "Bash", {"command": command}))

    def oversized_project(self, old_test, name="big"):
        repo = self.project(name, old_test + PADDING)
        self.start(repo)
        return repo

    def filled_project(self, fillers):
        repo = self.project("filled", CLEAN_FILE)
        (repo / "assets").mkdir()
        for index in range(fillers):
            (repo / "assets" / f"{index}.txt").touch()
        self.start(repo)
        return repo

    def test_new_behavior_test_is_denied_while_the_project_has_debt(self):
        repo = self.debt_project()
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (DENY, [(LOCK, 1)]))

    def test_shell_write_of_a_new_test_is_denied_while_the_project_has_debt(self):
        repo = self.debt_project()
        command = "printf " + json.dumps(NEW_BEHAVIOR) + " > " + NEW_TEST
        self.assertEqual(self.verdict(self.tool(repo, "Bash", {"command": command})), (DENY, [(LOCK, 1)]))

    def test_in_place_shell_edit_of_a_test_is_denied_while_the_project_has_debt(self):
        repo = self.debt_project()
        command = "sed -i.bak 's/2/3/' " + OLD_TEST
        self.assertEqual(self.verdict(self.tool(repo, "Bash", {"command": command})), (DENY, [(LOCK, 1)]))

    def test_main_agent_gets_the_lock_next_to_the_test_write_gate(self):
        repo = self.debt_project()
        response = self.write(repo, NEW_TEST, NEW_BEHAVIOR, agent=None)
        self.assertEqual(self.verdict(response), (DENY, [(GATE, 1), (LOCK, 1)]))

    def test_rewriting_the_wording_test_into_a_behavior_test_passes(self):
        repo = self.debt_project()
        decision, rows = self.verdict(self.edit(repo, WORDING_ASSERT, BEHAVIOR_ASSERT))
        self.assertEqual((decision == DENY, rows), (False, []))

    def test_deleting_the_wording_test_passes(self):
        repo = self.debt_project()
        decision, rows = self.verdict(self.write(repo, OLD_TEST, SIZE_TEST))
        self.assertEqual((decision == DENY, rows), (False, []))

    def test_replacing_one_wording_test_with_one_new_test_passes(self):
        repo = self.debt_project()
        decision, rows = self.verdict(self.write(repo, OLD_TEST, SIZE_TEST + "\n\n" + NEW_BEHAVIOR))
        self.assertEqual((decision == DENY, rows), (False, []))

    def test_rewrite_that_adds_a_second_new_test_is_denied_at_the_first_added_test(self):
        repo = self.debt_project()
        extra = "def test_product():\n    assert 2 * 3 == 6\n"
        content = SIZE_TEST + "\n\n\n" + NEW_BEHAVIOR + "\n\n" + extra
        self.assertEqual(self.verdict(self.write(repo, OLD_TEST, content)), (DENY, [(LOCK, ADDED_LINE)]))

    def test_edit_that_keeps_the_debt_is_denied_at_the_remaining_wording_test(self):
        repo = self.debt_project()
        response = self.edit(repo, "len([1, 2]) == 2", "len([1, 2, 3]) == 3")
        self.assertEqual(self.verdict(response), (DENY, [(LOCK, WORDING_LINE)]))

    def test_open_timed_window_does_not_lift_the_lock(self):
        repo = self.project("debt", DEBT_FILE)
        (repo / CONFIG).write_text(json.dumps({"tests_allow_until": int(time.time()) + 3600}), encoding="utf-8")
        self.start(repo)
        response = self.write(repo, NEW_TEST, NEW_BEHAVIOR, agent=None)
        self.assertEqual(self.verdict(response), (DENY, [(LOCK, 1)]))

    def test_allow_tests_policy_does_not_lift_the_lock(self):
        repo = self.project("debt", DEBT_FILE)
        (repo / CONFIG).write_text(json.dumps({"tests": "allow"}), encoding="utf-8")
        self.start(repo)
        response = self.write(repo, NEW_TEST, NEW_BEHAVIOR, agent=None)
        self.assertEqual(self.verdict(response), (DENY, [(LOCK, 1)]))

    def test_project_without_debt_lets_the_test_writer_add_a_test(self):
        repo = self.project("clean", CLEAN_FILE)
        self.start(repo)
        self.assertEqual(self.write(repo, NEW_TEST, NEW_BEHAVIOR), ({}, []))

    def test_project_without_debt_gives_the_main_agent_only_the_test_write_gate(self):
        repo = self.project("clean", CLEAN_FILE)
        self.start(repo)
        response = self.write(repo, NEW_TEST, NEW_BEHAVIOR, agent=None)
        self.assertEqual(self.verdict(response), (DENY, [(GATE, 1)]))

    def test_debt_in_one_project_does_not_lock_a_clean_project_in_the_same_session(self):
        self.debt_project()
        clean = self.project("clean", CLEAN_FILE)
        self.assertEqual(self.write(clean, NEW_TEST, NEW_BEHAVIOR), ({}, []))

    def test_debt_project_is_locked_after_a_session_started_in_a_clean_one(self):
        clean = self.project("clean", CLEAN_FILE)
        self.start(clean)
        repo = self.project("debt", DEBT_FILE)
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (DENY, [(LOCK, 1)]))

    def test_write_into_a_clean_project_from_a_debt_project_cwd_is_not_locked(self):
        repo = self.debt_project()
        clean = self.project("clean", CLEAN_FILE)
        tool_input = {"file_path": str(clean / NEW_TEST), "content": NEW_BEHAVIOR}
        self.assertEqual(self.verdict(self.tool(repo, "Write", tool_input)), (None, []))

    def test_recorded_rewrite_clears_the_debt_for_the_next_new_test(self):
        repo = self.debt_project()
        rewritten = DEBT_FILE.replace(WORDING_ASSERT, BEHAVIOR_ASSERT)
        (repo / OLD_TEST).write_text(rewritten, encoding="utf-8")
        edit_input = {"file_path": str(repo / OLD_TEST), "old_string": WORDING_ASSERT, "new_string": BEHAVIOR_ASSERT}
        posted = {"cwd": str(repo), "tool_name": "Edit", "tool_input": edit_input, "agent_id": "a1", "agent_type": WRITER}
        self.hook("PostToolUse", {**posted, "tool_response": {"filePath": str(repo / OLD_TEST)}})
        self.assertEqual(self.write(repo, NEW_TEST, NEW_BEHAVIOR), ({}, []))

    def test_shell_delete_of_the_wording_test_file_passes_the_lock(self):
        for index, command in enumerate(PASSING_DELETES):
            with self.subTest(command=command):
                decision, rows = self.bash(self.tracked_debt_project(f"debt{index}"), command)
                self.assertEqual((decision == DENY, rows), (False, []))

    def test_shell_command_that_keeps_or_hides_the_file_is_locked(self):
        for index, command in enumerate(LOCKED_DELETES):
            with self.subTest(command=command):
                self.assertEqual(self.bash(self.tracked_debt_project(f"debt{index}"), command), (DENY, [(LOCK, 1)]))

    def test_recorded_shell_delete_clears_the_debt_for_the_next_new_test(self):
        repo = self.debt_project()
        command = {"command": "rm " + OLD_TEST}
        self.tool(repo, "Bash", command)
        (repo / OLD_TEST).unlink()
        posted = {"cwd": str(repo), "tool_name": "Bash", "tool_input": command, "agent_id": "a1", "agent_type": WRITER}
        self.hook("PostToolUse", {**posted, "tool_response": {"stdout": "", "stderr": ""}})
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (None, []))

    def test_unparsable_test_file_still_gets_the_session_contract(self):
        clean = self.project("clean", CLEAN_FILE)
        deep = self.project("deep", DEEP_TEST)
        contract = self.hook("SessionStart", {"cwd": str(clean), "source": "startup"})[0]
        self.assertEqual(self.hook("SessionStart", {"cwd": str(deep), "source": "startup"})[0], contract)

    def test_unparsable_test_file_counts_as_debt(self):
        repo = self.project("deep", DEEP_TEST)
        self.start(repo)
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (DENY, [(LOCK, 1)]))


    def test_padded_rewrite_that_keeps_the_wording_test_is_denied(self):
        repo = self.debt_project()
        self.assertEqual(self.verdict(self.write(repo, OLD_TEST, DEBT_FILE + PADDING)), (DENY, [(LOCK, 1)]))

    def test_padded_new_test_file_is_denied_while_the_project_has_debt(self):
        repo = self.debt_project()
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR + PADDING)), (DENY, [(LOCK, 1)]))

    def test_shrinking_an_oversized_wording_test_file_is_denied(self):
        repo = self.oversized_project(DEBT_FILE)
        self.assertEqual(self.verdict(self.write(repo, OLD_TEST, SIZE_TEST)), (DENY, [(LOCK, 1)]))

    def test_rewriting_the_wording_test_inside_an_oversized_file_is_denied(self):
        repo = self.oversized_project(DEBT_FILE)
        self.assertEqual(self.verdict(self.edit(repo, WORDING_ASSERT, BEHAVIOR_ASSERT)), (DENY, [(LOCK, 1)]))

    def test_shell_delete_of_an_oversized_wording_test_file_passes_the_lock(self):
        repo = self.oversized_project(DEBT_FILE)
        self.assertEqual(self.bash(repo, "rm " + OLD_TEST), (None, []))

    def test_shell_command_that_keeps_an_oversized_wording_test_file_is_locked(self):
        for index, command in enumerate(OVERSIZED_LOCKED_DELETES):
            with self.subTest(command=command):
                repo = self.oversized_project(DEBT_FILE, f"big{index}")
                self.assertEqual(self.bash(repo, command), (DENY, [(LOCK, 1)]))

    def test_emptying_an_oversized_wording_test_file_passes_the_lock(self):
        repo = self.oversized_project(DEBT_FILE)
        self.assertEqual(self.verdict(self.write(repo, OLD_TEST, "")), (None, []))

    def test_oversized_test_file_without_wording_tests_counts_as_debt(self):
        repo = self.oversized_project(SIZE_TEST)
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (DENY, [(LOCK, 1)]))

    def test_project_past_the_walk_limit_is_locked_without_wording_tests(self):
        repo = self.filled_project(WALK_LIMIT)
        self.assertEqual(self.verdict(self.write(repo, NEW_TEST, NEW_BEHAVIOR)), (DENY, [(LOCK, 1)]))

    def test_project_just_below_the_walk_limit_without_wording_tests_is_not_locked(self):
        repo = self.filled_project(WALK_LIMIT - 10)
        self.assertEqual(self.write(repo, NEW_TEST, NEW_BEHAVIOR), ({}, []))


if __name__ == "__main__":
    unittest.main()
