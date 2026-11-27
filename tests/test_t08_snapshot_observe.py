import hashlib
import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
from unittest import mock
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView


PRINCIPLE = "A test must verify what the code does for its client, never how it does it."
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
    return head + "\n\ndef test_render(snapshot):\n" + f'    """{PRINCIPLE}"""\n' + "    result = render()\n" + "".join("    " + line + "\n" for line in body.splitlines())


def unittest_style(body, prelude=""):
    head = "import unittest\n" + (prelude + "\n" if prelude else "")
    indented = "".join("        " + line + "\n" for line in body.splitlines())
    docstring = f'        """{PRINCIPLE}"""\n'
    return head + "\n\nclass RenderTest(unittest.TestCase):\n    def test_render(self):\n" + docstring + "        result = render()\n" + indented


def jest_style(body):
    return "// Principle: " + PRINCIPLE + "\n" + uncited_jest(body)


def uncited_jest(body):
    return "test(\"renders\", () => {\n  const result = render();\n" + "".join("  " + line + "\n" for line in body.splitlines()) + "});\n"


class Case(NamedTuple):
    name: str
    path: str
    content: str
    line: int


class Outcome(NamedTuple):
    decision: str
    ledger: tuple[tuple[str, str], ...]
    lines: tuple[tuple[str, int], ...]


REPORTED = (
    Case("syrupy_actual_equals_snapshot", PY, pytest_style("assert result == snapshot"), 7),
    Case("syrupy_snapshot_equals_actual", PY, pytest_style("assert snapshot == result"), 7),
    Case("syrupy_named_snapshot_call", PY, pytest_style("assert result == snapshot(name=\"wide\")"), 7),
    Case("syrupy_snapshot_with_extension", PY, pytest_style("assert result == snapshot.use_extension(JSONExtension)"), 7),
    Case("pytest_snapshot_assert_match", PY, pytest_style("snapshot.assert_match(result, \"render.txt\")"), 7),
    Case("pytest_snapshot_assert_match_dir", PY, pytest_style("snapshot.assert_match_dir({\"a.txt\": result}, \"render\")"), 7),
    Case("snapshottest_assert_match_snapshot", PY, unittest_style("self.assertMatchSnapshot(result)"), 8),
    Case("approvaltests_imported_verify", PY, unittest_style("verify(result)", "from approvaltests import verify"), 9),
    Case("approvaltests_approvals_class_verify", PY, unittest_style("Approvals.verify(result)", "from approvaltests.approvals import Approvals"), 9),
    Case("approvaltests_module_verify", PY, unittest_style("approvaltests.verify(result)", "import approvaltests"), 9),
    Case("jest_to_match_snapshot", JS, jest_style("expect(result).toMatchSnapshot();"), 4),
    Case("jest_to_match_inline_snapshot", JS, jest_style("expect(result).toMatchInlineSnapshot();"), 4),
    Case("jest_to_match_file_snapshot_in_spec_file", TS_SPEC, jest_style("expect(result).toMatchFileSnapshot(\"render.txt\");"), 4),
    Case("jest_to_throw_error_matching_snapshot", JS, jest_style("expect(() => render()).toThrowErrorMatchingSnapshot();"), 4),
    Case("jest_to_throw_error_matching_inline_snapshot", JS, jest_style("expect(() => render()).toThrowErrorMatchingInlineSnapshot();"), 4),
    Case("python_compare_with_read_of_expected_file", PY, unittest_style("assert result == Path(\"expected.txt\").read_text()", PATH_IMPORT), 9),
    Case("python_assert_equal_with_name_bound_to_golden_read", PY, unittest_style("golden = Path(\"render.txt\").read_text()\nself.assertEqual(result, golden)", PATH_IMPORT), 10),
    Case("python_assert_equal_with_bound_read_of_golden_path", PY, unittest_style("stored = Path(\"golden/render.txt\").read_bytes()\nself.assertEqual(result, stored)", PATH_IMPORT), 10),
    Case("javascript_expect_against_read_of_expected_file", JS, jest_style("expect(result).toEqual(fs.readFileSync(\"expected.txt\", \"utf8\"));"), 4),
    Case("javascript_expect_against_const_bound_to_golden_read", JS, jest_style("const golden = fs.readFileSync(\"render.txt\", \"utf8\");\nexpect(result).toEqual(golden);"), 5),
    Case("javascript_expect_against_unterminated_const_bound_to_expected_read", JS, jest_style("const stored = fs.readFileSync(\"expected.txt\", \"utf8\")\nexpect(result).toEqual(stored)"), 5),
    Case("javascript_expect_against_terminated_const_bound_to_expected_read", JS, jest_style("const stored = fs.readFileSync(\"expected.txt\", \"utf8\");\nexpect(result).toEqual(stored);"), 5),
)

SILENT = (
    Case("snapshot_name_that_is_not_a_test_parameter", PY, unittest_style("snapshot = load_snapshot()\nassert result == snapshot"), 0),
    Case("assert_equal_with_a_literal_value", PY, unittest_style("self.assertEqual(result, \"wide\")"), 0),
    Case("verify_call_without_an_approvaltests_import", PY, unittest_style("verify(result)"), 0),
    Case("python_read_then_compare_without_expected_or_golden", PY, unittest_style("stored = Path(\"render.txt\").read_text()\nself.assertEqual(result, stored)", PATH_IMPORT), 0),
    Case("javascript_read_then_compare_without_expected_or_golden", JS, jest_style("const stored = fs.readFileSync(\"render.txt\", \"utf8\");\nexpect(result).toEqual(stored);"), 0),
    Case("jest_snapshot_matcher_outside_a_test_file", SRC_JS, uncited_jest("expect(result).toMatchSnapshot();"), 0),
    Case("javascript_read_binding_whose_path_sits_on_the_next_line", JS, jest_style("const stored = fs.readFileSync(\n  \"expected.txt\", \"utf8\");\nexpect(result).toEqual(stored);"), 0),
)


HOLLOW = "hollow_test"
APPROVAL_IMPORT = "from approvaltests import verify_all, verify_as_json, verify_xml"


def approval_style(call):
    return APPROVAL_IMPORT + "\n\n\ndef test_render():\n" + f'    """{PRINCIPLE}"""\n' + "    result = render()\n" + ("    " + call + "\n" if call else "")


APPROVAL_ASSERTED = (
    Case("approvaltests_verify_as_json", PY, approval_style("verify_as_json(result)"), 7),
    Case("approvaltests_verify_all", PY, approval_style("verify_all(\"render\", [result])"), 7),
    Case("approvaltests_verify_xml", PY, approval_style("verify_xml(result)"), 7),
)

HOLLOW_BODIES = (
    Case("test_without_any_assertion", PY, approval_style(""), 4),
    Case("test_whose_only_call_is_a_verify_prefixed_helper", PY, approval_style("verifyUser(result)"), 4),
)


def decision_of(response):
    output = response.get("hookSpecificOutput", {})
    if output.get("permissionDecision") == DENY:
        return DENY
    return CONTEXT if output.get("additionalContext") else NONE


GATE_STATES = {"default": None, "enforce": "enforce", "off": "off"}


def configured(folder, state):
    folder.mkdir()
    files = {} if state is None else {CONFIG_NAME: json.dumps({"rule_gates": {RULE: state}})}
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return {"disk": files, "index": files}


class SnapshotObserveRule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / ".adw-state"
        (cls.root / "home").mkdir()
        cls.git = FakeGitView({"roots": {str(cls.root / name): configured(cls.root / name, state)
                                         for name, state in GATE_STATES.items()}})
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home"), "PATH": os.environ.get("PATH", "")}, clear=True)
        environment.start()
        cls.addClassCleanup(environment.stop)

    def session(self):
        return "s" + hashlib.sha256(self.id().encode()).hexdigest()[:20]

    def write(self, path, content, project="default"):
        folder = self.root / project
        payload = {"cwd": str(folder), "session_id": self.session(), "hook_event_name": "PreToolUse",
                   "agent_type": TEST_WRITER, "tool_name": "Write",
                   "tool_input": {"file_path": str(folder / path), "content": content}}
        return run_hook("PreToolUse", payload, state_dir=self.state, git_view=self.git)

    def reports(self):
        return [finding for report in sorted((self.state / "reports").glob(self.session() + "-*.json"))
                for finding in json.loads(report.read_text(encoding="utf-8"))]

    def ledger(self):
        path = self.state / "ledger" / "ledger.jsonl"
        rows = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        return {(row["rule"], row["outcome"]) for row in rows if row.get("session_id") == self.session() and row.get("rule")}

    def outcome(self, path, content, project="default"):
        response = self.write(path, content, project)
        lines = {(finding["rule"], finding["line"]) for finding in self.reports()}
        return Outcome(decision_of(response), tuple(sorted(self.ledger())), tuple(sorted(lines)))

    def shown_row(self, path, content):
        message = self.write(path, content)["systemMessage"]
        finding = next(item for item in self.reports() if item["rule"] == RULE)
        rows = [row for row in message.splitlines() if row.endswith(f"({RULE})")]
        return finding, rows

    def test_snapshot_assertion_passes_as_context_by_default(self):
        outcome = self.outcome(PY, pytest_style("assert result == snapshot"))
        self.assertEqual(CONTEXT, outcome.decision)

    def test_enforce_gate_turns_the_observed_row_into_a_deny(self):
        outcome = self.outcome(PY, pytest_style("assert result == snapshot"), "enforce")
        self.assertEqual(Outcome(DENY, ((RULE, BLOCKED),), ((RULE, 7),)), outcome)

    def test_off_gate_drops_the_finding_from_the_report(self):
        outcome = self.outcome(PY, pytest_style("assert result == snapshot"), "off")
        self.assertEqual((NONE, ()), (outcome.decision, outcome.lines))

    def test_shown_row_names_the_written_file_and_the_assertion_line(self):
        _, rows = self.shown_row(PY, unittest_style("self.assertMatchSnapshot(result)"))
        locations = [re.search(r"(\S+):(\d+) ", row).groups() for row in rows]
        self.assertEqual([(str(self.root / "default" / PY), "8")], locations)

    def test_shown_row_carries_catalog_wording_instead_of_the_name_placeholder(self):
        finding, rows = self.shown_row(PY, pytest_style("assert result == snapshot"))
        shape = re.escape(f"{self.root / 'default' / PY}:7 ") + r"(.+)" + re.escape(f". {finding['action']} ({RULE})")
        title = re.search(shape, rows[0]).group(1)
        self.assertNotEqual(RULE.replace("_", " "), title.lower())


def reported(case):
    def test(self):
        self.assertEqual(Outcome(CONTEXT, ((RULE, OBSERVED),), ((RULE, case.line),)), self.outcome(case.path, case.content))
    return test


def silent(case):
    def test(self):
        self.assertEqual(Outcome(NONE, (), ()), self.outcome(case.path, case.content))
    return test


def approval_counts_as_assertion(case):
    def test(self):
        self.assertEqual(Outcome(CONTEXT, ((RULE, OBSERVED),), ((RULE, case.line),)), self.outcome(case.path, case.content))
    return test


def hollow(case):
    def test(self):
        self.assertEqual(Outcome(DENY, ((HOLLOW, BLOCKED),), ((HOLLOW, case.line),)), self.outcome(case.path, case.content))
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
