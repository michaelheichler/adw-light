import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
NO_REPOSITORY_VIEW = Path(__file__).resolve().parent / "fixtures" / "no_repository_view.json"
SHAPE = "comment_shape"
MARKER = "deferred_work_comment"
OBSERVED = "would_block"
BLOCKED = "block"
DENY = "deny"


class Outcome(NamedTuple):
    exit_code: int
    response: dict
    reported: list
    ledger: Counter

    @property
    def decision(self):
        return self.response.get("hookSpecificOutput", {}).get("permissionDecision")

    def shape_rows(self):
        return sorted(row for row in self.reported if row[0] in (SHAPE, MARKER))

    def observed(self, rule):
        return self.ledger[(rule, OBSERVED)]

    def blocked(self, rule):
        return self.ledger[(rule, BLOCKED)]


def source(*lines):
    return "\n".join(lines) + "\n"


def java_method(*body):
    return source("class Probe {", "  void probe(Object value) {", *("    " + line for line in body), "  }", "}")


def js_function(*body):
    return source("function probe(a) {", *("  " + line for line in body), "}")


def js_marker(comment):
    return source(comment, "function probe() {", "  return 1;", "}")


def javadoc_bitmap(summary):
    return source(
        "class Probe {",
        "  /** " + summary + " */",
        "  public static Bitmap drawableToBitmap(Drawable drawable) {",
        "    return null;",
        "  }",
        "}",
    )


def rfc_requirement(keyword):
    return java_method(
        r"// http://tools.ietf.org/html/rfc4180 says CSV lines " + keyword + r" be terminated by CRLF, hence the \r\n.",
        r'csvStringBuilder.append("\r\n");',
    )


class CommentShapeGate(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.repo.mkdir()
        self.home.mkdir()

    def write(self, name, content):
        payload = {
            "cwd": str(self.repo), "session_id": "shape", "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / name), "content": content},
        }
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state), "--git-view", str(NO_REPOSITORY_VIEW)],
            input=json.dumps(payload), text=True, capture_output=True, check=False,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        response = json.loads(call.stdout) if call.stdout.strip() else {}
        return Outcome(call.returncode, response, self.reported(), self.ledger())

    def reported(self):
        reports = sorted((self.state / "reports").glob("*.json"))
        return [(row["rule"], row["line"]) for report in reports for row in json.loads(report.read_text(encoding="utf-8"))]

    def ledger(self):
        ledger = self.state / "ledger" / "ledger.jsonl"
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()] if ledger.exists() else []
        return Counter((row.get("rule"), row.get("outcome")) for row in rows if row.get("rule"))

    def assert_no_shape_rows(self, outcome):
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual((outcome.observed(SHAPE), outcome.observed(MARKER)), (0, 0))
        self.assertEqual((outcome.blocked(SHAPE), outcome.blocked(MARKER)), (0, 0))

    def assert_observed_only(self, outcome, rule, count):
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual((outcome.observed(rule), outcome.blocked(rule)), (count, 0))

    def assert_clean_allow(self, outcome):
        self.assertEqual(outcome.exit_code, 0)
        self.assertIsNone(outcome.decision)
        self.assertEqual(outcome.reported, [])
        self.assertNotIn("systemMessage", outcome.response)

    def assert_observe_report(self, outcome, rows):
        self.assertEqual(outcome.exit_code, 0)
        self.assertNotEqual(outcome.decision, DENY)
        self.assertIn("systemMessage", outcome.response)
        self.assertEqual(outcome.shape_rows(), rows)


class GoodGoldExamples(CommentShapeGate):
    def test_rule5_note_beside_a_double_null_check_is_allowed_without_any_report(self):
        outcome = self.write("Rule5.java", java_method(
            "// Note that JSONTokener.nextValue() may return",
            "// a value equals() to null.",
            "if (value == null || value.equals(null)) {",
            "  return;",
            "}",
        ))
        self.assert_clean_allow(outcome)

    def test_rule6_javadoc_attribution_with_a_link_is_allowed_without_any_report(self):
        outcome = self.write("Rule6.java", source(
            "class Probe {",
            "  /** Converts a Drawable to Bitmap. via https://stackoverflow.com/a/46018816/2219998. */",
            "  public static Bitmap drawableToBitmap(Drawable drawable) {",
            "    return null;",
            "  }",
            "}",
        ))
        self.assert_clean_allow(outcome)

    def test_rule6_thanks_with_a_link_wrapped_over_two_lines_is_allowed_without_any_report(self):
        outcome = self.write("Rule6Thanks.java", source(
            "class Probe {",
            "  // Many thanks to Chris Veness at http://www.movable-type.co.uk/scripts/latlong.html",
            "  // for a great reference and examples.",
            "  double beam(double lat1, double lng1) {",
            "    return lat1;",
            "  }",
            "}",
        ))
        self.assert_clean_allow(outcome)

    def test_rule8_issue_reference_above_the_fix_is_allowed_without_any_report(self):
        outcome = self.write("Rule8.java", java_method(
            "// Use the name as the title if the properties did not include one (issue #1425)",
            "title = name;",
        ))
        self.assert_clean_allow(outcome)

    def test_rule8_issue_reference_beside_the_fix_is_allowed_without_any_report(self):
        outcome = self.write("Rule8Inline.java", java_method(
            "title = name; // Use the name as the title if the properties did not include one (issue #1425)",
        ))
        self.assert_clean_allow(outcome)

    def test_rule7_rfc_requirement_wrapped_over_two_lines_is_allowed_without_any_report(self):
        outcome = self.write("Rule7Wrapped.java", java_method(
            "// http://tools.ietf.org/html/rfc4180 suggests that CSV lines",
            r"// SHOULD be terminated by CRLF, hence the \r\n.",
            r'csvStringBuilder.append("\r\n");',
        ))
        self.assert_clean_allow(outcome)

    def test_rule7_rfc_uppercase_requirement_with_hence_gets_no_shape_row(self):
        outcome = self.write("Rule7.java", rfc_requirement("MUST"))
        self.assert_no_shape_rows(outcome)

    def test_owner_three_part_why_above_the_catch_is_allowed_without_any_report(self):
        outcome = self.write("drop.js", source(
            "function onDrop(event) {",
            "  try {",
            "    return handle(event);",
            "  // the catch case exists, because mouse event drops outside window",
            "  } catch (error) {",
            "    return null;",
            "  }",
            "}",
        ))
        self.assert_clean_allow(outcome)

    def test_owner_three_part_why_beside_code_is_allowed_without_any_report(self):
        outcome = self.write("dropInline.js", js_function(
            "return handle(a); // the catch case exists, because mouse event drops outside window",
        ))
        self.assert_clean_allow(outcome)


class BadGoldExamples(CommentShapeGate):
    def test_rule1_restating_comments_are_reported_on_each_line_without_a_deny(self):
        outcome = self.write("Rule1.java", java_method(
            "if (i < 9) {",
            "  i = i + 1;         // Add one to i",
            "} // if",
        ))
        self.assert_observe_report(outcome, [(SHAPE, 4), (SHAPE, 5)])
        self.assert_observed_only(outcome, SHAPE, 2)

    def test_rule1_token_by_token_comments_each_get_an_observe_row(self):
        outcome = self.write("Rule1Tokens.java", java_method(
            "// create a for loop // <-- comment",
            "for // start for loop",
            "(   // round bracket",
            "int // type for declaration",
            "i = 0; i < 9; i++) {",
            "}",
        ))
        self.assert_observed_only(outcome, SHAPE, 4)

    def test_rule2_comments_that_hide_a_poor_name_each_get_an_observe_row(self):
        outcome = self.write("Rule2.java", java_method(
            "Node n; // best child node candidate",
            "for (Node child: children) {",
            "  // update n if the current state is better",
            "  if (n == null || utility(child) > utility(n)) {",
            "    n = child;",
            "  }",
            "}",
        ))
        self.assert_observed_only(outcome, SHAPE, 2)

    def test_rule3_unexplained_warning_gets_an_observe_row(self):
        outcome = self.write("rule3.c", source(
            "int probe(void) {",
            "  // You are not expected to understand this",
            "  return swtch();",
            "}",
        ))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_rule6_attribution_without_a_link_gets_one_observe_row(self):
        outcome = self.write("Rule6Bad.java", java_method(
            "// Magical formula taken from a stackoverflow post, reputedly related to",
            "// human vision perception.",
            "return (int) (0.3 * r + 0.59 * g + 0.11 * b);",
        ))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_commkit_comment_that_restates_the_signature_gets_an_observe_row(self):
        outcome = self.write("adder.js", source(
            "// Add two numbers (x and y) together.",
            "function adder(x, y) {",
            "  return x + y;",
            "}",
        ))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_commkit_vague_todo_is_reported_as_deferred_work_on_its_line(self):
        outcome = self.write("adderTodo.js", js_marker("// TODO: make it work."))
        self.assert_observe_report(outcome, [(MARKER, 1)])


class WorkMarkerAcceptance(CommentShapeGate):
    def test_todo_fix_this_is_reported_as_deferred_work_on_its_line(self):
        outcome = self.write("fix.js", js_marker("// TODO: fix this"))
        self.assert_observe_report(outcome, [(MARKER, 1)])
        self.assert_observed_only(outcome, MARKER, 1)

    def test_todo_with_verb_object_and_purpose_is_allowed_without_any_report(self):
        outcome = self.write("export.js", js_marker("// TODO: Add CSV export to the report view to let users share results"))
        self.assert_clean_allow(outcome)

    def test_shebang_and_spdx_header_are_allowed_without_any_report(self):
        outcome = self.write("run.sh", source("#!/usr/bin/env bash", "# SPDX-License-Identifier: MIT", "set -eu", "echo ok"))
        self.assert_clean_allow(outcome)


class WorkMarkerShapes(CommentShapeGate):
    def test_xxx_and_done_tags_on_adjacent_lines_each_get_a_deferred_work_row(self):
        outcome = self.write("banned.js", source(
            "// XXX: Remove the cache so stale rows stop showing",
            "// DONE: Add the export so users share results",
            "function probe() {",
            "  return 1;",
            "}",
        ))
        self.assert_observed_only(outcome, MARKER, 2)

    def test_bug_and_optimize_tags_each_get_a_deferred_work_row(self):
        outcome = self.write("tracker.js", source(
            "// BUG: Fix the parser so blank rows parse",
            "// OPTIMIZE: Cache the rows so reports load fast",
            "function probe() {",
            "  return 1;",
            "}",
        ))
        self.assert_observed_only(outcome, MARKER, 2)

    def test_marker_that_holds_only_a_link_is_reported(self):
        outcome = self.write("link.js", js_marker("// TODO: https://github.com/org/repo/issues/12"))
        self.assert_observe_report(outcome, [(MARKER, 1)])

    def test_lowercase_tag_is_reported(self):
        outcome = self.write("lower.js", js_marker("// todo: Add CSV export to the report view to let users share results"))
        self.assert_observe_report(outcome, [(MARKER, 1)])

    def test_tag_without_a_colon_is_reported(self):
        outcome = self.write("colon.js", js_marker("// TODO Add CSV export to the report view to let users share results"))
        self.assert_observe_report(outcome, [(MARKER, 1)])

    def test_vague_object_is_reported(self):
        outcome = self.write("vague.js", js_marker("// TODO: Fix stuff so builds pass"))
        self.assert_observe_report(outcome, [(MARKER, 1)])

    def test_fixme_that_names_no_defect_is_reported(self):
        outcome = self.write("fixme.js", js_marker("// FIXME: Handle empty rows so the report looks tidy"))
        self.assert_observe_report(outcome, [(MARKER, 1)])

    def test_fixme_that_names_the_defect_is_allowed_without_any_report(self):
        outcome = self.write("fixmeGood.js", js_marker("// FIXME: Handle empty rows so the parser stops crashes on blank CSV lines"))
        self.assert_clean_allow(outcome)

    def test_hack_without_a_removal_condition_is_reported(self):
        outcome = self.write("hack.js", js_marker("// HACK: Pin the parser version to keep builds green"))
        self.assert_observe_report(outcome, [(MARKER, 1)])

    def test_hack_with_a_removal_condition_is_allowed_without_any_report(self):
        outcome = self.write("hackGood.js", js_marker("// HACK: Pin the parser version to keep builds green until upstream ships 0.27"))
        self.assert_clean_allow(outcome)


class CommentShapeLimits(CommentShapeGate):
    def test_note_beside_code_that_does_not_look_redundant_gets_an_observe_row(self):
        outcome = self.write("note.js", js_function("// Note that the cache warms up on the first request.", "return a + 1;"))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_why_comment_past_twenty_five_words_is_reported_on_its_line(self):
        reason = " ".join((
            "the retry loop exists, because the vendor gateway drops the first request after every idle period",
            "of more than five full minutes on most weekdays in the busy season",
        ))
        outcome = self.write("long.js", js_function("return a + 1; // " + reason))
        self.assert_observe_report(outcome, [(SHAPE, 2)])

    def test_why_comment_of_two_sentences_is_reported_on_its_line(self):
        outcome = self.write("twice.js", js_function(
            "return a + 1; // the cache stays warm, because the vendor API bills per cold request. The bill doubles in June.",
        ))
        self.assert_observe_report(outcome, [(SHAPE, 2)])

    def test_python_inline_comment_that_restates_the_code_gets_an_observe_row(self):
        outcome = self.write("calc.py", source("y = 2  # add two"))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_typescript_checker_directive_is_allowed_without_any_report(self):
        outcome = self.write("loose.ts", source("// @ts-nocheck", "export const a = 1;"))
        self.assert_clean_allow(outcome)

    def test_python_type_ignore_directive_is_allowed_without_any_report(self):
        outcome = self.write("typed.py", source("import os  # type: ignore"))
        self.assert_clean_allow(outcome)

    def test_why_shaped_comment_that_repeats_the_adjacent_code_is_reported(self):
        outcome = self.write("echo.js", js_function(
            "cache.warm(vendor.bill(flush)); // cache warm vendor bill flush because gateway drops", "return a;",
        ))
        self.assert_observe_report(outcome, [(SHAPE, 2)])

    def test_inline_linter_directive_is_allowed_without_any_report(self):
        outcome = self.write("lint.ts", source("export const a = 1; // eslint-disable-line no-console"))
        self.assert_clean_allow(outcome)


class AttributionAndStandardShapes(CommentShapeGate):
    def test_via_without_a_link_gets_an_observe_row(self):
        outcome = self.write("ViaBare.java", javadoc_bitmap("Converts a Drawable to Bitmap. via the web"))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_from_with_a_link_is_not_an_attribution_and_gets_an_observe_row(self):
        outcome = self.write("FromLink.java", javadoc_bitmap("Converts a Drawable to Bitmap. from https://example.com/a"))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_capitalised_via_with_a_link_gets_no_shape_row(self):
        outcome = self.write("ViaUpper.java", javadoc_bitmap(
            "Via https://stackoverflow.com/a/46018816/2219998, converts a Drawable to Bitmap.",
        ))
        self.assert_no_shape_rows(outcome)

    def test_adapted_from_a_link_gets_no_shape_row(self):
        outcome = self.write("Adapted.java", javadoc_bitmap("Converts a Drawable to Bitmap, adapted from https://example.com/a"))
        self.assert_no_shape_rows(outcome)

    def test_source_link_gets_no_shape_row(self):
        outcome = self.write("Sourced.java", javadoc_bitmap("Converts a Drawable to Bitmap, source https://example.com/a"))
        self.assert_no_shape_rows(outcome)

    def test_author_link_gets_no_shape_row(self):
        outcome = self.write("Author.java", javadoc_bitmap("Converts a Drawable to Bitmap, author https://example.com/a"))
        self.assert_no_shape_rows(outcome)

    def test_license_link_gets_no_shape_row(self):
        outcome = self.write("Licensed.java", javadoc_bitmap("Converts a Drawable to Bitmap under the license at https://example.com/a"))
        self.assert_no_shape_rows(outcome)

    def test_rfc_lowercase_must_with_hence_gets_an_observe_row(self):
        outcome = self.write("Rule7Must.java", rfc_requirement("must"))
        self.assert_observed_only(outcome, SHAPE, 1)

    def test_rfc_lowercase_should_with_hence_gets_an_observe_row(self):
        outcome = self.write("Rule7Should.java", rfc_requirement("should"))
        self.assert_observed_only(outcome, SHAPE, 1)
