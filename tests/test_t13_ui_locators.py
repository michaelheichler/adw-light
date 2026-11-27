import unittest

from t13_wording_home import LOCATOR, PASSED, Outcome, WordingHome, denial, lines, quoted, words
from t20_layer_home import CITATION, LIBRARY_NEEDED, LIBRARY_READY, decision_of


SPEC = "tests/ui.spec.ts"
CYPRESS_SPEC = "cypress/e2e/cart.cy.js"
OTHER_AGENT = "general-purpose"
TEST_GATE = "test_write_gate"
WELCOME = words("Welcome", "back", "to", "the", "store")
ADD = words("Add", "it", "to", "the", "cart")


def playwright(locator):
    return lines(
        CITATION, 'test("greets", async ({ page }) => {', f"  await {locator}.click();", '  expect(page.url()).toBe("/cart");', "});",
    )


def cypress(locator):
    return lines(CITATION, 'it("adds", () => {', f"  {locator}.click();", '  expect(cart.size()).toBe(1);', "});")


class LocatorCases:
    exact = False

    @classmethod
    def setUpClass(cls):
        cls.home = WordingHome.open(cls, cls.exact)

    def verdict(self, relative, content):
        return self.home.outcome(self, relative, content)

    def test_get_by_text_with_prose_denies_on_the_locator_line(self):
        self.assertEqual(self.verdict(SPEC, playwright(f"page.getByText({quoted(WELCOME)})")), denial(LOCATOR, 3))

    def test_text_selector_with_prose_denies(self):
        self.assertEqual(self.verdict(SPEC, playwright(f"page.locator({quoted('text=' + ADD)})")), denial(LOCATOR, 3))

    def test_has_text_selector_with_prose_denies(self):
        selector = quoted(f"button:has-text('{ADD}')")
        self.assertEqual(self.verdict(SPEC, playwright(f"page.locator({selector})")), denial(LOCATOR, 3))

    def test_xpath_text_selector_with_prose_denies(self):
        selector = quoted(f"//button[text()='{ADD}']")
        self.assertEqual(self.verdict(SPEC, playwright(f"page.locator({selector})")), denial(LOCATOR, 3))

    def test_testing_library_get_by_text_with_prose_denies(self):
        content = lines(
            CITATION, 'test("adds", () => {', f"  fireEvent.click(screen.getByText({quoted(ADD)}));",
            "  expect(cart.size()).toBe(1);", "});",
        )
        self.assertEqual(self.verdict("tests/cart.test.js", content), denial(LOCATOR, 3))

    def test_role_and_test_id_locators_pass(self):
        content = lines(
            CITATION, 'test("signs in", async ({ page }) => {',
            f"  await page.getByRole(\"button\", {{ name: {quoted(WELCOME)} }}).click();",
            '  await page.getByTestId("add-to-cart").click();', '  expect(page.url()).toBe("/cart");', "});",
        )
        self.assertEqual(self.verdict(SPEC, content), PASSED)

    def test_single_word_label_passes(self):
        self.assertEqual(self.verdict(SPEC, playwright('page.getByText("Checkout")')), PASSED)

    def test_cypress_contains_with_prose_denies(self):
        self.assertEqual(self.verdict("tests/cart.cy.js", cypress(f"cy.contains({quoted(ADD)})")), denial(LOCATOR, 3))

    def test_cypress_contains_in_the_default_e2e_folder_denies(self):
        self.assertEqual(self.verdict(CYPRESS_SPEC, cypress(f"cy.contains({quoted(ADD)})")), denial(LOCATOR, 3))

    def test_cypress_typescript_spec_in_the_default_e2e_folder_denies(self):
        content = cypress(f"cy.contains({quoted(ADD)})")
        self.assertEqual(self.verdict("cypress/e2e/cart.cy.ts", content), denial(LOCATOR, 3))

    def test_cypress_data_test_selector_in_the_default_e2e_folder_passes(self):
        self.assertEqual(self.verdict(CYPRESS_SPEC, cypress('cy.get("[data-test=add]")')), PASSED)


class LocatorsWithTheFallbackLexer(LocatorCases, unittest.TestCase):
    exact = False


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class LocatorsWithTheLanguageLayer(LocatorCases, unittest.TestCase):
    exact = True

    def test_cypress_module_spec_in_the_default_e2e_folder_denies(self):
        content = cypress(f"cy.contains({quoted(ADD)})")
        self.assertEqual(self.verdict("cypress/e2e/cart.cy.mjs", content), denial(LOCATOR, 3))


class CypressSpecsAreTestFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = WordingHome.open(cls, exact=False)

    def removal(self, relative):
        fields = {"agent_type": OTHER_AGENT, "tool_name": "Bash", "tool_input": {"command": "rm " + relative}}
        response = self.home.hook(self, "PreToolUse", fields)
        rows = tuple(sorted({(row["rule"], row["line"]) for row in self.home.report_rows(self) if row["rule"] == TEST_GATE}))
        return Outcome(decision_of(response), rows)

    def test_other_agent_removing_a_cypress_spec_is_denied_by_the_test_gate(self):
        self.assertEqual(self.removal(CYPRESS_SPEC), denial(TEST_GATE, 1))

    def test_other_agent_removing_a_typescript_cypress_spec_is_denied_by_the_test_gate(self):
        self.assertEqual(self.removal("cypress/e2e/cart.cy.ts"), denial(TEST_GATE, 1))

    def test_other_agent_removing_a_cypress_support_file_passes(self):
        self.assertEqual(self.removal("cypress/support/commands.js"), PASSED)


if __name__ == "__main__":
    unittest.main()
