import unittest

from t13_wording_home import ASSERTION, LOCATION, PASSED, WordingHome, denial, lines, quoted, words
from t20_layer_home import LIBRARY_NEEDED, LIBRARY_READY


TEST = "tests/order.test.js"
SHIPPED = words("Your", "order", "has", "shipped", "and", "will", "arrive", "soon.")
BASKET = words("Your", "basket", "is", "waiting", "for", "you")
EMPTY = words("the", "cart", "is", "empty")
SHOWN_EMPTY = words("The", "cart", "is", "empty")
BOUND = words("the", "cart", "is", "empty", "right", "now")
JSX = lines(
    "export function Order() {", "  return (", "    <section>", f"      <p>{SHIPPED}</p>", "    </section>", "  );", "}",
)
VUE = lines("<template>", f"  <p>{BASKET}</p>", "</template>")


def jest(*body):
    return lines('test("ships", () => {', *body, "});")


SHIPPED_ASSERTION = jest("  render(<Order />);", f"  expect(container.textContent).toContain({quoted(SHIPPED)});")
CAPITALIZE = jest(f"  expect(capitalize({quoted(EMPTY)})).toBe({quoted(SHOWN_EMPTY)});")


class JestWordingCases:
    exact = False

    def home(self):
        return WordingHome(self, self.exact)

    def verdict(self, content, home=None):
        return (home or self.home()).outcome(TEST, content)

    def test_to_contain_with_a_sentence_denies_on_the_expect_line(self):
        self.assertEqual(self.verdict(SHIPPED_ASSERTION), denial(ASSERTION, 3))

    def test_sentence_from_a_jsx_template_names_the_template_line_as_its_source(self):
        hook = self.home()
        hook.place("src/Order.jsx", JSX)
        outcome = hook.outcome(TEST, SHIPPED_ASSERTION)
        self.assertEqual((outcome, hook.matches(ASSERTION)), (denial(ASSERTION, 3), ("src/Order.jsx:4",)))

    def test_sentence_from_a_vue_template_names_the_template_line_as_its_source(self):
        hook = self.home()
        hook.place("src/Basket.vue", VUE)
        outcome = hook.outcome(TEST, jest(f"  expect(wrapper.text()).toContain({quoted(BASKET)});"))
        self.assertEqual((outcome, hook.matches(ASSERTION)), (denial(ASSERTION, 2), ("src/Basket.vue:2",)))

    def test_sentence_bound_to_a_constant_then_asserted_denies_on_the_expect_line(self):
        content = f"const EXPECTED = {quoted(BOUND)};\n" + jest("  expect(view()).toContain(EXPECTED);")
        self.assertEqual(self.verdict(content), denial(ASSERTION, 3))

    def test_machine_token_expected_values_pass(self):
        content = jest('  expect(code()).toBe("wording_assertion");', '  expect(status()).toBe("SHIPPED");')
        self.assertEqual(self.verdict(content), PASSED)

    def test_sentence_passed_only_as_input_passes(self):
        self.assertEqual(self.verdict(jest(f"  expect(parse({quoted(BOUND)})).toBe(true);")), PASSED)

    def test_capitalize_style_test_whose_expected_text_derives_from_its_input_passes(self):
        self.assertEqual(self.verdict(CAPITALIZE), PASSED)

    def test_capitalize_style_test_whose_expected_text_sits_in_source_denies_and_names_it(self):
        hook = self.home()
        hook.place("src/cart.js", f"export const EMPTY = {quoted(SHOWN_EMPTY)};\n")
        outcome = hook.outcome(TEST, CAPITALIZE)
        self.assertEqual((outcome, hook.matches(ASSERTION)), (denial(ASSERTION, 2), ("src/cart.js:1",)))

    def test_input_that_is_a_near_copy_of_the_expected_text_denies(self):
        content = jest(f"  expect(trim({quoted('  ' + EMPTY + '  ')})).toBe({quoted(EMPTY)});")
        self.assertEqual(self.verdict(content), denial(ASSERTION, 2))

    def test_second_call_under_test_removes_the_transformation_exception(self):
        content = jest("  const cart = setup();", f"  expect(capitalize({quoted(EMPTY)})).toBe({quoted(SHOWN_EMPTY)});")
        self.assertEqual(self.verdict(content), denial(ASSERTION, 3))

    def test_typed_out_file_and_line_denies_as_a_literal_location(self):
        content = jest('  expect(where()).toBe("tests/fixtures/sample.py:3");')
        self.assertEqual(self.verdict(content), denial(LOCATION, 2))


class JestWordingWithTheFallbackLexer(JestWordingCases, unittest.TestCase):
    exact = False


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class JestWordingWithTheLanguageLayer(JestWordingCases, unittest.TestCase):
    exact = True


if __name__ == "__main__":
    unittest.main()
