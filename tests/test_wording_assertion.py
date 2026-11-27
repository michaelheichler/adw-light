import json
import os
import stat
import subprocess
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


NO_REPOSITORY = FakeGitView({"roots": {}})

RULE = "wording_assertion"
DENY = "deny"
AGENT = "adw-test-writer"
TEST_FILE = "tests/test_probe.py"
BODY_START = 9
SENTENCE = " ".join(("the", "cache", "is", "empty"))
VERSIONED = " ".join(("Requires", "Python", "3.11", "or", "later"))
SOURCE_LINE = 3
APP_SOURCE = "src/app.py"
SHELL_SOURCE = "scripts/run.sh"
INDEX_DIR = "wording_index"
MOVED_LINE = 5
ALLOWED_SHAPES = (
    "a = b", "x == y or z", "a b", "SELECT name FROM users", "SELECT * FROM users",
    "--help for more", "run with -v for more",
)
HEADER = ("import re", "import unittest", "", "", "class Probe(unittest.TestCase):", "", "    def test_probe(self):",
          '        """A test must verify what the code does for its client, never how it does it."""')
SHOUTED = " ".join(("THE", "FILE", "IS", "NOT", "IN", "THE", "CACHE"))
SHOUTED_COMMAND = " ".join(("DELETE", "THE", "OLD", "FILE", "NOW"))
SHOUTED_CLAUSE = " ".join(("THE", "ROW", "FROM", "THE", "TABLE"))
SQL_INSERT = " ".join(("INSERT", "INTO", "the", "log"))
SQL_UPDATE = " ".join(("UPDATE", "the", "users", "SET", "active"))
SQL_CREATE = " ".join(("CREATE", "TABLE", "IF", "NOT", "EXISTS", "users"))
AMPERSAND_SENTENCE = " ".join(("Save", "&", "exit", "to", "continue"))
AND_OPERATOR = " ".join(("Save", "&&", "exit", "to", "continue"))
FILLER_DIR = "aaa"
FILLER_FILES = 25
PLAIN = "plain"
APP_PROJECT = "app_source"
SHELL_PROJECT = "shell_source"
OTHER_TEST_PROJECT = "other_test"
EDITED_PROJECT = "edited_source"
UNMARKED_PROJECT = "unmarked"
EMPTY_PROJECT = "empty"
BUDGET_PROJECT = "budget"
APP_TEXT = "import os\n\nMESSAGE = " + json.dumps(SENTENCE + " for now") + "\n"
MOVED_APP_TEXT = "import os\nimport re\n\n\nMESSAGE = " + json.dumps(SENTENCE + " for now") + "\n"
SHELL_TEXT = "#!/bin/sh\nset -e\necho " + json.dumps(SENTENCE + " for now") + "\n"
FILLER_LINE = "echo " + json.dumps(" ".join(("filler", "words", "for", "the", "budget", "row"))) + "\n"


def quoted(text):
    return json.dumps(text)


def probe_module(*body, top=()):
    lines = (*top, *HEADER, *("        " + line for line in body))
    return "\n".join(lines) + "\n"


def place(folder, relative, text):
    target = folder / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def project(root, name, files=(), marked=True):
    folder = root / name
    (folder / "tests").mkdir(parents=True)
    if marked:
        (folder / ".git").mkdir()
    for relative, text in files:
        place(folder, relative, text)
    return folder


def filled_budget_files(limit):
    lines = FILLER_LINE * (limit // len(FILLER_LINE))
    padded = lines + "#" * (limit - len(lines) - 1) + "\n"
    return [(f"{FILLER_DIR}/filler_{number:02d}.sh", padded) for number in range(FILLER_FILES)]


class WordingAssertionGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        (cls.root / "home").mkdir()
        project(cls.root, PLAIN)
        project(cls.root, APP_PROJECT, [(APP_SOURCE, APP_TEXT)])
        project(cls.root, SHELL_PROJECT, [(SHELL_SOURCE, SHELL_TEXT)])
        project(cls.root, OTHER_TEST_PROJECT, [("tests/test_other.py", APP_TEXT)])
        project(cls.root, EDITED_PROJECT, [(APP_SOURCE, APP_TEXT)])
        project(cls.root, EMPTY_PROJECT)
        project(cls.root, UNMARKED_PROJECT, [(APP_SOURCE, APP_TEXT)], marked=False)
        project(cls.root, BUDGET_PROJECT, [(APP_SOURCE, APP_TEXT), *filled_budget_files(1_000_000)])
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)

    def own_state(self):
        return self.root / ("state-" + self._testMethodName)

    def wording_rows(self, content, name=PLAIN, state=None):
        state = state or self.state
        repo = self.root / name
        payload = {
            "cwd": str(repo), "session_id": self._testMethodName, "hook_event_name": "PreToolUse",
            "agent_type": AGENT, "tool_name": "Write",
            "tool_input": {"file_path": str(repo / TEST_FILE), "content": content},
        }
        earlier = set((state / "reports").glob("*.json"))
        response = run_hook("PreToolUse", payload, state_dir=state, git_view=NO_REPOSITORY)
        added = sorted(set((state / "reports").glob("*.json")) - earlier)
        rows = [row for report in added for row in json.loads(report.read_text(encoding="utf-8"))]
        return response, [row for row in rows if row["rule"] == RULE]

    def verdict(self, content):
        response, rows = self.wording_rows(content)
        decision = response.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, tuple(row["line"] for row in rows)

    def first_row(self, content, name=PLAIN):
        return self.wording_rows(content, name)[1][0]

    def match_in_new_process(self, content, name):
        repo = self.root / name
        payload = {
            "cwd": str(repo), "session_id": self._testMethodName, "hook_event_name": "PreToolUse",
            "agent_type": AGENT, "tool_name": "Write",
            "tool_input": {"file_path": str(repo / TEST_FILE), "content": content},
        }
        earlier = set((self.state / "reports").glob("*.json"))
        subprocess.run(
            [sys.executable, "-ISB", str(PROJECT / "adw_light.py"), "PreToolUse", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True, check=True,
            env={"HOME": str(self.root / "home"), "PATH": os.environ.get("PATH", "")},
        )
        added = sorted(set((self.state / "reports").glob("*.json")) - earlier)
        rows = [row for report in added for row in json.loads(report.read_text(encoding="utf-8"))]
        return tuple(row.get("match") for row in rows if row["rule"] == RULE)

    def match_of(self, content, name):
        return tuple(row.get("match") for row in self.wording_rows(content, name)[1])

    def index_modes(self):
        return {stat.S_IMODE(path.stat().st_mode) for path in (self.state / INDEX_DIR).glob("*.json")}

    def expected_value_verdict(self, expected):
        return self.verdict(probe_module("computed = render()", f"self.assertEqual(computed, {expected})"))

    def test_assert_in_with_a_sentence_denies_on_the_assert_line(self):
        content = probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_plain_assert_in_with_a_sentence_denies(self):
        content = probe_module("computed = render()", f"assert {quoted(SENTENCE)} in computed")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_bare_equality_with_the_sentence_on_the_right_denies(self):
        content = probe_module("computed = render()", f"assert computed == {quoted(SENTENCE)}")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_bare_equality_with_the_sentence_on_the_left_denies(self):
        content = probe_module("computed = render()", f"assert {quoted(SENTENCE)} == computed")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_assert_equal_with_the_sentence_second_denies(self):
        content = probe_module("computed = render()", f"self.assertEqual(computed, {quoted(SENTENCE)})")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_assert_equal_with_the_sentence_first_denies(self):
        content = probe_module("computed = render()", f"self.assertEqual({quoted(SENTENCE)}, computed)")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_prose_nested_inside_the_actual_call_is_allowed(self):
        content = probe_module(f"self.assertEqual({quoted(DENY)}, decide({quoted(SENTENCE)}))")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_rule_id_expected_value_is_allowed(self):
        content = probe_module("computed = render()", f"self.assertEqual(computed, {quoted(RULE)})")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_camel_case_json_key_expected_value_is_allowed(self):
        content = probe_module("computed = render()", "self.assertIn(\"permissionDecision\", computed)")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_integer_exit_code_expected_value_is_allowed(self):
        content = probe_module("computed = render()", "self.assertEqual(computed, 2)")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_decision_token_expected_value_is_allowed(self):
        content = probe_module("computed = render()", f"self.assertEqual(computed, {quoted(DENY)})")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_sentence_with_a_path_separator_is_allowed(self):
        marked = " ".join(("see", "docs/setup", "for", "the", "steps"))
        content = probe_module("computed = render()", f"self.assertIn({quoted(marked)}, computed)")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_sentence_with_a_template_placeholder_is_allowed(self):
        marked = " ".join(("{name}", "is", "not", "in", "the", "list"))
        content = probe_module("computed = render()", f"self.assertIn({quoted(marked)}, computed)")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_multi_word_command_without_a_stopword_is_allowed(self):
        command = " ".join(("git", "status", "--short"))
        content = probe_module("computed = render()", f"self.assertIn({quoted(command)}, computed)")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_sentence_with_a_version_number_denies(self):
        content = probe_module("computed = render()", f"self.assertEqual(computed, {quoted(VERSIONED)})")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_match_names_the_python_source_that_holds_the_text(self):
        content = probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)")
        self.assertEqual(self.first_row(content, APP_PROJECT)["match"], f"{APP_SOURCE}:{SOURCE_LINE}")

    def test_match_names_the_shell_source_that_holds_the_text(self):
        content = probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)")
        self.assertEqual(self.first_row(content, SHELL_PROJECT)["match"], f"{SHELL_SOURCE}:{SOURCE_LINE}")

    def test_text_held_only_by_another_test_file_is_not_named_as_source(self):
        content = probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)")
        self.assertNotIn("match", self.first_row(content, OTHER_TEST_PROJECT))

    def test_sentence_without_a_source_match_still_denies(self):
        content = probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_name_bound_once_in_the_test_body_counts_as_the_literal(self):
        content = probe_module(f"expected = {quoted(SENTENCE)}", "computed = render()", "self.assertEqual(computed, expected)")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 2,)))

    def test_name_bound_twice_in_the_test_body_is_not_a_literal(self):
        content = probe_module(f"expected = {quoted(SENTENCE)}", "expected = render()", "self.assertEqual(lookup(), expected)")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_module_level_name_bound_once_counts_as_the_literal(self):
        content = probe_module("computed = render()", "self.assertEqual(computed, EXPECTED)",
                               top=(f"EXPECTED = {quoted(SENTENCE)}",))
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 2,)))

    def test_module_level_name_shadowed_in_the_test_is_not_a_literal(self):
        content = probe_module("EXPECTED = render()", "self.assertEqual(lookup(), EXPECTED)",
                               top=(f"EXPECTED = {quoted(SENTENCE)}",))
        self.assertEqual(self.verdict(content), (None, ()))

    def test_assert_true_over_a_sentence_membership_denies(self):
        content = probe_module("computed = render()", f"self.assertTrue({quoted(SENTENCE)} in computed)")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_startswith_a_sentence_denies(self):
        content = probe_module("computed = render()", f"assert computed.startswith({quoted(SENTENCE)})")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_re_search_for_a_sentence_denies(self):
        content = probe_module("computed = render()", f"assert re.search({quoted(SENTENCE)}, computed)")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_assert_regex_with_a_sentence_pattern_denies(self):
        content = probe_module("computed = render()", f"self.assertRegex(computed, {quoted(SENTENCE)})")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_f_string_sentence_expected_value_denies(self):
        template = "f" + quoted(" ".join(("the", "{size}", "cache", "is", "empty")))
        content = probe_module("size = 3", "computed = render()", f"self.assertEqual(computed, {template})")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 2,)))

    def test_both_sides_literal_is_allowed(self):
        content = probe_module("render()", f"self.assertEqual({quoted(SENTENCE)}, {quoted(SENTENCE)})")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_neither_side_literal_is_allowed(self):
        content = probe_module("computed = render()", "self.assertEqual(computed, lookup())")
        self.assertEqual(self.verdict(content), (None, ()))

    def test_second_run_reports_the_same_match_as_the_first(self):
        content = probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)")
        runs = self.match_of(content, APP_PROJECT) + self.match_of(content, APP_PROJECT)
        self.assertEqual(runs, (f"{APP_SOURCE}:{SOURCE_LINE}",) * 2)

    def test_edited_source_moves_the_match_on_the_next_run(self):
        content = probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)")
        first = self.match_in_new_process(content, EDITED_PROJECT)
        place(self.root / EDITED_PROJECT, APP_SOURCE, MOVED_APP_TEXT)
        second = self.match_in_new_process(content, EDITED_PROJECT)
        self.assertEqual(first + second, (f"{APP_SOURCE}:{SOURCE_LINE}", f"{APP_SOURCE}:{MOVED_LINE}"))

    def test_sentence_without_a_source_match_has_no_match_field(self):
        self.assertNotIn("match", self.first_row(probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)")))

    def test_sentence_without_a_source_match_leaves_no_index_folder(self):
        self.wording_rows(probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)"), EMPTY_PROJECT, self.own_state())
        self.assertFalse((self.own_state() / INDEX_DIR).exists())

    def test_project_without_git_has_no_match_field(self):
        self.assertNotIn("match", self.first_row(probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)"), UNMARKED_PROJECT))

    def test_project_without_git_leaves_no_index_folder(self):
        self.wording_rows(probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)"), UNMARKED_PROJECT, self.own_state())
        self.assertFalse((self.own_state() / INDEX_DIR).exists())

    def test_index_files_are_private_to_the_owner(self):
        self.wording_rows(probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)"), APP_PROJECT)
        self.assertEqual(self.index_modes(), {0o600})

    def test_assert_equal_with_keyword_operands_denies(self):
        content = probe_module("computed = render()", f"self.assertEqual(first=computed, second={quoted(SENTENCE)})")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_assert_in_with_keyword_operands_denies(self):
        content = probe_module("computed = render()", f"self.assertIn(member={quoted(SENTENCE)}, container=computed)")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_assert_regex_with_keyword_operands_denies(self):
        content = probe_module("computed = render()", f"self.assertRegex(text=computed, expected_regex={quoted(SENTENCE)})")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_assert_true_with_a_keyword_membership_denies(self):
        content = probe_module("computed = render()", f"self.assertTrue(expr={quoted(SENTENCE)} in computed)")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_sentence_inside_a_list_expected_value_denies(self):
        self.assertEqual(self.expected_value_verdict(f"[{quoted(SENTENCE)}, 2]"), (DENY, (BODY_START + 1,)))

    def test_sentence_as_a_dict_value_denies(self):
        expected = "{" + f"{quoted('error')}: {quoted(SENTENCE)}, {quoted('code')}: 2" + "}"
        self.assertEqual(self.expected_value_verdict(expected), (DENY, (BODY_START + 1,)))

    def test_sentence_nested_in_a_tuple_of_dicts_denies(self):
        expected = "({" + f"{quoted('rows')}: [{quoted(SENTENCE)}]" + "},)"
        self.assertEqual(self.expected_value_verdict(expected), (DENY, (BODY_START + 1,)))

    def test_sentence_joined_from_two_literals_denies(self):
        expected = quoted(" ".join(("the", "cache", ""))) + " + " + quoted(" ".join(("is", "empty")))
        self.assertEqual(self.expected_value_verdict(expected), (DENY, (BODY_START + 1,)))

    def test_sentence_with_a_contraction_denies(self):
        expected = quoted(" ".join(("don" + "'t", "retry", "later")))
        self.assertEqual(self.expected_value_verdict(expected), (DENY, (BODY_START + 1,)))

    def test_sentence_with_uppercase_acronyms_denies(self):
        expected = quoted(" ".join(("the", "HTTP", "API", "is", "down")))
        self.assertEqual(self.expected_value_verdict(expected), (DENY, (BODY_START + 1,)))

    def test_operator_expression_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(ALLOWED_SHAPES[0])), (None, ()))

    def test_comparison_expression_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(ALLOWED_SHAPES[1])), (None, ()))

    def test_single_letter_words_are_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(ALLOWED_SHAPES[2])), (None, ()))

    def test_sql_query_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(ALLOWED_SHAPES[3])), (None, ()))

    def test_sql_query_with_a_star_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(ALLOWED_SHAPES[4])), (None, ()))

    def test_text_led_by_a_long_flag_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(ALLOWED_SHAPES[5])), (None, ()))

    def test_text_holding_a_short_flag_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(ALLOWED_SHAPES[6])), (None, ()))

    def test_tuple_with_a_computed_element_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(f"(render(), {quoted(SENTENCE)})"), (None, ()))

    def test_literal_joined_with_a_computed_part_is_allowed(self):
        expected = quoted(" ".join(("the", "cache", ""))) + " + str(3)"
        self.assertEqual(self.expected_value_verdict(expected), (None, ()))

    def test_prose_inside_a_list_argument_of_the_actual_call_is_allowed(self):
        content = probe_module(f"self.assertEqual({quoted(DENY)}, decide([{quoted(SENTENCE)}]))")
        self.assertEqual(self.verdict(content), (None, ()))

    def pair_verdict(self, method, first, second):
        return self.verdict(probe_module("computed = render()", f"self.{method}({first}, {second})"))

    def test_all_caps_sentence_expected_value_denies(self):
        self.assertEqual(self.expected_value_verdict(quoted(SHOUTED)), (DENY, (BODY_START + 1,)))

    def test_all_caps_sentence_against_an_upper_cased_actual_denies(self):
        content = probe_module("computed = render()", f"self.assertIn({quoted(SHOUTED)}, computed.upper())")
        self.assertEqual(self.verdict(content), (DENY, (BODY_START + 1,)))

    def test_sql_insert_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(SQL_INSERT)), (None, ()))

    def test_sql_update_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(SQL_UPDATE)), (None, ()))

    def test_sql_create_table_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(SQL_CREATE)), (None, ()))

    def test_all_caps_sentence_led_by_a_sql_verb_denies(self):
        self.assertEqual(self.expected_value_verdict(quoted(SHOUTED_COMMAND)), (DENY, (BODY_START + 1,)))

    def test_all_caps_sentence_holding_a_sql_clause_word_denies(self):
        self.assertEqual(self.expected_value_verdict(quoted(SHOUTED_CLAUSE)), (DENY, (BODY_START + 1,)))

    def test_assert_list_equal_with_a_sentence_list_denies(self):
        self.assertEqual(self.pair_verdict("assertListEqual", "computed", f"[{quoted(SENTENCE)}]"), (DENY, (BODY_START + 1,)))

    def test_assert_count_equal_with_a_sentence_list_denies(self):
        self.assertEqual(self.pair_verdict("assertCountEqual", "computed", f"[{quoted(SENTENCE)}]"), (DENY, (BODY_START + 1,)))

    def test_assert_sequence_equal_with_a_sentence_list_denies(self):
        self.assertEqual(self.pair_verdict("assertSequenceEqual", "computed", f"[{quoted(SENTENCE)}]"), (DENY, (BODY_START + 1,)))

    def test_assert_tuple_equal_with_a_sentence_tuple_denies(self):
        self.assertEqual(self.pair_verdict("assertTupleEqual", "computed", f"({quoted(SENTENCE)},)"), (DENY, (BODY_START + 1,)))

    def test_assert_dict_equal_with_a_sentence_value_denies(self):
        expected = "{" + f"{quoted('error')}: {quoted(SENTENCE)}" + "}"
        self.assertEqual(self.pair_verdict("assertDictEqual", "computed", expected), (DENY, (BODY_START + 1,)))

    def test_assert_set_equal_with_a_sentence_set_denies(self):
        self.assertEqual(self.pair_verdict("assertSetEqual", "computed", "{" + quoted(SENTENCE) + "}"), (DENY, (BODY_START + 1,)))

    def test_assert_list_equal_with_literals_on_both_sides_is_allowed(self):
        literal = f"[{quoted(SENTENCE)}]"
        self.assertEqual(self.pair_verdict("assertListEqual", literal, literal), (None, ()))

    def test_assert_count_equal_with_literals_on_both_sides_is_allowed(self):
        literal = f"[{quoted(SENTENCE)}]"
        self.assertEqual(self.pair_verdict("assertCountEqual", literal, literal), (None, ()))

    def test_assert_sequence_equal_with_literals_on_both_sides_is_allowed(self):
        literal = f"[{quoted(SENTENCE)}]"
        self.assertEqual(self.pair_verdict("assertSequenceEqual", literal, literal), (None, ()))

    def test_assert_tuple_equal_with_literals_on_both_sides_is_allowed(self):
        literal = f"({quoted(SENTENCE)},)"
        self.assertEqual(self.pair_verdict("assertTupleEqual", literal, literal), (None, ()))

    def test_assert_dict_equal_with_literals_on_both_sides_is_allowed(self):
        literal = "{" + f"{quoted('error')}: {quoted(SENTENCE)}" + "}"
        self.assertEqual(self.pair_verdict("assertDictEqual", literal, literal), (None, ()))

    def test_assert_set_equal_with_literals_on_both_sides_is_allowed(self):
        literal = "{" + quoted(SENTENCE) + "}"
        self.assertEqual(self.pair_verdict("assertSetEqual", literal, literal), (None, ()))

    def test_sentence_with_a_lone_ampersand_denies(self):
        self.assertEqual(self.expected_value_verdict(quoted(AMPERSAND_SENTENCE)), (DENY, (BODY_START + 1,)))

    def test_double_ampersand_operator_is_allowed(self):
        self.assertEqual(self.expected_value_verdict(quoted(AND_OPERATOR)), (None, ()))

    def test_src_match_survives_an_earlier_folder_that_exhausts_the_read_budget(self):
        content = probe_module("computed = render()", f"self.assertIn({quoted(SENTENCE)}, computed)")
        self.assertEqual(self.first_row(content, BUDGET_PROJECT).get("match"), f"{APP_SOURCE}:{SOURCE_LINE}")


if __name__ == "__main__":
    unittest.main()
