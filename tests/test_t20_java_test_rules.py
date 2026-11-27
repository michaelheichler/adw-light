import unittest

from t20_layer_home import (
    ACT, BLOCK, CITATION, CONTEXT, DENY, IF, LIBRARY_NEEDED, LIBRARY_READY, LOOP, NONE, OBSERVED, LayerHome, Outcome,
)


JAVA_TEST = "src/test/java/CalcTest.java"
JAVA_MAIN = "src/main/java/Calc.java"


def java_test(*body):
    return "class CalcTest {\n" + CITATION + "\n@Test\nvoid addsAll() {\n" + "".join(line + "\n" for line in body) + "}\n}\n"


FOR_LOOP = java_test("for (int i : xs) {", "assertEquals(i, calc.add(i));", "}")
UNCITED_FOR_LOOP = FOR_LOOP.replace(CITATION + "\n", "")
WHILE_LOOP = java_test("int i = 0;", "while (i < 3) {", "assertTrue(calc.add(i) >= i);", "i++;", "}")
ASSERT_KEYWORD_LOOP = java_test("for (int i : xs) {", "assert calc.add(i) == i;", "}")
NESTED_LOOPS = java_test(
    "for (int i : xs) {", "assertEquals(i, calc.add(i));", "while (calc.more()) {", "assertTrue(calc.check(i));", "}", "}",
)
TWO_LOOPS = java_test(
    "for (int i : xs) {", "assertEquals(i, calc.add(i));", "}", "for (int j : ys) {", "assertNotNull(calc.add(j));", "}",
)
BOTH_BRANCHES = java_test("boolean a = calc.ready();", "if (a) { assertTrue(x); } else { assertFalse(y); }")
ONE_BRANCH = java_test("boolean a = calc.ready();", "if (a) { assertTrue(x); }")
ONE_ASSERTING_BRANCH = java_test("boolean a = calc.ready();", "if (a) { assertTrue(x); } else { calc.reset(); }")
TWO_ACTS = java_test("sut.run();", "assertTrue(a);", "sut.stop();", "assertTrue(b);")
ONE_ACT = java_test("sut.run();", "assertTrue(a);", "assertTrue(b);")
LAMBDA_IN_LOOP = java_test("for (List<String> xs : groups) {", "xs.forEach(x -> assertNull(x));", "}")
STRAIGHT = java_test("assertEquals(3, calc.add(1, 2));")
ARRANGING_LOOP = java_test("int total = 0;", "for (int i : xs) {", "total += calc.add(i);", "}", "assertEquals(6, total);")
UNMARKED_HELPER = "class Calc {\nvoid testConnection() {\nfor (int i : xs) {\nassertEquals(i, add(i));\n}\n}\n}\n"
UNMARKED_HELPER_BESIDE_A_MARKED_TEST = (
    "class Calc {\n@Test\nvoid checks() {\nassertTrue(ready());\n}\n"
    "void testConnection() {\nfor (int i : xs) {\nassertEquals(i, add(i));\n}\n}\n}\n"
)
MARKED_OUTSIDE_TEST_TREE = (
    "class Calc {\n@Test\nvoid testConnection() {\nfor (int i : xs) {\nassertEquals(i, add(i));\n}\n}\n}\n"
)


def loop_denial(*lines):
    return Outcome(DENY, tuple((LOOP, BLOCK, line) for line in lines))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class JavaTestRulesUnderTheExactLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = LayerHome.open(cls, exact=True)

    def exact(self, relative, content):
        return self.home.outcome(self, relative, content)

    def test_assertion_inside_a_for_loop_denies_on_the_assertion_line(self):
        self.assertEqual(self.exact(JAVA_TEST, FOR_LOOP), loop_denial(6))

    def test_assertion_inside_a_while_loop_denies_on_the_assertion_line(self):
        self.assertEqual(self.exact(JAVA_TEST, WHILE_LOOP), loop_denial(7))

    def test_assert_keyword_inside_a_loop_denies(self):
        self.assertEqual(self.exact(JAVA_TEST, ASSERT_KEYWORD_LOOP), loop_denial(6))

    def test_nested_loops_give_one_finding_at_the_first_assertion_of_the_outer_loop(self):
        self.assertEqual(self.exact(JAVA_TEST, NESTED_LOOPS), loop_denial(6))

    def test_each_outer_loop_gets_its_own_finding(self):
        self.assertEqual(self.exact(JAVA_TEST, TWO_LOOPS), loop_denial(6, 9))

    def test_assertion_inside_a_lambda_in_a_loop_passes(self):
        self.assertEqual(self.exact(JAVA_TEST, LAMBDA_IN_LOOP), Outcome(NONE, ()))

    def test_loop_that_only_arranges_before_a_single_assertion_passes(self):
        self.assertEqual(self.exact(JAVA_TEST, ARRANGING_LOOP), Outcome(NONE, ()))

    def test_straight_line_test_passes(self):
        self.assertEqual(self.exact(JAVA_TEST, STRAIGHT), Outcome(NONE, ()))

    def test_asserting_in_both_branches_reports_the_if_line(self):
        self.assertEqual(self.exact(JAVA_TEST, BOTH_BRANCHES), Outcome(CONTEXT, ((IF, OBSERVED, 6),)))

    def test_if_without_else_passes(self):
        self.assertEqual(self.exact(JAVA_TEST, ONE_BRANCH), Outcome(NONE, ()))

    def test_assertion_in_only_one_of_two_branches_passes(self):
        self.assertEqual(self.exact(JAVA_TEST, ONE_ASSERTING_BRANCH), Outcome(NONE, ()))

    def test_second_sut_call_after_an_assert_reports_the_second_act(self):
        self.assertEqual(self.exact(JAVA_TEST, TWO_ACTS), Outcome(CONTEXT, ((ACT, OBSERVED, 7),)))

    def test_single_sut_call_passes(self):
        self.assertEqual(self.exact(JAVA_TEST, ONE_ACT), Outcome(NONE, ()))

    def test_unannotated_test_named_method_in_production_code_passes(self):
        self.assertEqual(self.exact(JAVA_MAIN, UNMARKED_HELPER), Outcome(NONE, ()))

    def test_test_name_without_annotation_outside_the_test_tree_is_no_unit(self):
        self.assertEqual(self.exact(JAVA_MAIN, UNMARKED_HELPER_BESIDE_A_MARKED_TEST), Outcome(NONE, ()))

    def test_test_annotation_outside_the_test_tree_still_marks_a_unit(self):
        self.assertEqual(self.exact(JAVA_MAIN, MARKED_OUTSIDE_TEST_TREE), loop_denial(5))

    def test_cached_java_grammar_logs_no_reduced_precision(self):
        self.home.write(self, JAVA_TEST, FOR_LOOP)
        self.assertEqual(self.home.precision_reasons(JAVA_TEST), ())


class JavaTestRulesWithoutTheLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = LayerHome.open(cls, exact=False)

    def test_java_loop_assertion_gets_no_finding_and_logs_the_missing_library(self):
        outcome = self.home.outcome(self, JAVA_TEST, UNCITED_FOR_LOOP)
        found = (outcome, self.home.precision_reasons(JAVA_TEST))
        self.assertEqual(found, (Outcome(NONE, ()), (("java", "library_missing"),)))

    def test_java_fallback_answers_with_a_hook_response(self):
        self.assertIs(type(self.home.write(self, JAVA_TEST, UNCITED_FOR_LOOP)), dict)


if __name__ == "__main__":
    unittest.main()
