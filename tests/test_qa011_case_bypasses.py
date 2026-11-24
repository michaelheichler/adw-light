from commit_gate_home import SCANNED, SHEBANG, CommitGateHome
import unittest


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


class CommitInsideACaseArmIsFound(CommitGateHome):
    def test_shopt_nocasematch_spelled_with_empty_quotes_keeps_the_commit_scanned(self):
        response = self.run_script(NOCASE_SHOPT + ARGUMENT_DISPATCH, "COMMIT")

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_bash_started_with_the_nocasematch_option_keeps_the_commit_scanned(self):
        self.place("s.sh", SHEBANG + ARGUMENT_DISPATCH)

        response = self.gate("bash -O nocasematch ./s.sh COMMIT")

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_bash_env_file_that_may_set_options_keeps_the_commit_scanned(self):
        self.place("e.sh", "shopt -s nocasematch\n")
        self.place("s.sh", SHEBANG + ARGUMENT_DISPATCH)

        response = self.gate("BASH_ENV=./e.sh bash ./s.sh COMMIT")

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_printf_into_an_ansi_quoted_variable_name_keeps_the_commit_scanned(self):
        response = self.run_script(PRINTF_RENAME + VARIABLE_DISPATCH)

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_uppercase_declare_on_an_ansi_quoted_variable_name_keeps_the_commit_scanned(self):
        response = self.run_script(UPPERCASE_DECLARE)

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_ansi_quoted_case_word_keeps_the_commit_scanned(self):
        response = self.run_script(ANSI_WORD)

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_ansi_quoted_case_pattern_keeps_the_commit_scanned(self):
        response = self.run_script(ANSI_PATTERN, "commit")

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_sourced_file_that_resets_the_arguments_keeps_the_commit_scanned(self):
        self.place("lib.sh", "set -- commit\n")

        response = self.run_script(". ./lib.sh\n" + ARGUMENT_DISPATCH, "sync")

        self.assertEqual(SCANNED, self.message_finding(response))


if __name__ == "__main__":
    unittest.main()
