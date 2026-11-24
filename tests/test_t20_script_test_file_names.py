import unittest

from t20_layer_home import BLOCK, DENY, LIBRARY_NEEDED, LIBRARY_READY, LOOP, NONE, LayerHome, Outcome


BRACELESS_LOOP = "test(\"doubles\", () => {\n  for (const v of [1, 2]) expect(double(v)).toBe(v * 2);\n});\n"
LOOP_DENIAL = Outcome(DENY, ((LOOP, BLOCK, 2),))
NO_FINDING = Outcome(NONE, ())
TEST_NAMED_FILES = (
    ("test_ts", "src/calc.test.ts"),
    ("spec_ts", "src/calc.spec.ts"),
    ("test_js", "src/calc.test.js"),
    ("spec_js", "src/calc.spec.js"),
    ("test_tsx", "src/calc.test.tsx"),
    ("camel_case_test_declaration", "src/CalcTest.d.ts"),
)
PLAIN_FILES = (
    ("plain_module", "src/calc.ts"),
    ("test_inside_a_longer_word", "src/latest.config.js"),
    ("test_at_the_end_of_a_longer_word", "src/contest.ts"),
    ("plain_declaration", "src/calc.d.ts"),
)


def outcome(case, relative, exact):
    return LayerHome(case, exact=exact).outcome(relative, BRACELESS_LOOP)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class TestNamedScriptFilesOutsideATestFolderReachTheLayer(unittest.TestCase):
    pass


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class PlainScriptFilesGetNoTestUnitsFromTheLayer(unittest.TestCase):
    pass


class BracelessLoopWithoutTheLibrary(unittest.TestCase):
    def test_lexer_alone_misses_the_one_statement_loop_body(self):
        self.assertEqual(outcome(self, "src/calc.test.ts", exact=False), NO_FINDING)


def denies_on_the_assertion_line(relative):
    def test(self):
        self.assertEqual(outcome(self, relative, exact=True), LOOP_DENIAL)
    return test


def passes(relative):
    def test(self):
        self.assertEqual(outcome(self, relative, exact=True), NO_FINDING)
    return test


for _name, _relative in TEST_NAMED_FILES:
    setattr(
        TestNamedScriptFilesOutsideATestFolderReachTheLayer,
        "test_braceless_loop_assertion_denies_in_" + _name, denies_on_the_assertion_line(_relative),
    )

for _name, _relative in PLAIN_FILES:
    setattr(PlainScriptFilesGetNoTestUnitsFromTheLayer, "test_braceless_loop_passes_in_" + _name, passes(_relative))


if __name__ == "__main__":
    unittest.main()
