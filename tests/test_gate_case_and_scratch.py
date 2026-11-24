from quiet_git import disable_background_maintenance
from commit_gate_home import SHEBANG, CommitGateHome
from false_positive_home import IDENTITY, OLD_TEST, PASSED, REFUSED, SIZE_TEST, FalsePositiveHome
import subprocess
import unittest


COMMIT_ARM = 'git add x && git commit -m "We utilize this."'
DISPATCH = 'case "$1" in\n  sync) touch notes.txt ;;\n  commit) ' + COMMIT_ARM + ' ;;\nesac\n'
FALL_THROUGH = 'case "$1" in\n  sync) touch notes.txt ;&\n  commit) ' + COMMIT_ARM + ' ;;\nesac\n'
HEREDOC_ARM = (
    'case "$1" in\n  sync) touch notes.txt ;;\n  notes) cat > notes.txt <<EOF\nnote\nEOF\n  ;;\n'
    '  commit) ' + COMMIT_ARM + ' ;;\nesac\n'
)
SCRATCH_IGNORE = {".gitignore": ".claude/tmp/\n", OLD_TEST: SIZE_TEST}
TEST_CHANGE = {OLD_TEST: SIZE_TEST + "\n"}
DOCS_CHANGE = {"docs/guide.md": "Guide.\n"}
FLAGGED_MERGE = 'git merge --no-ff -m "sync" other'


class CaseArmSelection(CommitGateHome):
    def staged_commit_refusal(self):
        return self.refusal_of_script("reference.sh", COMMIT_ARM + "\n")

    def test_script_run_with_an_argument_that_picks_a_non_committing_arm_is_refused_for_its_committing_arm(self):
        expected = self.staged_commit_refusal()

        refusal = self.deny_reason(self.run_script(DISPATCH, "sync"))

        self.assertEqual(expected, refusal)

    def test_script_run_with_an_argument_that_picks_the_committing_arm_is_refused_like_the_commit_itself(self):
        expected = self.staged_commit_refusal()

        refusal = self.deny_reason(self.run_script(DISPATCH, "commit"))

        self.assertEqual(expected, refusal)

    def test_sourced_script_with_an_unknown_first_argument_is_refused_for_its_committing_arm(self):
        expected = self.staged_commit_refusal()
        self.place("s.sh", SHEBANG + DISPATCH)

        refusal = self.deny_reason(self.gate("source ./s.sh"))

        self.assertEqual(expected, refusal)

    def test_arm_that_falls_through_into_the_committing_arm_is_refused(self):
        expected = self.staged_commit_refusal()

        refusal = self.deny_reason(self.run_script(FALL_THROUGH, "sync"))

        self.assertEqual(expected, refusal)

    def test_heredoc_in_a_skipped_arm_keeps_the_committing_arm_refused(self):
        expected = self.staged_commit_refusal()

        refusal = self.deny_reason(self.run_script(HEREDOC_ARM, "sync"))

        self.assertEqual(expected, refusal)


class NestedScratchClones(FalsePositiveHome):
    def clone_with_tests(self, repo, relative):
        clone = repo / relative
        clone.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        disable_background_maintenance(str(clone))
        self.place(clone, {OLD_TEST: SIZE_TEST})
        subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
        subprocess.run(["git", "-C", str(clone), *IDENTITY, "commit", "-qm", "up"], check=True)
        return repo

    def test_pull_in_a_clone_under_an_ignored_scratch_folder_passes(self):
        repo = self.clone_with_tests(self.committed_repo(SCRATCH_IGNORE), ".claude/tmp/up")
        self.assertEqual(self.bash(repo, "git -C .claude/tmp/up pull"), PASSED)

    def test_pull_in_a_clone_under_a_folder_that_is_not_ignored_is_refused(self):
        repo = self.clone_with_tests(self.committed_repo(SCRATCH_IGNORE), "vendor/up")
        self.assertEqual(self.bash(repo, "git -C vendor/up pull"), REFUSED)


class ArchiveExtraction(FalsePositiveHome):
    def test_archive_extracted_into_an_ignored_scratch_folder_passes(self):
        repo = self.committed_repo(SCRATCH_IGNORE)
        (repo / ".claude/tmp/x").mkdir(parents=True)
        self.assertEqual(self.bash(repo, "git archive HEAD | tar -x -C .claude/tmp/x"), PASSED)

    def test_archive_extracted_into_the_tests_folder_of_a_repo_with_ignored_scratch_is_refused(self):
        repo = self.committed_repo(SCRATCH_IGNORE)
        self.assertEqual(self.bash(repo, "git archive HEAD | tar -x -C tests/"), REFUSED)


class MergeOfChangedPaths(FalsePositiveHome):
    def test_flagged_merge_of_a_branch_that_changes_only_docs_passes(self):
        repo = self.commit_on_other(self.committed_repo(SCRATCH_IGNORE), DOCS_CHANGE)
        self.assertEqual(self.bash(repo, FLAGGED_MERGE), PASSED)

    def test_flagged_merge_of_a_branch_that_changes_a_test_file_is_refused(self):
        repo = self.commit_on_other(self.committed_repo(SCRATCH_IGNORE), TEST_CHANGE)
        self.assertEqual(self.bash(repo, FLAGGED_MERGE), REFUSED)

    def test_merge_of_fetch_head_is_refused_even_when_it_points_at_a_docs_only_commit(self):
        repo = self.commit_on_other(self.committed_repo(SCRATCH_IGNORE), DOCS_CHANGE)
        fetched = subprocess.run(["git", "-C", str(repo), "rev-parse", "other"], capture_output=True, text=True, check=True)
        (repo / ".git/FETCH_HEAD").write_text(fetched.stdout.strip() + "\t\tbranch 'other' of .\n", encoding="utf-8")
        self.assertEqual(self.bash(repo, "git merge FETCH_HEAD"), REFUSED)


if __name__ == "__main__":
    unittest.main()
