from fake_repo_home import PASSED, README, REFUSED, SIZE_TEST, FakeRepoCase, Repo
import unittest


SAMPLE_TEST = "tests/test_sample.py"
REPO = "repo"
REMOVE_ALL = '; rm "${files[@]}"'
REMOVE_ALL_FOLDERS = '; rm -r "${files[@]}"'


class ArrayHome(FakeRepoCase):
    projects = {REPO: Repo({"a": "first\n", "b": "second\n", SAMPLE_TEST: SIZE_TEST, **README})}

    def removal(self, setup, remover=REMOVE_ALL):
        return self.bash(REPO, setup + remover)


class LiteralBashArrays(ArrayHome):
    def test_literal_array_of_plain_files_passes_rm(self):
        self.assertEqual(self.removal("files=(a b)"), PASSED)

    def test_literal_array_holding_a_test_file_gets_the_test_write_deny(self):
        self.assertEqual(self.removal("files=(a " + SAMPLE_TEST + ")"), REFUSED)

    def test_literal_array_naming_the_tests_folder_gets_the_test_write_deny(self):
        """Feed input, check the return value."""
        self.assertEqual(self.removal("files=(a tests)", REMOVE_ALL_FOLDERS), REFUSED)


if __name__ == "__main__":
    unittest.main()
