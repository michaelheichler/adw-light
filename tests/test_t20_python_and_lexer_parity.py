import unittest

from t20_layer_home import (
    BLOCK, CITATION, CONTEXT, DENY, IF, LIBRARY_NEEDED, LIBRARY_READY, LOOP, NONE, OBSERVED, PRINCIPLE, LayerHome, Outcome,
)
from test_ported_test_rules import CASES


RUST_FILE = "src/lib.rs"
RUST_HEAD = "#[cfg(test)]\nmod tests {\n    #[test]\n    fn doubles() {\n"
RUST_LOOP = RUST_HEAD + "        for v in [1, 2] { assert_eq!(double(v), v * 2); }\n    }\n}\n"
RUST_BRANCH = RUST_HEAD + "        if ready() { assert!(first()); } else { assert!(second()); }\n    }\n}\n"
RUST_EXPECT_LOOP = RUST_HEAD + "        for v in [1, 2] { lookup(v).expect(\"present\"); }\n    }\n}\n"
TS_FILE = "src/calc.test.ts"
TS_LOOP = CITATION + "\ntest(\"doubles\", () => {\n  for (const v of [1, 2]) { expect(double(v)).toBe(v * 2); }\n});\n"
TS_BRANCH = (
    CITATION + "\ntest(\"doubles\", () => {\n  if (ready()) { expect(first()).toBe(1); } else { expect(second()).toBe(2); }\n});\n"
)
PY_FILE = "tests/test_sample.py"
DOCSTRING = '"""' + PRINCIPLE + '"""'
PY_LOOP = (
    "import unittest\n\n\nclass SampleTest(unittest.TestCase):\n    def test_sample(self):\n        " + DOCSTRING + "\n"
    "        for value in [1, 2]:\n            assert double(value) == value * 2\n"
)
LEXER_CASES = (
    ("rust_loop", RUST_FILE, RUST_LOOP, Outcome(DENY, ((LOOP, BLOCK, 5),))),
    ("rust_branch", RUST_FILE, RUST_BRANCH, Outcome(CONTEXT, ((IF, OBSERVED, 5),))),
    ("rust_expect_unwrap_in_loop", RUST_FILE, RUST_EXPECT_LOOP, Outcome(NONE, ())),
    ("typescript_loop", TS_FILE, TS_LOOP, Outcome(DENY, ((LOOP, BLOCK, 3),))),
    ("typescript_branch", TS_FILE, TS_BRANCH, Outcome(CONTEXT, ((IF, OBSERVED, 3),))),
)


def cited(case):
    lines = case.content.splitlines(keepends=True)
    header = next(index for index, line in enumerate(lines) if line.lstrip().startswith("def test_")) + 1
    indent = " " * (len(lines[header - 1]) - len(lines[header - 1].lstrip()) + 4)
    content = "".join((*lines[:header], indent + DOCSTRING + "\n", *lines[header:]))
    rows = tuple((rule, outcome, line + 1 if line > header else line) for rule, outcome, line in case.rows)
    return case._replace(content=content, rows=rows)


class LayerCases:
    exact = True

    @classmethod
    def setUpClass(cls):
        cls.home = LayerHome.open(cls, cls.exact)

    def outcome(self, relative, content):
        return self.home.outcome(self, relative, content)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class PythonCharacterizationUnderTheExactLayer(LayerCases, unittest.TestCase):
    exact = True

    def test_python_loop_logs_no_language_layer_row(self):
        self.home.write(self, PY_FILE, PY_LOOP)
        self.assertEqual(self.home.precision_reasons(PY_FILE), ())


class LexerLanguagesWithoutTheLibrary(LayerCases, unittest.TestCase):
    exact = False


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class LexerLanguagesUnderTheExactLayer(LayerCases, unittest.TestCase):
    exact = True


def characterization(case):
    def test(self):
        self.assertEqual(self.outcome(case.path, case.content), Outcome(case.decision, case.rows))
    return test


def lexer_parity(path, content, expected):
    def test(self):
        self.assertEqual(self.outcome(path, content), expected)
    return test


for _case in (*(cited(case) for case in CASES if case.path.endswith(".py")), *(case for case in CASES if not case.path.endswith(".py"))):
    setattr(PythonCharacterizationUnderTheExactLayer, "test_exact_" + _case.name, characterization(_case))

for _name, _path, _content, _expected in LEXER_CASES:
    setattr(LexerLanguagesWithoutTheLibrary, "test_fallback_" + _name, lexer_parity(_path, _content, _expected))
    setattr(LexerLanguagesUnderTheExactLayer, "test_exact_" + _name, lexer_parity(_path, _content, _expected))


if __name__ == "__main__":
    unittest.main()
