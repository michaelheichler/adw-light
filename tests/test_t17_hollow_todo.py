import unittest

from t17_home import DENY, LIBRARY_NEEDED, LIBRARY_READY, TEST_WRITER, WELL_FORMED_TODO, source, verdict


ASSERT_WORD_TODO = "TODO: Add an assert on the export result to let users trust it"
EXPECT_CALL_TODO = "TODO: Add an expect(run()) check on the export to let users trust it"
JAVA_ASSERT_TODO = "TODO: Add an assertEquals(1, run()) check to let users trust the sum"


def python_test(marker, body="run()"):
    return source("def test_export():", "    # " + marker, "    " + body)


def js_test(marker, body="run();"):
    return source('it("exports", () => {', "  // " + marker, "  " + body, "});")


def js_block_test(marker, body="run();"):
    return source('it("exports", () => {', "  /* " + marker + " */", "  " + body, "});")


def java_test(marker, body="run();"):
    return source("class CalcTest {", "    @Test", "    void sums() {", "        // " + marker, "        " + body, "    }", "}")


def written_by_test_writer(case, relative, content, exact=False):
    return verdict(case, relative, content, agent=TEST_WRITER, exact=exact)


def reported_rules(returned):
    return {rule for rule, _ in returned[2]}


class PythonHollowTestWithTodo(unittest.TestCase):
    def test_test_without_assertion_and_a_well_formed_todo_is_denied_as_hollow_at_the_def(self):
        returned = written_by_test_writer(self, "tests/test_export.py", python_test(WELL_FORMED_TODO))
        self.assertEqual(returned, (0, DENY, (("added_comment", 2), ("hollow_test", 1))))

    def test_todo_that_mentions_assert_does_not_count_as_an_assertion(self):
        returned = written_by_test_writer(self, "tests/test_export.py", python_test(ASSERT_WORD_TODO))
        self.assertEqual(returned, (0, DENY, (("added_comment", 2), ("hollow_test", 1))))

    def test_test_with_a_real_assert_and_a_todo_gets_no_hollow_row(self):
        returned = written_by_test_writer(self, "tests/test_export.py", python_test(WELL_FORMED_TODO, "assert run() == 1"))
        self.assertEqual(returned, (0, DENY, (("added_comment", 2),)))

    def test_writer_outside_the_test_agent_gets_the_hollow_row_beside_the_write_gate(self):
        returned = verdict(self, "tests/test_export.py", python_test(WELL_FORMED_TODO))
        self.assertEqual(returned, (0, DENY, (("added_comment", 2), ("hollow_test", 1), ("test_write_gate", 1))))


class JavaScriptHollowTestWithTodo(unittest.TestCase):
    def test_it_callback_without_expect_and_a_well_formed_todo_is_denied_as_hollow_at_the_call(self):
        returned = written_by_test_writer(self, "tests/a.test.js", js_test(WELL_FORMED_TODO))
        self.assertEqual(returned, (0, DENY, (("hollow_test", 1),)))

    def test_todo_that_mentions_an_expect_call_does_not_count_as_an_assertion(self):
        returned = written_by_test_writer(self, "tests/a.test.js", js_test(EXPECT_CALL_TODO))
        self.assertEqual(returned, (0, DENY, (("hollow_test", 1),)))

    def test_it_callback_with_a_real_expect_and_a_todo_is_allowed(self):
        returned = written_by_test_writer(self, "tests/a.test.js", js_test(WELL_FORMED_TODO, "expect(run()).toBe(1);"))
        self.assertEqual(returned, (0, None, ()))


class PythonLineBreaksBeforeAComment(unittest.TestCase):
    def test_form_feed_above_an_asserting_test_with_a_trailing_comment_gets_no_hollow_row(self):
        content = source("X = 1\x0c", "def test_export():", "    assert run() == 1  # x")
        self.assertNotIn("hollow_test", reported_rules(written_by_test_writer(self, "tests/test_export.py", content)))

    def test_form_feed_above_an_asserting_test_with_a_type_ignore_directive_is_allowed(self):
        content = source("X = 1\x0c", "def test_export():", "    assert run() == 1  # type: ignore")
        self.assertEqual(written_by_test_writer(self, "tests/test_export.py", content), (0, None, ()))

    def test_lone_carriage_return_above_an_asserting_test_with_a_trailing_comment_gets_no_hollow_row(self):
        content = source("X = 1\rY = 2", "def test_export():", "    assert run() == 1  # x")
        self.assertNotIn("hollow_test", reported_rules(written_by_test_writer(self, "tests/test_export.py", content)))

    def test_crlf_test_with_a_comment_on_its_def_line_and_a_real_assert_gets_no_hollow_row(self):
        content = "\r\n".join(("X = 1", "def test_export():  # x", "    assert run() == 1", ""))
        self.assertNotIn("hollow_test", reported_rules(written_by_test_writer(self, "tests/test_export.py", content)))

    def test_form_feed_inside_a_def_line_comment_above_a_real_assert_gets_no_hollow_row(self):
        content = source("def test_export():  # a\x0cb", "    assert run() == 1")
        self.assertNotIn("hollow_test", reported_rules(written_by_test_writer(self, "tests/test_export.py", content)))


class JavaScriptLineBreaksInsideATodoComment(unittest.TestCase):
    def test_form_feed_at_the_end_of_a_line_comment_keeps_the_hollow_row_at_the_call(self):
        returned = written_by_test_writer(self, "tests/a.test.js", js_test(EXPECT_CALL_TODO + " \x0c"))
        self.assertEqual(returned, (0, DENY, (("hollow_test", 1),)))

    def test_lone_carriage_return_inside_a_block_comment_keeps_the_hollow_row_at_the_call(self):
        returned = written_by_test_writer(self, "tests/a.test.js", js_block_test(EXPECT_CALL_TODO + " \r"))
        self.assertEqual(returned, (0, DENY, (("hollow_test", 1),)))

    def test_line_separator_at_the_end_of_a_line_comment_keeps_the_hollow_row_at_the_call(self):
        returned = written_by_test_writer(self, "tests/a.test.js", js_test(EXPECT_CALL_TODO + "\u2028"))
        self.assertEqual(returned, (0, DENY, (("hollow_test", 1),)))

    def test_form_feed_comment_above_a_real_expect_is_allowed(self):
        returned = written_by_test_writer(self, "tests/a.test.js", js_test(EXPECT_CALL_TODO + " \x0c", "expect(run()).toBe(1);"))
        self.assertEqual(returned, (0, None, ()))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class LayerHollowTestWithTodo(unittest.TestCase):
    def test_java_test_whose_todo_names_an_assertion_call_is_denied_as_hollow(self):
        returned = written_by_test_writer(self, "src/CalcTest.java", java_test(JAVA_ASSERT_TODO), exact=True)
        self.assertEqual(returned, (0, DENY, (("hollow_test", 3),)))

    def test_java_test_with_a_real_assertion_and_a_todo_is_allowed(self):
        content = java_test(WELL_FORMED_TODO, "assertEquals(1, run());")
        self.assertEqual(written_by_test_writer(self, "src/CalcTest.java", content, exact=True), (0, None, ()))


if __name__ == "__main__":
    unittest.main()
