import unittest

from t20_layer_home import (
    BLOCK, CITATION, DENY, HASH_CITATION, LIBRARY_NEEDED, LIBRARY_READY, LOOP, NONE, LayerHome, Outcome,
)


GO_LOOP = (
    "package calc\n\n" + CITATION + "\nfunc TestAdd(t *testing.T) {\n\tfor _, x := range xs {\n"
    "\t\trequire.Equal(t, x, Add(x))\n\t}\n}\n"
)
GO_FAILURE_LOOP = (
    "package calc\n\n" + CITATION + "\nfunc TestAdd(t *testing.T) {\n\tfor _, x := range xs {\n"
    "\t\tif Add(x) != x {\n\t\t\tt.Errorf(\"%d\", x)\n\t\t}\n\t}\n}\n"
)
GO_SUBTEST_LOOP = (
    "package calc\n\n" + CITATION + "\nfunc TestAdd(t *testing.T) {\n\tfor _, x := range xs {\n"
    "\t\tt.Run(\"a\", func(t *testing.T) { require.True(t, Add(x) > 0) })\n\t}\n}\n"
)
KOTLIN_LOOPS = (
    "class CalcTest {\n    " + CITATION + "\n    @Test\n    fun addsAll() {\n        for (x in xs) {\n"
    "            assertEquals(x, add(x))\n        }\n        while (more()) {\n            assertTrue(check())\n        }\n    }\n}\n"
)
CSHARP_LOOP = (
    "public class CalcTests {\n    " + CITATION + "\n    [Fact]\n    public void AddsAll() {\n        foreach (var x in xs) {\n"
    "            Assert.Equal(x, Add(x));\n        }\n    }\n}\n"
)
PHP_LOOP = (
    "<?php\nclass CalcTest extends TestCase {\n    " + CITATION + "\n    public function testAddsAll(): void {\n"
    "        foreach ($this->xs as $x) {\n            $this->assertSame($x, add($x));\n        }\n    }\n}\n"
)
RUBY_LOOP = (
    "class CalcTest < Minitest::Test\n  " + HASH_CITATION + "\n  def test_adds_all\n    for x in xs\n      assert_equal x, add(x)\n"
    "    end\n  end\nend\n"
)
RUBY_BRACE_BLOCK_LOOP = (
    "class CalcTest < Minitest::Test\n  " + HASH_CITATION + "\n  def test_adds_all\n    for group in groups\n"
    "      group.each { |x| assert_equal x, add(x) }\n    end\n  end\nend\n"
)
SWIFT_LOOP = (
    "final class CalcTests: XCTestCase {\n    " + CITATION + "\n    func testAddsAll() {\n        for x in xs {\n"
    "            XCTAssertEqual(x, add(x))\n        }\n    }\n}\n"
)
CPP_LOOP = CITATION + "\nTEST(Calc, AddsAll) {\n    for (int x : xs) {\n        EXPECT_EQ(x, add(x));\n    }\n}\n"


def loop_denial(*lines):
    return Outcome(DENY, tuple((LOOP, BLOCK, line) for line in lines))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class LoopAssertionsAcrossLayerLanguages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = LayerHome.open(cls, exact=True)

    def exact(self, relative, content):
        return self.home.outcome(self, relative, content)

    def test_go_require_call_inside_a_range_loop_denies(self):
        self.assertEqual(self.exact("pkg/calc_test.go", GO_LOOP), loop_denial(6))

    def test_go_testing_failure_inside_a_loop_denies(self):
        self.assertEqual(self.exact("pkg/calc_test.go", GO_FAILURE_LOOP), loop_denial(7))

    def test_go_assertion_inside_a_subtest_closure_passes(self):
        self.assertEqual(self.exact("pkg/calc_test.go", GO_SUBTEST_LOOP), Outcome(NONE, ()))

    def test_kotlin_for_and_while_loops_each_deny(self):
        self.assertEqual(self.exact("src/test/kotlin/CalcTest.kt", KOTLIN_LOOPS), loop_denial(6, 9))

    def test_csharp_foreach_loop_denies(self):
        self.assertEqual(self.exact("tests/CalcTests.cs", CSHARP_LOOP), loop_denial(6))

    def test_php_foreach_loop_denies(self):
        self.assertEqual(self.exact("tests/CalcTest.php", PHP_LOOP), loop_denial(6))

    def test_ruby_for_loop_denies(self):
        self.assertEqual(self.exact("test/calc_test.rb", RUBY_LOOP), loop_denial(5))

    def test_ruby_assertion_inside_a_brace_block_in_a_loop_passes(self):
        self.assertEqual(self.exact("test/calc_test.rb", RUBY_BRACE_BLOCK_LOOP), Outcome(NONE, ()))

    def test_swift_for_loop_denies(self):
        self.assertEqual(self.exact("Tests/CalcTests.swift", SWIFT_LOOP), loop_denial(5))

    def test_cpp_gtest_range_loop_denies(self):
        self.assertEqual(self.exact("test/calc_test.cpp", CPP_LOOP), loop_denial(4))


if __name__ == "__main__":
    unittest.main()
