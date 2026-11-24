import unittest

from test_t15_comment_shapes import DENY, MARKER, SHAPE, CommentShapeGate, java_method, js_function, js_marker, source


BLOCKED = "block"


def py_function(*body):
    return source("def probe(value):", *("    " + line for line in body))


def blocked_rules(outcome):
    return frozenset(rule for rule, result in outcome.ledger if result == BLOCKED)


class ShapeGate(CommentShapeGate):
    def assert_denied_by(self, outcome, rules):
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.decision, DENY)
        self.assertLessEqual(frozenset(rules), blocked_rules(outcome))


class PythonGoldExamples(ShapeGate):
    def test_rule5_note_above_a_double_none_check_is_allowed_without_any_report(self):
        outcome = self.write("rule5.py", py_function(
            "# Note that JSONTokener.nextValue() may return",
            "# a value equal to None.",
            "if value is None or value == None:",
            "    return None",
            "return value",
        ))
        self.assert_clean_allow(outcome)

    def test_rule6_thanks_with_a_link_wrapped_over_two_lines_is_allowed_without_any_report(self):
        outcome = self.write("rule6.py", source(
            "# Many thanks to Chris Veness at http://www.movable-type.co.uk/scripts/latlong.html",
            "# for a great reference and examples.",
            "def beam(lat1):",
            "    return lat1",
        ))
        self.assert_clean_allow(outcome)

    def test_rule7_rfc_requirement_wrapped_over_two_lines_is_allowed_without_any_report(self):
        outcome = self.write("rule7.py", py_function(
            "# http://tools.ietf.org/html/rfc4180 suggests that CSV lines",
            r"# SHOULD be terminated by CRLF, hence the \r\n.",
            r'value.append("\r\n")',
        ))
        self.assert_clean_allow(outcome)

    def test_rule8_issue_reference_above_the_fix_is_allowed_without_any_report(self):
        outcome = self.write("rule8.py", py_function(
            "# Use the name as the title if the properties did not include one (issue #1425)",
            "title = value",
            "return title",
        ))
        self.assert_clean_allow(outcome)

    def test_rule8_issue_reference_beside_the_fix_is_allowed_without_any_report(self):
        outcome = self.write("rule8Inline.py", py_function(
            "return value  # Use the name as the title if the properties did not include one (issue #1425)",
        ))
        self.assert_clean_allow(outcome)

    def test_owner_three_part_why_above_the_except_is_allowed_without_any_report(self):
        outcome = self.write("drop.py", py_function(
            "try:",
            "    return handle(value)",
            "# the catch case exists, because mouse event drops outside window",
            "except ValueError:",
            "    return None",
        ))
        self.assert_clean_allow(outcome)

    def test_owner_three_part_why_beside_code_is_allowed_without_any_report(self):
        outcome = self.write("dropInline.py", py_function(
            "return handle(value)  # the catch case exists, because mouse event drops outside window",
        ))
        self.assert_clean_allow(outcome)

    def test_shebang_and_spdx_header_are_allowed_without_any_report(self):
        outcome = self.write("run.py", source("#!/usr/bin/env python3", "# SPDX-License-Identifier: MIT", "print(1)"))
        self.assert_clean_allow(outcome)


class WrappingLimitsByShape(ShapeGate):
    def test_why_comment_wrapped_over_two_lines_is_denied_and_reported_as_a_shape_row(self):
        outcome = self.write("wrapWhy.js", js_function(
            "// the catch case exists, because mouse event drops",
            "// outside the browser window",
            "return a;",
        ))
        self.assert_denied_by(outcome, ("what_comment", "prose_comment_block"))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_tracker_comment_wrapped_over_two_lines_is_denied_and_reported_as_a_shape_row(self):
        outcome = self.write("WrapTracker.java", java_method(
            "// Use the name as the title if the properties did not",
            "// include one (issue #1425)",
            "title = name;",
        ))
        self.assert_denied_by(outcome, ("what_comment", "prose_comment_block"))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_note_comment_over_three_lines_above_a_null_check_is_denied(self):
        outcome = self.write("WrapNote.java", java_method(
            "// Note that JSONTokener.nextValue() may return",
            "// a value that equals() to null, so this",
            "// check covers both cases.",
            "if (value == null || value.equals(null)) {",
            "  return;",
            "}",
        ))
        self.assert_denied_by(outcome, ("what_comment", "prose_comment_block"))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_work_marker_wrapped_over_two_lines_is_denied_and_reported_as_deferred_work(self):
        outcome = self.write("wrapTodo.js", source(
            "// TODO: Add CSV export to the report view",
            "// to let users share results",
            "function probe() {",
            "  return 1;",
            "}",
        ))
        self.assert_denied_by(outcome, ("what_comment", "prose_comment_block"))
        self.assert_observed_only(outcome, MARKER, 1)


class OlderRulesStillBlock(ShapeGate):
    def test_javascript_comment_that_restates_the_next_line_is_denied_as_a_what_comment(self):
        outcome = self.write("increment.js", js_function("// increment i", "return a + 1;"))
        self.assert_denied_by(outcome, ("what_comment",))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_python_comment_that_restates_the_next_line_is_denied_as_added_and_what_comment(self):
        outcome = self.write("increment.py", py_function("# increment i", "return value + 1"))
        self.assert_denied_by(outcome, ("added_comment", "what_comment"))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_python_docstring_with_a_note_and_hence_is_still_denied_as_a_docstring(self):
        outcome = self.write("note.py", py_function('"""Note that i may be None, hence the check."""', "return value"))
        self.assert_denied_by(outcome, ("added_docstring", "what_docstring"))

    def test_multi_line_doc_block_holding_an_attribution_is_still_denied(self):
        outcome = self.write("DocBlock.java", source(
            "class Probe {",
            "  /**",
            "   * Many thanks to Chris Veness at http://www.movable-type.co.uk/scripts/latlong.html",
            "   * for a great reference and examples.",
            "   */",
            "  double beam(double lat1) {",
            "    return lat1;",
            "  }",
            "}",
        ))
        self.assert_denied_by(outcome, ("what_comment", "prose_comment_block"))

    def test_note_above_code_that_looks_ordinary_is_denied_and_reported(self):
        outcome = self.write("NotePrint.java", java_method(
            "// Note that the value gets printed twice here",
            "System.out.println(value);",
        ))
        self.assert_denied_by(outcome, ("what_comment",))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_one_line_work_marker_with_a_purpose_is_allowed_without_any_report(self):
        outcome = self.write("todo.js", js_marker("// TODO: Add CSV export to the report view to let users share results"))
        self.assert_clean_allow(outcome)


if __name__ == "__main__":
    unittest.main()
