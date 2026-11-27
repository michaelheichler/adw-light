import unittest

from after_commit_home import CLEAN_MESSAGE, FLAGGED_MESSAGE, FLAGGED_TEXT, NOTES, AfterCommitCase
from after_run_home import CAUGHT, LET_THROUGH, Project
from fake_repo_home import BASE, README


REPO = "repo"
NESTED = "repo/vendor/lib"
FEATURE = "feature-0"
TOPIC = "refs/heads/topic"
FETCHED = "refs/remotes/origin/main"
MAIN_REFS = ("HEAD", "main", "refs/heads/main")
OLD = 1
PASSED_THROUGH = (LET_THROUGH, LET_THROUGH, None)
SCANNED_AND_BLOCKED = (LET_THROUGH, CAUGHT, CAUGHT)


class CommitHistoryHome(AfterCommitCase):
    projects = {
        REPO: Project({**README, NOTES: "Old.\n"}, branches={"feature": [{NOTES: FLAGGED_TEXT}]}),
        NESTED: Project({NOTES: "Old.\n"}, listed=False),
    }

    def moved_to(self, commit, refs=MAIN_REFS):
        return dict.fromkeys(refs, commit)

    def pulled(self):
        fetched = self.branch_commit(REPO, FETCHED, {NOTES: FLAGGED_TEXT}, FLAGGED_MESSAGE, time=OLD)
        self.point(REPO, self.moved_to(fetched))

    def cloned(self, time=None):
        self.reveal(NESTED)
        self.make_commit(NESTED, {NOTES: FLAGGED_TEXT}, FLAGGED_MESSAGE, time=time)


class InheritedHistory(CommitHistoryHome):
    def test_checkout_of_an_existing_branch_with_flagged_history_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        moved = self.moving(REPO, {"HEAD": FEATURE})
        self.assertEqual(self.run_and_stop(REPO, "git checkout feature", moved), PASSED_THROUGH)

    def test_reset_to_a_flagged_ref_that_existed_before_the_run_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        moved = self.moving(REPO, self.moved_to(FEATURE))
        self.assertEqual(self.run_and_stop(REPO, "git reset --hard feature", moved), PASSED_THROUGH)

    def test_fast_forward_to_an_old_fetched_flagged_commit_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        self.assertEqual(self.run_and_stop(REPO, "git pull --ff-only", self.pulled), PASSED_THROUGH)

    def test_nested_repo_that_arrives_with_old_flagged_commits_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        arrived = lambda: self.cloned(time=OLD)
        self.assertEqual(self.run_and_stop(REPO, "git clone ../lib vendor/lib", arrived), PASSED_THROUGH)


class FreshCommits(CommitHistoryHome):
    def test_new_repo_with_a_fresh_flagged_commit_blocks(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.run_and_stop(REPO, "bash tools/make_repo.sh", self.cloned), SCANNED_AND_BLOCKED)

    def test_backdated_flagged_commit_in_an_existing_repo_passes_as_a_known_gap(self):
        """A test must verify what the code does for its client, never how it does it."""
        dated = lambda: self.make_commit(REPO, message=FLAGGED_MESSAGE, time=OLD)
        self.assertEqual(self.run_and_stop(REPO, "bash tools/run.sh", dated), PASSED_THROUGH)

    def test_flagged_commit_on_a_branch_that_head_does_not_point_to_blocks(self):
        """The test fails when a real bug enters the code it exercises."""
        side = lambda: self.branch_commit(REPO, TOPIC, message=FLAGGED_MESSAGE)
        self.assertEqual(self.run_and_stop(REPO, "bash tools/run.sh", side), SCANNED_AND_BLOCKED)

    def test_clean_commit_on_a_branch_that_head_does_not_point_to_passes(self):
        """Feed input, check the return value."""
        side = lambda: self.branch_commit(REPO, TOPIC, message=CLEAN_MESSAGE)
        self.assertEqual(self.run_and_stop(REPO, "bash tools/run.sh", side), PASSED_THROUGH)


class QuietBranchMoves(CommitHistoryHome):
    def test_quiet_checkout_that_creates_a_branch_passes_before_and_after_the_run(self):
        """Feed input, check the return value."""
        tip = self.make_commit(REPO)
        created = self.moving(REPO, {"refs/heads/other": tip, "other": tip})
        self.assertEqual(self.run_and_stop(REPO, "git checkout -qb other", created), PASSED_THROUGH)

    def test_quiet_switch_to_an_existing_branch_passes_before_and_after_the_run(self):
        """Feed input, check the return value."""
        self.make_commit(REPO)
        switched = self.moving(REPO, {"HEAD": BASE})
        self.assertEqual(self.run_and_stop(REPO, "git switch -q other", switched), PASSED_THROUGH)


if __name__ == "__main__":
    unittest.main()
