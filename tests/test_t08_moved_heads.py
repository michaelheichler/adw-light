import json
import time
import unittest

from after_commit_home import NOTES, AfterCommitCase, commit_project
from after_run_home import CONFIG, LET_THROUGH, Project, identity, project_files
from fake_repo_home import SIZE_TEST


REPO = "repo"
WINDOW = "window"
RUN = "bash tools/run.sh"
OPEN_WINDOW = {CONFIG: json.dumps({"tests_allow_until": int(time.time()) + 3600})}


class MovedHeadCase(AfterCommitCase):
    projects = {
        REPO: commit_project(),
        WINDOW: Project(project_files({NOTES: "Old.\n", **OPEN_WINDOW})),
    }


def new_test(run):
    return "tests/test_new_" + run + ".py"


class CommittedTestChanges(MovedHeadCase):
    def committed_test(self, name=REPO, command=RUN, agent=None, run=""):
        self.start(name, run=run)
        effect = self.committing(name, {new_test(run): SIZE_TEST})
        _, verdict, _ = self.pair(name, command, effect, before=agent, run=run)
        return verdict, self.stop(name, run=run)

    def test_the_test_writer_an_open_window_an_exempt_command_and_a_replay_pass_a_committed_test(self):
        """Use a parameterized test for several inputs."""
        verdicts = {
            "test_writer": self.committed_test(agent=identity("adw-test-writer"), run="writer"),
            "window": self.committed_test(WINDOW, run="window"),
            "merge": self.committed_test(command="git merge topic", run="merge"),
            "rebase": self.committed_test(command="git rebase main", run="rebase"),
            "pull": self.committed_test(command="git pull", run="pull"),
        }
        self.assertEqual(verdicts, dict.fromkeys(verdicts, (LET_THROUGH, None)))


if __name__ == "__main__":
    unittest.main()
