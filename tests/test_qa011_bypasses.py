from quiet_git import disable_background_maintenance
from false_positive_home import DENY, GATE, IDENTITY, OLD_TEST, PASSED, REFUSED, SIZE_TEST, FalsePositiveHome
import shlex
import subprocess
import unittest


TRACKED_TESTS = {OLD_TEST: SIZE_TEST, "README.md": "Short.\n"}
TEST_CHANGE = {OLD_TEST: SIZE_TEST + "\n"}
DOCS_CHANGE = {"docs/guide.md": "Guide.\n"}
SCRATCH_TEST = "scratch/test_probe.py"
SCRATCH_RULE = "scratch/\n"
WRITE_REFUSED = (DENY, [GATE])
LINK_REFUSED = (DENY, ["state_deletion", "state_mutation"])
TAR_INTO_SCRATCH = "git archive HEAD | tar -x --insecure -C .claude/tmp/x"


class GitHome(FalsePositiveHome):
    def git(self, repo, *arguments):
        subprocess.run(["git", "-C", str(repo), *IDENTITY, *arguments], check=True, capture_output=True)

    def repo_on_main(self, files):
        repo = self.committed_repo(files)
        self.git(repo, "branch", "-M", "main")
        return repo

    def branch_with(self, repo, name, *changes):
        self.git(repo, "checkout", "-q", "-b", name, "main")
        for number, change in enumerate(changes):
            self.place(repo, change)
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-qm", f"{name} {number}")
        self.git(repo, "checkout", "-q", "main")
        return repo


class IncomingRefsThatMoveOrSpan(GitHome):
    def test_merge_of_a_docs_only_branch_that_a_fetch_first_repoints_is_refused(self):
        repo = self.branch_with(self.repo_on_main(TRACKED_TESTS), "docsonly", DOCS_CHANGE)
        self.branch_with(repo, "x", TEST_CHANGE)
        self.assertEqual(self.bash(repo, "git fetch . +x:docsonly && git merge docsonly"), REFUSED)

    def test_cherry_pick_of_a_range_whose_tip_changes_only_docs_is_refused(self):
        repo = self.branch_with(self.repo_on_main(TRACKED_TESTS), "x", TEST_CHANGE, DOCS_CHANGE)
        self.assertEqual(self.bash(repo, "git cherry-pick ^main x"), REFUSED)


class ScratchFolderThatBorrowsTheProjectRepo(GitHome):
    def scratch_worktree(self, repo, relative, git_file):
        folder = repo / relative
        folder.mkdir(parents=True)
        (folder / ".git").write_text(git_file, encoding="utf-8")
        return repo

    def test_hard_reset_through_a_git_file_pointing_at_the_project_git_dir_is_refused(self):
        repo = self.committed_repo({".gitignore": SCRATCH_RULE, **TRACKED_TESTS})
        self.scratch_worktree(repo, "scratch/w", "gitdir: " + str(repo / ".git") + "\n")
        self.assertEqual(self.bash(repo, "git -C scratch/w reset --hard"), REFUSED)

    def test_hard_reset_of_a_separate_clone_under_ignored_scratch_passes(self):
        repo = self.committed_repo({".gitignore": SCRATCH_RULE, **TRACKED_TESTS})
        clone = repo / "scratch" / "up"
        clone.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        disable_background_maintenance(str(clone))
        self.place(clone, {OLD_TEST: SIZE_TEST})
        self.git(clone, "add", ".")
        self.git(clone, "commit", "-qm", "up")
        self.assertEqual(self.bash(repo, "git -C scratch/up reset --hard"), PASSED)


class InsecureTarIntoScratch(GitHome):
    def test_insecure_tar_extraction_from_stdin_into_ignored_scratch_is_refused(self):
        repo = self.committed_repo({".gitignore": ".claude/tmp/\n", **TRACKED_TESTS})
        (repo / ".claude/tmp/x").mkdir(parents=True)
        self.assertEqual(self.bash(repo, TAR_INTO_SCRATCH), REFUSED)


class IgnoreRulesThatDoNotExemptTests(GitHome):
    def test_folder_ignored_only_by_an_uncommitted_gitignore_does_not_exempt_a_new_test(self):
        repo = self.committed_repo({"README.md": "Short.\n"})
        self.place(repo, {".gitignore": SCRATCH_RULE})
        self.assertEqual(self.write(repo, repo / SCRATCH_TEST, SIZE_TEST), WRITE_REFUSED)

    def test_folder_ignored_only_by_an_uncommitted_line_of_a_committed_gitignore_does_not_exempt_a_new_test(self):
        repo = self.committed_repo({"README.md": "Short.\n", ".gitignore": "build/\n"})
        self.place(repo, {".gitignore": "build/\n" + SCRATCH_RULE})
        self.assertEqual(self.write(repo, repo / SCRATCH_TEST, SIZE_TEST), WRITE_REFUSED)

    def test_folder_ignored_only_by_the_info_exclude_file_does_not_exempt_a_new_test(self):
        repo = self.committed_repo({"README.md": "Short.\n"})
        self.place(repo, {".git/info/exclude": SCRATCH_RULE})
        self.assertEqual(self.write(repo, repo / SCRATCH_TEST, SIZE_TEST), WRITE_REFUSED)

    def test_folder_ignored_only_by_the_global_excludes_file_does_not_exempt_a_new_test(self):
        repo = self.committed_repo({"README.md": "Short.\n"})
        self.place(self.home, {".config/git/ignore": SCRATCH_RULE})
        self.assertEqual(self.write(repo, repo / SCRATCH_TEST, SIZE_TEST), WRITE_REFUSED)


class RemovingTreesThatHoldProtectedLinks(GitHome):
    def tree_with_link(self):
        protected = self.home / ".adw"
        self.place(protected, {"state.json": "{}\n"})
        tree = self.root / "tree"
        tree.mkdir()
        (tree / ".adw").symlink_to(protected, target_is_directory=True)
        return tree

    def test_recursive_removal_of_a_temp_tree_holding_a_link_to_a_protected_folder_passes(self):
        repo = self.committed_repo({"README.md": "Short.\n"})
        tree = self.tree_with_link()
        self.assertEqual(self.bash(repo, "rm -rf " + shlex.quote(str(tree))), PASSED)

    def test_recursive_removal_through_the_link_with_a_trailing_slash_is_refused(self):
        repo = self.committed_repo({"README.md": "Short.\n"})
        tree = self.tree_with_link()
        self.assertEqual(self.bash(repo, "rm -rf " + shlex.quote(str(tree / ".adw")) + "/"), LINK_REFUSED)


if __name__ == "__main__":
    unittest.main()
