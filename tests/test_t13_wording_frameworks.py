import unittest

from t13_wording_home import ASSERTION, EXCEPTION, PASSED, WordingHome, denial, lines, quoted, words
from t20_layer_home import CITATION, HASH_CITATION, LIBRARY_NEEDED, LIBRARY_READY


ON_ITS_WAY = words("The", "order", "is", "on", "its", "way")
DRIVER_NOTE = words("a", "note", "for", "the", "driver")
NOT_FOUND = words("the", "order", "was", "not", "found")
STATUS_MESSAGE = words("the", "status", "should", "match", "here")
SAID = quoted(ON_ITS_WAY)


def junit5(statement):
    return lines(
        "import org.junit.jupiter.api.Test;", "class OrderTest {", "  " + CITATION, "  @Test", "  void ships() {", "    " + statement,
        "  }", "}",
    )


def junit4(statement):
    return lines(
        "import org.junit.Test;", "class OrderTest {", "  " + CITATION, "  @Test", "  public void ships() {", "    " + statement, "  }",
        "}",
    )


def go(*body):
    return lines("package order", CITATION, "func TestShip(t *testing.T) {", "\tgot := Ship()", *("\t" + line for line in body), "}")


def bats(statement):
    return lines(HASH_CITATION, '@test "ships" {', "  run ship", "  " + statement, "}")


def csharp(marker, statement):
    return lines("public class OrderTests {", "  " + CITATION, f"  [{marker}]", "  public void Ships() {", "    " + statement, "  }", "}")


def phpunit(statement):
    return lines(
        "<?php", "class OrderTest extends TestCase {", "  " + CITATION, "  public function testShips() {", "    " + statement, "  }", "}",
    )


def minitest(statement):
    return lines("class OrderTest < Minitest::Test", "  " + HASH_CITATION, "  def test_ships", "    " + statement, "  end", "end")


def kotest(statement):
    return lines("class OrderTest {", "  " + CITATION, "  @Test", "  fun ships() {", "    " + statement, "  }", "}")


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class FrameworkWordingUnderTheLanguageLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = WordingHome.open(cls, exact=True)

    def verdict(self, relative, content):
        return self.home.outcome(self, relative, content)

    def test_junit_assert_equals_with_a_prose_expected_value_denies(self):
        content = junit5(f"assertEquals({SAID}, order.status());")
        self.assertEqual(self.verdict("tests/OrderTest.java", content), denial(ASSERTION, 6))

    def test_junit_machine_token_expected_value_passes(self):
        self.assertEqual(self.verdict("tests/OrderTest.java", junit5('assertEquals("SHIPPED", order.code());')), PASSED)

    def test_junit4_leading_message_is_skipped_so_a_token_expected_value_passes(self):
        content = junit4(f'assertEquals({quoted(STATUS_MESSAGE)}, "SHIPPED", order.code());')
        self.assertEqual(self.verdict("tests/OrderTest.java", content), PASSED)

    def test_assertj_is_equal_to_with_prose_denies(self):
        content = junit5(f"assertThat(order.note()).isEqualTo({quoted(DRIVER_NOTE)});")
        self.assertEqual(self.verdict("tests/OrderTest.java", content), denial(ASSERTION, 6))

    def test_testify_equal_with_a_sentence_denies(self):
        self.assertEqual(self.verdict("tests/order_test.go", go(f"assert.Equal(t, {SAID}, got)")), denial(ASSERTION, 5))

    def test_testify_equal_with_a_machine_token_passes(self):
        self.assertEqual(self.verdict("tests/order_test.go", go('assert.Equal(t, "SHIPPED", got)')), PASSED)

    def test_testify_require_contains_with_a_sentence_denies(self):
        content = go(f"require.Contains(t, got, {SAID})")
        self.assertEqual(self.verdict("tests/order_test.go", content), denial(ASSERTION, 5))

    def test_testify_suite_equal_with_a_sentence_denies(self):
        self.assertEqual(self.verdict("tests/order_test.go", go(f"suite.Equal({SAID}, got)")), denial(ASSERTION, 5))

    def test_go_comparison_guarding_a_failure_denies_on_the_if_line(self):
        content = go(f"if got != {SAID} {{", '\tt.Errorf("bad")', "}")
        self.assertEqual(self.verdict("tests/order_test.go", content), denial(ASSERTION, 5))

    def test_go_contains_check_outside_an_asserting_if_passes(self):
        content = go(f"if strings.Contains(got, {SAID}) {{", "\tskipIt()", "}", "assert.Equal(t, 4, len(got))")
        self.assertEqual(self.verdict("tests/order_test.go", content), PASSED)

    def test_bats_partial_output_assertion_with_prose_denies(self):
        content = bats(f"assert_output --partial {SAID}")
        self.assertEqual(self.verdict("tests/order.bats", content), denial(ASSERTION, 4))

    def test_bats_bracket_test_on_output_with_prose_denies(self):
        content = bats(f'[ "$output" = {SAID} ]')
        self.assertEqual(self.verdict("tests/order.bats", content), denial(ASSERTION, 4))

    def test_xunit_assert_equal_with_prose_denies(self):
        content = csharp("Fact", f"Assert.Equal({SAID}, order.Status);")
        self.assertEqual(self.verdict("tests/OrderTests.cs", content), denial(ASSERTION, 5))

    def test_nunit_is_equal_to_with_prose_denies(self):
        content = csharp("Test", f"Assert.That(order.Note, Is.EqualTo({quoted(DRIVER_NOTE)}));")
        self.assertEqual(self.verdict("tests/OrderTests.cs", content), denial(ASSERTION, 5))

    def test_phpunit_assert_same_with_prose_denies(self):
        content = phpunit(f"$this->assertSame({SAID}, $order->status());")
        self.assertEqual(self.verdict("tests/OrderTest.php", content), denial(ASSERTION, 5))

    def test_phpunit_expected_exception_message_denies_as_an_exception_match(self):
        content = phpunit(f"$this->expectExceptionMessage({quoted(NOT_FOUND)});")
        self.assertEqual(self.verdict("tests/OrderTest.php", content), denial(EXCEPTION, 5))

    def test_minitest_assert_equal_with_prose_denies(self):
        content = minitest(f"assert_equal {SAID}, order.status")
        self.assertEqual(self.verdict("tests/order_test.rb", content), denial(ASSERTION, 4))

    def test_minitest_must_equal_expectation_with_prose_denies(self):
        content = minitest(f"_(order.note).must_equal {quoted(DRIVER_NOTE)}")
        self.assertEqual(self.verdict("tests/order_test.rb", content), denial(ASSERTION, 4))

    def test_kotest_infix_should_be_with_prose_denies(self):
        self.assertEqual(self.verdict("tests/OrderTest.kt", kotest(f"order.status() shouldBe {SAID}")), denial(ASSERTION, 5))

    def test_kotest_should_contain_with_prose_denies(self):
        content = kotest(f"order.note().shouldContain({quoted(DRIVER_NOTE)})")
        self.assertEqual(self.verdict("tests/OrderTest.kt", content), denial(ASSERTION, 5))


if __name__ == "__main__":
    unittest.main()
