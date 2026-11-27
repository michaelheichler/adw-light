from fake_repo_home import (DEBT_FILE, DENY, GATE, LOCK, OBSERVED, OLD_TEST, PASSED, PLAIN_MODULE, REFUSED, SIZE_TEST,
                            FakeRepoCase, Repo, loose)
import unittest


NEW_TEST = "tests/test_x.py"
CHECKED_OUT_TEST = "tests/test_a.py"
TRACKED_TESTS = {OLD_TEST: SIZE_TEST, "notes.txt": "needle\n"}
TRACKED = "tracked"
DEBT = "debt"
NO_TESTS = "no-tests"
TEST_NAMED = "test-named"
TEST_CHANGED = "test-changed"
NOTES_CHANGED = "notes-changed"
SCRATCH = "scratch"
NEST = "tracked/nest"
CHECKOUT = "checkout"


class GateHome(FakeRepoCase):
    projects = {
        TRACKED: Repo(TRACKED_TESTS),
        DEBT: Repo({OLD_TEST: DEBT_FILE}),
        NO_TESTS: Repo({"src/app.py": PLAIN_MODULE, "notes.txt": "needle\n"}),
        TEST_NAMED: Repo({"engine/lib/test_units.py": PLAIN_MODULE, "engine/lib/other.py": PLAIN_MODULE}),
        TEST_CHANGED: Repo(TRACKED_TESTS, branches={"other": [{OLD_TEST: SIZE_TEST + "\n"}]}),
        NOTES_CHANGED: Repo(TRACKED_TESTS, branches={"other": [{"notes.txt": "other needle\n"}]}),
        SCRATCH: loose({"test_probe.py": SIZE_TEST}),
        CHECKOUT: Repo({CHECKED_OUT_TEST: SIZE_TEST}),
    }

    @classmethod
    def arrange(cls):
        (cls.root / NEST / ".git").mkdir(parents=True)


class IndexOnlyAndScratchCommands(GateHome):
    def test_git_rm_cached_on_a_test_file_passes_the_test_write_gate(self):
        self.assertEqual(self.bash(TRACKED, "git rm --cached " + OLD_TEST), PASSED)

    def test_git_rm_dry_run_on_a_test_file_passes_the_test_write_gate(self):
        self.assertEqual(self.bash(TRACKED, "git rm -n " + OLD_TEST), PASSED)

    def test_git_rm_cached_that_hides_a_debt_file_passes_and_only_notes_the_wording_debt(self):
        verdict = self.bash(DEBT, "git rm --cached " + OLD_TEST)
        self.assertEqual((verdict, self.ledger_outcomes(LOCK)), ((None, [LOCK]), [(LOCK, OBSERVED)]))

    def test_removing_a_temp_folder_with_test_named_files_outside_the_repo_passes(self):
        self.assertEqual(self.bash(TRACKED, "rm -rf " + str(self.root / SCRATCH)), PASSED)

    def test_git_archive_extracted_into_a_temp_folder_outside_the_repo_passes(self):
        self.assertEqual(self.bash(TRACKED, "git archive HEAD | tar -x -C " + str(self.root / SCRATCH)), PASSED)

    def test_git_archive_extracted_into_the_tests_folder_is_refused(self):
        self.assertEqual(self.bash(TRACKED, "git archive HEAD | tar -x -C tests"), REFUSED)

    def test_deleting_a_test_named_module_that_holds_no_tests_passes(self):
        self.assertEqual(self.bash(TEST_NAMED, "rm engine/lib/test_units.py"), PASSED)

    def test_copying_over_a_test_named_module_is_refused(self):
        self.assertEqual(self.bash(TEST_NAMED, "cp engine/lib/other.py engine/lib/test_units.py"), REFUSED)


class BypassAttempts(GateHome):
    def test_git_rm_cached_undone_by_no_cached_is_refused(self):
        self.assertEqual(self.bash(TRACKED, "git rm --cached --no-cached " + OLD_TEST), REFUSED)

    def test_git_rm_dry_run_undone_by_no_dry_run_is_refused(self):
        self.assertEqual(self.bash(TRACKED, "git rm -n --no-dry-run " + OLD_TEST), REFUSED)

    def test_shell_write_into_the_outer_tests_folder_from_an_empty_nested_git_folder_is_refused(self):
        command = "echo hi > " + str(self.root / TRACKED / NEW_TEST)
        self.assertEqual(self.bash(TRACKED, command, cwd=self.root / NEST), REFUSED)

    def test_write_tool_into_the_outer_tests_folder_from_an_empty_nested_git_folder_is_refused(self):
        self.assertEqual(self.write(TRACKED, NEW_TEST, SIZE_TEST, cwd=self.root / NEST), (DENY, [GATE]))

    def test_git_checkout_of_a_test_file_through_git_dir_from_another_folder_is_refused(self):
        repo = self.root / TRACKED
        command = f"GIT_DIR={repo}/.git GIT_WORK_TREE={repo} git -C /tmp checkout HEAD -- {OLD_TEST}"
        self.assertEqual(self.bash(TRACKED, command), REFUSED)


class XargsReaders(GateHome):
    def test_xargs_feeding_ps_through_a_replace_marker_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(TRACKED, "git ls-files | xargs -I{} ps -p {}"), PASSED)

    def test_xargs_ripgrep_with_an_option_end_marker_passes(self):
        self.assertEqual(self.bash(TRACKED, "find . -name '*.txt' | xargs rg -- needle"), PASSED)


class ReplayReaders(GateHome):
    def test_replay_readers_preserve_test_write_blocks(self):
        command = "git diff -U3 -- " + OLD_TEST + " | awk '{print}' > " + OLD_TEST
        self.assertEqual(self.bash(TRACKED, command), REFUSED)


class WorktreeRewrites(GateHome):
    def test_git_reset_hard_passes_where_no_tests_are_tracked(self):
        self.assertEqual(self.bash(NO_TESTS, "git reset --hard"), PASSED)

    def test_git_merge_of_a_branch_that_changes_a_test_file_passes(self):
        self.assertEqual(self.bash(TEST_CHANGED, "git merge other"), PASSED)

    def test_git_merge_of_a_branch_that_changes_only_a_non_test_file_passes(self):
        self.assertEqual(self.bash(NOTES_CHANGED, "git merge other"), PASSED)

    def test_git_merge_passes_where_no_tests_are_tracked(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(NO_TESTS, "git merge other"), PASSED)

    def test_stash_pop_and_apply_allow_stashed_test_changes(self):
        self.assertEqual(self.bash(TRACKED, "git stash apply && git stash pop"), PASSED)

    def test_restore_of_the_tests_folder_from_a_stash_is_refused(self):
        self.assertEqual(self.bash(TRACKED, "git restore --source=stash@{0} -- tests/"), REFUSED)

    def test_checkout_of_a_test_file_from_head_is_refused(self):
        self.assertEqual(self.bash(CHECKOUT, "git checkout HEAD -- " + CHECKED_OUT_TEST), REFUSED)

    def test_git_stash_passes_where_no_tests_are_tracked(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(NO_TESTS, "git stash"), PASSED)

    def test_git_checkout_of_a_branch_passes_where_no_tests_are_tracked(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(NO_TESTS, "git checkout other"), PASSED)

    def test_git_read_tree_with_update_passes_where_no_tests_are_tracked(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(NO_TESTS, "git read-tree -u -m HEAD"), PASSED)

    def test_git_checkout_index_passes_where_no_tests_are_tracked(self):
        self.assertEqual(self.bash(NO_TESTS, "git checkout-index -a"), PASSED)


if __name__ == "__main__":
    unittest.main()
