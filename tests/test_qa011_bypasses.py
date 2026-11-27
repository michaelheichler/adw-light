from fake_repo_home import DENY, GATE, INFO_EXCLUDE, OLD_TEST, PASSED, README, SIZE_TEST, FakeRepoCase, Repo, place
from lib.shell_self_protection import command_findings
import shlex
import unittest


TRACKED_TESTS = {OLD_TEST: SIZE_TEST, **README}
TEST_CHANGE = {OLD_TEST: SIZE_TEST + "\n"}
DOCS_CHANGE = {"docs/guide.md": "Guide.\n"}
SCRATCH_TEST = "scratch/test_probe.py"
SCRATCH_RULE = "scratch/\n"
WRITE_REFUSED = (DENY, [GATE])
LINK_REFUSED = (DENY, ["state_deletion"])
TAR_INTO_SCRATCH = "git archive HEAD | tar -x --insecure -C .claude/tmp/x"
GLOBAL_EXCLUDES = ".config/git/ignore"
GLOBAL_RULE = {"source": GLOBAL_EXCLUDES, "pattern": "scratch/", "global": True}
LIVE_STATE = ".adw-lite"
RETIRED_GUARD_COMMANDS = (
    "git commit --no-verify -m fix", "git commit -n -m fix", "ADW_FUNC_BLOCK_LINES=500 make",
    "ADW_SENTENCE_WORD_CAP=90 make", "bash install.sh", "python3 merge-claude-settings.py", "adw update --claude",
)


class IncomingRefsThatMoveOrSpan(FakeRepoCase):
    projects = {
        "repointed": Repo(TRACKED_TESTS, branches={"docsonly": [DOCS_CHANGE], "x": [TEST_CHANGE]}),
        "range": Repo(TRACKED_TESTS, branches={"x": [TEST_CHANGE, DOCS_CHANGE]}),
    }

    def test_merge_of_a_branch_that_a_fetch_first_repoints_at_test_changes_passes(self):
        self.assertEqual(self.bash("repointed", "git fetch . +x:docsonly && git merge docsonly"), PASSED)

    def test_cherry_pick_of_a_range_that_changes_a_test_file_passes(self):
        self.assertEqual(self.bash("range", "git cherry-pick ^main x"), PASSED)


class ScratchFolderThatBorrowsTheProjectRepo(FakeRepoCase):
    projects = {
        "repo": Repo({".gitignore": SCRATCH_RULE, **TRACKED_TESTS}),
        "repo/scratch/w": Repo({}, fields={"index": TRACKED_TESTS}, shares="repo"),
        "repo/scratch/up": Repo({OLD_TEST: SIZE_TEST}),
    }

    @classmethod
    def arrange(cls):
        folder = cls.root / "repo/scratch/w"
        (folder / ".git").rmdir()
        (folder / ".git").write_text("gitdir: " + str(cls.root / "repo/.git") + "\n", encoding="utf-8")

    def test_hard_reset_of_a_separate_clone_under_ignored_scratch_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash("repo", "git -C scratch/up reset --hard"), PASSED)


class IgnoreRulesThatDoNotExemptTests(FakeRepoCase):
    projects = {
        "uncommitted": Repo(README, {".gitignore": SCRATCH_RULE}),
        "uncommitted-line": Repo({**README, ".gitignore": "build/\n"}, {".gitignore": "build/\n" + SCRATCH_RULE}),
        "info-exclude": Repo(README, {INFO_EXCLUDE: SCRATCH_RULE}),
        "global": Repo(README, fields={"ignore": [GLOBAL_RULE]}),
    }

    @classmethod
    def arrange(cls):
        place(cls.home, {GLOBAL_EXCLUDES: SCRATCH_RULE})

    def test_folder_ignored_only_by_an_uncommitted_gitignore_does_not_exempt_a_new_test(self):
        self.assertEqual(self.write("uncommitted", SCRATCH_TEST, SIZE_TEST), WRITE_REFUSED)

    def test_folder_ignored_only_by_an_uncommitted_line_of_a_committed_gitignore_does_not_exempt_a_new_test(self):
        self.assertEqual(self.write("uncommitted-line", SCRATCH_TEST, SIZE_TEST), WRITE_REFUSED)

    def test_folder_ignored_only_by_the_info_exclude_file_does_not_exempt_a_new_test(self):
        self.assertEqual(self.write("info-exclude", SCRATCH_TEST, SIZE_TEST), WRITE_REFUSED)

    def test_folder_ignored_only_by_the_global_excludes_file_does_not_exempt_a_new_test(self):
        self.assertEqual(self.write("global", SCRATCH_TEST, SIZE_TEST), WRITE_REFUSED)


class RemovingTreesThatHoldProtectedLinks(FakeRepoCase):
    projects = {"repo": Repo(README)}

    @classmethod
    def arrange(cls):
        place(cls.home / LIVE_STATE, {"state.json": "{}\n"})
        (cls.root / "tree").mkdir()
        (cls.root / "tree" / LIVE_STATE).symlink_to(cls.home / LIVE_STATE, target_is_directory=True)

    def test_recursive_removal_of_a_temp_tree_holding_a_link_to_a_protected_folder_passes(self):
        self.assertEqual(self.bash("repo", "rm -rf " + shlex.quote(str(self.root / "tree"))), PASSED)

    def test_recursive_removal_through_the_link_with_a_trailing_slash_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash("repo", "rm -rf " + shlex.quote(str(self.root / "tree" / LIVE_STATE)) + "/"), LINK_REFUSED)


class RetiredSelfGuards(FakeRepoCase):
    projects = {"repo": Repo(README)}

    def test_removed_self_guards_no_longer_refuse(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        folder = self.root / "repo"

        findings = {command: command_findings(command, home=self.home, cwd=folder) for command in RETIRED_GUARD_COMMANDS}

        self.assertEqual(findings, dict.fromkeys(RETIRED_GUARD_COMMANDS, []))


if __name__ == "__main__":
    unittest.main()
