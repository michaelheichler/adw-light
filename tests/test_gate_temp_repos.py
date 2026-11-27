from fake_repo_home import OLD_TEST, PASSED, README, REFUSED, SIZE_TEST, FakeRepoCase, Repo
import shlex
import unittest


TRACKED_TEST = "tests/test_a.py"
ADDED_TEST = "printf '%s\\n' 'def test_b():' '    assert 2 + 2 == 4' >> "
PROJECT = "project"
SCRATCH = "scratch"


class TempRepoHome(FakeRepoCase):
    projects = {
        PROJECT: Repo(README),
        SCRATCH: Repo({TRACKED_TEST: SIZE_TEST, **README}),
        PROJECT + "/vendor/up": Repo({OLD_TEST: SIZE_TEST}),
    }

    def from_project(self, command_for):
        return self.bash(PROJECT, command_for(shlex.quote(str(self.root / SCRATCH))))


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
        self.assertEqual(self.bash(SCRATCH, "rm -rf tests"), REFUSED)

    def test_removing_a_tracked_test_file_is_refused(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(SCRATCH, "rm -f " + TRACKED_TEST), REFUSED)

    def test_appending_test_code_to_a_tracked_test_is_refused(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(SCRATCH, ADDED_TEST + TRACKED_TEST), REFUSED)


if __name__ == "__main__":
    unittest.main()
