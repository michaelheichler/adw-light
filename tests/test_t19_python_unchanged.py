import unittest

from t19_language_home import ALLOWED, LIBRARY_NEEDED, LIBRARY_READY, blocked, exact_verdict, fallback_verdict


HOLLOW_PATH = "tests/test_x.py"
HOLLOW_TEST = "def test_x():\n    run()\n"
ASSERTING_TEST = "def test_x():\n    assert run() == 1\n"
LONG_PATH = "src/mod.py"
LONG_FUNCTION = "def long():\n" + "".join(f"    x{index} = {index}\n" for index in range(85)) + "    return 0\n"
HOLLOW = blocked(("hollow_test", 1))
TOO_LONG = blocked(("function_too_long", 1))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class PythonWithTheLibraryInstalled(unittest.TestCase):
    def test_hollow_python_test_is_blocked_at_its_def_line(self):
        self.assertEqual(exact_verdict(self, HOLLOW_PATH, HOLLOW_TEST), HOLLOW)

    def test_asserting_python_test_passes(self):
        self.assertEqual(exact_verdict(self, HOLLOW_PATH, ASSERTING_TEST), ALLOWED)

    def test_long_python_function_is_blocked_at_its_def_line(self):
        self.assertEqual(exact_verdict(self, LONG_PATH, LONG_FUNCTION), TOO_LONG)


class PythonWithoutTheLibrary(unittest.TestCase):
    def test_hollow_python_test_is_blocked_at_its_def_line(self):
        self.assertEqual(fallback_verdict(self, HOLLOW_PATH, HOLLOW_TEST), HOLLOW)

    def test_asserting_python_test_passes(self):
        self.assertEqual(fallback_verdict(self, HOLLOW_PATH, ASSERTING_TEST), ALLOWED)

    def test_long_python_function_is_blocked_at_its_def_line(self):
        self.assertEqual(fallback_verdict(self, LONG_PATH, LONG_FUNCTION), TOO_LONG)


if __name__ == "__main__":
    unittest.main()
