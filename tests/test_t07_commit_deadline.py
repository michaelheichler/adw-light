import unittest

from after_commit_home import CLEAN_MESSAGE, FLAGGED_MESSAGE, AfterCommitCase, ResolvingGitView, commit_project
from after_run_home import CAUGHT, LET_THROUGH
from lib import baseline


REPO = "repo"
COMMAND = "bash tools/run.sh"
NOTE = "systemMessage"


class LateGitView(ResolvingGitView):
    def __init__(self, data, late):
        super().__init__(data)
        self.late = late

    def commit_parents(self, root, ref, *, until=None):
        if ref in self.late:
            raise baseline.OutOfTime
        return super().commit_parents(root, ref, until=until)


class DeadlineHome(AfterCommitCase):
    projects = {REPO: commit_project()}

    def view(self):
        return LateGitView(self.view_data(), self.__dict__.get("late", frozenset()))

    def three_commits(self, messages):
        return lambda: [self.make_commit(REPO, message=message) for message in messages]

    def late_run(self, messages, *, late_at_stop):
        self.start(REPO)
        self.pre(REPO, COMMAND)
        names = self.three_commits(messages)()
        self.late = frozenset(names[:2])
        self.post(REPO, COMMAND)
        verdict = self.batch(REPO, COMMAND).get("decision")
        self.late = self.late if late_at_stop else frozenset()
        stopped = self.respond("Stop", REPO, {"last_assistant_message": "Done."})
        return verdict, stopped.get("decision"), NOTE in stopped


class CommitsPastTheDeadline(DeadlineHome):
    def test_flagged_commit_scanned_before_the_deadline_blocks_at_once(self):
        """The test fails when a real bug enters the code it exercises."""
        outcome = self.late_run([CLEAN_MESSAGE, CLEAN_MESSAGE, FLAGGED_MESSAGE], late_at_stop=False)
        self.assertEqual(outcome[:2], (CAUGHT, CAUGHT))

    def test_flagged_commit_left_past_the_deadline_blocks_at_stop(self):
        """The test fails when a real bug enters the code it exercises."""
        outcome = self.late_run([FLAGGED_MESSAGE, CLEAN_MESSAGE, CLEAN_MESSAGE], late_at_stop=False)
        self.assertEqual(outcome[:2], (LET_THROUGH, CAUGHT))

    def test_clean_commits_left_past_the_deadline_release_at_stop(self):
        """Feed input, check the return value."""
        outcome = self.late_run([CLEAN_MESSAGE, CLEAN_MESSAGE, CLEAN_MESSAGE], late_at_stop=False)
        self.assertEqual(outcome, (LET_THROUGH, None, False))

    def test_stop_that_runs_out_of_time_too_adds_a_note_and_no_block(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        outcome = self.late_run([FLAGGED_MESSAGE, CLEAN_MESSAGE, CLEAN_MESSAGE], late_at_stop=True)
        self.assertEqual(outcome, (LET_THROUGH, None, True))


if __name__ == "__main__":
    unittest.main()
