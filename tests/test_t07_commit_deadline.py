import unittest

from after_commit_home import CLEAN_MESSAGE, AfterCommitCase, ResolvingGitView, commit_project
from after_run_home import LET_THROUGH
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


    def test_clean_commits_left_past_the_deadline_release_at_stop(self):
        """Feed input, check the return value."""
        outcome = self.late_run([CLEAN_MESSAGE, CLEAN_MESSAGE, CLEAN_MESSAGE], late_at_stop=False)
        self.assertEqual(outcome, (LET_THROUGH, None, False))


if __name__ == "__main__":
    unittest.main()
