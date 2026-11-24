from quiet_git import disable_background_maintenance
from false_positive_home import IDENTITY, OLD_TEST, PASSED, REFUSED, SIZE_TEST, FalsePositiveHome
import shlex
import subprocess
import unittest


TRACKED_TEST = "tests/test_a.py"
ADDED_TEST = "printf '%s\\n' 'def test_b():' '    assert 2 + 2 == 4' >> "
PLAIN_PROJECT = {"README.md": "Short.\n"}


class TempRepoHome(FalsePositiveHome):
    def temp_repo(self):
        scratch = self.root / "scratch"
        scratch.mkdir()
        subprocess.run(["git", "init", "-q", str(scratch)], check=True)
        disable_background_maintenance(str(scratch))
        self.place(scratch, {TRACKED_TEST: SIZE_TEST, "README.md": "Short.\n"})
        subprocess.run(["git", "-C", str(scratch), "add", "."], check=True)
        subprocess.run(["git", "-C", str(scratch), *IDENTITY, "commit", "-qm", "base"], check=True)
        return scratch

    def from_project(self, command_for):
        project = self.committed_repo(PLAIN_PROJECT)
        return self.bash(project, command_for(shlex.quote(str(self.temp_repo()))))


class TempRepoSeenFromOutside(TempRepoHome):
    def test_removing_the_whole_temp_repo_passes(self):
        self.assertEqual(self.from_project(lambda scratch: "rm -rf " + scratch), PASSED)

    def test_removing_the_tests_folder_of_the_temp_repo_passes(self):
        self.assertEqual(self.from_project(lambda scratch: "rm -rf " + scratch + "/tests"), PASSED)

    def test_appending_test_code_to_a_tracked_test_of_the_temp_repo_passes(self):
        self.assertEqual(self.from_project(lambda scratch: ADDED_TEST + scratch + "/" + TRACKED_TEST), PASSED)

    def test_hard_reset_of_the_temp_repo_passes(self):
        self.assertEqual(self.from_project(lambda scratch: "git -C " + scratch + " reset --hard"), PASSED)


class TempRepoSeenFromInside(TempRepoHome):
    def test_removing_the_tests_folder_is_refused(self):
        self.assertEqual(self.bash(self.temp_repo(), "rm -rf tests"), REFUSED)

    def test_removing_a_tracked_test_file_is_refused(self):
        self.assertEqual(self.bash(self.temp_repo(), "rm -f " + TRACKED_TEST), REFUSED)

    def test_hard_reset_is_refused(self):
        self.assertEqual(self.bash(self.temp_repo(), "git reset --hard"), REFUSED)

    def test_appending_test_code_to_a_tracked_test_is_refused(self):
        self.assertEqual(self.bash(self.temp_repo(), ADDED_TEST + TRACKED_TEST), REFUSED)


class CloneNestedInATempProject(FalsePositiveHome):
    def test_pull_in_a_clone_under_a_folder_that_is_not_ignored_is_refused(self):
        project = self.committed_repo(PLAIN_PROJECT)
        clone = project / "vendor" / "up"
        clone.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        disable_background_maintenance(str(clone))
        self.place(clone, {OLD_TEST: SIZE_TEST})
        subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
        subprocess.run(["git", "-C", str(clone), *IDENTITY, "commit", "-qm", "up"], check=True)
        self.assertEqual(self.bash(project, "git -C vendor/up pull"), REFUSED)


if __name__ == "__main__":
    unittest.main()
