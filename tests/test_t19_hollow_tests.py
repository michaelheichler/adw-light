import unittest

from t19_language_home import ALLOWED, LIBRARY_NEEDED, LIBRARY_READY, blocked, cited, exact_verdict, fallback_verdict, uncited


KOTLIN_PATH = "src/CalcTest.kt"
KOTLIN_SOURCE = """package calc

class CalcTest {
    // CITE_PRINCIPLE
    @Test
    fun `adds two numbers`() {
        Calc().add(1, 2)
    }

    // CITE_PRINCIPLE
    @Test
    fun real() {
        assertEquals(3, Calc().add(1, 2))
    }
}
"""
KOTLIN_TESTS = cited(KOTLIN_SOURCE)
KOTLIN_QUOTED_ASSERTION = cited("""class StrTest {
    // CITE_PRINCIPLE
    @Test
    fun quoted() {
        val s = "assertEquals(1, 1)"
    }
}
""")
RUST_TESTS = """#[test]
fn hollow() {
    run();
}

#[test]
fn real() {
    assert_eq!(run(), 1);
}
"""
GO_TESTS = cited("""package calc

// CITE_PRINCIPLE
func TestHollow(t *testing.T) {
\trun()
}

// CITE_PRINCIPLE
func TestReal(t *testing.T) {
\tif run() != 1 {
\t\tt.Errorf("bad")
\t}
}
""")
GO_NON_TESTS = cited("""package calc

func BenchmarkRun(b *testing.B) {
\trun()
}

// CITE_PRINCIPLE
func TestMain(m *testing.M) {
\tm.Run()
}
""")
JAVA_TESTS = cited("""class CalcTest {
    // CITE_PRINCIPLE
    @Test
    void hollow() {
        run();
    }

    // CITE_PRINCIPLE
    @Test
    void real() {
        assertThat(run()).isEqualTo(1);
    }
}
""")
JAVA_CONSTRUCTOR = """class testSupport {
    testSupport() {
        run();
    }
}
"""
CSHARP_TESTS = cited("""public class CalcTests
{
    // CITE_PRINCIPLE
    [Fact]
    public void Hollow()
    {
        Run();
    }

    // CITE_PRINCIPLE
    [Fact]
    public void Real()
    {
        Assert.Equal(1, Run());
    }
}
""")
PHP_TESTS = cited("""<?php
class CalcTest extends TestCase
{
    // CITE_PRINCIPLE
    public function testHollow(): void
    {
        run();
    }

    // CITE_PRINCIPLE
    public function testReal(): void
    {
        $this->assertSame(1, run());
    }
}
""")
RUBY_TESTS = cited("""class CalcTest < Minitest::Test
  # CITE_PRINCIPLE
  def test_hollow
    run
  end

  # CITE_PRINCIPLE
  def test_real
    assert_equal 1, run
  end
end
""")
SWIFT_TESTS = cited("""final class CalcTests: XCTestCase {
    // CITE_PRINCIPLE
    func testHollow() {
        run()
    }

    // CITE_PRINCIPLE
    func testReal() {
        XCTAssertEqual(run(), 1)
    }
}
""")
HALF_WRITTEN_JS_TESTS = """import { from 'x';
it('works', () => {
  run();
});
"""
JS_TESTS = cited("""// CITE_PRINCIPLE
it('works', () => {
  run();
});

// CITE_PRINCIPLE
it('checks', () => {
  expect(run()).toBe(1);
});
""")


def hollow_at(line):
    return blocked(("hollow_test", line))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class ExactLayerFindsHollowTests(unittest.TestCase):
    def test_kotlin_backtick_test_without_assertion_is_blocked_at_its_name_line(self):
        self.assertEqual(exact_verdict(self, KOTLIN_PATH, KOTLIN_TESTS), hollow_at(6))

    def test_kotlin_assertion_inside_a_string_does_not_count(self):
        self.assertEqual(exact_verdict(self, "src/StrTest.kt", KOTLIN_QUOTED_ASSERTION), hollow_at(4))

    def test_rust_test_attribute_marks_the_hollow_test(self):
        self.assertEqual(exact_verdict(self, "src/lib.rs", RUST_TESTS), hollow_at(2))

    def test_go_test_name_marks_the_hollow_test(self):
        self.assertEqual(exact_verdict(self, "calc_test.go", GO_TESTS), hollow_at(4))

    def test_go_benchmark_and_test_main_are_not_tests(self):
        self.assertEqual(exact_verdict(self, "calc_test.go", GO_NON_TESTS), ALLOWED)

    def test_java_test_annotation_marks_the_hollow_test(self):
        self.assertEqual(exact_verdict(self, "src/CalcTest.java", JAVA_TESTS), hollow_at(4))

    def test_java_constructor_with_a_test_name_is_not_a_test(self):
        self.assertEqual(exact_verdict(self, "src/testSupport.java", JAVA_CONSTRUCTOR), ALLOWED)

    def test_csharp_fact_attribute_marks_the_hollow_test(self):
        self.assertEqual(exact_verdict(self, "CalcTests.cs", CSHARP_TESTS), hollow_at(5))

    def test_php_test_method_name_marks_the_hollow_test(self):
        self.assertEqual(exact_verdict(self, "tests/CalcTest.php", PHP_TESTS), hollow_at(5))

    def test_ruby_test_method_name_marks_the_hollow_test(self):
        self.assertEqual(exact_verdict(self, "test/calc_test.rb", RUBY_TESTS), hollow_at(3))

    def test_swift_test_method_name_marks_the_hollow_test(self):
        self.assertEqual(exact_verdict(self, "Tests/CalcTests.swift", SWIFT_TESTS), hollow_at(3))

    def test_javascript_it_callback_without_expect_is_blocked_at_the_call(self):
        self.assertEqual(exact_verdict(self, "src/a.test.js", JS_TESTS), hollow_at(2))

    def test_half_written_javascript_test_keeps_the_lexer_hollow_finding(self):
        self.assertEqual(exact_verdict(self, "src/a.test.js", HALF_WRITTEN_JS_TESTS),
                         blocked(("hollow_test", 2), ("test_principle_citation", 2)))


class FallbackKeepsOldHollowBehavior(unittest.TestCase):
    def test_hollow_kotlin_test_passes_without_the_library(self):
        self.assertEqual(fallback_verdict(self, KOTLIN_PATH, uncited(KOTLIN_SOURCE)), ALLOWED)

    def test_javascript_lexer_still_blocks_the_hollow_it_callback(self):
        self.assertEqual(fallback_verdict(self, "src/a.test.js", JS_TESTS), hollow_at(2))


if __name__ == "__main__":
    unittest.main()
