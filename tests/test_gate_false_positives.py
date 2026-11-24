from false_positive_home import (
    DENY,
    GATE,
    LOCK,
    OLD_TEST,
    PASSED,
    PLAIN_MODULE,
    REFUSED,
    SIZE_TEST,
    DEBT_FILE,
    FalsePositiveHome,
)
import unittest


NEW_TEST = "tests/test_x.py"
TRACKED_TESTS = {OLD_TEST: SIZE_TEST, "notes.txt": "needle\n"}
DEBT_TESTS = {OLD_TEST: DEBT_FILE}
NO_TESTS = {"src/app.py": PLAIN_MODULE, "notes.txt": "needle\n"}
TEST_NAMED_MODULE = {"engine/lib/test_units.py": PLAIN_MODULE, "engine/lib/other.py": PLAIN_MODULE}


class IndexOnlyAndScratchCommands(FalsePositiveHome):
    def scratch_folder(self):
        scratch = self.root / "scratch"
        self.place(scratch, {"test_probe.py": SIZE_TEST})
        return scratch

    def test_git_rm_cached_on_a_test_file_passes_the_test_write_gate(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(repo, "git rm --cached " + OLD_TEST), PASSED)

    def test_git_rm_dry_run_on_a_test_file_passes_the_test_write_gate(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(repo, "git rm -n " + OLD_TEST), PASSED)

    def test_git_rm_cached_that_hides_a_debt_file_meets_only_the_wording_debt_lock(self):
        repo = self.committed_repo(DEBT_TESTS)
        self.assertEqual(self.bash(repo, "git rm --cached " + OLD_TEST), (DENY, [LOCK]))

    def test_removing_a_temp_folder_with_test_named_files_outside_the_repo_passes(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(repo, "rm -rf " + str(self.scratch_folder())), PASSED)

    def test_git_archive_extracted_into_a_temp_folder_outside_the_repo_passes(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(repo, "git archive HEAD | tar -x -C " + str(self.scratch_folder())), PASSED)

    def test_git_archive_extracted_into_the_tests_folder_is_refused(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(repo, "git archive HEAD | tar -x -C tests"), REFUSED)

    def test_deleting_a_test_named_module_that_holds_no_tests_passes(self):
        repo = self.committed_repo(TEST_NAMED_MODULE)
        self.assertEqual(self.bash(repo, "rm engine/lib/test_units.py"), PASSED)

    def test_copying_over_a_test_named_module_is_refused(self):
        repo = self.committed_repo(TEST_NAMED_MODULE)
        self.assertEqual(self.bash(repo, "cp engine/lib/other.py engine/lib/test_units.py"), REFUSED)


class BypassAttempts(FalsePositiveHome):
    def nested_cwd(self, repo):
        nest = repo / "nest"
        (nest / ".git").mkdir(parents=True)
        return nest

    def test_git_rm_cached_undone_by_no_cached_is_refused(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(repo, "git rm --cached --no-cached " + OLD_TEST), REFUSED)

    def test_git_rm_dry_run_undone_by_no_dry_run_is_refused(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(repo, "git rm -n --no-dry-run " + OLD_TEST), REFUSED)

    def test_shell_write_into_the_outer_tests_folder_from_an_empty_nested_git_folder_is_refused(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(self.nested_cwd(repo), "echo hi > " + str(repo / NEW_TEST)), REFUSED)

    def test_write_tool_into_the_outer_tests_folder_from_an_empty_nested_git_folder_is_refused(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.write(self.nested_cwd(repo), repo / NEW_TEST, SIZE_TEST), (DENY, [GATE]))

    def test_git_checkout_of_a_test_file_through_git_dir_from_another_folder_is_refused(self):
        repo = self.committed_repo(TRACKED_TESTS)
        command = f"GIT_DIR={repo}/.git GIT_WORK_TREE={repo} git -C /tmp checkout HEAD -- {OLD_TEST}"
        self.assertEqual(self.bash(repo, command), REFUSED)


class XargsReaders(FalsePositiveHome):
    def test_xargs_feeding_ps_through_a_replace_marker_passes(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(repo, "git ls-files | xargs -I{} ps -p {}"), PASSED)

    def test_xargs_ripgrep_without_an_option_end_marker_is_refused(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(repo, "find . -name '*.txt' | xargs rg needle"), (DENY, ["dynamic_write_target"]))

    def test_xargs_ripgrep_with_an_option_end_marker_passes(self):
        repo = self.committed_repo(TRACKED_TESTS)
        self.assertEqual(self.bash(repo, "find . -name '*.txt' | xargs rg -- needle"), PASSED)


class WorktreeRewrites(FalsePositiveHome):
    def test_git_reset_hard_is_refused_where_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(TRACKED_TESTS), "git reset --hard"), REFUSED)

    def test_git_reset_hard_passes_where_no_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(NO_TESTS), "git reset --hard"), PASSED)

    def test_git_merge_of_a_branch_that_changes_a_test_file_is_refused(self):
        repo = self.commit_on_other(self.committed_repo(TRACKED_TESTS), {OLD_TEST: SIZE_TEST + "\n"})
        self.assertEqual(self.bash(repo, "git merge other"), REFUSED)

    def test_git_merge_of_a_branch_that_changes_only_a_non_test_file_passes(self):
        repo = self.commit_on_other(self.committed_repo(TRACKED_TESTS), {"notes.txt": "other needle\n"})
        self.assertEqual(self.bash(repo, "git merge other"), PASSED)

    def test_git_merge_passes_where_no_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(NO_TESTS), "git merge other"), PASSED)

    def test_git_stash_is_refused_where_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(TRACKED_TESTS), "git stash"), REFUSED)

    def test_git_stash_passes_where_no_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(NO_TESTS), "git stash"), PASSED)

    def test_git_checkout_of_a_branch_is_refused_where_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(TRACKED_TESTS), "git checkout other"), REFUSED)

    def test_git_checkout_of_a_branch_passes_where_no_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(NO_TESTS), "git checkout other"), PASSED)

    def test_git_read_tree_with_update_is_refused_where_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(TRACKED_TESTS), "git read-tree -u -m HEAD"), REFUSED)

    def test_git_read_tree_with_update_passes_where_no_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(NO_TESTS), "git read-tree -u -m HEAD"), PASSED)

    def test_git_checkout_index_is_refused_where_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(TRACKED_TESTS), "git checkout-index -a"), REFUSED)

    def test_git_checkout_index_passes_where_no_tests_are_tracked(self):
        self.assertEqual(self.bash(self.committed_repo(NO_TESTS), "git checkout-index -a"), PASSED)


if __name__ == "__main__":
    unittest.main()
