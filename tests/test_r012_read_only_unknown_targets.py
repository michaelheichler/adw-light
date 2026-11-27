from fake_repo_home import PASSED, README, SIZE_TEST, FakeRepoCase, Repo
import os
import unittest


PROJECT = Repo({"tests/test_sample.py": SIZE_TEST, **README})
REPO = "repo"
EACH_NESTED_REPO = 'for d in repos/*; do git -C "$d" '
LOOP_END = "; done"
FIXED_TEMP_GLOB = os.path.join(os.sep, "tmp", "name-*")


class NestedReposHome(FakeRepoCase):
    projects = {REPO: PROJECT, REPO + "/repos/one": Repo({"tests/test_one.py": SIZE_TEST})}

    def over_each_nested_repo(self, verb):
        return self.bash(REPO, EACH_NESTED_REPO + verb + LOOP_END)

    def with_unknown_folder(self, verb):
        return self.bash(REPO, 'git -C "$d" ' + verb)


class ReadOnlyGitOverGlobbedFolders(NestedReposHome):
    def test_worktree_list_in_a_loop_over_a_glob_passes(self):
        self.assertEqual(self.over_each_nested_repo("worktree list"), PASSED)

    def test_status_in_a_loop_over_a_glob_passes(self):
        self.assertEqual(self.over_each_nested_repo("status"), PASSED)

    def test_log_in_a_loop_over_a_glob_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.over_each_nested_repo("log --oneline"), PASSED)

class ReadOnlyGitInAnUnknownFolder(NestedReposHome):
    def test_worktree_list_passes(self):
        self.assertEqual(self.with_unknown_folder("worktree list"), PASSED)

    def test_status_passes(self):
        self.assertEqual(self.with_unknown_folder("status"), PASSED)

    def test_log_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.with_unknown_folder("log"), PASSED)

class GlobbedDeleteWithTempFolderBesideTheProject(FakeRepoCase):
    projects = {REPO: PROJECT}

    @classmethod
    def arrange(cls):
        (cls.root / "tmpdir").mkdir()

    def hook_environment(self):
        return {"TMPDIR": str(self.root / "tmpdir")}

    def test_delete_of_a_glob_under_tmpdir_passes(self):
        self.assertEqual(self.bash(REPO, 'rm -rf "$TMPDIR"/name-*'), PASSED)

    def test_delete_of_a_glob_under_slash_tmp_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(REPO, "rm -rf " + FIXED_TEMP_GLOB), PASSED)


if __name__ == "__main__":
    unittest.main()
