import json
import time
import unittest

from after_run_home import (
    CONFIG, LET_THROUGH, AfterRunCase, Project, project_files,
)
from fake_repo_home import SIZE_TEST


REPO = "repo"
WINDOW = "window"
OUTSIDE = "outside"
NEW_TEST = "tests/test_added.py"
ADDED = SIZE_TEST.replace("test_size", "test_added")
HIDDEN_WRITE = "python3 -c \"import pathlib; pathlib.Path('tes' + 'ts/test_added.py').write_text('x')\""
GIT_OPERATIONS = ("git merge topic", "git cherry-pick topic", "git worktree add ../spare")
OPEN_WINDOW = {CONFIG: json.dumps({"tests_allow_until": int(time.time()) + 3600})}


class ExemptedRuns(AfterRunCase):
    projects = {
        REPO: Project(project_files(), branches={"topic": [{"docs/topic.md": "Topic.\n"}]}),
        WINDOW: Project(project_files(OPEN_WINDOW)),
        OUTSIDE: Project({"tests/test_far.py": SIZE_TEST}, git=False),
    }

    def add_test(self, name=REPO, relative=NEW_TEST):
        return lambda: self.change(name, relative, ADDED)

    def git_verdicts(self):
        return {command: self.shell(REPO, command, self.add_test(), run=command) for command in GIT_OPERATIONS}

    def test_open_tests_window_lets_a_main_agent_test_write_through(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.after_run(WINDOW, HIDDEN_WRITE, self.add_test(WINDOW)), LET_THROUGH)

    def test_merge_cherry_pick_and_worktree_add_do_not_count_as_test_writes(self):
        """Use a parameterized test for several inputs."""
        self.assertEqual(self.git_verdicts(), dict.fromkeys(GIT_OPERATIONS, (None, LET_THROUGH)))

    def test_test_file_written_outside_the_project_is_not_reported(self):
        """Feed input, check the return value."""
        effect = lambda: self.change(OUTSIDE, "tests/test_far.py", ADDED)
        self.assertEqual(self.after_run(REPO, HIDDEN_WRITE, effect), LET_THROUGH)


if __name__ == "__main__":
    unittest.main()
