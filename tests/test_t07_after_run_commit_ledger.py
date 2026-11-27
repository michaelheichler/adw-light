import json
import unittest

from after_commit_home import CLEAN_TEXT, FLAGGED_MESSAGE, FLAGGED_TEXT, NOTES, AfterCommitCase, commit_project
from after_run_home import CAUGHT, CODEX, CONFIG, LET_THROUGH
from lib.findings import Finding


REPO = "repo"
OBSERVED = "observed"
PROSE_RULE = "utilize"
OBSERVE_PROSE = {CONFIG: json.dumps({"rule_gates": {PROSE_RULE: "observe"}})}
FLAGGED_COMMIT = 'git commit -m "We utilize this tool for the build."'
VARIABLE_ALIAS_COMMIT = 'git -c alias.zap="$X" zap'
SCRIPT_COMMIT = "printf x > tools/run.sh; bash tools/run.sh"
BACKGROUND = "sleep 5 &"
SNAPSHOT_FAILED = "snapshot_failed"
MESSAGE_PATH = "commit_message.md"


class CommitLedgerHome(AfterCommitCase):
    projects = {REPO: commit_project(), OBSERVED: commit_project(OBSERVE_PROSE)}

    def observed_commit(self, effect):
        self.start(OBSERVED)
        self.pair(OBSERVED, FLAGGED_COMMIT, effect)
        return self.finding_rules()

    def flagged_on_codex(self, command):
        return self.run_and_stop(REPO, command, self.committing(REPO, message=FLAGGED_MESSAGE), CODEX)

    def failures(self):
        return [row["event"] for row in self.timing_rows() if row["rule"] == SNAPSHOT_FAILED]

    def reported_rows(self):
        reports = sorted((self.state / "reports").glob(self.session() + "-*.json"))
        return [row for report in reports for row in json.loads(report.read_text(encoding="utf-8"))]


class ObservedCommitFindings(CommitLedgerHome):
    def test_commit_whose_bytes_match_the_receipt_is_not_scanned_again(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.observed_commit(self.committing(OBSERVED, message=FLAGGED_MESSAGE)), [])

    def test_commit_whose_bytes_differ_from_the_receipt_is_scanned(self):
        """The test fails when a real bug enters the code it exercises."""
        differing = self.committing(OBSERVED, {NOTES: CLEAN_TEXT}, FLAGGED_MESSAGE)
        self.assertEqual(self.observed_commit(differing), [PROSE_RULE])


class CommitRowsReachTheLedger(CommitLedgerHome):
    def test_commit_finding_on_codex_post_tool_use_is_logged_without_a_snapshot_failure(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.flagged_on_codex(VARIABLE_ALIAS_COMMIT)
        self.assertEqual((self.finding_rules(), self.failures()), ([PROSE_RULE], []))

    def test_commit_from_a_background_job_is_logged_at_the_first_stop_without_a_snapshot_failure(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.start(REPO)
        self.pair(REPO, BACKGROUND)
        self.make_commit(REPO, message=FLAGGED_MESSAGE)
        verdict = self.stop(REPO)
        self.assertEqual((verdict, self.finding_rules(), self.failures()), (CAUGHT, [PROSE_RULE], []))

    def test_commit_rows_carry_only_finding_keys(self):
        """The test fails when a real bug enters the code it exercises."""
        self.run_and_stop(REPO, SCRIPT_COMMIT, self.committing(REPO, {NOTES: FLAGGED_TEXT}, FLAGGED_MESSAGE))
        found = {(Finding.from_dict(row).path, row["rule"]) for row in self.reported_rows()}
        self.assertEqual(found, {(MESSAGE_PATH, PROSE_RULE), (NOTES, PROSE_RULE)})


class CodexCommitScan(CommitLedgerHome):
    def test_variable_alias_commit_on_codex_blocks_its_flagged_commit_at_post_tool_use(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_on_codex(VARIABLE_ALIAS_COMMIT), (LET_THROUGH, CAUGHT, CAUGHT))

    def test_script_commit_on_codex_blocks_its_flagged_commit_at_post_tool_use(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_on_codex(SCRIPT_COMMIT), (LET_THROUGH, CAUGHT, CAUGHT))

    def test_clean_commit_on_codex_releases(self):
        """Feed input, check the return value."""
        clean = self.committing(REPO, {NOTES: CLEAN_TEXT})
        self.assertEqual(self.run_and_stop(REPO, VARIABLE_ALIAS_COMMIT, clean, CODEX), (LET_THROUGH, LET_THROUGH, None))


if __name__ == "__main__":
    unittest.main()
