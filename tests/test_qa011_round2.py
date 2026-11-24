from quiet_git import disable_background_maintenance
from false_positive_home import IDENTITY, OLD_TEST, PASSED, REFUSED, SIZE_TEST, FalsePositiveHome
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest


NESTED_IGNORE = {"README.md": "Short.\n", "lib/.gitignore": "test_*.py\n*_test.go\n", "lib/util.py": "def helper():\n    return 1\n"}
GO_TEST = "package lib\n\nimport \"testing\"\n\nfunc TestSize(t *testing.T) {\n\tif len([]int{1, 2}) != 2 {\n\t\tt.Fatal(\"size\")\n\t}\n}\n"
PKG_TEST = "pkg/tests/test_p.py"
FOLDED_TEST = "PKG/tests/test_p.py"
SCRATCH_IGNORE = {".gitignore": ".scratch/\n.claude/tmp/\n", OLD_TEST: SIZE_TEST}
CASE_NEEDED = "needs a case-insensitive temp folder"


def case_insensitive_temp():
    with TemporaryDirectory() as folder:
        (Path(folder) / "Case").touch()
        return (Path(folder) / "CASE").exists()


class NestedGitignoreOfTestNames(FalsePositiveHome):
    def test_python_test_matched_by_a_committed_nested_gitignore_is_refused(self):
        repo = self.committed_repo(NESTED_IGNORE)
        self.assertEqual(self.write(repo, repo / "lib/test_new.py", SIZE_TEST), REFUSED)

    def test_go_test_matched_by_a_committed_nested_gitignore_is_refused(self):
        repo = self.committed_repo(NESTED_IGNORE)
        self.assertEqual(self.write(repo, repo / "lib/x_test.go", GO_TEST), REFUSED)


@unittest.skipUnless(case_insensitive_temp(), CASE_NEEDED)
class CaseFoldedSpellingOfAnIgnoredFolder(FalsePositiveHome):
    def repo_with_forced_test(self):
        repo = self.committed_repo({".gitignore": "/pkg/\n", "README.md": "Short.\n"})
        self.place(repo, {PKG_TEST: SIZE_TEST})
        subprocess.run(["git", "-C", str(repo), "add", "-f", PKG_TEST], check=True)
        subprocess.run(["git", "-C", str(repo), *IDENTITY, "commit", "-qm", "forced"], check=True)
        return repo

    def test_write_to_the_upper_case_spelling_of_a_tracked_test_is_refused(self):
        repo = self.repo_with_forced_test()
        self.assertEqual(self.write(repo, repo / FOLDED_TEST, SIZE_TEST + "\n"), REFUSED)

    def test_copy_onto_the_upper_case_spelling_of_a_tracked_test_is_refused(self):
        repo = self.repo_with_forced_test()
        self.assertEqual(self.bash(repo, "cp README.md " + FOLDED_TEST), REFUSED)


class ScratchFoldersBesideTrackedTests(FalsePositiveHome):
    def clone_under(self, repo, relative):
        clone = repo / relative
        clone.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        disable_background_maintenance(str(clone))
        self.place(clone, {OLD_TEST: SIZE_TEST})
        subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
        subprocess.run(["git", "-C", str(clone), *IDENTITY, "commit", "-qm", "up"], check=True)
        return repo

    def repo_with_scratch_folder(self):
        repo = self.committed_repo(SCRATCH_IGNORE)
        (repo / ".scratch/x").mkdir(parents=True)
        return repo

    def test_pull_in_a_clone_under_the_ignored_claude_tmp_folder_passes(self):
        repo = self.clone_under(self.committed_repo(SCRATCH_IGNORE), ".claude/tmp/up")
        self.assertEqual(self.bash(repo, "git -C .claude/tmp/up pull"), PASSED)

    def test_archive_extracted_into_the_ignored_scratch_folder_passes(self):
        repo = self.repo_with_scratch_folder()
        self.assertEqual(self.bash(repo, "git archive HEAD | tar -x -C .scratch/x"), PASSED)

    def test_test_file_written_into_the_ignored_scratch_folder_passes(self):
        repo = self.repo_with_scratch_folder()
        self.assertEqual(self.write(repo, repo / ".scratch/x/test_probe.py", SIZE_TEST), PASSED)

    def test_test_file_written_under_an_upper_case_tests_folder_is_refused(self):
        repo = self.committed_repo(SCRATCH_IGNORE)
        self.assertEqual(self.write(repo, repo / "TESTS/test_b.py", SIZE_TEST), REFUSED)

    def test_archive_extracted_into_the_tests_folder_is_refused(self):
        repo = self.committed_repo(SCRATCH_IGNORE)
        self.assertEqual(self.bash(repo, "git archive HEAD | tar -x -C tests/"), REFUSED)


if __name__ == "__main__":
    unittest.main()
