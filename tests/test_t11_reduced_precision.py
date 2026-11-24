import json
import unittest

from t11_language_home import (
    DENY, GO_RAW, LIBRARY_NEEDED, LIBRARY_READY, HookHome, comment_findings,
)


GO_FILE = "main.go"
GO_LINE = 4
OLD_VERSION = "0.0.1"


def precision_row(hook, reason):
    return {"event": "reduced_precision", "path": str(hook.repo / GO_FILE), "language": "go", "reason": reason}


class ReducedPrecisionFallback(unittest.TestCase):
    def test_missing_library_scans_with_the_lexer_and_logs_the_reason(self):
        hook = HookHome(self)
        verdict = hook.verdict(GO_FILE, GO_RAW)
        self.assertEqual((verdict, hook.precision_rows()),
                         ((DENY, comment_findings(GO_LINE)), [precision_row(hook, "library_missing")]))

    def test_unpinned_library_version_is_not_used(self):
        hook = HookHome(self).stub_library(OLD_VERSION)
        verdict = hook.verdict(GO_FILE, GO_RAW)
        self.assertEqual((verdict, hook.precision_rows()),
                         ((DENY, comment_findings(GO_LINE)), [precision_row(hook, "version_mismatch")]))

    def test_fallback_answers_with_json_and_exit_zero(self):
        call = HookHome(self).write(GO_FILE, GO_RAW)
        self.assertEqual((call.returncode, type(json.loads(call.stdout))), (0, dict))


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class BlockedGrammarDownload(unittest.TestCase):
    def test_blocked_download_scans_with_the_lexer_and_logs_the_reason(self):
        hook = HookHome(self).link_library().block_grammar_cache()
        verdict = hook.verdict(GO_FILE, GO_RAW)
        self.assertEqual((verdict, hook.precision_rows()),
                         ((DENY, comment_findings(GO_LINE)), [precision_row(hook, "download_failed")]))

    def test_blocked_download_answers_with_json_and_exit_zero(self):
        call = HookHome(self).link_library().block_grammar_cache().write(GO_FILE, GO_RAW)
        self.assertEqual((call.returncode, type(json.loads(call.stdout))), (0, dict))

    def test_cached_grammar_logs_no_reduced_precision(self):
        hook = HookHome(self).link_library().link_grammars()
        hook.write(GO_FILE, GO_RAW)
        self.assertEqual(hook.precision_rows(), [])


if __name__ == "__main__":
    unittest.main()
