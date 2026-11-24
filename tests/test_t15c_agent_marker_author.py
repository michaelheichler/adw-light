import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from t19_language_home import BUNDLE, GRAMMARS, LIBRARY_NEEDED, LIBRARY_READY, VENV


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
CONFIG_NAME = ".agent-discipline.json"
SESSION = "t15c"
TEST_WRITER = "adw-test-writer"
DENY = "deny"
AGENT = "agent_marker_author"
MARKER = "deferred_work_comment"
SEAL = "config_seal"
BODY = "Add CSV export to the report view to let users share results"
ALLOWED = (0, None, ())


def source(*lines):
    return "\n".join(lines) + "\n"


def ts_function(comment):
    return source("export function probe() {", "  " + comment, "  return 1", "}")


def python_stub(comment):
    return source("def export(rows):", "    " + comment, "    raise NotImplementedError")


def java_method(comment):
    return source("class Report {", "    // " + comment, "    int size() {", "        return 1;", "    }", "}")


def js_test(comment, body="run();"):
    return source('it("exports", () => {', "  // " + comment, "  " + body, "});")


def denied(*rows):
    return 0, DENY, tuple(sorted(rows))


class MarkerRepo:
    def __init__(self, case, exact=False):
        workspace = TemporaryDirectory()
        case.addCleanup(workspace.cleanup)
        root = Path(workspace.name)
        self.repo = root / "repo"
        self.state = root / "state"
        self.home = root / "home"
        self.repo.mkdir()
        adw = self.home / ".adw-lite"
        (adw / "install").mkdir(parents=True)
        (adw / "cache" / "grammars").mkdir(parents=True)
        if exact:
            (adw / "install" / "venv").symlink_to(VENV, target_is_directory=True)
            (adw / "cache" / "grammars" / BUNDLE).symlink_to(GRAMMARS / BUNDLE, target_is_directory=True)
        self.response = {}

    def put(self, relative, content):
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def configure(self, settings):
        self.put(CONFIG_NAME, json.dumps(settings))

    def start_session(self):
        payload = {"session_id": SESSION, "cwd": str(self.repo), "hook_event_name": "SessionStart", "source": "startup"}
        return self._call("SessionStart", payload).returncode

    def write(self, relative, content, agent=None):
        return self._tool("Write", {"file_path": str(self.repo / relative), "content": content}, agent)

    def edit(self, relative, old, new):
        return self._tool("Edit", {"file_path": str(self.repo / relative), "old_string": old, "new_string": new}, None)

    def _tool(self, tool, tool_input, agent):
        payload = {
            "session_id": SESSION, "cwd": str(self.repo), "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": tool_input,
        }
        payload.update({"agent_type": agent} if agent else {})
        before = set(self._reports())
        call = self._call("PreToolUse", payload)
        self.response = json.loads(call.stdout) if call.stdout.strip() else {}
        decision = self.response.get("hookSpecificOutput", {}).get("permissionDecision")
        fresh = [report for report in self._reports() if report not in before]
        rows = {(row["rule"], row["line"]) for report in fresh for row in json.loads(report.read_text(encoding="utf-8"))}
        return call.returncode, decision, tuple(sorted(rows))

    def _reports(self):
        return sorted((self.state / "reports").glob("*.json"))

    def _call(self, event, payload):
        return subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True, timeout=120, check=False,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )


class AgentSignedMarkersAreDenied(unittest.TestCase):
    def test_todo_signed_by_claude_is_denied_on_its_line(self):
        verdict = MarkerRepo(self).write("src/x.ts", ts_function("// TODO(claude): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_capitalized_codex_author_is_denied_on_its_line(self):
        verdict = MarkerRepo(self).write("src/x.ts", ts_function("// TODO(Codex): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_agent_signed_todo_in_a_python_stub_is_denied_on_its_line(self):
        verdict = MarkerRepo(self).write("src/mod.py", python_stub("# TODO(claude): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_agent_name_inside_a_two_word_author_is_denied(self):
        verdict = MarkerRepo(self).write("src/x.ts", ts_function("// TODO(Claude Code): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_fixme_signed_by_codex_is_denied_on_its_line(self):
        marker = "// FIXME(codex): Repair the date parser that drops leap days so reports balance"
        verdict = MarkerRepo(self).write("src/x.ts", ts_function(marker))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_agent_author_after_a_space_is_still_denied(self):
        verdict = MarkerRepo(self).write("src/x.ts", ts_function("// TODO (claude): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))


class HumanSignedMarkersFollowTheShapeRule(unittest.TestCase):
    def test_well_formed_todo_signed_by_a_human_is_allowed_without_any_report(self):
        verdict = MarkerRepo(self).write("src/x.ts", ts_function("// TODO(alice): " + BODY))
        self.assertEqual(verdict, ALLOWED)

    def test_well_formed_todo_signed_by_a_human_in_a_python_stub_is_allowed(self):
        verdict = MarkerRepo(self).write("src/mod.py", python_stub("# TODO(alice): " + BODY))
        self.assertEqual(verdict, ALLOWED)

    def test_human_author_after_a_space_is_allowed_without_any_report(self):
        verdict = MarkerRepo(self).write("src/x.ts", ts_function("// TODO (alice): " + BODY))
        self.assertEqual(verdict, ALLOWED)

    def test_human_author_after_a_space_in_a_python_stub_is_allowed(self):
        verdict = MarkerRepo(self).write("src/mod.py", python_stub("# TODO (alice): " + BODY))
        self.assertEqual(verdict, ALLOWED)

    def test_space_before_the_colon_without_an_author_still_gets_an_observe_row(self):
        verdict = MarkerRepo(self).write("src/x.ts", ts_function("// TODO : " + BODY))
        self.assertEqual(verdict, (0, None, ((MARKER, 2),)))

    def test_author_that_only_starts_with_an_agent_name_is_allowed(self):
        verdict = MarkerRepo(self).write("src/x.ts", ts_function("// TODO(claudette): " + BODY))
        self.assertEqual(verdict, ALLOWED)

    def test_vague_todo_signed_by_a_human_gets_an_observe_row_and_no_deny(self):
        repo = MarkerRepo(self)
        verdict = repo.write("src/x.ts", ts_function("// TODO(alice): fix this"))
        surfaces = ("systemMessage" in repo.response, "additionalContext" in repo.response.get("hookSpecificOutput", {}))
        self.assertEqual((verdict, surfaces), ((0, None, ((MARKER, 2),)), (True, True)))

    def test_vague_todo_signed_by_a_human_in_a_python_stub_is_denied_as_an_added_comment(self):
        verdict = MarkerRepo(self).write("src/mod.py", python_stub("# TODO(alice): fix this"))
        self.assertEqual(verdict, denied(("added_comment", 2)))


class InheritedAgentMarkers(unittest.TestCase):
    def inherited(self):
        repo = MarkerRepo(self)
        repo.put("src/kept.ts", ts_function("// TODO(claude): " + BODY))
        return repo

    def test_edit_elsewhere_in_a_file_with_an_inherited_agent_marker_is_allowed(self):
        repo = self.inherited()
        repo.start_session()
        self.assertEqual(repo.edit("src/kept.ts", "return 1", "return 2"), ALLOWED)

    def test_edit_elsewhere_is_allowed_without_a_session_start_snapshot(self):
        repo = self.inherited()
        self.assertEqual(repo.edit("src/kept.ts", "return 1", "return 2"), ALLOWED)

    def test_new_agent_marker_added_beside_an_inherited_one_is_denied_on_the_new_line(self):
        repo = self.inherited()
        repo.start_session()
        verdict = repo.edit("src/kept.ts", "  return 1", "\n  // TODO(codex): " + BODY + "\n  return 1")
        self.assertEqual(verdict, denied((AGENT, 4)))


class ProjectConfigCannotWeakenTheAgentRule(unittest.TestCase):
    def configured(self, state):
        repo = MarkerRepo(self)
        repo.configure({"rule_gates": {AGENT: state}})
        return repo

    def test_rule_set_to_observe_still_denies_a_claude_marker(self):
        verdict = self.configured("observe").write("src/x.ts", ts_function("// TODO(claude): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_rule_set_to_observe_still_denies_a_codex_marker(self):
        verdict = self.configured("observe").write("src/x.ts", ts_function("// TODO(Codex): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_rule_set_to_off_still_denies_a_claude_marker(self):
        verdict = self.configured("off").write("src/x.ts", ts_function("// TODO(claude): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_rule_set_to_enforce_denies_a_claude_marker(self):
        verdict = self.configured("enforce").write("src/x.ts", ts_function("// TODO(claude): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_rule_set_to_off_leaves_a_human_signed_marker_allowed(self):
        verdict = self.configured("off").write("src/x.ts", ts_function("// TODO(alice): " + BODY))
        self.assertEqual(verdict, ALLOWED)


class PolicyWritesThatWeakenTheAgentRuleAreSealed(unittest.TestCase):
    def policy_write(self, rule, state):
        return MarkerRepo(self).write(CONFIG_NAME, json.dumps({"rule_gates": {rule: state}}, indent=2) + "\n")

    def test_setting_the_agent_rule_to_observe_is_denied_as_a_config_seal(self):
        self.assertEqual(self.policy_write(AGENT, "observe"), denied((SEAL, 1)))

    def test_setting_the_agent_rule_to_off_is_denied_as_a_config_seal(self):
        self.assertEqual(self.policy_write(AGENT, "off"), denied((SEAL, 1)))

    def test_setting_prose_comment_block_to_observe_is_denied_the_same_way(self):
        self.assertEqual(self.policy_write("prose_comment_block", "observe"), denied((SEAL, 1)))

    def test_setting_an_unlocked_rule_to_observe_is_allowed(self):
        self.assertEqual(self.policy_write("comment_shape", "observe"), ALLOWED)


class SignedMarkersInTests(unittest.TestCase):
    def test_test_without_assertion_and_a_human_signed_todo_is_denied_as_hollow(self):
        verdict = MarkerRepo(self).write("tests/a.test.js", js_test("TODO(alice): " + BODY), agent=TEST_WRITER)
        self.assertEqual(verdict, denied(("hollow_test", 1)))

    def test_asserting_test_with_a_human_signed_todo_is_allowed(self):
        content = js_test("TODO(alice): " + BODY, "expect(run()).toBe(1);")
        self.assertEqual(MarkerRepo(self).write("tests/a.test.js", content, agent=TEST_WRITER), ALLOWED)

    def test_asserting_test_with_an_agent_signed_todo_is_denied_on_the_marker_line(self):
        content = js_test("TODO(claude): " + BODY, "expect(run()).toBe(1);")
        self.assertEqual(MarkerRepo(self).write("tests/a.test.js", content, agent=TEST_WRITER), denied((AGENT, 2)))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class SignedMarkersThroughTheLanguageLayer(unittest.TestCase):
    def test_claude_signed_todo_in_typescript_is_denied_on_its_line(self):
        verdict = MarkerRepo(self, exact=True).write("src/x.ts", ts_function("// TODO(claude): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_codex_signed_todo_in_java_is_denied_on_its_line(self):
        verdict = MarkerRepo(self, exact=True).write("src/Report.java", java_method("TODO(codex): " + BODY))
        self.assertEqual(verdict, denied((AGENT, 2)))

    def test_human_signed_todo_in_java_is_allowed(self):
        verdict = MarkerRepo(self, exact=True).write("src/Report.java", java_method("TODO(alice): " + BODY))
        self.assertEqual(verdict, ALLOWED)


if __name__ == "__main__":
    unittest.main()
