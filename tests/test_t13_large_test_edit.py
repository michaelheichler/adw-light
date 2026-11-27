import time
import unittest

from t13_wording_home import ASSERTION, Outcome, WordingHome, denial, lines, quoted, words
from t20_layer_home import CITATION, CONTEXT, LIBRARY_NEEDED, LIBRARY_READY


TEST = "src/__tests__/legacy.test.js"
LEGACY_PROJECT = "legacy"
HOOK_SECONDS = 10
CASES = 3_500
INHERITED_ONLY = Outcome(CONTEXT, ())


def jest_case(number):
    return lines(
        f"test('case {number}', () => {{", f"  const label = 'item-{number}';",
        f"  expect(format(label)).toBe('item-{number}');", "});", "",
    )


LEGACY = "".join(jest_case(number) for number in range(CASES))
EDITED = CITATION + "\n" + LEGACY.replace("item-0'", "item-zero'", 1).removesuffix("\n")
WORDED_CASE = CASES // 2
TOKEN_LINE = f"  expect(format(label)).toBe('item-{WORDED_CASE}');"
WORDED_LINE = "  expect(format(label)).toBe(" + quoted(words("The", "item", "could", "not", "be", "shown", "today.")) + ");"
WORDED = CITATION + "\n" + LEGACY.replace(TOKEN_LINE, WORDED_LINE, 1).removesuffix("\n")
WORDED_AT = WORDED.splitlines().index(WORDED_LINE) + 1


class LargeJestEditCases:
    exact = False

    @classmethod
    def setUpClass(cls):
        cls.home = WordingHome.open(cls, cls.exact, {LEGACY_PROJECT: {TEST: LEGACY}})

    def timed_edit(self):
        started = time.monotonic()
        outcome = self.home.outcome(self, TEST, EDITED, project=LEGACY_PROJECT)
        return outcome, time.monotonic() - started

    def test_fixture_is_a_legacy_file_of_at_least_300_kb(self):
        self.assertGreaterEqual(len(LEGACY.encode("utf-8")), 300_000)

    def test_edit_of_a_large_existing_jest_file_is_answered_within_the_hook_budget(self):
        outcome, seconds = self.timed_edit()
        self.assertEqual((outcome, seconds < HOOK_SECONDS), (INHERITED_ONLY, True))

    def test_edit_that_adds_a_sentence_assertion_deep_in_a_large_jest_file_is_denied_on_that_line(self):
        """Protection against regressions. The test fails when a real bug enters the code it exercises."""
        outcome = self.home.outcome(self, TEST, WORDED, project=LEGACY_PROJECT)
        self.assertEqual(outcome, denial(ASSERTION, WORDED_AT))


class LargeJestEditWithTheFallbackLexer(LargeJestEditCases, unittest.TestCase):
    exact = False


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class LargeJestEditWithTheLanguageLayer(LargeJestEditCases, unittest.TestCase):
    exact = True


if __name__ == "__main__":
    unittest.main()
