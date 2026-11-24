from quiet_git import disable_background_maintenance
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
RULE = "snapshot_assertion"
DENY = "deny"
CONTEXT = "context"
NONE = "none"
OBSERVED = "would_block"
BLOCKED = "block"
TEST_WRITER = "adw-test-writer"
CONFIG_NAME = ".agent-discipline.json"
PY = "tests/test_render.py"
JS = "tests/render.test.js"
TS_SPEC = "tests/render.spec.ts"
SRC_JS = "src/render.js"
PATH_IMPORT = "from pathlib import Path"


def pytest_style(body, prelude=""):
    head = "import unittest\n" + (prelude + "\n" if prelude else "")
    return head + "\n\ndef test_render(snapshot):\n    result = render()\n" + "".join("    " + line + "\n" for line in body.splitlines())


def unittest_style(body, prelude=""):
    head = "import unittest\n" + (prelude + "\n" if prelude else "")
    indented = "".join("        " + line + "\n" for line in body.splitlines())
    return head + "\n\nclass RenderTest(unittest.TestCase):\n    def test_render(self):\n        result = render()\n" + indented


def jest_style(body):
    return "test(\"renders\", () => {\n  const result = render();\n" + "".join("  " + line + "\n" for line in body.splitlines()) + "});\n"


class Case(NamedTuple):
    name: str
    path: str
    content: str
    line: int


class Outcome(NamedTuple):
    exit_code: int
    decision: str
    ledger: tuple[tuple[str, str], ...]
    lines: tuple[tuple[str, int], ...]


REPORTED = (
    Case("syrupy_actual_equals_snapshot", PY, pytest_style("assert result == snapshot"), 6),
    Case("syrupy_snapshot_equals_actual", PY, pytest_style("assert snapshot == result"), 6),
    Case("syrupy_named_snapshot_call", PY, pytest_style("assert result == snapshot(name=\"wide\")"), 6),
    Case("syrupy_snapshot_with_extension", PY, pytest_style("assert result == snapshot.use_extension(JSONExtension)"), 6),
    Case("pytest_snapshot_assert_match", PY, pytest_style("snapshot.assert_match(result, \"render.txt\")"), 6),
    Case("pytest_snapshot_assert_match_dir", PY, pytest_style("snapshot.assert_match_dir({\"a.txt\": result}, \"render\")"), 6),
    Case("snapshottest_assert_match_snapshot", PY, unittest_style("self.assertMatchSnapshot(result)"), 7),
    Case("approvaltests_imported_verify", PY, unittest_style("verify(result)", "from approvaltests import verify"), 8),
    Case("approvaltests_approvals_class_verify", PY, unittest_style("Approvals.verify(result)", "from approvaltests.approvals import Approvals"), 8),
    Case("approvaltests_module_verify", PY, unittest_style("approvaltests.verify(result)", "import approvaltests"), 8),
    Case("jest_to_match_snapshot", JS, jest_style("expect(result).toMatchSnapshot();"), 3),
    Case("jest_to_match_inline_snapshot", JS, jest_style("expect(result).toMatchInlineSnapshot();"), 3),
    Case("jest_to_match_file_snapshot_in_spec_file", TS_SPEC, jest_style("expect(result).toMatchFileSnapshot(\"render.txt\");"), 3),
    Case("jest_to_throw_error_matching_snapshot", JS, jest_style("expect(() => render()).toThrowErrorMatchingSnapshot();"), 3),
    Case("jest_to_throw_error_matching_inline_snapshot", JS, jest_style("expect(() => render()).toThrowErrorMatchingInlineSnapshot();"), 3),
    Case("python_compare_with_read_of_expected_file", PY, unittest_style("assert result == Path(\"expected.txt\").read_text()", PATH_IMPORT), 8),
    Case("python_assert_equal_with_name_bound_to_golden_read", PY, unittest_style("golden = Path(\"render.txt\").read_text()\nself.assertEqual(result, golden)", PATH_IMPORT), 9),
    Case("python_assert_equal_with_bound_read_of_golden_path", PY, unittest_style("stored = Path(\"golden/render.txt\").read_bytes()\nself.assertEqual(result, stored)", PATH_IMPORT), 9),
    Case("javascript_expect_against_read_of_expected_file", JS, jest_style("expect(result).toEqual(fs.readFileSync(\"expected.txt\", \"utf8\"));"), 3),
    Case("javascript_expect_against_const_bound_to_golden_read", JS, jest_style("const golden = fs.readFileSync(\"render.txt\", \"utf8\");\nexpect(result).toEqual(golden);"), 4),
    Case("javascript_expect_against_unterminated_const_bound_to_expected_read", JS, jest_style("const stored = fs.readFileSync(\"expected.txt\", \"utf8\")\nexpect(result).toEqual(stored)"), 4),
    Case("javascript_expect_against_terminated_const_bound_to_expected_read", JS, jest_style("const stored = fs.readFileSync(\"expected.txt\", \"utf8\");\nexpect(result).toEqual(stored);"), 4),
)

SILENT = (
    Case("snapshot_name_that_is_not_a_test_parameter", PY, unittest_style("snapshot = load_snapshot()\nassert result == snapshot"), 0),
    Case("assert_equal_with_a_literal_value", PY, unittest_style("self.assertEqual(result, \"wide\")"), 0),
    Case("verify_call_without_an_approvaltests_import", PY, unittest_style("verify(result)"), 0),
    Case("python_read_then_compare_without_expected_or_golden", PY, unittest_style("stored = Path(\"render.txt\").read_text()\nself.assertEqual(result, stored)", PATH_IMPORT), 0),
    Case("javascript_read_then_compare_without_expected_or_golden", JS, jest_style("const stored = fs.readFileSync(\"render.txt\", \"utf8\");\nexpect(result).toEqual(stored);"), 0),
    Case("jest_snapshot_matcher_outside_a_test_file", SRC_JS, jest_style("expect(result).toMatchSnapshot();"), 0),
    Case("javascript_read_binding_whose_path_sits_on_the_next_line", JS, jest_style("const stored = fs.readFileSync(\n  \"expected.txt\", \"utf8\");\nexpect(result).toEqual(stored);"), 0),
)


HOLLOW = "hollow_test"
APPROVAL_IMPORT = "from approvaltests import verify_all, verify_as_json, verify_xml"


def approval_style(call):
    return APPROVAL_IMPORT + "\n\n\ndef test_render():\n    result = render()\n" + ("    " + call + "\n" if call else "")


APPROVAL_ASSERTED = (
    Case("approvaltests_verify_as_json", PY, approval_style("verify_as_json(result)"), 6),
    Case("approvaltests_verify_all", PY, approval_style("verify_all(\"render\", [result])"), 6),
    Case("approvaltests_verify_xml", PY, approval_style("verify_xml(result)"), 6),
)

HOLLOW_BODIES = (
    Case("test_without_any_assertion", PY, approval_style(""), 4),
    Case("test_whose_only_call_is_a_verify_prefixed_helper", PY, approval_style("verifyUser(result)"), 4),
)


def json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def decision_of(response):
    output = response.get("hookSpecificOutput", {})
    if output.get("permissionDecision") == DENY:
        return DENY
    return CONTEXT if output.get("additionalContext") else NONE


class SnapshotObserveRule(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.state = self.root / ".adw-state"
        self.home = self.root / "home"
        self.home.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        disable_background_maintenance(self.root)

    def configure(self, state):
        (self.root / CONFIG_NAME).write_text(json.dumps({"rule_gates": {RULE: state}}))

    def write(self, path, content):
        payload = {"cwd": str(self.root), "session_id": "snapshot-observe", "hook_event_name": "PreToolUse",
                   "agent_type": TEST_WRITER, "tool_name": "Write",
                   "tool_input": {"file_path": str(self.root / path), "content": content}}
        return subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True,
            env={"HOME": str(self.home), "PATH": os.environ["PATH"]},
        )

    def reports(self):
        return [finding for report in sorted((self.state / "reports").glob("*.json")) for finding in json.loads(report.read_text())]

    def outcome(self, path, content):
        call = self.write(path, content)
        ledger = {(row["rule"], row["outcome"]) for row in json_lines(self.state / "ledger" / "ledger.jsonl") if row.get("rule")}
        lines = {(finding["rule"], finding["line"]) for finding in self.reports()}
        return Outcome(call.returncode, decision_of(json.loads(call.stdout)), tuple(sorted(ledger)), tuple(sorted(lines)))

    def shown_row(self, path, content):
        message = json.loads(self.write(path, content).stdout)["systemMessage"]
        finding = next(item for item in self.reports() if item["rule"] == RULE)
        rows = [row for row in message.splitlines() if row.endswith(f"({RULE})")]
        return finding, rows

    def test_snapshot_assertion_passes_as_context_by_default(self):
        outcome = self.outcome(PY, pytest_style("assert result == snapshot"))
        self.assertEqual((0, CONTEXT), outcome[:2])

    def test_enforce_gate_turns_the_observed_row_into_a_deny(self):
        self.configure("enforce")
        self.assertEqual(Outcome(0, DENY, ((RULE, BLOCKED),), ((RULE, 6),)), self.outcome(PY, pytest_style("assert result == snapshot")))

    def test_off_gate_drops_the_finding_from_the_report(self):
        self.configure("off")
        outcome = self.outcome(PY, pytest_style("assert result == snapshot"))
        self.assertEqual((0, NONE, ()), (outcome.exit_code, outcome.decision, outcome.lines))

    def test_shown_row_names_the_written_file_and_the_assertion_line(self):
        _, rows = self.shown_row(PY, unittest_style("self.assertMatchSnapshot(result)"))
        locations = [re.search(r"(\S+):(\d+) ", row).groups() for row in rows]
        self.assertEqual([(str(self.root / PY), "7")], locations)

    def test_shown_row_carries_catalog_wording_instead_of_the_name_placeholder(self):
        finding, rows = self.shown_row(PY, pytest_style("assert result == snapshot"))
        shape = re.escape(f"{self.root / PY}:6 ") + r"(.+)" + re.escape(f". {finding['action']} ({RULE})")
        title = re.search(shape, rows[0]).group(1)
        self.assertNotEqual(RULE.replace("_", " "), title.lower())


def reported(case):
    def test(self):
        self.assertEqual(Outcome(0, CONTEXT, ((RULE, OBSERVED),), ((RULE, case.line),)), self.outcome(case.path, case.content))
    return test


def silent(case):
    def test(self):
        self.assertEqual(Outcome(0, NONE, (), ()), self.outcome(case.path, case.content))
    return test


def approval_counts_as_assertion(case):
    def test(self):
        self.assertEqual(Outcome(0, CONTEXT, ((RULE, OBSERVED),), ((RULE, case.line),)), self.outcome(case.path, case.content))
    return test


def hollow(case):
    def test(self):
        self.assertEqual(Outcome(0, DENY, ((HOLLOW, BLOCKED),), ((HOLLOW, case.line),)), self.outcome(case.path, case.content))
    return test


for _case in APPROVAL_ASSERTED:
    setattr(SnapshotObserveRule, "test_" + _case.name + "_counts_as_the_assertion_and_is_only_observed", approval_counts_as_assertion(_case))
for _case in HOLLOW_BODIES:
    setattr(SnapshotObserveRule, "test_" + _case.name + "_is_denied_as_hollow", hollow(_case))
for _case in REPORTED:
    setattr(SnapshotObserveRule, "test_" + _case.name + "_is_observed_and_passes", reported(_case))
for _case in SILENT:
    setattr(SnapshotObserveRule, "test_" + _case.name + "_gives_no_row", silent(_case))


if __name__ == "__main__":
    unittest.main()
