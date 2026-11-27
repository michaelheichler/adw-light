import json
import unittest

from after_commit_home import CLEAN_TEXT, FLAGGED_MESSAGE, NOTES, AfterCommitCase, commit_project
from after_run_home import CODEX, CONFIG, LET_THROUGH


REPO = "repo"
OBSERVED = "observed"
PROSE_RULE = "utilize"
OBSERVE_PROSE = {CONFIG: json.dumps({"rule_gates": {PROSE_RULE: "observe"}})}
FLAGGED_COMMIT = 'git commit -m "We utilize this tool for the build."'
VARIABLE_ALIAS_COMMIT = 'git -c alias.zap="$X" zap'


class CommitLedgerHome(AfterCommitCase):
    projects = {REPO: commit_project(), OBSERVED: commit_project(OBSERVE_PROSE)}

    def observed_commit(self, effect):
        self.start(OBSERVED)
        self.pair(OBSERVED, FLAGGED_COMMIT, effect)
        return self.finding_rules()


class ObservedCommitFindings(CommitLedgerHome):
    def test_commit_whose_bytes_match_the_receipt_is_not_scanned_again(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.observed_commit(self.committing(OBSERVED, message=FLAGGED_MESSAGE)), [])


class CodexCommitScan(CommitLedgerHome):


    def test_clean_commit_on_codex_releases(self):
        """Feed input, check the return value."""
        clean = self.committing(REPO, {NOTES: CLEAN_TEXT})
        self.assertEqual(self.run_and_stop(REPO, VARIABLE_ALIAS_COMMIT, clean, CODEX), (LET_THROUGH, LET_THROUGH, None))


if __name__ == "__main__":
    unittest.main()
