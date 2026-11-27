import unittest

from fake_repo_home import README, FakeRepoCase, Repo
from in_memory_hook import ALLOWED, DENY, PASSED, PRINCIPLE, InMemoryHookCase, blocked


HARD_PATH = (DENY, ["hard_coded_path"])
PATH_NOTED = (None, ["hard_coded_system_path"])
PASSIVE = (DENY, ["passive_voice"])
PASSIVE_NOTED = (None, ["passive_voice"])
PLAN = "docs/plan.md"
STEP = "The plan has one step: auth runs first."
THREE_COLONS = "Lint runs second: it checks types too. The deploy waits for one thing: a green build. Rollback stays simple: revert the release tag and redeploy the previous build from the archive.\n"
STEPS = STEP + " " + THREE_COLONS.strip()
REPLY = "assistant-reply.md"
BATS_PATH = "tests/suite.bats"
SHELL_PATH = "tools/run.sh"
SOURCE_BATS_PATH = "scripts/suite.bats"
BATS_HEAD = "#!/usr/bin/env bats\n\n"
CITED_BATS_HEAD = "#!/usr/bin/env bats\n# Principle: " + PRINCIPLE + "\n"
SHELL_HEAD = "#!/bin/bash\n\n"
BROKEN_TEARDOWN = 'teardown() {\n  [ "$x" = - ]\n  echo done\n}\n\n'
BROKEN_TAIL = '\nteardown() {\n  [ "$x" = - ]\n  echo done\n}\n'


def echo_lines(count, indent="  "):
    return "".join(f'{indent}echo "line {number}"\n' for number in range(1, count + 1))


def setup_block(count, closed=True):
    return "setup() {\n" + echo_lines(count) + ("}\n" if closed else "")


class ColonsInMarkdownHeadings(InMemoryHookCase):
    def verdict(self, content):
        return self.write(self.work / PLAN, content)

    def test_atx_heading_with_a_colon_passes(self):
        self.assertEqual(self.verdict("# Plan\n\n## Phase 1: Auth\n\nShort line here.\n"), PASSED)

    def test_setext_heading_underlined_with_equals_signs_passes(self):
        self.assertEqual(self.verdict("Phase 1: Auth\n=============\n\nShort line here.\n"), PASSED)

    def test_setext_heading_underlined_with_hyphens_passes(self):
        self.assertEqual(self.verdict("# Plan\n\nPhase 1: Auth\n-------------\n\nShort line here.\n"), PASSED)

    def test_colons_inside_prose_sentences_pass_in_a_markdown_document(self):
        self.assertEqual(self.verdict("# Plan\n\n" + STEPS + "\n"), PASSED)

    def test_colons_inside_a_list_item_pass_in_a_markdown_document(self):
        self.assertEqual(self.verdict("# Plan\n\n- " + STEPS + "\n"), PASSED)

    def test_colons_inside_a_table_cell_pass_in_a_markdown_document(self):
        table = "| Step | Note |\n|---|---|\n| one | " + STEPS + " |\n"
        self.assertEqual(self.verdict("# Plan\n\n" + table), PASSED)


class ColonsBesideHeadingsInReplies(InMemoryHookCase):
    def reply_colon_found(self, message):
        self.respond("Stop", {"cwd": str(self.work), "last_assistant_message": message})
        return "prose_colon" in self.ledger_rules(REPLY)

    def test_setext_heading_with_a_colon_is_left_out_of_the_reply_colon_count(self):
        self.assertFalse(self.reply_colon_found(STEP + "\n---\n\n" + THREE_COLONS))

    def test_list_item_above_a_hyphen_line_is_not_read_as_a_heading(self):
        self.assertTrue(self.reply_colon_found("- " + STEP + "\n---\n" + THREE_COLONS))

    def test_sentence_above_a_blank_line_and_a_thematic_break_is_still_counted(self):
        self.assertTrue(self.reply_colon_found(STEP + "\n\n---\n" + THREE_COLONS))


class ProgramOutputFiles(FakeRepoCase):
    projects = {"repo": Repo({**README, ".gitignore": "logs/\n"})}

    def verdict(self, relative, content):
        return self.write("repo", relative, content)

    def plugin_cache(self):
        return str(self.home / ".adw-lite" / "cache" / "plugin" / "adw-light")

    def release_line(self):
        return "copied release to " + str(self.root / "stage" / "release") + "\n"

    def test_deploy_log_line_holding_a_temp_folder_path_in_an_ignored_folder_passes(self):
        self.assertEqual(self.verdict("logs/deploy.log", self.release_line()), PASSED)

    def test_deploy_log_line_holding_a_temp_folder_path_in_a_folder_that_is_not_ignored_is_noted(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.verdict("reports/deploy.log", self.release_line()), PATH_NOTED)

    def test_deploy_log_line_holding_a_plugin_cache_path_in_an_ignored_folder_passes(self):
        self.assertEqual(self.verdict("logs/deploy.log", "installed " + self.plugin_cache() + "\n"), PASSED)

    def test_deploy_log_line_holding_a_plugin_cache_path_in_a_folder_that_is_not_ignored_is_refused(self):
        self.assertEqual(self.verdict("reports/deploy.log", "installed " + self.plugin_cache() + "\n"), HARD_PATH)

    def test_out_file_line_holding_a_plugin_cache_path_in_an_ignored_folder_passes(self):
        self.assertEqual(self.verdict("logs/deploy.out", "installed " + self.plugin_cache() + "\n"), PASSED)

    def test_out_file_line_holding_a_plugin_cache_path_in_a_folder_that_is_not_ignored_is_refused(self):
        self.assertEqual(self.verdict("reports/deploy.out", "installed " + self.plugin_cache() + "\n"), HARD_PATH)

    def test_log_file_is_allowed_and_keeps_its_prose_findings(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.verdict("logs/deploy.log", "The cache was rebuilt, cleaned and saved.\n"), PASSIVE_NOTED)


class PluginCachePathInSource(InMemoryHookCase):
    def test_python_string_holding_a_plugin_cache_path_is_still_refused(self):
        cache = self.home / ".adw-lite" / "cache" / "plugin" / "adw-light"
        source = "TARGET = " + repr(str(cache)) + "\n"
        self.assertEqual(self.write(self.work / "src/deploy.py", source), HARD_PATH)


class ShellFunctionsBesideParseErrors(InMemoryHookCase):
    language = True

    def test_long_bats_function_after_a_function_holding_a_parse_error_is_refused(self):
        content = BATS_HEAD + BROKEN_TEARDOWN + setup_block(85)
        self.assertEqual(self.exact_verdict(SOURCE_BATS_PATH, content), blocked(("function_too_long", 8)))

    def test_long_shell_function_after_a_function_holding_a_parse_error_is_refused(self):
        content = SHELL_HEAD + BROKEN_TEARDOWN + setup_block(85)
        self.assertEqual(self.exact_verdict(SHELL_PATH, content), blocked(("function_too_long", 8)))

    def test_long_bats_function_running_to_the_end_of_the_file_after_a_parse_error_is_refused(self):
        content = BATS_HEAD + BROKEN_TEARDOWN + setup_block(85, closed=False)
        self.assertEqual(self.exact_verdict(SOURCE_BATS_PATH, content), blocked(("function_too_long", 8)))

    def test_brace_at_column_zero_inside_a_quoted_string_does_not_end_a_long_function(self):
        body = '  msg="first\n}\nlast"\n  [ "$x" = - ]\n' + echo_lines(85) + "}\n"
        content = BATS_HEAD + "setup() {\n" + body
        self.assertEqual(self.exact_verdict(SOURCE_BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_short_function_before_a_parse_error_elsewhere_passes(self):
        content = BATS_HEAD + setup_block(5) + BROKEN_TAIL + echo_lines(90, "")
        self.assertEqual(self.exact_verdict(SOURCE_BATS_PATH, content), ALLOWED)

    def test_long_function_before_a_parse_error_elsewhere_is_still_refused(self):
        content = BATS_HEAD + setup_block(85) + BROKEN_TAIL
        self.assertEqual(self.exact_verdict(SOURCE_BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_short_function_running_to_the_end_of_the_file_passes(self):
        content = BATS_HEAD + setup_block(5, closed=False)
        self.assertEqual(self.exact_verdict(SOURCE_BATS_PATH, content), ALLOWED)


def bats_block(count, closed=True):
    return '@test "runs the steps" {\n' + echo_lines(count) + ("}\n" if closed else "")


def wrapped_bats_block(opening, closing):
    return '@test "runs the steps" {\n' + opening + "}\n" + closing + echo_lines(85) + "}\n"


class BatsTestBlockLength(InMemoryHookCase):
    language = True

    def test_test_block_of_86_lines_in_a_clean_bats_file_is_refused(self):
        content = CITED_BATS_HEAD + bats_block(84)
        self.assertEqual(self.exact_verdict(BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_test_block_of_20_lines_in_a_clean_bats_file_passes(self):
        content = CITED_BATS_HEAD + bats_block(18)
        self.assertEqual(self.exact_verdict(BATS_PATH, content), ALLOWED)

    def test_long_test_block_left_open_at_the_end_of_the_file_is_refused(self):
        content = CITED_BATS_HEAD + bats_block(85, closed=False)
        self.assertEqual(self.exact_verdict(BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_brace_at_column_zero_inside_a_heredoc_does_not_end_a_long_bats_block(self):
        content = CITED_BATS_HEAD + wrapped_bats_block("  cat <<EOF\n", "EOF\n")
        self.assertEqual(self.exact_verdict(BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_brace_at_column_zero_inside_a_multi_line_quote_does_not_end_a_long_bats_block(self):
        content = CITED_BATS_HEAD + wrapped_bats_block('  msg="first\n', 'last"\n')
        self.assertEqual(self.exact_verdict(BATS_PATH, content), blocked(("function_too_long", 3)))

    def test_long_function_in_a_clean_shell_file_is_still_refused(self):
        content = SHELL_HEAD + setup_block(85)
        self.assertEqual(self.exact_verdict(SHELL_PATH, content), blocked(("function_too_long", 3)))

    def test_short_function_in_a_clean_shell_file_still_passes(self):
        content = SHELL_HEAD + setup_block(18)
        self.assertEqual(self.exact_verdict(SHELL_PATH, content), ALLOWED)


if __name__ == "__main__":
    unittest.main()
