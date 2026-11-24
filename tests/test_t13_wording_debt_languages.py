import unittest

from t13_wording_home import LOCK, PASSED, WordingHome, denial, lines, quoted, words
from t20_layer_home import LIBRARY_NEEDED, LIBRARY_READY


NEW_TEST = "tests/new.test.js"
BEHAVIOR = lines('test("adds", () => {', "  expect(add(1, 2)).toBe(3);", "});")
EMPTY = words("the", "cart", "is", "empty")
JEST_WORDING = lines('test("shows", () => {', f"  expect(view()).toContain({quoted(EMPTY + ' right now')});", "});")
JEST_CAPITALIZE = lines(
    'test("caps", () => {', f"  expect(capitalize({quoted(EMPTY)})).toBe({quoted(EMPTY.capitalize())});", "});",
)
JUNIT_WORDING = lines(
    "class OldTest {", "  @Test", "  void ships() {",
    f"    assertEquals({quoted(words('The', 'order', 'is', 'on', 'its', 'way'))}, order.status());", "  }", "}",
)


class DebtCases:
    exact = False

    def new_test_after(self, old_path, old_content):
        hook = WordingHome(self, self.exact)
        hook.place(old_path, old_content)
        hook.start()
        return hook.outcome(NEW_TEST, BEHAVIOR, rules={LOCK})

    def test_jest_wording_test_on_disk_locks_a_new_test(self):
        self.assertEqual(self.new_test_after("tests/old.test.js", JEST_WORDING), denial(LOCK, 1))

    def test_jest_capitalize_style_test_on_disk_leaves_the_project_unlocked(self):
        self.assertEqual(self.new_test_after("tests/old.test.js", JEST_CAPITALIZE), PASSED)


class DebtWithTheFallbackLexer(DebtCases, unittest.TestCase):
    exact = False


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class DebtWithTheLanguageLayer(DebtCases, unittest.TestCase):
    exact = True

    def test_junit_wording_test_on_disk_locks_a_new_test(self):
        self.assertEqual(self.new_test_after("tests/OldTest.java", JUNIT_WORDING), denial(LOCK, 1))


if __name__ == "__main__":
    unittest.main()
