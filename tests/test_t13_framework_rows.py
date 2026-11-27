import unittest

from t13_wording_home import ASSERTION, EXCEPTION, PASSED, WordingHome, denial, lines, quoted, words
from t20_layer_home import CITATION, HASH_CITATION, LIBRARY_NEEDED, LIBRARY_READY, PRINCIPLE


SHIPPED = quoted(words("The", "order", "is", "on", "its", "way"))
REFUSED = quoted(words("the", "amount", "must", "be", "positive"))
TOKEN = quoted("E_AUTH")
DASH_CITATION = "-- Principle: " + PRINCIPLE
OK = quoted("ok")


def xctest(statement):
    return lines(
        "import XCTest", "final class BannerTests: XCTestCase {", CITATION, "    func testBanner() {", "        " + statement,
        "    }", "}",
    )


def swift_testing(statement):
    return lines("import Testing", CITATION, "@Test func greets() {", "    " + statement, "}")


def gtest(statement):
    return lines("#include <gtest/gtest.h>", CITATION, "TEST(Order, Ships) {", "    " + statement, "}")


def catch2(statement):
    return lines("#include <catch2/catch_test_macros.hpp>", CITATION, 'TEST_CASE("ships") {', "    " + statement, "}")


def unity(statement):
    return lines('#include "unity.h"', CITATION, "void test_ships(void) {", "    " + statement, "}")


def exunit(statement):
    return lines(
        "defmodule OrderTest do", "  use ExUnit.Case", "  " + HASH_CITATION, '  test "ships" do', "    " + statement, "  end",
        "end",
    )


def busted(statement):
    return lines(
        'describe("order", function()', "  " + DASH_CITATION, '  it("ships", function()', "    " + statement, "  end)", "end)",
    )


def scala_junit(statement):
    return lines(
        "import org.junit.Test", "class OrderTest {", "  " + CITATION, "  @Test def ships(): Unit = {", "    " + statement,
        "  }", "}",
    )


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class NewFrameworkRowsUnderTheLanguageLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = WordingHome.open(cls, exact=True)

    def verdict(self, relative, content):
        return self.home.outcome(self, relative, content)

    def swift(self, content):
        return self.verdict("Tests/BannerTests.swift", content)

    def cpp(self, content):
        return self.verdict("test/order_test.cpp", content)

    def elixir(self, content):
        return self.verdict("test/order_test.exs", content)

    def lua(self, content):
        return self.verdict("spec/order_spec.lua", content)

    def test_xctest_assert_equal_with_a_sentence_denies(self):
        self.assertEqual(self.swift(xctest(f"XCTAssertEqual(banner(), {SHIPPED})")), denial(ASSERTION, 5))

    def test_xctest_assert_equal_with_a_machine_token_passes(self):
        self.assertEqual(self.swift(xctest(f"XCTAssertEqual(banner(), {TOKEN})")), PASSED)

    def test_swift_testing_expect_macro_comparing_to_a_sentence_denies(self):
        self.assertEqual(self.swift(swift_testing(f"#expect(greet() == {SHIPPED})")), denial(ASSERTION, 4))

    def test_swift_testing_expect_macro_comparing_to_a_machine_token_passes(self):
        self.assertEqual(self.swift(swift_testing(f"#expect(greet() == {OK})")), PASSED)

    def test_googletest_expect_eq_with_a_sentence_denies(self):
        self.assertEqual(self.cpp(gtest(f"EXPECT_EQ(order.status(), {SHIPPED});")), denial(ASSERTION, 4))

    def test_googletest_assert_streq_with_a_sentence_denies(self):
        self.assertEqual(self.cpp(gtest(f"ASSERT_STREQ({SHIPPED}, order.status());")), denial(ASSERTION, 4))

    def test_googletest_expect_eq_with_a_machine_token_passes(self):
        self.assertEqual(self.cpp(gtest(f"EXPECT_EQ(order.status(), {TOKEN});")), PASSED)

    def test_gmock_has_substr_matcher_with_a_sentence_denies(self):
        content = gtest(f"EXPECT_THAT(order.status(), testing::HasSubstr({SHIPPED}));")
        self.assertEqual(self.cpp(content), denial(ASSERTION, 4))

    def test_gmock_has_substr_matcher_with_a_machine_token_passes(self):
        self.assertEqual(self.cpp(gtest(f"EXPECT_THAT(order.status(), testing::HasSubstr({TOKEN}));")), PASSED)

    def test_catch2_require_comparing_to_a_sentence_denies(self):
        self.assertEqual(self.cpp(catch2(f"REQUIRE(order.status() == {SHIPPED});")), denial(ASSERTION, 4))

    def test_catch2_require_comparing_to_a_machine_token_passes(self):
        self.assertEqual(self.cpp(catch2(f"REQUIRE(order.status() == {OK});")), PASSED)

    def test_catch2_contains_substring_matcher_with_a_sentence_denies(self):
        content = catch2(f"CHECK_THAT(order.status(), Catch::Matchers::ContainsSubstring({SHIPPED}));")
        self.assertEqual(self.cpp(content), denial(ASSERTION, 4))

    def test_unity_string_assertion_with_a_sentence_denies(self):
        content = unity(f"TEST_ASSERT_EQUAL_STRING({SHIPPED}, order_status());")
        self.assertEqual(self.verdict("test/test_order.c", content), denial(ASSERTION, 4))

    def test_unity_string_assertion_with_a_machine_token_passes(self):
        content = unity(f"TEST_ASSERT_EQUAL_STRING({TOKEN}, order_status());")
        self.assertEqual(self.verdict("test/test_order.c", content), PASSED)

    def test_exunit_assert_equality_with_a_sentence_denies(self):
        self.assertEqual(self.elixir(exunit(f"assert Order.status() == {SHIPPED}")), denial(ASSERTION, 5))

    def test_exunit_assert_regex_match_with_a_sentence_denies(self):
        self.assertEqual(self.elixir(exunit(f"assert Order.status() =~ {SHIPPED}")), denial(ASSERTION, 5))

    def test_exunit_assert_equality_with_a_machine_token_passes(self):
        self.assertEqual(self.elixir(exunit(f"assert Order.status() == {OK}")), PASSED)

    def test_exunit_assert_raise_with_a_prose_message_denies_as_an_exception_match(self):
        content = exunit(f"assert_raise ArgumentError, {REFUSED}, fn -> Order.charge(-1) end")
        self.assertEqual(self.elixir(content), denial(EXCEPTION, 5))

    def test_exunit_assert_raise_with_a_machine_token_message_passes(self):
        content = exunit(f"assert_raise ArgumentError, {TOKEN}, fn -> Order.charge(-1) end")
        self.assertEqual(self.elixir(content), PASSED)

    def test_busted_are_equal_with_a_sentence_denies(self):
        self.assertEqual(self.lua(busted(f"assert.are.equal({SHIPPED}, order.status())")), denial(ASSERTION, 4))

    def test_busted_are_equal_with_a_machine_token_passes(self):
        self.assertEqual(self.lua(busted(f"assert.are.equal({TOKEN}, order.status())")), PASSED)

    def test_luaunit_assert_equals_with_a_sentence_denies(self):
        self.assertEqual(self.lua(busted(f"lu.assertEquals(order.status(), {SHIPPED})")), denial(ASSERTION, 4))

    def test_plain_lua_assert_comparing_to_a_sentence_denies(self):
        self.assertEqual(self.lua(busted(f"assert(order.status() == {SHIPPED})")), denial(ASSERTION, 4))

    def test_plain_lua_assert_comparing_to_a_machine_token_passes(self):
        self.assertEqual(self.lua(busted(f"assert(order.status() == {OK})")), PASSED)

    def test_scala_junit_assert_equals_with_a_sentence_as_the_actual_argument_denies(self):
        content = scala_junit(f"assertEquals(order.status(), {SHIPPED})")
        self.assertEqual(self.verdict("src/test/scala/OrderTest.scala", content), denial(ASSERTION, 5))

    def test_scala_junit_assert_equals_with_a_machine_token_passes(self):
        content = scala_junit(f"assertEquals(order.status(), {TOKEN})")
        self.assertEqual(self.verdict("src/test/scala/OrderTest.scala", content), PASSED)


if __name__ == "__main__":
    unittest.main()
