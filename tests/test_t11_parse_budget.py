import json
import time
import unittest
from pathlib import Path

from t11_language_home import BUNDLE_NEEDED, BUNDLE_READY, LIBRARY_NEEDED, LIBRARY_READY, HookHome


PRECISE_CHARS = 262_144
SHARED_FILE_CHARS = 100_000
SINGLE_PATCH_CHARS = 150_000
LARGE_PATCH_CHARS = 200_000
REMAINDER_PATCH_CHARS = 62_000
SHARED_FETCH_SECONDS = 1.5
SIZE_BUDGET = "size_budget"
DOWNLOAD_PENDING = "download_pending"
SHARED_FILES = (("first.go", "f"), ("second.go", "g"), ("third.go", "h"))
GO_HEAD = "package main\n\n"
LANGUAGES = ("go", "ruby", "rust")
SOURCES = {"main.go": "package main\n\nfunc main() {}\n", "main.rb": "def main\nend\n", "main.rs": "fn main() {}\n"}


def go_source(chars, function="f"):
    line = f"func {function}() {{}}\n"
    body = GO_HEAD + line * ((chars - len(GO_HEAD)) // len(line))
    return body + "\n" * (chars - len(body))


def fallback_by_file(rows):
    return {Path(row["path"]).name: row["reason"] for row in rows}


def fallbacks(rows):
    return sorted((row["language"], row["reason"]) for row in rows)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class PreciseParseSizeLimit(unittest.TestCase):
    def test_go_write_one_char_over_the_limit_answers_with_json_and_falls_back_for_size(self):
        hook = HookHome(self).link_library().link_grammars()
        call = hook.write("big.go", go_source(PRECISE_CHARS + 1))
        self.assertEqual((call.returncode, type(json.loads(call.stdout)), fallbacks(hook.precision_rows())),
                         (0, dict, [("go", SIZE_BUDGET)]))

    def test_go_write_at_the_limit_is_parsed_by_tree_sitter(self):
        hook = HookHome(self).link_library().link_grammars()
        hook.write("edge.go", go_source(PRECISE_CHARS))
        self.assertEqual(hook.precision_rows(), [])

    def test_files_in_one_call_share_the_limit_so_a_small_later_file_falls_back_for_size(self):
        hook = HookHome(self).link_library().link_grammars()
        hook.patch({name: go_source(SHARED_FILE_CHARS, function) for name, function in SHARED_FILES})
        self.assertEqual(fallback_by_file(hook.precision_rows()), {"third.go": SIZE_BUDGET})

    def test_go_file_added_by_patch_over_half_the_limit_is_parsed_by_tree_sitter(self):
        hook = HookHome(self).link_library().link_grammars()
        hook.patch({"one.go": go_source(SINGLE_PATCH_CHARS)})
        self.assertEqual(hook.precision_rows(), [])

    def test_patch_files_that_together_fit_the_limit_are_both_parsed_by_tree_sitter(self):
        hook = HookHome(self).link_library().link_grammars()
        hook.patch({"large.go": go_source(LARGE_PATCH_CHARS, "f"), "small.go": go_source(REMAINDER_PATCH_CHARS, "g")})
        self.assertEqual(hook.precision_rows(), [])


@unittest.skipUnless(BUNDLE_READY, BUNDLE_NEEDED)
class SharedGrammarFetchDeadline(unittest.TestCase):
    def test_call_needing_three_missing_grammars_waits_one_fetch_deadline_and_logs_each_pending(self):
        """Compute expected values independently."""
        hook = HookHome(self, SHARED_FETCH_SECONDS).link_library().bundle_without_grammars().hold_download_lock(*LANGUAGES)
        started = time.monotonic()
        hook.patch(SOURCES)
        elapsed = time.monotonic() - started
        self.assertEqual(fallbacks(hook.precision_rows()), [(language, DOWNLOAD_PENDING) for language in LANGUAGES])
        self.assertLess(elapsed, 2 * SHARED_FETCH_SECONDS)

    def test_grammars_fetched_in_the_background_are_used_by_the_next_call(self):
        hook = HookHome(self).link_library().bundle_without_grammars().hold_download_lock(*LANGUAGES)
        hook.patch(SOURCES)
        hook.release_download_lock()
        logged = len(hook.wait_for_grammars(*LANGUAGES).precision_rows())
        hook.patch(SOURCES)
        self.assertEqual(hook.precision_rows()[logged:], [])


if __name__ == "__main__":
    unittest.main()
