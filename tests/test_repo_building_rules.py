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
REPO = ("test_builds_repo", "block")
TREE = ("test_builds_tree", "would_block")
RULES_OFF = {("test_builds_repo", "release"), ("test_builds_tree", "release")}
CONFIG_NAME = ".agent-discipline.json"
ENABLED_GATES = {"rule_gates": {"test_builds_repo": "enforce", "test_builds_tree": "observe"}}
PY_TEST = "tests/test_sample.py"
JS_TEST = "tests/sample.test.js"
BATS_TEST = "tests/sample.bats"
PY_SOURCE = "src/build.py"
PY_HEADER = ("import os", "import subprocess", "import tempfile", "import unittest", "", "", "class Sample(unittest.TestCase):")
JS_HEADER = ("const { execSync, spawnSync } = require('child_process');", "")
PRINCIPLE = "A test must verify what the code does for its client, never how it does it."
GIT_INIT_RUN = 'subprocess.run(["git", "init", "-q", "repo"], check=True)'


def python_test(*body, setup=()):
    setup_lines = ("    def setUp(self):", *("        " + line for line in setup), "") if setup else ()
    lines = (*PY_HEADER, *setup_lines, "    def test_sample(self):", '        """' + PRINCIPLE + '"""', *("        " + line for line in body))
    return "\n".join(lines) + "\n"


def python_test_running(call):
    return python_test(call, "self.assertEqual(build(), 1)")


def js_test(call):
    lines = (*JS_HEADER, "// Principle: " + PRINCIPLE, "test('builds', () => {", "  " + call, "  expect(build()).toBe(1);", "});")
    return "\n".join(lines) + "\n"


def bats_test(line):
    lines = ("#!/usr/bin/env bats", "", '@test "builds" {', "  " + line, "  run build", '  [ "$status" -eq 0 ]', "}")
    return "\n".join(lines) + "\n"


def decision_of(response):
    output = response.get("hookSpecificOutput", {})
    if output.get("permissionDecision") == DENY:
        return DENY
    return CONTEXT if output.get("additionalContext") else NONE


class RepoBuildingTestRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = TemporaryDirectory()
        cls.root = Path(cls.workspace.name)
        cls.home = cls.root / "home"
        cls.state = cls.root / "state"
        cls.home.mkdir()
        cls.enabled = cls.root / "enabled"
        cls.defaults = cls.root / "defaults"
        cls.enabled.mkdir()
        cls.defaults.mkdir()
        (cls.enabled / CONFIG_NAME).write_text(json.dumps(ENABLED_GATES), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def verdict(self, relative, content, project=None):
        project = project or self.enabled
        payload = {
            "cwd": str(project), "session_id": self.id(), "hook_event_name": "PreToolUse",
            "agent_type": TEST_WRITER, "tool_name": "Write",
            "tool_input": {"file_path": str(project / relative), "content": content},
        }
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state), "--git-view", str(NO_REPOSITORY_VIEW)],
            input=json.dumps(payload), text=True, capture_output=True, check=True,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        return decision_of(json.loads(call.stdout)), self.ledger_rules()

    def ledger_rules(self):
        lines = (self.state / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        rows = (json.loads(line) for line in lines if line.strip())
        return {(row["rule"], row["outcome"]) for row in rows if row.get("session_id") == self.id() and row.get("rule")}

    def test_python_test_running_git_init_through_subprocess_run_is_denied(self):
        self.assertEqual(self.verdict(PY_TEST, python_test_running(GIT_INIT_RUN)), (DENY, {REPO}))

    def test_python_test_running_git_init_through_check_call_is_denied(self):
        content = python_test_running('subprocess.check_call(["git", "init", "repo"])')
        self.assertEqual(self.verdict(PY_TEST, content), (DENY, {REPO}))

    def test_python_test_running_git_init_through_os_system_is_denied(self):
        content = python_test_running('os.system("git init repo")')
        self.assertEqual(self.verdict(PY_TEST, content), (DENY, {REPO}))

    def test_python_test_running_git_clone_as_a_shell_string_is_denied(self):
        content = python_test_running('subprocess.run("git clone https://example.com/r.git copy", shell=True, check=True)')
        self.assertEqual(self.verdict(PY_TEST, content), (DENY, {REPO}))

    def test_python_test_running_only_git_status_is_allowed(self):
        content = python_test_running('subprocess.run(["git", "status", "--short"], capture_output=True)')
        self.assertEqual(self.verdict(PY_TEST, content), (NONE, set()))

    def test_python_test_running_only_git_log_is_allowed(self):
        content = python_test_running('subprocess.run(["git", "log", "--oneline", "-1"], capture_output=True)')
        self.assertEqual(self.verdict(PY_TEST, content), (NONE, set()))

    def test_python_test_holding_a_git_init_command_as_input_text_is_allowed(self):
        content = python_test('command = "git init -q repo"', f"self.assertEqual(decide(command), {DENY!r})")
        self.assertEqual(self.verdict(PY_TEST, content), (NONE, set()))

    def test_javascript_test_running_git_init_through_exec_sync_is_denied(self):
        self.assertEqual(self.verdict(JS_TEST, js_test("execSync('git init');")), (DENY, {REPO}))

    def test_javascript_test_running_git_commit_through_spawn_sync_is_denied(self):
        content = js_test("spawnSync('git', ['commit', '-m', 'seed']);")
        self.assertEqual(self.verdict(JS_TEST, content), (DENY, {REPO}))

    def test_javascript_test_running_only_git_status_is_allowed(self):
        self.assertEqual(self.verdict(JS_TEST, js_test("execSync('git status --short');")), (NONE, set()))

    def test_bats_test_with_a_plain_git_init_line_is_denied(self):
        self.assertEqual(self.verdict(BATS_TEST, bats_test("git init repo")), (DENY, {REPO}))

    def test_bats_test_with_a_plain_git_log_line_is_allowed(self):
        self.assertEqual(self.verdict(BATS_TEST, bats_test("git log --oneline -1")), (NONE, set()))

    def test_per_test_setup_writing_files_into_a_temp_tree_is_observed(self):
        setup = ("self.tree = tempfile.TemporaryDirectory()",
                 'with open(os.path.join(self.tree.name, "input.txt"), "w") as handle:',
                 '    handle.write("seed")')
        content = python_test("self.assertEqual(build(self.tree.name), 1)", setup=setup)
        self.assertEqual(self.verdict(PY_TEST, content), (CONTEXT, {TREE}))

    def test_per_test_setup_building_input_in_memory_is_not_observed(self):
        content = python_test("self.assertEqual(build(self.files), 1)", setup=('self.files = {"input.txt": "seed"}',))
        self.assertEqual(self.verdict(PY_TEST, content), (NONE, set()))

    def test_default_config_leaves_the_repo_and_tree_rules_off(self):
        setup = ("self.tree = tempfile.TemporaryDirectory()",
                 'with open(os.path.join(self.tree.name, "input.txt"), "w") as handle:',
                 '    handle.write("seed")')
        content = python_test(GIT_INIT_RUN, "self.assertEqual(build(self.tree.name), 1)", setup=setup)
        self.assertEqual(self.verdict(PY_TEST, content, project=self.defaults), (NONE, RULES_OFF))

    def test_source_file_running_git_init_is_outside_the_rule(self):
        content = "import subprocess\n\n\ndef build():\n    " + GIT_INIT_RUN + "\n"
        self.assertEqual(self.verdict(PY_SOURCE, content), (NONE, set()))


if __name__ == "__main__":
    unittest.main()
