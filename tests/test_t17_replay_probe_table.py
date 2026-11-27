from fake_repo_home import DENY, OLD_TEST, PASSED, PLAIN_MODULE, REFUSED, SIZE_TEST, FakeRepoCase, Repo
import unittest

from after_run_home import CAUGHT, LET_THROUGH, AfterRunCase, Project, project_files


TRACKED = "tracked"
TEST_CHANGED = "test-changed"
STASHED = "stashed"
CLONE = "clone-scratch"
SCRATCH = "loop-scratch"
NEW_TEST = "tests/test_new.py"
URL = "https://example.com/repo.git"
SESSION_LOG = ".claude/projects/p/session.jsonl"
READ_LOOP = ("grep -n needle notes.txt | awk -F: '{print $1}' | tail -3"
             " | while read n; do sed -n \"${n}p\" notes.txt; done")
TRACKED_FILES = {OLD_TEST: SIZE_TEST, "src/app.py": PLAIN_MODULE, "notes.txt": "needle\n"}
STASH_TIP = "stash-0"
STASH_REFS = {"HEAD": "base", "main": "base", "refs/heads/main": "base", "refs/stash": STASH_TIP, "stash@{0}": STASH_TIP}


def stashed_repo():
    changed = {**TRACKED_FILES, OLD_TEST: SIZE_TEST + "\n"}
    fields = {"revisions": {"base": TRACKED_FILES, STASH_TIP: changed},
              "parents": {"base": [], STASH_TIP: ["base"]}, "refs": STASH_REFS}
    return Repo(TRACKED_FILES, disk={".git/refs/stash": STASH_TIP + "\n"}, fields=fields)


class ReplayHome(FakeRepoCase):
    projects = {
        TRACKED: Repo(TRACKED_FILES),
        TEST_CHANGED: Repo(TRACKED_FILES, branches={"other": [{OLD_TEST: SIZE_TEST + "\n"}]}),
        STASHED: stashed_repo(),
    }

    @classmethod
    def arrange(cls):
        (cls.root / CLONE).mkdir()
        (cls.root / SCRATCH).mkdir()
        (cls.root / TRACKED / "a").mkdir()
        (cls.root / TRACKED / "b").mkdir()
        log = cls.home / SESSION_LOG
        log.parent.mkdir(parents=True)
        log.write_text('{"rule": "test_write_gate"}\n', encoding="utf-8")


class ReplayedReadersAndHistoryCommandsPass(ReplayHome):
    def test_diff_of_a_test_file_piped_into_awk_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "git diff -U3 -- some.test.mjs | awk '{print $1}'"), PASSED)

    def test_shallow_clone_into_a_temp_folder_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "git clone --depth 1 " + URL + " " + str(self.root / CLONE)), PASSED)

    def test_loop_that_lists_worktrees_and_branches_of_temp_folders_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, f'for d in {self.root / CLONE} {self.root / SCRATCH}; do git -C "$d" worktree list; git -C "$d" branch; done'), PASSED)

    def test_unquoted_loop_over_plain_folder_names_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "for d in a b; do git -C $d worktree list; git -C $d branch; done"), PASSED)

    def test_quoted_loop_over_plain_folder_names_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, 'for d in a b; do git -C "$d" worktree list; git -C "$d" branch; done'), PASSED)

    def test_stash_pop_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "git stash pop"), PASSED)

    def test_stash_apply_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "git stash apply"), PASSED)

    def test_stash_pop_of_stashed_test_changes_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(STASHED, "git stash pop"), PASSED)

    def test_stash_apply_of_stashed_test_changes_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(STASHED, "git stash apply"), PASSED)

    def test_restore_of_the_source_folder_from_a_stash_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "git restore --source=stash@{0} --worktree -- src/"), PASSED)

    def test_read_tree_into_a_scratch_index_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "GIT_INDEX_FILE=tmp git read-tree HEAD"), PASSED)

    def test_tracked_files_searched_through_xargs_rg_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "git ls-files | xargs rg -n needle"), PASSED)

    def test_grep_on_a_session_log_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "grep -n test_write_gate " + str(self.home / SESSION_LOG)), PASSED)

    def test_grep_awk_tail_while_read_sed_loop_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, READ_LOOP), PASSED)

    def test_merge_of_a_branch_with_test_changes_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TEST_CHANGED, "git merge other"), PASSED)

    def test_revert_of_a_commit_with_test_changes_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TEST_CHANGED, "git revert other-0"), PASSED)

    def test_cherry_pick_of_a_commit_with_test_changes_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TEST_CHANGED, "git cherry-pick other-0"), PASSED)

    def test_shell_payload_with_a_reading_curl_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "sh -c 'curl -s " + URL + "'"), PASSED)


class TestWritesStayRefusedForOtherAgents(ReplayHome):
    def test_write_tool_into_a_new_test_file_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.write(TRACKED, NEW_TEST, SIZE_TEST), REFUSED)

    def test_echo_into_a_test_file_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(TRACKED, "echo x > " + OLD_TEST), REFUSED)

    def test_copy_onto_a_test_file_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(TRACKED, "cp src/app.py " + OLD_TEST), REFUSED)

    def test_removal_of_a_test_file_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(TRACKED, "rm " + OLD_TEST), REFUSED)

    def test_restore_of_the_tests_folder_from_a_stash_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(STASHED, "git restore --source=stash@{0} -- tests/"), REFUSED)

    def test_restore_of_the_tests_folder_from_a_stash_with_the_short_flag_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(STASHED, "git restore -s stash@{0} -- tests/"), REFUSED)

    def test_checkout_of_the_tests_folder_from_a_stash_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(STASHED, "git checkout stash@{0} -- tests/"), REFUSED)

    def test_checkout_of_a_test_file_from_head_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(TRACKED, "git checkout HEAD -- " + OLD_TEST), REFUSED)

    def test_restore_of_a_test_file_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(TRACKED, "git restore " + OLD_TEST), REFUSED)


class WriteBypassShapesStayRefused(ReplayHome):
    def test_merge_chained_with_a_test_write_is_refused(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.bash(TEST_CHANGED, "git merge other && echo x > " + OLD_TEST), REFUSED)

    def test_stash_pop_chained_with_a_test_write_is_refused(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.bash(STASHED, "git stash pop && cp src/app.py " + OLD_TEST), REFUSED)

    def test_clone_into_the_tests_folder_is_refused(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.bash(TRACKED, "git clone --depth 1 " + URL + " tests/clone"), REFUSED)

    def test_clone_into_the_tests_folder_after_a_double_dash_is_refused(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.bash(TRACKED, "git clone --depth 1 -- " + URL + " tests/clone"), REFUSED)

    def test_clone_with_a_separate_git_dir_into_the_tests_folder_is_refused(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.bash(TRACKED, "git clone --separate-git-dir " + str(self.root / CLONE) + " " + URL + " tests/clone"), REFUSED)

    def test_shell_payload_with_curl_output_into_a_test_file_is_refused(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.bash(TRACKED, "sh -c 'curl -s -o " + OLD_TEST + " " + URL + "'"), REFUSED)

    def test_shell_payload_with_curl_redirected_into_a_test_file_is_refused(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.bash(TRACKED, "sh -c 'curl -s " + URL + " > " + OLD_TEST + "'"), REFUSED)


PASSING_MINUS_C_WORDS = (
    "git -C $d status",
    "git -C $REPO log --oneline -3",
    "git -C $TMPDIR status",
    "git -C ${REPO} status",
    "git --no-pager -C $d status",
    "git -C */ status",
    "git -C * status",
)
READING_SUBCOMMANDS = (
    "branch", "branch -a -vv", "worktree list", "diff --stat", "show HEAD",
    "rev-parse --show-toplevel", "ls-files", "remote -v", "config --get user.name",
    "worktree add ../x",
)
READING_LOOPS = (
    "for d in */; do git -C $d status; done",
    "for d in $(ls); do git -C $d log -1; done",
    "for d in */; do echo $d; git -C $d worktree list; git -C $d branch; done",
)
QUOTED_MINUS_C_WORDS = (
    'git -C "$d" status',
    'git -C "$(dirname x)" status',
    'git -C "$(git rev-parse --show-toplevel)" status',
)
BRACED_REMOVAL = "git -C {.,rm,-f," + OLD_TEST + "} status"
LOOPED_REMOVAL = "for f in " + OLD_TEST + "; do rm $f; done"


class UnknownMinusCFoldersThatOnlyReadPass(ReplayHome):
    def test_unset_and_globbed_minus_c_folders_pass(self):
        """A test must verify what the code does for its client, never how it does it."""
        for command in PASSING_MINUS_C_WORDS:
            with self.subTest(command=command):
                self.assertEqual(self.bash(TRACKED, command), PASSED)

    def test_reading_subcommands_after_an_unset_minus_c_folder_pass(self):
        """A test must verify what the code does for its client, never how it does it."""
        for subcommand in READING_SUBCOMMANDS:
            with self.subTest(subcommand=subcommand):
                self.assertEqual(self.bash(TRACKED, "git -C $d " + subcommand), PASSED)

    def test_loops_that_run_reading_git_over_folder_words_pass(self):
        """A test must verify what the code does for its client, never how it does it."""
        for command in READING_LOOPS:
            with self.subTest(command=command):
                self.assertEqual(self.bash(TRACKED, command), PASSED)

    def test_quoted_minus_c_folders_pass(self):
        """A test must verify what the code does for its client, never how it does it."""
        for command in QUOTED_MINUS_C_WORDS:
            with self.subTest(command=command):
                self.assertEqual(self.bash(TRACKED, command), PASSED)

    def test_loop_that_writes_plain_files_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.bash(TRACKED, "for f in a b; do echo x > $f; done"), PASSED)


class MinusCWordsTheShellCanSplitAreRefused(ReplayHome):
    def test_braced_minus_c_word_that_expands_into_a_test_removal_is_refused(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.bash(TRACKED, BRACED_REMOVAL), REFUSED)


class WritingGitAfterAnUnknownMinusCFolderIsRefused(ReplayHome):
    def test_loop_that_removes_a_named_test_file_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(TRACKED, LOOPED_REMOVAL), REFUSED)


AFTER_RUN = "after-run"
AWK_TARGET = "tests/x"
AFTER_RUN_SHAPES = {
    "awk": "awk '{print > \"" + AWK_TARGET + "\"}' src/app.py",
    "sed": "git ls-files | xargs sed -i s/2]/3]/",
    "rg": "git ls-files | xargs rg --pre rm needle",
}


class ReplayedBypassShapesAreCaughtAfterTheRun(AfterRunCase):
    projects = {AFTER_RUN: Project(project_files({"src/app.py": PLAIN_MODULE}))}

    def effect(self, kind):
        return {
            "awk": lambda: self.change(AFTER_RUN, AWK_TARGET, PLAIN_MODULE),
            "sed": lambda: self.change(AFTER_RUN, OLD_TEST, SIZE_TEST.replace("2]", "3]")),
            "rg": lambda: self.remove(AFTER_RUN, OLD_TEST),
        }[kind]

    def verdicts(self):
        return {kind: self.shell(AFTER_RUN, command, self.effect(kind), run=kind) for kind, command in AFTER_RUN_SHAPES.items()}

    def test_awk_redirect_and_xargs_test_changes_pass_before_the_run_and_block_after_it(self):
        """Use a parameterized test for several inputs."""
        self.assertEqual(self.verdicts(), dict.fromkeys(AFTER_RUN_SHAPES, (LET_THROUGH, CAUGHT)))


if __name__ == "__main__":
    unittest.main()
