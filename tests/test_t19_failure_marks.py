import unittest

from t19_language_home import ALLOWED, LIBRARY_NEEDED, LIBRARY_READY, blocked, cited, exact_verdict


JUNIT4_EXPECTED = cited("""class ParserTest {
    // CITE_PRINCIPLE
    @Test(expected = IllegalArgumentException.class)
    public void rejectsBlank() {
        Parser.parse("");
    }
}
""")
JUNIT4_EXPECTED_BESIDE_HOLLOW = cited("""class ParserTest {
    // CITE_PRINCIPLE
    @Test(expected = IllegalArgumentException.class)
    public void rejectsBlank() {
        Parser.parse("");
    }

    // CITE_PRINCIPLE
    @Test
    public void parsesDigit() {
        Parser.parse("1");
    }
}
""")
KOTLIN_EXPECTED = cited("""class ParserTest {
    // CITE_PRINCIPLE
    @Test(expected = IllegalArgumentException::class)
    fun rejectsBlank() {
        Parser.parse("")
    }
}
""")
RUST_SHOULD_PANIC = """#[test]
#[should_panic]
fn rejects_blank() {
    parse("");
}
"""
RUST_SHOULD_PANIC_BESIDE_HOLLOW = """#[test]
#[should_panic]
fn rejects_blank() {
    parse("");
}

#[test]
fn parses_digit() {
    parse("1");
}
"""
MSTEST_EXPECTED_EXCEPTION = cited("""public class ParserTests
{
    // CITE_PRINCIPLE
    [TestMethod]
    [ExpectedException(typeof(ArgumentException))]
    public void RejectsBlank()
    {
        Parser.Parse("");
    }
}
""")
GO_SUITE_EQUAL = cited("""package calc

// CITE_PRINCIPLE
func (s *CalcSuite) TestAdd() {
\ts.Equal(3, Add(1, 2))
}
""")
GO_SUITE_REQUIRE = cited("""package calc

// CITE_PRINCIPLE
func (s *CalcSuite) TestLoad() {
\terr := Load()
\ts.Require().NoError(err)
}
""")
GO_SUITE_PACKAGE_EQUAL = cited("""package order

// CITE_PRINCIPLE
func (s *OrderSuite) TestShips() {
\tgot := Ship()
\tsuite.Equal("SHIPPED", got)
}
""")
GO_SUITE_NO_ASSERTION = cited("""package calc

// CITE_PRINCIPLE
func (s *CalcSuite) TestAdd() {
\tAdd(1, 2)
}
""")
GO_SUITE_FOREIGN_CONTAINS = cited("""package calc

// CITE_PRINCIPLE
func (s *CalcSuite) TestName() {
\tstrings.Contains(Name(), "calc")
}
""")
GO_SUITE_FIELD_ERROR_CALL = cited("""package calc

// CITE_PRINCIPLE
func (s *CalcSuite) TestLoad() {
\ts.err.Error()
}
""")
GO_SUITE_ZERO_ARGUMENT_HELPER = cited("""package calc

// CITE_PRINCIPLE
func (s *CalcSuite) TestReset() {
\ts.EmptyCache()
}
""")
SWIFT_UNWRAP = cited("""final class ParserTests: XCTestCase {
    // CITE_PRINCIPLE
    func testParse() throws {
        _ = try XCTUnwrap(parse("1"))
    }
}
""")
GO_SUITE_PATH = "calc_suite_test.go"


def hollow_at(line):
    return blocked(("hollow_test", line))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class ExpectedFailureMarksCountAsAssertions(unittest.TestCase):
    def test_junit4_expected_exception_test_is_allowed(self):
        self.assertEqual(exact_verdict(self, "src/test/java/ParserTest.java", JUNIT4_EXPECTED), ALLOWED)

    def test_junit4_expected_mark_does_not_cover_the_next_hollow_test(self):
        verdict = exact_verdict(self, "src/test/java/ParserTest.java", JUNIT4_EXPECTED_BESIDE_HOLLOW)
        self.assertEqual(verdict, hollow_at(10))

    def test_kotlin_expected_exception_test_is_allowed(self):
        self.assertEqual(exact_verdict(self, "src/test/kotlin/ParserTest.kt", KOTLIN_EXPECTED), ALLOWED)

    def test_rust_should_panic_test_is_allowed(self):
        self.assertEqual(exact_verdict(self, "src/lib.rs", RUST_SHOULD_PANIC), ALLOWED)

    def test_rust_should_panic_does_not_cover_the_next_hollow_test(self):
        self.assertEqual(exact_verdict(self, "src/lib.rs", RUST_SHOULD_PANIC_BESIDE_HOLLOW), hollow_at(8))

    def test_mstest_expected_exception_attribute_is_allowed(self):
        self.assertEqual(exact_verdict(self, "ParserTests.cs", MSTEST_EXPECTED_EXCEPTION), ALLOWED)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class GoSuiteAssertionsAreTiedToTheReceiver(unittest.TestCase):
    def test_suite_method_calling_a_receiver_assertion_is_allowed(self):
        self.assertEqual(exact_verdict(self, GO_SUITE_PATH, GO_SUITE_EQUAL), ALLOWED)

    def test_suite_method_using_require_is_allowed(self):
        self.assertEqual(exact_verdict(self, GO_SUITE_PATH, GO_SUITE_REQUIRE), ALLOWED)

    def test_suite_method_checking_through_the_suite_root_is_allowed(self):
        self.assertEqual(exact_verdict(self, GO_SUITE_PATH, GO_SUITE_PACKAGE_EQUAL), ALLOWED)

    def test_suite_method_without_assertion_is_blocked_at_its_header(self):
        self.assertEqual(exact_verdict(self, GO_SUITE_PATH, GO_SUITE_NO_ASSERTION), hollow_at(4))

    def test_contains_on_another_package_is_not_a_suite_assertion(self):
        self.assertEqual(exact_verdict(self, GO_SUITE_PATH, GO_SUITE_FOREIGN_CONTAINS), hollow_at(4))

    def test_error_method_on_a_receiver_field_is_not_a_suite_assertion(self):
        self.assertEqual(exact_verdict(self, GO_SUITE_PATH, GO_SUITE_FIELD_ERROR_CALL), hollow_at(4))

    def test_zero_argument_helper_named_like_an_assertion_is_not_a_suite_assertion(self):
        self.assertEqual(exact_verdict(self, GO_SUITE_PATH, GO_SUITE_ZERO_ARGUMENT_HELPER), hollow_at(4))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class SwiftUnwrapCountsAsAssertion(unittest.TestCase):
    def test_swift_test_that_only_unwraps_is_allowed(self):
        self.assertEqual(exact_verdict(self, "Tests/ParserTests.swift", SWIFT_UNWRAP), ALLOWED)


if __name__ == "__main__":
    unittest.main()
