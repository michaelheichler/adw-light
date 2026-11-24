import unittest

from t20_layer_home import (
    BLOCK, CONTEXT, DENY, IF, LIBRARY_NEEDED, LIBRARY_READY, LOOP, NONE, OBSERVED, LayerHome, Outcome,
)
from test_ported_test_rules import CASES


RUST_FILE = "src/lib.rs"
RUST_HEAD = "#[cfg(test)]\nmod tests {\n    #[test]\n    fn doubles() {\n"
RUST_LOOP = RUST_HEAD + "        for v in [1, 2] { assert_eq!(double(v), v * 2); }\n    }\n}\n"
RUST_BRANCH = RUST_HEAD + "        if ready() { assert!(first()); } else { assert!(second()); }\n    }\n}\n"
RUST_EXPECT_LOOP = RUST_HEAD + "        for v in [1, 2] { lookup(v).expect(\"present\"); }\n    }\n}\n"
TS_FILE = "src/calc.test.ts"
TS_LOOP = "test(\"doubles\", () => {\n  for (const v of [1, 2]) { expect(double(v)).toBe(v * 2); }\n});\n"
TS_BRANCH = "test(\"doubles\", () => {\n  if (ready()) { expect(first()).toBe(1); } else { expect(second()).toBe(2); }\n});\n"
PY_FILE = "tests/test_sample.py"
PY_LOOP = (
    "import unittest\n\n\nclass SampleTest(unittest.TestCase):\n    def test_sample(self):\n"
    "        for value in [1, 2]:\n            assert double(value) == value * 2\n"
)
LEXER_CASES = (
    ("rust_loop", RUST_FILE, RUST_LOOP, Outcome(DENY, ((LOOP, BLOCK, 5),))),
    ("rust_branch", RUST_FILE, RUST_BRANCH, Outcome(CONTEXT, ((IF, OBSERVED, 5),))),
    ("rust_expect_unwrap_in_loop", RUST_FILE, RUST_EXPECT_LOOP, Outcome(NONE, ())),
    ("typescript_loop", TS_FILE, TS_LOOP, Outcome(DENY, ((LOOP, BLOCK, 2),))),
    ("typescript_branch", TS_FILE, TS_BRANCH, Outcome(CONTEXT, ((IF, OBSERVED, 2),))),
)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class PythonCharacterizationUnderTheExactLayer(unittest.TestCase):
    def test_python_loop_logs_no_language_layer_row(self):
        hook = LayerHome(self, exact=True)
        hook.write(PY_FILE, PY_LOOP)
        self.assertEqual(hook.precision_reasons(), ())


class LexerLanguagesWithoutTheLibrary(unittest.TestCase):
    pass


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class LexerLanguagesUnderTheExactLayer(unittest.TestCase):
    pass


def characterization(case, exact):
    def test(self):
        self.assertEqual(LayerHome(self, exact=exact).outcome(case.path, case.content), Outcome(case.decision, case.rows))
    return test


def lexer_parity(path, content, expected, exact):
    def test(self):
        self.assertEqual(LayerHome(self, exact=exact).outcome(path, content), expected)
    return test


for _case in CASES:
    setattr(PythonCharacterizationUnderTheExactLayer, "test_exact_" + _case.name, characterization(_case, True))

for _name, _path, _content, _expected in LEXER_CASES:
    setattr(LexerLanguagesWithoutTheLibrary, "test_fallback_" + _name, lexer_parity(_path, _content, _expected, False))
    setattr(LexerLanguagesUnderTheExactLayer, "test_exact_" + _name, lexer_parity(_path, _content, _expected, True))


if __name__ == "__main__":
    unittest.main()
