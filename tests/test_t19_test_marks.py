import unittest

from t19_language_home import ALLOWED, LIBRARY_NEEDED, LIBRARY_READY, blocked, cited, exact_verdict


PRODUCTION_JS_TEST_NAME = """function testConnection() {
  connect();
}
"""
PRODUCTION_GO_TEST_NAME = """package util

func TestHelper() {
\trun()
}
"""
PRODUCTION_JS_IT_CALL = """it('works', () => {
  run();
});
"""
INLINE_RUST_SHOULD_TEST = """#[test]
fn should_parse() {
    run();
}
"""
JAVA_SHOULD_TEST = cited("""class AppTest {
    // CITE_PRINCIPLE
    @Test void shouldWork() { run(); }
}
""")
KOTLIN_SHOULD_BACKTICK_TEST = cited("""class CalcTest {
    // CITE_PRINCIPLE
    @Test fun `should work`() { run() }
}
""")
KOTLIN_EXPECTS_TEST = cited("""class CalcTest {
    // CITE_PRINCIPLE
    @Test fun expectsValue() { run() }
}
""")
KOTLIN_EXPECTS_TEST_WITH_ASSERTION = cited("""class CalcTest {
    // CITE_PRINCIPLE
    @Test fun expectsValue() { assertEquals(1, run()) }
}
""")
GO_TEST_FILE_HOLLOW = cited("""package util

// CITE_PRINCIPLE
func TestHelper(t *testing.T) {
\trun()
}
""")
JS_TEST_FILE_TEST_NAME = cited("""// CITE_PRINCIPLE
function testConnection() {
  connect();
}
""")
JS_TEST_FILE_EXPECT_ARROW = cited("""// CITE_PRINCIPLE
it('works', () => expect(run()).toBe(1));
""")
KOTLIN_TEST_PATH = "src/test/kotlin/CalcTest.kt"


def hollow_at(line):
    return blocked(("hollow_test", line))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class NameMarkedTestsCountOnlyInTestFiles(unittest.TestCase):
    def test_production_javascript_function_named_like_a_test_is_allowed(self):
        self.assertEqual(exact_verdict(self, "src/db.js", PRODUCTION_JS_TEST_NAME), ALLOWED)

    def test_production_go_function_named_like_a_test_is_allowed(self):
        self.assertEqual(exact_verdict(self, "src/util.go", PRODUCTION_GO_TEST_NAME), ALLOWED)

    def test_production_javascript_it_call_is_allowed(self):
        self.assertEqual(exact_verdict(self, "src/app.js", PRODUCTION_JS_IT_CALL), ALLOWED)

    def test_go_test_name_in_a_test_file_still_blocks_a_hollow_test(self):
        self.assertEqual(exact_verdict(self, "util_test.go", GO_TEST_FILE_HOLLOW), hollow_at(4))

    def test_javascript_test_name_in_a_test_file_still_blocks_a_hollow_test(self):
        self.assertEqual(exact_verdict(self, "db.test.js", JS_TEST_FILE_TEST_NAME), hollow_at(2))

    def test_rust_test_attribute_in_production_source_still_blocks_a_hollow_test(self):
        self.assertEqual(exact_verdict(self, "src/lib.rs", INLINE_RUST_SHOULD_TEST), hollow_at(2))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class AssertionSearchSkipsTheTestName(unittest.TestCase):
    def test_java_test_named_should_without_assertion_is_blocked(self):
        self.assertEqual(exact_verdict(self, "tests/AppTest.java", JAVA_SHOULD_TEST), hollow_at(3))

    def test_kotlin_backtick_name_with_should_without_assertion_is_blocked(self):
        self.assertEqual(exact_verdict(self, KOTLIN_TEST_PATH, KOTLIN_SHOULD_BACKTICK_TEST), hollow_at(3))

    def test_kotlin_test_named_expects_without_assertion_is_blocked(self):
        self.assertEqual(exact_verdict(self, KOTLIN_TEST_PATH, KOTLIN_EXPECTS_TEST), hollow_at(3))

    def test_kotlin_test_named_expects_with_an_assertion_in_its_body_is_allowed(self):
        self.assertEqual(exact_verdict(self, KOTLIN_TEST_PATH, KOTLIN_EXPECTS_TEST_WITH_ASSERTION), ALLOWED)

    def test_javascript_arrow_body_holding_expect_is_allowed(self):
        self.assertEqual(exact_verdict(self, "db.test.js", JS_TEST_FILE_EXPECT_ARROW), ALLOWED)


if __name__ == "__main__":
    unittest.main()
