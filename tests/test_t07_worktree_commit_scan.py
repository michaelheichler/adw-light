import json
import unittest

from after_commit_home import CLEAN_TEXT as CLEAN_NOTE, FLAGGED_MESSAGE as FLAGGED_BODY, AfterCommitCase, commit_project
from after_run_home import CAUGHT, LET_THROUGH
from fake_repo_home import README, FakeRepoCase, Repo


DENY = "deny"
PASSED = None
PROSE_RULE = "utilize"
NOTES = "notes.md"
GONE = "gone.md"
STAGED = "staged.md"
MESSAGE = "commit_message.md"
CLEAN = "Short note.\n"
FLAGGED = "We utilize this tool for the build.\n"
HEAD = {**README, NOTES: "Old.\n", GONE: "Old.\n", STAGED: "Old.\n"}
SAFE_MESSAGE = ' -m "Add notes."'
FLAGGED_MESSAGE = ' -m "We utilize this."'
NO_ROWS = frozenset()


def project(worktree=None, index=None, deleted=()):
    disk = {name: text for name, text in {**HEAD, **(worktree or {})}.items() if name not in deleted}
    return Repo(HEAD, worktree or {}, fields={"index": {**HEAD, **(index or {})}, "disk": disk})


def refused(*paths):
    return DENY, frozenset((path, PROSE_RULE) for path in paths)


ALLOWED = (PASSED, NO_ROWS)
UNTRACKED_PATHSPEC = "git commit" + SAFE_MESSAGE + " missing.md"
UNREADABLE_MESSAGE_FILE = "git commit -a -F absent_message.txt"
SCANNED_AND_BLOCKED = (LET_THROUGH, CAUGHT, CAUGHT)
SCANNED_AND_RELEASED = (LET_THROUGH, LET_THROUGH, None)


class WorktreeCommitHome(FakeRepoCase):
    projects = {
        "clean": project({NOTES: CLEAN}),
        "flagged": project({NOTES: FLAGGED}),
        "partial_flagged_worktree": project({NOTES: FLAGGED}, {NOTES: CLEAN}),
        "partial_flagged_index": project({NOTES: CLEAN}, {NOTES: FLAGGED}),
        "deleted": project({NOTES: CLEAN}, deleted=(GONE,)),
        "unrelated_staged": project({NOTES: CLEAN}, {STAGED: FLAGGED}),
    }

    @classmethod
    def arrange(cls):
        (cls.root / "deleted" / GONE).unlink()

    def commit(self, name, command):
        decision, _ = self.bash(name, command)
        ledger = self.state / "ledger" / "ledger.jsonl"
        rows = (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())
        blocked = frozenset((row["path"], row["rule"]) for row in rows if row.get("session_id") == self.session() and row.get("outcome") == "block")
        return decision, blocked


class AllMode(WorktreeCommitHome):
    def test_short_all_flag_with_clean_worktree_content_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.commit("clean", "git commit -a" + SAFE_MESSAGE), ALLOWED)

    def test_short_all_flag_with_flagged_worktree_content_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.commit("flagged", "git commit -a" + SAFE_MESSAGE), refused(NOTES))

    def test_bundled_all_and_message_flags_with_clean_content_pass(self):
        """Feed input, check the return value."""
        self.assertEqual(self.commit("clean", 'git commit -am "Add notes."'), ALLOWED)

    def test_bundled_all_and_message_flags_with_flagged_content_are_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.commit("flagged", 'git commit -am "Add notes."'), refused(NOTES))

    def test_long_all_flag_with_clean_worktree_content_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.commit("clean", "git commit --all" + SAFE_MESSAGE), ALLOWED)

    def test_long_all_flag_with_flagged_worktree_content_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.commit("flagged", "git commit --all" + SAFE_MESSAGE), refused(NOTES))


class PathspecModes(WorktreeCommitHome):
    def test_literal_pathspec_with_clean_worktree_content_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.commit("clean", "git commit" + SAFE_MESSAGE + " " + NOTES), ALLOWED)

    def test_literal_pathspec_with_flagged_worktree_content_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.commit("flagged", "git commit" + SAFE_MESSAGE + " " + NOTES), refused(NOTES))

    def test_include_with_clean_worktree_content_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.commit("clean", "git commit --include" + SAFE_MESSAGE + " " + NOTES), ALLOWED)

    def test_include_with_flagged_worktree_content_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.commit("flagged", "git commit --include" + SAFE_MESSAGE + " " + NOTES), refused(NOTES))

    def test_only_with_clean_worktree_content_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.commit("clean", "git commit --only" + SAFE_MESSAGE + " " + NOTES), ALLOWED)

    def test_only_with_flagged_worktree_content_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.commit("flagged", "git commit --only" + SAFE_MESSAGE + " " + NOTES), refused(NOTES))


class ContentTheCommitTakes(WorktreeCommitHome):
    def test_partially_staged_file_is_scanned_in_its_worktree_form(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.commit("partial_flagged_worktree", "git commit -a" + SAFE_MESSAGE), refused(NOTES))

    def test_flagged_index_form_that_the_worktree_replaces_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.commit("partial_flagged_index", "git commit -a" + SAFE_MESSAGE), ALLOWED)

    def test_tracked_deletion_under_the_all_flag_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.commit("deleted", "git commit -a" + SAFE_MESSAGE), ALLOWED)

    def test_tracked_deletion_named_as_a_pathspec_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.commit("deleted", "git commit" + SAFE_MESSAGE + " " + GONE), ALLOWED)

    def test_include_scans_an_unrelated_staged_file(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.commit("unrelated_staged", "git commit --include" + SAFE_MESSAGE + " " + NOTES), refused(STAGED))

    def test_only_ignores_an_unrelated_staged_file(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.commit("unrelated_staged", "git commit --only" + SAFE_MESSAGE + " " + NOTES), ALLOWED)


class CommitRefusalsThatStay(WorktreeCommitHome):
    def test_message_with_a_blocking_finding_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.commit("clean", "git commit -a" + FLAGGED_MESSAGE), refused(MESSAGE))



class CommitsTheGateCannotReadAhead(AfterCommitCase):
    projects = {"repo": commit_project()}

    def flagged_commit(self, command):
        return self.run_and_stop("repo", command, self.committing("repo", message=FLAGGED_BODY))

    def clean_commit(self, command):
        return self.run_and_stop("repo", command, self.committing("repo", {NOTES: CLEAN_NOTE}))

    def test_pathspec_that_names_no_tracked_file_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(UNTRACKED_PATHSPEC), SCANNED_AND_BLOCKED)

    def test_pathspec_that_names_no_tracked_file_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(UNTRACKED_PATHSPEC), SCANNED_AND_RELEASED)

    def test_message_file_that_cannot_be_read_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(UNREADABLE_MESSAGE_FILE), SCANNED_AND_BLOCKED)

    def test_message_file_that_cannot_be_read_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(UNREADABLE_MESSAGE_FILE), SCANNED_AND_RELEASED)


if __name__ == "__main__":
    unittest.main()
