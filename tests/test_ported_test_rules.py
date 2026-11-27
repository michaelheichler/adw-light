import json
import os
import subprocess
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


NO_REPOSITORY = FakeGitView({"roots": {}})

ENGINE = PROJECT / "engine"
DENY = "deny"
CONTEXT = "context"
NONE = "none"
BLOCK = "block"
OBSERVED = "would_block"
TEST_WRITER = "adw-test-writer"
PRINCIPLE = "A test must verify what the code does for its client, never how it does it."
CITATION = '"""' + PRINCIPLE + '"""'
JS_CITATION = "// Principle: " + PRINCIPLE + "\n"
DUPLICATE_EXIT = 3


class Case(NamedTuple):
    name: str
    path: str
    content: str
    decision: str
    rows: tuple[tuple[str, str, int], ...]


class Outcome(NamedTuple):
    decision: str
    rows: tuple[tuple[str, str, int], ...]


def python_test(body, prelude=""):
    indented = "".join("        " + line + "\n" for line in (CITATION, *body.splitlines()))
    return "import unittest\n" + prelude + "\n\nclass SampleTest(unittest.TestCase):\n    def test_sample(self):\n" + indented


LOOP = "assert_in_loop"
NAME = "hardcoded_name_presence"
SOURCE = "hardcoded_literal_in_source"
METHOD = "exposing_private_methods_for_testing"
STATE = "exposing_private_state_for_testing"
CONCRETE = "mocking_concrete_classes"
INCOMPLETE = "incomplete_mock_call_verification"
IF = "if_statements_in_tests"
ACT = "multiple_act_sections_in_unit_test"
CTOR = "test_fixture_reuse_via_constructor"
PORTED_RULES = frozenset((LOOP, NAME, SOURCE, METHOD, STATE, CONCRETE, INCOMPLETE, IF, ACT, CTOR))
PY = "tests/test_sample.py"
JS = "tests/sample.test.js"
RS = "tests/sample.rs"
TABLE_PRELUDE = "\nTABLE = {\"k\": 1}"
REPO_PRELUDE = "from unittest.mock import Mock, create_autospec, patch\n\n\nclass Repo:\n    def save(self):\n        return 1"
PROTOCOL_PRELUDE = "from typing import Protocol\nfrom unittest.mock import create_autospec\n\n\nclass Repo(Protocol):\n    def save(self):\n        ..."
POINT_PRELUDE = "from collections import namedtuple\n\nPoint = namedtuple(\"Point\", \"x y\")"
FIXTURE_TEST = (
    "import unittest\n\n\nclass SampleTest(unittest.TestCase):\n"
    "    def setUp(self):\n        self.account = make_account()\n\n"
    "    def test_deposit(self):\n        " + CITATION + "\n        self.assertEqual(self.account.deposit(5), 5)\n"
)
OWN_FIXTURE_TEST = (
    "import unittest\n\n\nclass SampleTest(unittest.TestCase):\n"
    "    def setUp(self):\n        self.limit = 5\n\n"
    "    def test_deposit(self):\n        " + CITATION + "\n        account = make_account()\n        self.assertEqual(account.deposit(self.limit), 5)\n"
)

CASES = (
    Case("python_for_loop_with_self_assert_denies", PY, python_test("for value in [1, 2]:\n    self.assertEqual(double(value), value * 2)"), DENY, ((LOOP, BLOCK, 8),)),
    Case("python_while_loop_with_bare_assert_denies", PY, python_test("count = 0\nwhile count < 2:\n    assert double(count) == count * 2\n    count += 1"), DENY, ((LOOP, BLOCK, 9),)),
    Case("javascript_loop_with_expect_in_test_callback_denies", JS, JS_CITATION + "test(\"doubles\", () => {\n  for (const value of [1, 2]) {\n    expect(double(value)).toBe(value * 2);\n  }\n});\n", DENY, ((LOOP, BLOCK, 4),)),
    Case("rust_for_loop_with_assert_macro_denies", RS, JS_CITATION + "#[test]\nfn doubles() {\n    for value in [1, 2] {\n        assert_eq!(double(value), value * 2);\n    }\n}\n", DENY, ((LOOP, BLOCK, 5),)),
    Case("rust_while_loop_with_assert_macro_denies", RS, JS_CITATION + "#[test]\nfn doubles() {\n    let mut value = 0;\n    while value < 2 {\n        assert!(double(value) >= value);\n        value += 1;\n    }\n}\n", DENY, ((LOOP, BLOCK, 6),)),
    Case("rust_bare_loop_with_assert_macro_denies", RS, JS_CITATION + "#[test]\nfn doubles() {\n    let mut value = 0;\n    loop {\n        assert!(double(value) >= value);\n        value += 1;\n        if value > 2 { break; }\n    }\n}\n", DENY, ((LOOP, BLOCK, 6),)),
    Case("python_loop_asserting_inside_subtest_passes", PY, python_test("for value in [1, 2]:\n    with self.subTest(value=value):\n        self.assertEqual(double(value), value * 2)"), NONE, ()),
    Case("literal_in_uppercase_table_reports_name_presence", PY, python_test("assert \"k\" in TABLE", TABLE_PRELUDE), CONTEXT, ((NAME, OBSERVED, 8),)),
    Case("literal_equal_to_uppercase_table_entry_reports_name_presence", PY, python_test("self.assertEqual(TABLE[\"k\"], 1)", TABLE_PRELUDE), CONTEXT, ((NAME, OBSERVED, 8),)),
    Case("literal_in_path_read_text_reports_source_literal", PY, python_test("text = Path(\"src/app.py\").read_text()\nself.assertIn(\"def main\", text)", "from pathlib import Path"), CONTEXT, ((SOURCE, OBSERVED, 8),)),
    Case("literal_in_open_read_reports_source_literal", PY, python_test("with open(\"setup.cfg\") as handle:\n    text = handle.read()\nassert \"name\" in text"), CONTEXT, ((SOURCE, OBSERVED, 9),)),
    Case("literal_in_inspect_getsource_reports_source_literal", PY, python_test("text = inspect.getsource(double)\nassert \"return\" in text", "import inspect"), CONTEXT, ((SOURCE, OBSERVED, 8),)),
    Case("literal_in_file_under_temp_root_passes", PY, python_test("text = Path(tempfile.gettempdir(), \"out.txt\").read_text()\nassert \"done\" in text", "import tempfile\nfrom pathlib import Path"), NONE, ()),
    Case("private_method_call_on_other_object_reports_private_method", PY, python_test("parser = Parser()\nself.assertEqual(parser._split(\"a b\"), [\"a\", \"b\"])"), CONTEXT, ((METHOD, OBSERVED, 8),)),
    Case("getattr_private_name_then_call_reports_private_method", PY, python_test("parser = Parser()\nself.assertTrue(getattr(parser, \"_ready\")())"), CONTEXT, ((METHOD, OBSERVED, 8),)),
    Case("monkeypatch_private_name_reports_private_method", PY, "def test_sample(monkeypatch):\n    " + CITATION + "\n    parser = Parser()\n    monkeypatch.setattr(parser, \"_split\", list)\n    assert parser.parse(\"ab\") == [\"a\", \"b\"]\n", CONTEXT, ((METHOD, OBSERVED, 4),)),
    Case("private_method_call_on_self_passes", PY, python_test("self.assertTrue(self._ready())"), NONE, ()),
    Case("private_attribute_read_reports_private_state", PY, python_test("parser = Parser()\nparser.parse(\"a\")\nself.assertTrue(parser._done)"), CONTEXT, ((STATE, OBSERVED, 9),)),
    Case("private_key_in_instance_dict_reports_private_state", PY, python_test("parser = Parser()\nparser.parse(\"a\")\nself.assertTrue(parser.__dict__[\"_done\"])"), CONTEXT, ((STATE, OBSERVED, 9),)),
    Case("private_key_in_vars_reports_private_state", PY, python_test("parser = Parser()\nparser.parse(\"a\")\nself.assertTrue(vars(parser)[\"_done\"])"), CONTEXT, ((STATE, OBSERVED, 9),)),
    Case("namedtuple_replace_and_asdict_pass", PY, python_test("moved = Point(1, 2)._replace(x=3)\nself.assertEqual(moved._asdict(), dict(x=3, y=2))", POINT_PRELUDE), NONE, ()),
    Case("autospec_of_same_file_concrete_class_reports_concrete_mock", PY, python_test("repo = create_autospec(Repo)\nrepo.save.return_value = 2\nself.assertEqual(store(repo), 2)", REPO_PRELUDE), CONTEXT, ((CONCRETE, OBSERVED, 12),)),
    Case("mock_spec_of_same_file_concrete_class_reports_concrete_mock", PY, python_test("repo = Mock(spec=Repo)\nrepo.save.return_value = 2\nself.assertEqual(store(repo), 2)", REPO_PRELUDE), CONTEXT, ((CONCRETE, OBSERVED, 12),)),
    Case("patch_object_on_same_file_concrete_class_reports_concrete_mock", PY, python_test("with patch.object(Repo, \"save\", return_value=2):\n    self.assertEqual(store(Repo()), 2)", REPO_PRELUDE), CONTEXT, ((CONCRETE, OBSERVED, 12),)),
    Case("autospec_of_protocol_passes", PY, python_test("repo = create_autospec(Repo)\nrepo.save.return_value = 2\nself.assertEqual(store(repo), 2)", PROTOCOL_PRELUDE), NONE, ()),
    Case("bare_assert_called_reports_incomplete_verification", PY, python_test("sender = Mock()\nnotify(sender)\nsender.send.assert_called()", "from unittest.mock import Mock"), CONTEXT, ((INCOMPLETE, OBSERVED, 9),)),
    Case("bare_assert_called_once_reports_incomplete_verification", PY, python_test("sender = Mock()\nnotify(sender)\nsender.send.assert_called_once()", "from unittest.mock import Mock"), CONTEXT, ((INCOMPLETE, OBSERVED, 9),)),
    Case("assert_called_once_with_arguments_passes", PY, python_test("sender = Mock()\nnotify(sender)\nsender.send.assert_called_once_with(\"hi\")", "from unittest.mock import Mock"), NONE, ()),
    Case("if_with_asserts_in_both_branches_reports_if_statement", PY, python_test("result = double(2)\nif result > 3:\n    assert result == 4\nelse:\n    assert result == 2"), CONTEXT, ((IF, OBSERVED, 8),)),
    Case("match_with_two_asserting_cases_reports_if_statement", PY, python_test("result = double(2)\nmatch result:\n    case 4:\n        assert result > 3\n    case _:\n        assert result < 3"), CONTEXT, ((IF, OBSERVED, 8),)),
    Case("if_without_else_passes", PY, python_test("result = double(2)\nif result > 3:\n    assert result == 4"), NONE, ()),
    Case("second_sut_call_after_an_assert_reports_multiple_acts", PY, python_test("sut = make_counter()\nsut.add(1)\nassert sut.total == 1\nsut.add(2)\nassert sut.total == 3"), CONTEXT, ((ACT, OBSERVED, 10),)),
    Case("single_sut_call_passes", PY, python_test("sut = make_counter()\nsut.add(1)\nassert sut.total == 1"), NONE, ()),
    Case("test_calling_object_built_in_setup_reports_fixture_reuse", PY, FIXTURE_TEST, CONTEXT, ((CTOR, OBSERVED, 10),)),
    Case("test_building_its_own_object_passes", PY, OWN_FIXTURE_TEST, NONE, ()),
)


def decision_of(response):
    output = response.get("hookSpecificOutput", {})
    if output.get("permissionDecision") == DENY:
        return DENY
    return CONTEXT if output.get("additionalContext") else NONE


def json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def reports(state):
    return set((state / "reports").glob("*.json"))


def ported_rows(state, session, new_reports):
    ledger = (row for row in json_lines(state / "ledger" / "ledger.jsonl") if row.get("session_id") == session)
    outcomes = {(row["rule"], row["outcome"]) for row in ledger if row.get("rule") in PORTED_RULES}
    findings = {(finding["rule"], finding["line"]) for report in sorted(new_reports) for finding in json.loads(report.read_text()) if finding.get("rule") in PORTED_RULES}
    return tuple(sorted((rule, outcome, line) for rule, outcome in outcomes for found, line in findings if found == rule))


class PortedTestRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name)
        cls.state = cls.root / ".adw-state"
        (cls.root / "home").mkdir()
        home = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        home.start()
        cls.addClassCleanup(home.stop)

    def write_outcome(self, path, content):
        session = self._testMethodName
        payload = {"cwd": str(self.root), "session_id": session, "hook_event_name": "PreToolUse",
                   "agent_type": TEST_WRITER, "tool_name": "Write",
                   "tool_input": {"file_path": str(self.root / path), "content": content}}
        earlier = reports(self.state)
        response = run_hook("PreToolUse", payload, state_dir=self.state, git_view=NO_REPOSITORY)
        return Outcome(decision_of(response), ported_rows(self.state, session, reports(self.state) - earlier))


def characterization(case):
    def test(self):
        self.assertEqual(Outcome(case.decision, case.rows), self.write_outcome(case.path, case.content))
    return test


for _case in CASES:
    setattr(PortedTestRules, "test_" + _case.name, characterization(_case))


DUPLICATE_PROBE = (
    "import sys\n"
    "sys.path[:0] = [sys.argv[1], sys.argv[2]]\n"
    "from lib import test_rules\n"
    "import probe_rules\n"
    "try:\n"
    "    test_rules.discover(probe_rules.__path__, 'probe_rules')\n"
    "except ValueError:\n"
    "    sys.exit(" + str(DUPLICATE_EXIT) + ")\n"
)
RULE_MODULE = (
    "from lib.test_rules import Rule, RuleSet\n"
    "RULE_SET = RuleSet(rules=(Rule({name!r}, 'detail', 'action'),), check=lambda unit, text: [])\n"
)


def probe_folder(root, first, second):
    package = root / "probe_rules"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "first.py").write_text(RULE_MODULE.format(name=first))
    (package / "second.py").write_text(RULE_MODULE.format(name=second))
    (root / "probe.py").write_text(DUPLICATE_PROBE)


class RuleDiscovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name)
        probe_folder(cls.root / "same", "probe_rule", "probe_rule")
        probe_folder(cls.root / "distinct", "probe_rule", "other_probe_rule")

    def discover_exit(self, folder):
        root = self.root / folder
        call = subprocess.run(
            [sys.executable, "-ISB", str(root / "probe.py"), str(ENGINE), str(root)],
            text=True, capture_output=True, env={"HOME": str(self.root), "PATH": os.environ["PATH"]},
        )
        return call.returncode

    def test_two_modules_declaring_the_same_rule_fail_discovery(self):
        self.assertEqual(DUPLICATE_EXIT, self.discover_exit("same"))

    def test_two_modules_declaring_distinct_rules_pass_discovery(self):
        self.assertEqual(0, self.discover_exit("distinct"))


if __name__ == "__main__":
    unittest.main()
