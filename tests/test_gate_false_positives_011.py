from false_positive_home import DENY, PASSED, FalsePositiveHome
from t19_language_home import ALLOWED, LIBRARY_NEEDED, LIBRARY_READY, blocked, exact_verdict
import unittest


TEST_WRITER = "adw-test-writer"
PASSIVE = (DENY, ["passive_voice"])
NOTES = "docs/notes.md"
TAP_STREAM = (
    "TAP version 13\n1..3\nok 1 - parses input\nnot ok 2 - writes output\n"
    "  ---\n  message: failed\n  ...\n# skipped one\nok 3 - cleans up\n"
)
TAP_WITH_PROSE = "1..2\nok 1 - parses input\nThe cache was rebuilt, cleaned and saved.\nok 2 - cleans up\n"
TAP_FINDINGS = (DENY, ["prose_colon", "spaced_hyphen"])
OBSERVE_ONLY = {"ste_phrasal_verb"}
NOT_BLOCKED = (None, set())
PYTHON_TAP = "1..3\nok 1\n# Loop over the items and add them together.\n"
BATS_GLOB = (
    '#!/usr/bin/env bats\n\n@test "scripts run" {\n  run bash -c "$x"/scripts/*.sh | sed "s/a/b/"\n'
    '  run bash -c "$x"/scripts/*.sh | sed "s/a/b/"; [ "$status" -eq 0 ]\n}\n'
)
BATS_PATH = "tests/suite.bats"
BATS_HEAD = '#!/usr/bin/env bats\n\nsetup() {\n'
PARSE_ERROR_LINE = '  [ "$x" = - ]\n'
HEREDOC_WITH_BRACE = "  cat <<EOF\n}\nEOF\n"
TOO_LONG = blocked(("function_too_long", 3))


def notes(sentence):
    return "# Notes\n\n" + sentence + "\n"


def echo_lines(count, indent):
    return "".join(f'{indent}echo "line {number}"\n' for number in range(1, count + 1))


def bats_file(*body):
    return BATS_HEAD + "".join(body) + "}\n\n" + echo_lines(90, "")


class PassiveVoiceAfterCommandNames(FalsePositiveHome):
    def verdict(self, sentence):
        repo = self.committed_repo({"README.md": "Short.\n"})
        return self.write(repo, repo / NOTES, notes(sentence))

    def test_command_name_followed_by_a_flag_and_a_command_list_passes(self):
        self.assertEqual(self.verdict("Examples are reset --hard, stash, merge, clean and worktree add."), PASSED)

    def test_command_name_followed_by_a_short_flag_passes(self):
        self.assertEqual(self.verdict("The flags are set -e for scripts."), PASSED)

    def test_command_name_opening_a_list_of_bare_command_names_passes(self):
        self.assertEqual(self.verdict("The commands are reset, stash, merge and clean."), PASSED)

    def test_passive_with_a_by_phrase_is_still_refused(self):
        self.assertEqual(self.verdict("The file is reset by the tool."), PASSIVE)

    def test_base_form_participle_with_an_adverb_is_still_refused(self):
        self.assertEqual(self.verdict("Changes are reset nightly."), PASSIVE)

    def test_short_passive_is_still_refused(self):
        self.assertEqual(self.verdict("It was closed."), PASSIVE)

    def test_list_of_participles_is_still_refused(self):
        self.assertEqual(self.verdict("The cache was rebuilt, cleaned and saved."), PASSIVE)

    def test_base_form_participle_before_then_and_a_participle_is_still_refused(self):
        self.assertEqual(self.verdict("The branch is reset, then pushed."), PASSIVE)

    def test_base_form_participle_before_now_and_adjectives_is_still_refused(self):
        self.assertEqual(self.verdict("The counter is reset, now empty and ready."), PASSIVE)

    def test_participle_before_a_list_of_adjectives_is_still_refused(self):
        self.assertEqual(self.verdict("The tests are fixed, green and fast."), PASSIVE)


class TapStreams(FalsePositiveHome):
    def verdict(self, relative, content):
        repo = self.committed_repo({"README.md": "Short.\n", ".gitignore": "out/\n"})
        return self.write(repo, repo / relative, content)

    def unblocked(self, relative, content):
        decision, rules = self.verdict(relative, content)
        return decision, set(rules) - OBSERVE_ONLY

    def test_tap_stream_saved_as_text_in_an_ignored_folder_passes_without_blocking_findings(self):
        self.assertEqual(self.unblocked("out/results.txt", TAP_STREAM), NOT_BLOCKED)

    def test_tap_stream_saved_as_text_in_a_folder_that_is_not_ignored_keeps_its_findings(self):
        self.assertEqual(self.verdict("report/results.txt", TAP_STREAM), TAP_FINDINGS)

    def test_tap_stream_saved_as_tap_in_an_ignored_folder_passes_without_blocking_findings(self):
        self.assertEqual(self.unblocked("out/results.tap", TAP_STREAM), NOT_BLOCKED)

    def test_tap_stream_saved_as_tap_in_a_folder_that_is_not_ignored_keeps_its_findings(self):
        self.assertEqual(self.verdict("report/results.tap", TAP_STREAM), TAP_FINDINGS)

    def test_text_file_with_a_tap_plan_and_a_prose_line_keeps_its_prose_findings(self):
        self.assertEqual(self.verdict("out/run.txt", TAP_WITH_PROSE), (DENY, ["passive_voice", "spaced_hyphen"]))

    def test_python_file_that_starts_with_a_tap_plan_keeps_its_comment_findings(self):
        self.assertEqual(self.verdict("src/tap.py", PYTHON_TAP), (DENY, ["added_comment", "what_comment"]))


class BatsGlobLines(FalsePositiveHome):
    def test_bats_line_with_a_quoted_variable_glob_and_a_pipe_passes(self):
        repo = self.committed_repo({"README.md": "Short.\n"})
        tool_input = {"file_path": str(repo / BATS_PATH), "content": BATS_GLOB}
        payload = {"cwd": str(repo), "agent_type": TEST_WRITER, "tool_name": "Write", "tool_input": tool_input}
        self.assertEqual(self.hook("PreToolUse", payload), PASSED)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class BatsFunctionEnds(unittest.TestCase):
    def test_short_function_holding_a_parse_error_ends_at_its_column_zero_brace(self):
        content = bats_file(PARSE_ERROR_LINE, echo_lines(3, "  "))
        self.assertEqual(exact_verdict(self, BATS_PATH, content), ALLOWED)

    def test_brace_inside_a_heredoc_does_not_end_a_long_function_with_a_parse_error(self):
        content = bats_file(HEREDOC_WITH_BRACE, PARSE_ERROR_LINE, echo_lines(85, "  "))
        self.assertEqual(exact_verdict(self, BATS_PATH, content), TOO_LONG)

    def test_function_over_eighty_lines_with_a_parse_error_is_still_refused(self):
        content = bats_file(PARSE_ERROR_LINE, echo_lines(85, "  "))
        self.assertEqual(exact_verdict(self, BATS_PATH, content), TOO_LONG)


if __name__ == "__main__":
    unittest.main()
