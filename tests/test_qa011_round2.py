from fake_repo_home import OLD_TEST, PASSED, README, REFUSED, SIZE_TEST, FakeRepoCase, Repo
from lib.config_roots import grammar_cache_root
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


GO_TEST = "package lib\n\nimport \"testing\"\n\nfunc TestSize(t *testing.T) {\n\tif len([]int{1, 2}) != 2 {\n\t\tt.Fatal(\"size\")\n\t}\n}\n"
PKG_TEST = "pkg/tests/test_p.py"
FOLDED_TEST = "PKG/tests/test_p.py"
SCRATCH_IGNORE = {".gitignore": ".scratch/\n.claude/tmp/\n", OLD_TEST: SIZE_TEST}
CASE_NEEDED = "needs a case-insensitive temp folder"
NESTED = "nested"
FORCED = "forced"
SCRATCH = "scratch"


def case_insensitive_temp():
    with TemporaryDirectory() as folder:
        (Path(folder) / "Case").touch()
        return (Path(folder) / "CASE").exists()


class NestedGitignoreOfTestNames(FakeRepoCase):
    projects = {NESTED: Repo({**README, "lib/.gitignore": "test_*.py\n*_test.go\n", "lib/util.py": "def helper():\n    return 1\n"})}

    @classmethod
    def arrange(cls):
        failed = grammar_cache_root(cls.home) / "failed" / "go"
        failed.parent.mkdir(parents=True)
        failed.touch()

    def test_python_test_matched_by_a_committed_nested_gitignore_is_refused(self):
        self.assertEqual(self.write(NESTED, "lib/test_new.py", SIZE_TEST), REFUSED)

    def test_go_test_matched_by_a_committed_nested_gitignore_is_refused(self):
        self.assertEqual(self.write(NESTED, "lib/x_test.go", GO_TEST), REFUSED)


@unittest.skipUnless(case_insensitive_temp(), CASE_NEEDED)
class CaseFoldedSpellingOfAnIgnoredFolder(FakeRepoCase):
    projects = {FORCED: Repo({".gitignore": "/pkg/\n", **README, PKG_TEST: SIZE_TEST})}

    def test_write_to_the_upper_case_spelling_of_a_tracked_test_is_refused(self):
        self.assertEqual(self.write(FORCED, FOLDED_TEST, SIZE_TEST + "\n"), REFUSED)

    def test_copy_onto_the_upper_case_spelling_of_a_tracked_test_is_refused(self):
        self.assertEqual(self.bash(FORCED, "cp README.md " + FOLDED_TEST), REFUSED)


class ScratchFoldersBesideTrackedTests(FakeRepoCase):
    projects = {
        SCRATCH: Repo(SCRATCH_IGNORE),
        SCRATCH + "/.claude/tmp/up": Repo({OLD_TEST: SIZE_TEST}),
    }

    @classmethod
    def arrange(cls):
        (cls.root / SCRATCH / ".scratch/x").mkdir(parents=True)

    def test_pull_in_a_clone_under_the_ignored_claude_tmp_folder_passes(self):
        self.assertEqual(self.bash(SCRATCH, "git -C .claude/tmp/up pull"), PASSED)

    def test_archive_extracted_into_the_ignored_scratch_folder_passes(self):
        self.assertEqual(self.bash(SCRATCH, "git archive HEAD | tar -x -C .scratch/x"), PASSED)

    def test_test_file_written_into_the_ignored_scratch_folder_passes(self):
        self.assertEqual(self.write(SCRATCH, ".scratch/x/test_probe.py", SIZE_TEST), PASSED)

    def test_test_file_written_under_an_upper_case_tests_folder_is_refused(self):
        self.assertEqual(self.write(SCRATCH, "TESTS/test_b.py", SIZE_TEST), REFUSED)

    def test_archive_extracted_into_the_tests_folder_is_refused(self):
        self.assertEqual(self.bash(SCRATCH, "git archive HEAD | tar -x -C tests/"), REFUSED)


if __name__ == "__main__":
    unittest.main()
