import unittest

from after_commit_home import (
    CLEAN_MESSAGE, CLEAN_TEXT, FLAGGED_MESSAGE, FLAGGED_TEXT, NOTES, AfterCommitCase, commit_project,
)
from after_run_home import CAUGHT, LET_THROUGH
from lib.after_commit import CONTENT_ACTION, MESSAGE_ACTION


REPO = "repo"
COMMAND = "bash tools/run.sh"
OTHER = "other.md"
HELD = (CAUGHT, CAUGHT, CAUGHT)
RELEASED = (CAUGHT, LET_THROUGH, None)


class CommitReleaseHome(AfterCommitCase):
    projects = {REPO: commit_project()}

    def two_calls(self, first, second):
        self.start(REPO)
        _, blocked, _ = self.pair(REPO, COMMAND, first)
        _, later, _ = self.pair(REPO, COMMAND, second, call="c2")
        return blocked, later, self.stop(REPO)

    def flagged_then_clean(self):
        self.make_commit(REPO, message=FLAGGED_MESSAGE)
        self.make_commit(REPO, {OTHER: CLEAN_TEXT})

    def reworded(self):
        self.dropping(REPO, 2)()
        self.make_commit(REPO, message=CLEAN_MESSAGE)
        self.make_commit(REPO, {OTHER: CLEAN_TEXT})

    def block_reason(self, effect):
        self.start(REPO)
        self.pre(REPO, COMMAND)
        effect()
        self.post(REPO, COMMAND)
        return self.batch(REPO, COMMAND).get("reason", "")

    def actions_in(self, reason):
        return MESSAGE_ACTION in reason, CONTENT_ACTION in reason


class MessageFindings(CommitReleaseHome):
    def test_message_finding_holds_through_an_unrelated_commit(self):
        """The test fails when a real bug enters the code it exercises."""
        flagged = self.committing(REPO, message=FLAGGED_MESSAGE)
        unrelated = self.committing(REPO, {OTHER: CLEAN_TEXT})
        self.assertEqual(self.two_calls(flagged, unrelated), HELD)

    def test_message_finding_releases_after_a_reword_under_a_later_commit(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.two_calls(self.flagged_then_clean, self.reworded), RELEASED)

    def test_message_finding_releases_after_a_reset_that_drops_the_commit(self):
        """The test fails when a real bug enters the code it exercises."""
        flagged = self.committing(REPO, message=FLAGGED_MESSAGE)
        self.assertEqual(self.two_calls(flagged, self.dropping(REPO)), RELEASED)


class ContentFindings(CommitReleaseHome):
    def test_content_finding_releases_once_the_file_is_gone_from_head(self):
        """The test fails when a real bug enters the code it exercises."""
        flagged = self.committing(REPO, {NOTES: FLAGGED_TEXT})
        deleted = self.committing(REPO, {NOTES: None})
        self.assertEqual(self.two_calls(flagged, deleted), RELEASED)


class BlockActions(CommitReleaseHome):
    def test_message_block_names_the_reword_action_and_not_the_file_fix(self):
        """A test must verify what the code does for its client, never how it does it."""
        reason = self.block_reason(self.committing(REPO, message=FLAGGED_MESSAGE))
        self.assertEqual(self.actions_in(reason), (True, False))

    def test_content_block_names_the_file_fix_and_not_the_reword_action(self):
        """A test must verify what the code does for its client, never how it does it."""
        reason = self.block_reason(self.committing(REPO, {NOTES: FLAGGED_TEXT}))
        self.assertEqual(self.actions_in(reason), (False, True))


if __name__ == "__main__":
    unittest.main()
