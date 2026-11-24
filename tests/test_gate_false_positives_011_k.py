from false_positive_home import DENY, PASSED, FalsePositiveHome
from t19_language_home import ALLOWED, LIBRARY_NEEDED, LIBRARY_READY, blocked, exact_verdict
import unittest


COLON = (DENY, ["prose_colon"])
HARD_PATH = (DENY, ["hard_coded_path"])
PASSIVE = (DENY, ["passive_voice"])
PLAN = "docs/plan.md"
STEP = "The plan has one step: auth runs first."
BATS_PATH = "tests/suite.bats"
SHELL_PATH = "tools/run.sh"
BATS_HEAD = "#!/usr/bin/env bats\n\n"
SHELL_HEAD = "#!/bin/bash\n\n"
BROKEN_TEARDOWN = 'teardown() {\n  [ "$x" = - ]\n  echo done\n}\n\n'
BROKEN_TAIL = '\nteardown() {\n  [ "$x" = - ]\n  echo done\n}\n'


def echo_lines(count, indent="  "):
    return "".join(f'{indent}echo "line {number}"\n' for number in range(1, count + 1))


def setup_block(count, closed=True):
    return "setup() {\n" + echo_lines(count) + ("}\n" if closed else "")


class ColonsInMarkdownHeadings(FalsePositiveHome):
    def verdict(self, content):
        repo = self.committed_repo({"README.md": "Short.\n"})
        return self.write(repo, repo / PLAN, content)

    def test_atx_heading_with_a_colon_passes(self):
        self.assertEqual(self.verdict("# Plan\n\n## Phase 1: Auth\n\nShort line here.\n"), PASSED)

    def test_setext_heading_underlined_with_equals_signs_passes(self):
        self.assertEqual(self.verdict("Phase 1: Auth\n=============\n\nShort line here.\n"), PASSED)

    def test_setext_heading_underlined_with_hyphens_passes(self):
        self.assertEqual(self.verdict("# Plan\n\nPhase 1: Auth\n-------------\n\nShort line here.\n"), PASSED)

    def test_colon_inside_a_prose_sentence_is_still_refused(self):
        self.assertEqual(self.verdict("# Plan\n\n" + STEP + "\n"), COLON)

    def test_colon_inside_a_list_item_is_still_refused(self):
        self.assertEqual(self.verdict("# Plan\n\n- " + STEP + "\n"), COLON)

    def test_list_item_above_a_hyphen_line_is_not_read_as_a_heading(self):
        self.assertEqual(self.verdict("# Plan\n\n- " + STEP + "\n---\n"), COLON)

    def test_sentence_above_a_blank_line_and_a_thematic_break_is_still_refused(self):
        self.assertEqual(self.verdict("# Plan\n\n" + STEP + "\n\n---\n"), COLON)

    def test_colon_inside_a_table_cell_is_still_refused(self):
        table = "| Step | Note |\n|---|---|\n| one | " + STEP + " |\n"
        self.assertEqual(self.verdict("# Plan\n\n" + table), COLON)


class ProgramOutputFiles(FalsePositiveHome):
    def verdict(self, relative, content):
        repo = self.committed_repo({"README.md": "Short.\n", ".gitignore": "logs/\n"})
        return self.write(repo, repo / relative, content)

    def plugin_cache(self):
        return str(self.home / ".adw-lite" / "cache" / "plugin" / "adw-light")

    def release_line(self):
        return "copied release to " + str(self.root / "stage" / "release") + "\n"

    def test_deploy_log_line_holding_a_temp_folder_path_in_an_ignored_folder_passes(self):
        self.assertEqual(self.verdict("logs/deploy.log", self.release_line()), PASSED)

    def test_deploy_log_line_holding_a_temp_folder_path_in_a_folder_that_is_not_ignored_is_refused(self):
        self.assertEqual(self.verdict("reports/deploy.log", self.release_line()), HARD_PATH)

    def test_deploy_log_line_holding_a_plugin_cache_path_in_an_ignored_folder_passes(self):
        self.assertEqual(self.verdict("logs/deploy.log", "installed " + self.plugin_cache() + "\n"), PASSED)

    def test_deploy_log_line_holding_a_plugin_cache_path_in_a_folder_that_is_not_ignored_is_refused(self):
        self.assertEqual(self.verdict("reports/deploy.log", "installed " + self.plugin_cache() + "\n"), HARD_PATH)

    def test_out_file_line_holding_a_plugin_cache_path_in_an_ignored_folder_passes(self):
        self.assertEqual(self.verdict("logs/deploy.out", "installed " + self.plugin_cache() + "\n"), PASSED)

    def test_out_file_line_holding_a_plugin_cache_path_in_a_folder_that_is_not_ignored_is_refused(self):
        self.assertEqual(self.verdict("reports/deploy.out", "installed " + self.plugin_cache() + "\n"), HARD_PATH)

    def test_python_string_holding_a_plugin_cache_path_is_still_refused(self):
        source = "TARGET = " + repr(self.plugin_cache()) + "\n"
        self.assertEqual(self.verdict("src/deploy.py", source), HARD_PATH)

    def test_log_file_keeps_its_prose_findings(self):
        self.assertEqual(self.verdict("logs/deploy.log", "The cache was rebuilt, cleaned and saved.\n"), PASSIVE)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class ShellFunctionsBesideParseErrors(unittest.TestCase):
    def test_long_bats_function_after_a_function_holding_a_parse_error_is_refused(self):
        content = BATS_HEAD + BROKEN_TEARDOWN + setup_block(85)
        self.assertEqual(exact_verdict(self, BATS_PATH, content), blocked(("function_too_long", 8)))

    def test_long_shell_function_after_a_function_holding_a_parse_error_is_refused(self):
        content = SHELL_HEAD + BROKEN_TEARDOWN + setup_block(85)
        self.assertEqual(exact_verdict(self, SHELL_PATH, content), blocked(("function_too_long", 8)))

    def test_long_bats_function_running_to_the_end_of_the_file_after_a_parse_error_is_refused(self):
        content = BATS_HEAD + BROKEN_TEARDOWN + setup_block(85, closed=False)
        self.assertEqual(exact_verdict(self, BATS_PATH, content), blocked(("function_too_long", 8)))

    def test_brace_at_column_zero_inside_a_quoted_string_does_not_end_a_long_function(self):
        body = '  msg="first\n}\nlast"\n  [ "$x" = - ]\n' + echo_lines(85) + "}\n"
        content = BATS_HEAD + "setup() {\n" + body
        self.assertEqual(exact_verdict(self, BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_short_function_before_a_parse_error_elsewhere_passes(self):
        content = BATS_HEAD + setup_block(5) + BROKEN_TAIL + echo_lines(90, "")
        self.assertEqual(exact_verdict(self, BATS_PATH, content), ALLOWED)

    def test_long_function_before_a_parse_error_elsewhere_is_still_refused(self):
        content = BATS_HEAD + setup_block(85) + BROKEN_TAIL
        self.assertEqual(exact_verdict(self, BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_short_function_running_to_the_end_of_the_file_passes(self):
        content = BATS_HEAD + setup_block(5, closed=False)
        self.assertEqual(exact_verdict(self, BATS_PATH, content), ALLOWED)


def bats_block(count, closed=True):
    return '@test "runs the steps" {\n' + echo_lines(count) + ("}\n" if closed else "")


def wrapped_bats_block(opening, closing):
    return '@test "runs the steps" {\n' + opening + "}\n" + closing + echo_lines(85) + "}\n"


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class BatsTestBlockLength(unittest.TestCase):
    def test_test_block_of_86_lines_in_a_clean_bats_file_is_refused(self):
        content = BATS_HEAD + bats_block(84)
        self.assertEqual(exact_verdict(self, BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_test_block_of_20_lines_in_a_clean_bats_file_passes(self):
        content = BATS_HEAD + bats_block(18)
        self.assertEqual(exact_verdict(self, BATS_PATH, content), ALLOWED)

    def test_long_test_block_left_open_at_the_end_of_the_file_is_refused(self):
        content = BATS_HEAD + bats_block(85, closed=False)
        self.assertEqual(exact_verdict(self, BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_brace_at_column_zero_inside_a_heredoc_does_not_end_a_long_bats_block(self):
        content = BATS_HEAD + wrapped_bats_block("  cat <<EOF\n", "EOF\n")
        self.assertEqual(exact_verdict(self, BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_brace_at_column_zero_inside_a_multi_line_quote_does_not_end_a_long_bats_block(self):
        content = BATS_HEAD + wrapped_bats_block('  msg="first\n', 'last"\n')
        self.assertEqual(exact_verdict(self, BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_long_function_in_a_clean_shell_file_is_still_refused(self):
        content = SHELL_HEAD + setup_block(85)
        self.assertEqual(exact_verdict(self, SHELL_PATH, content), blocked(("function_too_long", 3)))

    def test_short_function_in_a_clean_shell_file_still_passes(self):
        content = SHELL_HEAD + setup_block(18)
        self.assertEqual(exact_verdict(self, SHELL_PATH, content), ALLOWED)


if __name__ == "__main__":
    unittest.main()
