import unittest

from t19_language_home import ALLOWED, LIBRARY_NEEDED, LIBRARY_READY, blocked, exact_verdict, fallback_verdict


GO_PATH = "src/long.go"
GO_HEADER = "func Long() int {"
GO_HEADER_LINE = 3
FUNCTION_CAP = 80


def go_function(total_lines):
    body = "".join(f"\tx{index} := {index}\n" for index in range(total_lines - 3))
    return "package main\n\n" + GO_HEADER + "\n" + body + "\treturn 0\n}\n"


LONG_GO = go_function(183)
HALF_WRITTEN_JS = "import { from 'x';\nfunction long() {\n" + "".join(
    f"  let x{index} = {index};\n" for index in range(90)) + "  return 0;\n}\n"
TOO_LONG = ("function_too_long", GO_HEADER_LINE)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class ExactLayerMeasuresFunctions(unittest.TestCase):
    def test_long_go_function_is_blocked_at_its_header_line(self):
        self.assertEqual(exact_verdict(self, GO_PATH, LONG_GO), blocked(TOO_LONG))

    def test_half_written_go_header_still_reports_the_long_function(self):
        broken = LONG_GO.replace(GO_HEADER, "func Long( int {")
        self.assertEqual(exact_verdict(self, GO_PATH, broken), blocked(TOO_LONG))

    def test_half_written_javascript_file_keeps_the_lexer_long_function(self):
        self.assertEqual(exact_verdict(self, "src/long.js", HALF_WRITTEN_JS), blocked(("function_too_long", 2)))

    def test_go_function_at_the_cap_passes(self):
        self.assertEqual(exact_verdict(self, GO_PATH, go_function(FUNCTION_CAP)), ALLOWED)

    def test_go_function_one_line_over_the_cap_is_blocked(self):
        self.assertEqual(exact_verdict(self, GO_PATH, go_function(FUNCTION_CAP + 1)), blocked(TOO_LONG))


class FallbackKeepsOldGoBehavior(unittest.TestCase):
    def test_long_go_function_passes_without_the_library(self):
        self.assertEqual(fallback_verdict(self, GO_PATH, LONG_GO), ALLOWED)


if __name__ == "__main__":
    unittest.main()
