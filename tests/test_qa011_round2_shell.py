from t19_language_home import ALLOWED, LIBRARY_NEEDED, LIBRARY_READY, blocked, exact_verdict
import unittest


SHELL_PATH = "tools/run.sh"
SHELL_HEAD = "#!/bin/bash\n\n"
PARSE_ERROR_LINE = '  [ "$x" = - ]\n'
TOO_LONG = blocked(("function_too_long", 3))


def echo_lines(count, indent="  "):
    return "".join(f'{indent}echo "line {number}"\n' for number in range(1, count + 1))


def long_function(opening):
    return SHELL_HEAD + "setup() {\n" + opening + PARSE_ERROR_LINE + echo_lines(85) + "}\n"


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class BraceWordsThatDoNotCloseALongFunction(unittest.TestCase):
    def test_echo_of_a_closing_brace_does_not_end_a_long_function(self):
        self.assertEqual(exact_verdict(self, SHELL_PATH, long_function("  echo }\n")), TOO_LONG)

    def test_closing_brace_after_a_hash_inside_a_word_does_not_end_a_long_function(self):
        self.assertEqual(exact_verdict(self, SHELL_PATH, long_function("  a#}\n")), TOO_LONG)

    def test_closing_brace_used_as_a_case_pattern_does_not_end_a_long_function(self):
        opening = "  case $x in\n    }) echo hi ;;\n  esac\n"
        self.assertEqual(exact_verdict(self, SHELL_PATH, long_function(opening)), TOO_LONG)

    def test_closing_brace_compared_inside_a_double_bracket_test_does_not_end_a_long_function(self):
        self.assertEqual(exact_verdict(self, SHELL_PATH, long_function("  [[ $x == } ]]\n")), TOO_LONG)

    def test_closing_brace_passed_as_a_printf_argument_does_not_end_a_long_function(self):
        self.assertEqual(exact_verdict(self, SHELL_PATH, long_function("  printf '%s\\n' x }\n")), TOO_LONG)

    def test_short_function_holding_an_escaped_opening_brace_passes(self):
        content = SHELL_HEAD + "setup() {\n" + PARSE_ERROR_LINE + "  echo \\{\n}\n" + echo_lines(85, "")
        self.assertEqual(exact_verdict(self, SHELL_PATH, content), ALLOWED)

    def test_short_function_closed_after_a_semicolon_passes(self):
        content = SHELL_HEAD + "setup() {\n" + PARSE_ERROR_LINE + "  echo done; }\n" + echo_lines(85, "")
        self.assertEqual(exact_verdict(self, SHELL_PATH, content), ALLOWED)


if __name__ == "__main__":
    unittest.main()
