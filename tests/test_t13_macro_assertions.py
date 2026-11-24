import unittest

from t19_language_home import ALLOWED, LIBRARY_NEEDED, LIBRARY_READY, blocked, exact_verdict


C_PATH = "test/test_order.c"
CPP_PATH = "test/order_test.cpp"
KOTLIN_PATH = "src/test/kotlin/OrderTest.kt"
RUBY_PATH = "test/order_test.rb"


def c_test(statement):
    return f"void test_ships(void) {{\n    {statement}\n}}\n"


def cmocka_test(statement):
    return f"static void test_ships(void **state) {{\n    {statement}\n}}\n"


def cpp_test(statement):
    return f"void test_ships() {{\n    {statement}\n}}\n"


def kotlin_test(statement):
    return f"class OrderTest {{\n    @Test\n    fun ships() {{\n        {statement}\n    }}\n}}\n"


def minitest(statement):
    return f"class OrderTest < Minitest::Test\n  def test_ships\n    {statement}\n  end\nend\n"


def hollow_at(line):
    return blocked(("hollow_test", line))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class FrameworkAssertionsSatisfyTheHollowCheck(unittest.TestCase):
    def test_cunit_string_equal_macro_counts_as_an_assertion(self):
        self.assertEqual(exact_verdict(self, C_PATH, c_test('CU_ASSERT_STRING_EQUAL(order_status(), "ok");')), ALLOWED)

    def test_catch2_require_macro_counts_as_an_assertion(self):
        self.assertEqual(exact_verdict(self, CPP_PATH, cpp_test('REQUIRE(order.status() == "ok");')), ALLOWED)

    def test_catch2_check_that_macro_counts_as_an_assertion(self):
        content = cpp_test('CHECK_THAT(order.status(), Catch::Matchers::Equals("ok"));')
        self.assertEqual(exact_verdict(self, CPP_PATH, content), ALLOWED)

    def test_googletest_expect_eq_macro_counts_as_an_assertion(self):
        self.assertEqual(exact_verdict(self, CPP_PATH, cpp_test('EXPECT_EQ(order.status(), "ok");')), ALLOWED)

    def test_cmocka_string_equal_counts_as_an_assertion(self):
        content = cmocka_test('assert_string_equal(order_status(), "ok");')
        self.assertEqual(exact_verdict(self, C_PATH, content), ALLOWED)

    def test_kotest_should_be_infix_counts_as_an_assertion(self):
        self.assertEqual(exact_verdict(self, KOTLIN_PATH, kotlin_test('order.status() shouldBe "ok"')), ALLOWED)

    def test_minitest_must_equal_expectation_counts_as_an_assertion(self):
        self.assertEqual(exact_verdict(self, RUBY_PATH, minitest('order.status.must_equal "ok"')), ALLOWED)

    def test_minitest_wont_equal_expectation_counts_as_an_assertion(self):
        self.assertEqual(exact_verdict(self, RUBY_PATH, minitest('order.status.wont_equal "ok"')), ALLOWED)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class LookalikeMacroNamesStayHollow(unittest.TestCase):
    def test_c_call_that_only_starts_with_check_is_hollow(self):
        self.assertEqual(exact_verdict(self, C_PATH, c_test("CHECKOUT_ready();")), hollow_at(1))

    def test_c_call_ending_in_a_lowercase_require_is_hollow(self):
        self.assertEqual(exact_verdict(self, C_PATH, c_test("my_require(order_status());")), hollow_at(1))


if __name__ == "__main__":
    unittest.main()
