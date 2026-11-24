import time
import unittest

from t13_wording_home import CONTEXT, Outcome, WordingHome, lines
from t20_layer_home import LIBRARY_NEEDED, LIBRARY_READY


TEST = "src/__tests__/legacy.test.js"
HOOK_SECONDS = 10
CASES = 3_500
INHERITED_ONLY = Outcome(CONTEXT, ())


def jest_case(number):
    return lines(
        f"test('case {number}', () => {{", f"  const label = 'item-{number}';",
        f"  expect(format(label)).toBe('item-{number}');", "});", "",
    )


LEGACY = "".join(jest_case(number) for number in range(CASES))
EDITED = LEGACY.replace("item-0'", "item-zero'", 1)


class LargeJestEditCases:
    exact = False

    def timed_edit(self):
        home = WordingHome(self, self.exact)
        home.place(TEST, LEGACY)
        started = time.monotonic()
        outcome = home.outcome(TEST, EDITED)
        return outcome, time.monotonic() - started

    def test_fixture_is_a_legacy_file_of_at_least_300_kb(self):
        self.assertGreaterEqual(len(LEGACY.encode("utf-8")), 300_000)

    def test_edit_of_a_large_existing_jest_file_is_answered_within_the_hook_budget(self):
        outcome, seconds = self.timed_edit()
        self.assertEqual((outcome, seconds < HOOK_SECONDS), (INHERITED_ONLY, True))


class LargeJestEditWithTheFallbackLexer(LargeJestEditCases, unittest.TestCase):
    exact = False


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class LargeJestEditWithTheLanguageLayer(LargeJestEditCases, unittest.TestCase):
    exact = True


if __name__ == "__main__":
    unittest.main()
