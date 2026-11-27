import json
import unittest

from after_commit_home import (
    CLEAN_MESSAGE, CLEAN_TEXT, FLAGGED_MESSAGE, FLAGGED_TEXT, NOTES, AfterCommitCase, commit_project,
)
from after_run_home import CAUGHT, LET_THROUGH


REPO = "repo"
LINK = "linked"
DENY = "deny"
PROSE_RULE = "utilize"
MESSAGE_PATH = "commit_message.md"
OLD_CAP = 8
SAFE_COMMIT = 'git commit -m "Add notes."'
PATHSPEC_COMMIT = SAFE_COMMIT + " " + NOTES
VARIABLE_ALIAS_COMMIT = 'git -c alias.zap="$X" zap'
NO_ROWS = frozenset()


def flagged_rows(*paths):
    return frozenset((path, PROSE_RULE) for path in paths)


class CommitScanHome(AfterCommitCase):
    projects = {REPO: commit_project()}
    links = {LINK: REPO}

    def gate_rows(self):
        rows = self.ledger_rows_of("pre_commit")
        return frozenset((row["path"], row["rule"]) for row in rows if row.get("outcome") == "block")

    def ledger_rows_of(self, hook):
        ledger = self.state / "ledger" / "ledger.jsonl"
        lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
        rows = (json.loads(line) for line in lines if line.strip())
        return [row for row in rows if row.get("session_id") == self.session() and row.get("hook") == hook]

    def before_the_run(self, command, cwd=None):
        self.start(REPO)
        decision = self.pre(REPO, command, identity={"cwd": str(cwd)} if cwd else None)
        return decision, self.gate_rows()

    def gate_findings(self, command):
        self.start(REPO)
        return self.pre(REPO, command), self.report_rules()

    def fixed_later(self, first, second):
        self.start(REPO)
        _, blocked, _ = self.pair(REPO, "bash tools/run.sh", first)
        _, released, _ = self.pair(REPO, "bash tools/run.sh", second, call="c2")
        return blocked, released, self.stop(REPO)

    def overflow_notice(self, times):
        self.start(REPO)
        _, _, posted = self.pair(REPO, "bash tools/run.sh", self.committing(REPO, times=times))
        return "additionalContext" in posted.get("hookSpecificOutput", {})

    def oldest_flagged_of(self, count):
        def commits():
            self.make_commit(REPO, message=FLAGGED_MESSAGE)
            self.committing(REPO, times=count - 1)()
        self.start(REPO)
        _, verdict, posted = self.pair(REPO, "bash tools/run.sh", commits)
        return verdict, "additionalContext" in posted.get("hookSpecificOutput", {})


class ReceiptOfThePreRunScan(CommitScanHome):
    def test_commit_whose_message_differs_from_the_passed_receipt_is_scanned(self):
        """The test fails when a real bug enters the code it exercises."""
        flagged = self.committing(REPO, message=FLAGGED_MESSAGE)
        self.assertEqual(self.run_and_stop(REPO, SAFE_COMMIT, flagged), (LET_THROUGH, CAUGHT, CAUGHT))

    def test_commit_whose_files_differ_from_the_passed_receipt_is_scanned(self):
        """The test fails when a real bug enters the code it exercises."""
        flagged = self.committing(REPO, {NOTES: FLAGGED_TEXT}, CLEAN_MESSAGE)
        self.assertEqual(self.run_and_stop(REPO, SAFE_COMMIT, flagged), (LET_THROUGH, CAUGHT, CAUGHT))

    def test_commit_that_matches_the_passed_receipt_releases(self):
        """Feed input, check the return value."""
        matching = self.committing(REPO, message=CLEAN_MESSAGE)
        self.assertEqual(self.run_and_stop(REPO, SAFE_COMMIT, matching), (LET_THROUGH, LET_THROUGH, None))


class CommitCap(CommitScanHome):
    def test_oldest_of_more_new_commits_than_the_old_cap_is_scanned_and_blocks_without_a_note(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.oldest_flagged_of(OLD_CAP + 1), (CAUGHT, False))

    def test_new_commits_up_to_the_old_cap_add_no_note(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.assertFalse(self.overflow_notice(OLD_CAP))


class ReleaseAfterAFix(CommitScanHome):
    def test_amend_that_fixes_the_message_releases_the_block(self):
        """The test fails when a real bug enters the code it exercises."""
        flagged = self.committing(REPO, message=FLAGGED_MESSAGE)
        amended = self.committing(REPO, message=CLEAN_MESSAGE, amend=True)
        self.assertEqual(self.fixed_later(flagged, amended), (CAUGHT, LET_THROUGH, None))

    def test_fixing_commit_on_top_of_a_flagged_file_releases_the_block(self):
        """The test fails when a real bug enters the code it exercises."""
        flagged = self.committing(REPO, {NOTES: FLAGGED_TEXT})
        fixing = self.committing(REPO, {NOTES: CLEAN_TEXT})
        self.assertEqual(self.fixed_later(flagged, fixing), (CAUGHT, LET_THROUGH, None))

    def test_commit_that_keeps_the_flagged_file_keeps_the_block(self):
        """The test fails when a real bug enters the code it exercises."""
        flagged = self.committing(REPO, {NOTES: FLAGGED_TEXT})
        unrelated = self.committing(REPO, {"other.md": CLEAN_TEXT})
        self.assertEqual(self.fixed_later(flagged, unrelated), (CAUGHT, CAUGHT, CAUGHT))


class SymlinkedWorkingFolder(CommitScanHome):
    def test_pathspec_commit_from_a_symlinked_folder_scans_the_named_file(self):
        """The test fails when a real bug enters the code it exercises."""
        self.change(REPO, NOTES, FLAGGED_TEXT)
        self.assertEqual(self.before_the_run(PATHSPEC_COMMIT, self.root / LINK), (DENY, flagged_rows(NOTES)))

    def test_clean_pathspec_commit_from_a_symlinked_folder_passes(self):
        """Feed input, check the return value."""
        self.change(REPO, NOTES, CLEAN_TEXT)
        self.assertEqual(self.before_the_run(PATHSPEC_COMMIT, self.root / LINK), (LET_THROUGH, NO_ROWS))


class StagingInTheSameCall(CommitScanHome):
    def test_add_of_a_literal_path_then_commit_scans_the_added_content(self):
        """The test fails when a real bug enters the code it exercises."""
        self.change(REPO, NOTES, FLAGGED_TEXT)
        self.assertEqual(self.before_the_run("git add " + NOTES + " && " + SAFE_COMMIT), (DENY, flagged_rows(NOTES)))

    def test_add_of_clean_content_then_commit_passes(self):
        """Feed input, check the return value."""
        self.change(REPO, NOTES, CLEAN_TEXT)
        self.assertEqual(self.before_the_run("git add " + NOTES + " && " + SAFE_COMMIT), (LET_THROUGH, NO_ROWS))

    def test_status_then_all_flag_commit_with_clean_content_passes(self):
        """Feed input, check the return value."""
        self.change(REPO, NOTES, CLEAN_TEXT)
        self.assertEqual(self.before_the_run("git status && git commit -am x"), (LET_THROUGH, NO_ROWS))


class CommitFlagsThatResolve(CommitScanHome):
    def test_all_flag_commit_without_hooks_scans_the_worktree_content(self):
        """The test fails when a real bug enters the code it exercises."""
        self.change(REPO, NOTES, FLAGGED_TEXT)
        self.assertEqual(self.before_the_run("git commit -am x --no-verify"), (DENY, flagged_rows(NOTES)))

    def test_amend_without_edit_scans_the_message_it_keeps(self):
        """The test fails when a real bug enters the code it exercises."""
        self.make_commit(REPO, message=FLAGGED_MESSAGE)
        self.assertEqual(self.before_the_run("git commit --amend --no-edit"), (DENY, flagged_rows(MESSAGE_PATH)))


class AliasesInTheCommand(CommitScanHome):
    def test_alias_for_status_passes_the_test_write_gate(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate_findings("git -c alias.zap=status status"), (LET_THROUGH, []))

    def test_variable_alias_before_a_log_passes_the_test_write_gate(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate_findings('git -c alias.zap="$X" log'), (LET_THROUGH, []))

    def test_variable_alias_commit_passes_before_the_run_and_its_flagged_commit_blocks(self):
        """The test fails when a real bug enters the code it exercises."""
        flagged = self.committing(REPO, message=FLAGGED_MESSAGE)
        self.assertEqual(self.run_and_stop(REPO, VARIABLE_ALIAS_COMMIT, flagged), (LET_THROUGH, CAUGHT, CAUGHT))

    def test_variable_alias_commit_with_clean_content_releases(self):
        """Feed input, check the return value."""
        clean = self.committing(REPO, {NOTES: CLEAN_TEXT})
        self.assertEqual(self.run_and_stop(REPO, VARIABLE_ALIAS_COMMIT, clean), (LET_THROUGH, LET_THROUGH, None))


if __name__ == "__main__":
    unittest.main()
