import unittest

from test_t15_comment_shapes import MARKER, SHAPE, CommentShapeGate, js_function, js_marker, source


def shell_script(comment):
    return source("#!/usr/bin/env bash", comment, 'echo $1')


def ruby_script(comment):
    return source(comment, "class Probe", "  def size", "    1", "  end", "end")


def why_beside_return(reference):
    return js_function("return a; // Use the name as the title if the properties did not include one (" + reference + ")")


class TrackerLookalikes(CommentShapeGate):
    def test_lowercase_x86_64_in_place_of_a_tracker_reference_gets_a_shape_row_on_its_line(self):
        outcome = self.write("arch.js", why_beside_return("x86-64"))
        self.assert_observe_report(outcome, [(SHAPE, 2)])

    def test_uppercase_x86_64_in_place_of_a_tracker_reference_gets_a_shape_row_on_its_line(self):
        outcome = self.write("archUpper.js", why_beside_return("X86-64"))
        self.assert_observe_report(outcome, [(SHAPE, 2)])

    def test_python_3_in_place_of_a_tracker_reference_gets_a_shape_row_on_its_line(self):
        outcome = self.write("python3.js", why_beside_return("python-3"))
        self.assert_observe_report(outcome, [(SHAPE, 2)])

    def test_covid_19_in_place_of_a_tracker_reference_gets_a_shape_row_on_its_line(self):
        outcome = self.write("covid.js", why_beside_return("covid-19"))
        self.assert_observe_report(outcome, [(SHAPE, 2)])


class RealTrackerReferences(CommentShapeGate):
    def test_jira_key_reference_is_allowed_without_any_report(self):
        outcome = self.write("jira.js", why_beside_return("ABC-123"))
        self.assert_clean_allow(outcome)

    def test_lowercase_issue_number_is_allowed_without_any_report(self):
        outcome = self.write("issueLower.js", why_beside_return("issue 42"))
        self.assert_clean_allow(outcome)

    def test_uppercase_issue_number_is_allowed_without_any_report(self):
        outcome = self.write("issueUpper.js", why_beside_return("ISSUE 42"))
        self.assert_clean_allow(outcome)


class ToolDirectives(CommentShapeGate):
    def test_flow_pragma_gets_no_shape_row(self):
        outcome = self.write("flow.js", js_marker("// @flow"))
        self.assert_no_shape_rows(outcome)

    def test_strict_flow_pragma_in_a_block_comment_gets_no_shape_row(self):
        outcome = self.write("flowStrict.js", js_marker("/* @flow strict */"))
        self.assert_no_shape_rows(outcome)

    def test_shellcheck_disable_gets_no_shape_row(self):
        outcome = self.write("quoted.sh", shell_script("# shellcheck disable=SC2086"))
        self.assert_no_shape_rows(outcome)

    def test_ruby_frozen_string_literal_gets_no_shape_row(self):
        outcome = self.write("frozen.rb", ruby_script("# frozen_string_literal: true"))
        self.assert_no_shape_rows(outcome)

    def test_sorbet_typed_sigil_gets_no_shape_row(self):
        outcome = self.write("typed.rb", ruby_script("# typed: strict"))
        self.assert_no_shape_rows(outcome)

    def test_leading_ts_expect_error_with_a_reason_gets_no_shape_row(self):
        outcome = self.write("expect.ts", source(
            "// @ts-expect-error the vendor typings omit the retry field",
            "export const a = vendor.retry;",
        ))
        self.assert_no_shape_rows(outcome)

    def test_inline_ts_expect_error_with_a_reason_gets_no_shape_row(self):
        outcome = self.write("expectInline.ts", source(
            "export const a = vendor.retry; // @ts-expect-error the vendor typings omit the retry field",
        ))
        self.assert_no_shape_rows(outcome)



class WorkMarkerPurposeClauses(CommentShapeGate):
    def test_to_before_a_place_is_not_a_purpose_and_gets_a_deferred_work_row(self):
        outcome = self.write("moveUtils.js", js_marker("// TODO: Move config to utils"))
        self.assert_observe_report(outcome, [(MARKER, 1)])

    def test_to_before_an_article_and_a_place_is_not_a_purpose_and_gets_a_deferred_work_row(self):
        outcome = self.write("moveModule.js", js_marker("// TODO: Move config to the utils module"))
        self.assert_observe_report(outcome, [(MARKER, 1)])

    def test_trailing_so_with_no_clause_gets_a_deferred_work_row(self):
        outcome = self.write("soBare.js", js_marker("// TODO: Cache tokens so"))
        self.assert_observe_report(outcome, [(MARKER, 1)])

    def test_so_followed_by_one_word_gets_a_deferred_work_row(self):
        outcome = self.write("soOneWord.js", js_marker("// TODO: Cache tokens so reruns"))
        self.assert_observe_report(outcome, [(MARKER, 1)])

    def test_later_to_before_a_purpose_verb_counts_after_a_place_is_allowed_without_any_report(self):
        outcome = self.write("moveShare.js", js_marker("// TODO: Move config to utils to share it with the CLI"))
        self.assert_clean_allow(outcome)

    def test_so_followed_by_a_clause_is_allowed_without_any_report(self):
        outcome = self.write("soClause.js", js_marker("// TODO: Cache tokens so reruns skip the fetch"))
        self.assert_clean_allow(outcome)

    def test_in_order_to_before_a_purpose_verb_is_allowed_without_any_report(self):
        outcome = self.write("inOrder.js", js_marker("// TODO: Retry uploads in order to survive flaky networks"))
        self.assert_clean_allow(outcome)


if __name__ == "__main__":
    unittest.main()
