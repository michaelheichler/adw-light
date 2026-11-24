import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
AGENT = "adw-test-writer"
TEST_FILE = "tests/test_probe.py"
DENY = "deny"
WORDING = "wording_assertion"
EXCEPTION = "wording_exception_match"
LOG = "wording_log_assertion"
LOCATION = "literal_location"
WATCHED = frozenset({WORDING, EXCEPTION, LOG, LOCATION})
BODY_START = 7
UNITTEST_HEADER = ("import logging", "import unittest", "", "", "class Probe(unittest.TestCase):", "    def test_probe(self):")
PYTEST_HEADER = ("import logging", "import pytest", "", "", "", "def test_probe(caplog):")
SENTENCE = " ".join(("name", "must", "not", "be", "empty"))
LOG_SENTENCE = " ".join(("the", "cache", "is", "empty"))
MACHINE_CODE = "E042"
RELATIVE_LOCATION = "/".join(("tests", "fixtures", "sample.py")) + ":3"
ABSOLUTE_LOCATION = "/".join(("", "tmp", "x", "sample.py")) + ":3"
HOST_AND_PORT = ".".join(("example", "com")) + ":8080"
FINDING_LOCATION = ":".join(("commit_message.md", "1"))
FINDING_SENTENCE = " ".join(("Long", "word", "for", "use", '"utilize".', "Use", "'use'.", "(utilize)"))
MACHINE_TOKEN = "_".join(("literal", "pin"))
TYPED_PATH = json.dumps(RELATIVE_LOCATION.rsplit(":", 1)[0])
NOT_READY = " ".join(("{}", "is", "not", "ready", "yet"))
SUBJECT = " ".join(("the", "cache"))


def quoted(text):
    return json.dumps(text)


def unittest_module(*body):
    return "\n".join((*UNITTEST_HEADER, *("        " + line for line in body))) + "\n"


def pytest_module(*body):
    return "\n".join((*PYTEST_HEADER, *("    " + line for line in body))) + "\n"


def unittest_module_with_constant(constant, *body):
    header = (*UNITTEST_HEADER[:2], constant, *UNITTEST_HEADER[3:])
    return "\n".join((*header, *("        " + line for line in body))) + "\n"


class WordingFormsGate(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.home.mkdir()
        (self.repo / "tests").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def write_test(self, content):
        payload = {
            "cwd": str(self.repo), "session_id": "forms", "hook_event_name": "PreToolUse",
            "agent_type": AGENT, "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / TEST_FILE), "content": content},
        }
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        self.assertEqual(call.returncode, 0)
        return json.loads(call.stdout)

    def watched_rows(self):
        reports = sorted((self.state / "reports").glob("*.json"))
        rows = [row for report in reports for row in json.loads(report.read_text(encoding="utf-8"))]
        return tuple((row["rule"], row["line"]) for row in rows if row["rule"] in WATCHED)

    def verdict(self, content):
        decision = self.write_test(content).get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, self.watched_rows()

    def test_pytest_raises_with_a_prose_match_denies_as_an_exception_match(self):
        content = pytest_module(f"with pytest.raises(ValueError, match={quoted(SENTENCE)}):", "    build()")
        self.assertEqual(self.verdict(content), (DENY, ((EXCEPTION, BODY_START),)))

    def test_pytest_raises_with_an_anchored_regex_of_prose_denies(self):
        pattern = "^" + SENTENCE + "\\.$"
        content = pytest_module(f"with pytest.raises(ValueError, match={quoted(pattern)}):", "    build()")
        self.assertEqual(self.verdict(content), (DENY, ((EXCEPTION, BODY_START),)))

    def test_assert_raises_regex_with_a_prose_pattern_denies(self):
        content = unittest_module(f"with self.assertRaisesRegex(ValueError, {quoted(SENTENCE)}):", "    build()")
        self.assertEqual(self.verdict(content), (DENY, ((EXCEPTION, BODY_START),)))

    def test_assert_raises_regex_with_a_keyword_prose_pattern_denies(self):
        content = unittest_module(f"with self.assertRaisesRegex(ValueError, expected_regex={quoted(SENTENCE)}):", "    build()")
        self.assertEqual(self.verdict(content), (DENY, ((EXCEPTION, BODY_START),)))

    def test_prose_checked_against_the_caught_exception_text_denies(self):
        content = pytest_module(
            "with pytest.raises(ValueError) as excinfo:", "    build()",
            f"assert {quoted(SENTENCE)} in str(excinfo.value)",
        )
        self.assertEqual(self.verdict(content), (DENY, ((EXCEPTION, BODY_START + 2),)))

    def test_excinfo_match_with_prose_denies(self):
        content = pytest_module("with pytest.raises(ValueError) as excinfo:", "    build()", f"excinfo.match({quoted(SENTENCE)})")
        self.assertEqual(self.verdict(content), (DENY, ((EXCEPTION, BODY_START + 2),)))

    def test_pytest_raises_with_a_machine_code_match_is_allowed(self):
        content = pytest_module(f"with pytest.raises(ValueError, match={quoted(MACHINE_CODE)}):", "    build()")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_pytest_raises_with_a_check_callable_is_allowed(self):
        content = pytest_module("with pytest.raises(ValueError, check=lambda error: error.code == 2):", "    build()")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_prose_in_caplog_text_denies_as_a_log_assertion(self):
        content = pytest_module("run()", f"assert {quoted(LOG_SENTENCE)} in caplog.text")
        self.assertEqual(self.verdict(content), (DENY, ((LOG, BODY_START + 1),)))

    def test_prose_in_assert_logs_output_denies(self):
        expected = quoted("INFO:app:" + LOG_SENTENCE)
        content = unittest_module(
            "with self.assertLogs(\"app\", level=\"INFO\") as logs:", "    run()",
            f"self.assertEqual(logs.output, [{expected}])",
        )
        self.assertEqual(self.verdict(content), (DENY, ((LOG, BODY_START + 2),)))

    def test_prose_in_messages_built_from_caplog_records_denies(self):
        content = pytest_module(
            "run()", "messages = [record.getMessage() for record in caplog.records]",
            f"assert {quoted(LOG_SENTENCE)} in messages",
        )
        self.assertEqual(self.verdict(content), (DENY, ((LOG, BODY_START + 2),)))

    def test_prose_in_messages_read_from_any_records_with_get_message_denies(self):
        content = pytest_module(
            "run()", "messages = [record.getMessage() for record in handler.records]",
            f"assert {quoted(LOG_SENTENCE)} in messages",
        )
        self.assertEqual(self.verdict(content), (DENY, ((LOG, BODY_START + 2),)))

    def test_prose_passed_to_a_mocked_logger_level_call_denies(self):
        content = unittest_module("logger = Mock()", "run(logger)", f"logger.warning.assert_called_once_with({quoted(LOG_SENTENCE)})")
        self.assertEqual(self.verdict(content), (DENY, ((LOG, BODY_START + 2),)))

    def test_caplog_level_name_check_is_allowed(self):
        content = pytest_module("run()", "assert caplog.records[0].levelname == \"WARNING\"")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_assert_logs_level_name_check_is_allowed(self):
        content = unittest_module(
            "with self.assertLogs(\"app\", level=\"INFO\") as logs:", "    run()",
            "self.assertEqual(logs.records[0].levelname, \"INFO\")",
        )
        self.assertEqual(self.verdict(content), (None, ()))

    def test_location_built_from_the_fixture_variable_is_allowed(self):
        content = pytest_module("fixture_path = make_fixture()", "assert scan(fixture_path) == f\"{fixture_path}:3\"")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_location_built_on_the_fixture_folder_variable_is_allowed(self):
        content = pytest_module("fixture_dir = make_fixture()", "assert scan(fixture_dir) == f\"{fixture_dir}/sample.py:3\"")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_relative_location_typed_out_denies(self):
        content = pytest_module(f"assert scan() == {quoted(RELATIVE_LOCATION)}")
        self.assertEqual(self.verdict(content), (DENY, ((LOCATION, BODY_START),)))

    def test_absolute_location_typed_out_denies(self):
        content = pytest_module(f"assert scan() == {quoted(ABSOLUTE_LOCATION)}")
        self.assertEqual(self.verdict(content), (DENY, ((LOCATION, BODY_START),)))

    def test_location_with_a_constant_line_placeholder_denies(self):
        typed_path = RELATIVE_LOCATION.rsplit(":", 1)[0]
        content = pytest_module(f"assert loc() == f\"{typed_path}:{{3}}\"")
        self.assertEqual(self.verdict(content), (DENY, ((LOCATION, BODY_START),)))

    def test_location_with_only_a_computed_line_denies(self):
        typed_path = RELATIVE_LOCATION.rsplit(":", 1)[0]
        content = pytest_module("line = 3", f"assert loc() == f\"{typed_path}:{{line}}\"")
        self.assertEqual(self.verdict(content), (DENY, ((LOCATION, BODY_START + 1),)))

    def test_location_whose_path_comes_from_a_computed_root_is_allowed(self):
        content = pytest_module("root = make_fixture()", "assert loc() == f\"{root}/sample.py:3\"")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_finding_constant_that_starts_with_a_location_denies_as_wording_and_location(self):
        constant = "FINDING = " + quoted(FINDING_LOCATION + " " + FINDING_SENTENCE)
        content = unittest_module_with_constant(constant, "self.assertIn(FINDING, report())")
        decision, rows = self.verdict(content)
        self.assertEqual((decision, sorted(rows)), (DENY, [(LOCATION, BODY_START), (WORDING, BODY_START)]))

    def test_location_followed_by_a_machine_token_denies_only_as_a_location(self):
        content = pytest_module(f"assert scan() == {quoted(RELATIVE_LOCATION + ' ' + MACHINE_TOKEN)}")
        self.assertEqual(self.verdict(content), (DENY, ((LOCATION, BODY_START),)))

    def test_location_typed_out_through_positional_format_denies(self):
        content = pytest_module(f"assert scan() == \"{{}}:{{}}\".format({TYPED_PATH}, 3)")
        self.assertEqual(self.verdict(content), (DENY, ((LOCATION, BODY_START),)))

    def test_location_typed_out_through_keyword_format_denies(self):
        content = pytest_module(f"assert scan() == \"{{path}}:3\".format(path={TYPED_PATH})")
        self.assertEqual(self.verdict(content), (DENY, ((LOCATION, BODY_START),)))

    def test_location_typed_out_through_percent_with_one_value_denies(self):
        content = pytest_module(f"assert scan() == \"%s:3\" % {TYPED_PATH}")
        self.assertEqual(self.verdict(content), (DENY, ((LOCATION, BODY_START),)))

    def test_location_typed_out_through_percent_with_a_tuple_denies(self):
        content = pytest_module(f"assert scan() == \"%s:%d\" % ({TYPED_PATH}, 3)")
        self.assertEqual(self.verdict(content), (DENY, ((LOCATION, BODY_START),)))

    def test_location_typed_out_through_percent_with_a_dict_denies(self):
        content = pytest_module(f"assert scan() == \"%(p)s:3\" % {{\"p\": {TYPED_PATH}}}")
        self.assertEqual(self.verdict(content), (DENY, ((LOCATION, BODY_START),)))

    def test_location_formatted_from_the_fixture_variable_is_allowed(self):
        content = pytest_module("fixture_path = make_fixture()", "assert scan(fixture_path) == \"{}:3\".format(fixture_path)")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_location_percent_formatted_from_the_fixture_variable_is_allowed(self):
        content = pytest_module("fixture_path = make_fixture()", "assert scan(fixture_path) == \"%s:%d\" % (fixture_path, 3)")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_sentence_formatted_from_constant_pieces_denies_as_wording(self):
        content = pytest_module(f"assert render() == {quoted(NOT_READY)}.format({quoted(SUBJECT)})")
        self.assertEqual(self.verdict(content), (DENY, ((WORDING, BODY_START),)))

    def test_host_and_port_is_not_a_location(self):
        content = pytest_module(f"assert scan() == {quoted(HOST_AND_PORT)}")
        self.assertEqual(self.verdict(content), (None, ()))


if __name__ == "__main__":
    unittest.main()
