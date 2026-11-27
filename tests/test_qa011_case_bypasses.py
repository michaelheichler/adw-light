import unittest

from commit_view_home import SCANNED, CommitViewHome, script


COMMIT_ARM = 'git commit -m "We utilize this."'
TOUCH_ARM = "touch notes.txt"


def dispatch(word, first="sync", second="commit"):
    return f"case {word} in\n  {first}) {TOUCH_ARM} ;;\n  {second}) {COMMIT_ARM} ;;\nesac\n"


ARGUMENT_DISPATCH = dispatch('"$1"')
VARIABLE_DISPATCH = dispatch('"$mode"')
NOCASE_SHOPT = "shopt -s nocase''match\n"
PRINTF_RENAME = "mode=sync\nprintf -v $'\\x6dode' %s commit\n"
UPPERCASE_DECLARE = "declare -u $'\\x6dode'\nmode=commit\n" + dispatch('"$mode"', "commit", "COMMIT")
ANSI_WORD = dispatch("$'\\x63ommit'")
ANSI_PATTERN = dispatch('"$1"', second="$'\\x63ommit'")
RUN = "bash ./s.sh"


class CaseArmHome(CommitViewHome):
    projects = {
        "nocase_shopt": {"s.sh": script(NOCASE_SHOPT + ARGUMENT_DISPATCH)},
        "argument_dispatch": {"s.sh": script(ARGUMENT_DISPATCH)},
        "bash_env": {"e.sh": "shopt -s nocasematch\n", "s.sh": script(ARGUMENT_DISPATCH)},
        "printf_rename": {"s.sh": script(PRINTF_RENAME + VARIABLE_DISPATCH)},
        "uppercase_declare": {"s.sh": script(UPPERCASE_DECLARE)},
        "ansi_word": {"s.sh": script(ANSI_WORD)},
        "ansi_pattern": {"s.sh": script(ANSI_PATTERN)},
        "sourced_reset": {"lib.sh": "set -- commit\n", "s.sh": script(". ./lib.sh\n" + ARGUMENT_DISPATCH)},
    }


class CommitInsideACaseArmIsFound(CaseArmHome):
    def test_shopt_nocasematch_spelled_with_empty_quotes_keeps_the_commit_scanned(self):
        self.assertEqual(SCANNED, self.message_finding("nocase_shopt", RUN + " COMMIT"))

    def test_bash_started_with_the_nocasematch_option_keeps_the_commit_scanned(self):
        self.assertEqual(SCANNED, self.message_finding("argument_dispatch", "bash -O nocasematch ./s.sh COMMIT"))

    def test_bash_env_file_that_may_set_options_keeps_the_commit_scanned(self):
        self.assertEqual(SCANNED, self.message_finding("bash_env", "BASH_ENV=./e.sh bash ./s.sh COMMIT"))

    def test_printf_into_an_ansi_quoted_variable_name_keeps_the_commit_scanned(self):
        self.assertEqual(SCANNED, self.message_finding("printf_rename", RUN))

    def test_uppercase_declare_on_an_ansi_quoted_variable_name_keeps_the_commit_scanned(self):
        self.assertEqual(SCANNED, self.message_finding("uppercase_declare", RUN))

    def test_ansi_quoted_case_word_keeps_the_commit_scanned(self):
        self.assertEqual(SCANNED, self.message_finding("ansi_word", RUN))

    def test_ansi_quoted_case_pattern_keeps_the_commit_scanned(self):
        self.assertEqual(SCANNED, self.message_finding("ansi_pattern", RUN + " commit"))

    def test_sourced_file_that_resets_the_arguments_keeps_the_commit_scanned(self):
        self.assertEqual(SCANNED, self.message_finding("sourced_reset", RUN + " sync"))


if __name__ == "__main__":
    unittest.main()
