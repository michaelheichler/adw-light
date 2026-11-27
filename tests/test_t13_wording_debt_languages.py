import unittest

from t13_wording_home import LOCK, PASSED, Outcome, WordingHome, lines, quoted, words
from t20_layer_home import CITATION, CONTEXT, LIBRARY_NEEDED, LIBRARY_READY, OBSERVED, session_of


NEW_TEST = "tests/new.test.js"
JEST_DEBT = "jest_debt"
CAPITALIZE_ONLY = "capitalize_only"
JUNIT_DEBT = "junit_debt"
BEHAVIOR = lines(CITATION, 'test("adds", () => {', "  expect(add(1, 2)).toBe(3);", "});")
EMPTY = words("the", "cart", "is", "empty")
JEST_WORDING = lines('test("shows", () => {', f"  expect(view()).toContain({quoted(EMPTY + ' right now')});", "});")
JEST_CAPITALIZE = lines(
    'test("caps", () => {', f"  expect(capitalize({quoted(EMPTY)})).toBe({quoted(EMPTY.capitalize())});", "});",
)
JUNIT_WORDING = lines(
    "class OldTest {", "  @Test", "  void ships() {",
    f"    assertEquals({quoted(words('The', 'order', 'is', 'on', 'its', 'way'))}, order.status());", "  }", "}",
)
NOTED = (Outcome(CONTEXT, ((LOCK, 2),)), ((LOCK, OBSERVED),))
PROJECTS = {
    JEST_DEBT: {"tests/old.test.js": JEST_WORDING},
    CAPITALIZE_ONLY: {"tests/old.test.js": JEST_CAPITALIZE},
    JUNIT_DEBT: {"tests/OldTest.java": JUNIT_WORDING},
}


class DebtCases:
    exact = False

    @classmethod
    def setUpClass(cls):
        cls.home = WordingHome.open(cls, cls.exact, PROJECTS)

    def new_test_after(self, project):
        self.home.start(self, project)
        outcome = self.home.outcome(self, NEW_TEST, BEHAVIOR, rules={LOCK}, project=project)
        session = session_of(self)
        noted = {(row["rule"], row["outcome"]) for row in self.home.ledger() if row.get("session_id") == session and row.get("rule") == LOCK}
        return outcome, tuple(sorted(noted))

    def test_jest_wording_test_on_disk_is_noted_beside_a_new_test(self):
        self.assertEqual(self.new_test_after(JEST_DEBT), NOTED)

    def test_jest_capitalize_style_test_on_disk_leaves_the_project_unlocked(self):
        self.assertEqual(self.new_test_after(CAPITALIZE_ONLY), (PASSED, ()))


class DebtWithTheFallbackLexer(DebtCases, unittest.TestCase):
    exact = False


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class DebtWithTheLanguageLayer(DebtCases, unittest.TestCase):
    exact = True

    def test_junit_wording_test_on_disk_is_noted_beside_a_new_test(self):
        self.assertEqual(self.new_test_after(JUNIT_DEBT), NOTED)


if __name__ == "__main__":
    unittest.main()
