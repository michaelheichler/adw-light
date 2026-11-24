from false_positive_home import DENY, PASSED, FalsePositiveHome
from t19_language_home import LIBRARY_NEEDED, LIBRARY_READY, blocked, exact_verdict
import unittest


TEST_WRITER = "adw-test-writer"
TEMP = "/" + "tmp"
PASSIVE = (DENY, ["passive_voice"])
PATH_FOUND = (DENY, ["hard_coded_path"])
TAP_DISGUISE = "1..1\nok 1 We utilize this for the parser.\n# step one - then two; three\n  note: done\n"
DISGUISE_FINDINGS = (DENY, ["prose_colon", "prose_semicolon", "spaced_hyphen", "utilize"])
SHELL_PATH = "tools/run.sh"
SHELL_HEAD = "#!/bin/bash\n\n"
PARSE_ERROR_LINE = '  [ "$x" = - ]\n'
TOO_LONG = blocked(("function_too_long", 3))
CACHE_SCRIPT = "scripts/cache.sh"
CACHE_TEST = "tests/test_cache.py"


def echo_lines(count):
    return "".join(f'  echo "line {number}"\n' for number in range(1, count + 1))


def long_function(opening):
    return SHELL_HEAD + "setup() {\n" + opening + PARSE_ERROR_LINE + echo_lines(85) + "}\n"


def cache_script(name):
    return "#!/bin/sh\ncache=" + TEMP + "/" + name + "$$\nmkdir -p \"$cache\"\n"


def escaping_test(tail):
    literal = 'f"' + TEMP + "/adwprobe-" + tail + '"'
    return "import os\n\n\ndef test_cache_folder():\n    assert os.path.basename(" + literal + ")\n"


class OutputFolderHome(FalsePositiveHome):
    def ignored_write(self, folder, relative, content):
        repo = self.committed_repo({"README.md": "Short.\n", ".gitignore": folder + "/\n"})
        return self.write(repo, repo / folder / relative, content)


class TapDisguise(OutputFolderHome):
    def test_tap_shaped_text_with_an_indented_label_line_in_an_ignored_folder_is_scanned(self):
        self.assertEqual(self.ignored_write("out", "results.txt", TAP_DISGUISE), DISGUISE_FINDINGS)


class CodeInsideIgnoredLogs(OutputFolderHome):
    def install_line(self):
        return "cp -R build " + str(self.home / ".adw-lite" / "cache" / "plugin" / "adw-light") + "\n"

    def test_ignored_log_that_starts_with_a_shebang_keeps_its_path_finding(self):
        content = "#!/bin/sh\n" + self.install_line()
        self.assertEqual(self.ignored_write("logs", "helper.log", content), PATH_FOUND)

    def test_ignored_log_that_defines_a_shell_function_keeps_its_path_finding(self):
        content = "helper() {\n  " + self.install_line() + "}\n"
        self.assertEqual(self.ignored_write("logs", "helper.log", content), PATH_FOUND)

    def test_ignored_log_of_plain_program_output_holding_the_same_path_passes(self):
        content = "step 1 done\n" + self.install_line()
        self.assertEqual(self.ignored_write("logs", "helper.log", content), PASSED)


class PassiveVoiceBesideCommandNames(FalsePositiveHome):
    def verdict(self, sentence):
        repo = self.committed_repo({"README.md": "Short.\n"})
        return self.write(repo, repo / "docs/notes.md", "# Notes\n\n" + sentence + "\n")

    def test_passive_that_opens_a_list_of_plain_clauses_is_refused(self):
        self.assertEqual(self.verdict("The budget was cut, staff left and morale fell."), PASSIVE)

    def test_passive_followed_by_a_hyphen_glued_to_a_word_is_refused(self):
        self.assertEqual(self.verdict("The report was written -quickly by the team."), PASSIVE)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class LongShellFunctionsWithMisleadingBraces(unittest.TestCase):
    def test_inner_group_closed_at_column_zero_does_not_end_a_long_function(self):
        content = long_function("  {\n    echo grouped\n}\n")
        self.assertEqual(exact_verdict(self, SHELL_PATH, content), TOO_LONG)

    def test_backslash_quoted_heredoc_holding_a_brace_line_does_not_end_a_long_function(self):
        content = long_function("  cat <<\\EOF\n}\nEOF\n")
        self.assertEqual(exact_verdict(self, SHELL_PATH, content), TOO_LONG)

    def test_ansi_quoted_escaped_apostrophe_does_not_unbalance_quotes_in_a_long_function(self):
        content = long_function("  x=$'it\\'s'\n  msg='first\n}\nlast'\n")
        self.assertEqual(exact_verdict(self, SHELL_PATH, content), TOO_LONG)


class TempLiteralsThatEscapeATrackedPrefix(FalsePositiveHome):
    def writer_write(self, content):
        repo = self.committed_repo({CACHE_SCRIPT: cache_script("adwprobe-")})
        tool_input = {"file_path": str(repo / CACHE_TEST), "content": content}
        payload = {"cwd": str(repo), "agent_type": TEST_WRITER, "tool_name": "Write", "tool_input": tool_input}
        return self.hook("PreToolUse", payload)

    def test_temp_literal_that_climbs_out_with_dot_dot_keeps_its_path_finding(self):
        self.assertEqual(self.writer_write(escaping_test("$x/../../etc")), PATH_FOUND)

    def test_temp_literal_that_names_the_home_variable_keeps_its_path_finding(self):
        self.assertEqual(self.writer_write(escaping_test("$HOME/{os.getpid()}")), PATH_FOUND)


if __name__ == "__main__":
    unittest.main()
