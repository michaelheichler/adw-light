from fake_repo_home import PASSED, README, REFUSED, SIZE_TEST, FakeRepoCase, Repo
import os
import shlex
import unittest
import uuid


PROJECT = Repo({"tests/test_a.py": SIZE_TEST, **README})
REPO = "repo"
BESIDE = "wt-x"
ADD = "git worktree add --detach "
AT_HEAD = " HEAD"
INNER = "./inner"
REMOVE_PROJECT_TESTS = " && rm -rf tests"
PRINTF_INTO_TRACKED_TEST = " && printf a > tests/test_a.py"
VBW_ADD = "git worktree add -b adw/test-author ../vbw-test-author origin/main"
NESTED = "outer/repo"


def slash_tmp_target():
    return os.path.join(os.sep, "tmp", "adw-wt-" + uuid.uuid4().hex)


class WorktreeHome(FakeRepoCase):
    projects = {REPO: PROJECT}

    def add_into(self, target):
        return self.bash(REPO, ADD + target + AT_HEAD)


class WorktreeAddIntoSystemTemp(WorktreeHome):
    def test_add_into_a_system_temp_folder_beside_the_project_passes(self):
        self.assertEqual(self.add_into(shlex.quote(str(self.root / BESIDE))), PASSED)

    def test_add_into_slash_tmp_passes(self):
        self.assertEqual(self.add_into(slash_tmp_target()), PASSED)

    def test_add_into_a_folder_inside_the_project_passes(self):
        self.assertEqual(self.add_into(INNER), PASSED)

    def test_add_into_an_absolute_folder_inside_the_project_passes(self):
        self.assertEqual(self.add_into(shlex.quote(str(self.root / REPO / "inner"))), PASSED)

    def test_add_into_slash_tmp_followed_by_removing_project_tests_is_refused(self):
        self.assertEqual(self.bash(REPO, ADD + slash_tmp_target() + AT_HEAD + REMOVE_PROJECT_TESTS), REFUSED)


class WorktreeAddIntoTmpdirBesideTheProject(WorktreeHome):
    @classmethod
    def arrange(cls):
        (cls.root / "tmpdir").mkdir()

    def hook_environment(self):
        return {"TMPDIR": str(self.root / "tmpdir")}

    def test_add_into_tmpdir_passes(self):
        self.assertEqual(self.add_into('"$TMPDIR"/wt-x'), PASSED)


class WorktreeAddIntoTmpdirAroundTheProject(WorktreeHome):
    def hook_environment(self):
        return {"TMPDIR": str(self.root)}

    def test_add_into_a_tmpdir_path_that_lands_inside_the_project_passes(self):
        self.assertEqual(self.add_into('"$TMPDIR"/repo/inner'), PASSED)


class WorktreeAddChainedOrIntoHome(WorktreeHome):
    def test_fetch_then_worktree_add_beside_the_project_passes(self):
        self.assertEqual(self.bash(REPO, "git fetch origin && " + VBW_ADD), PASSED)

    def test_add_into_a_home_folder_passes(self):
        self.assertEqual(self.add_into('"$HOME"/wt-x'), PASSED)

    def test_add_followed_by_printf_into_a_tracked_test_is_refused(self):
        self.assertEqual(self.bash(REPO, ADD + "../wt-y" + AT_HEAD + PRINTF_INTO_TRACKED_TEST), REFUSED)


class WorktreeAddFromAProjectInsideAnotherWorkTree(WorktreeHome):
    projects = {"outer": Repo(README), NESTED: PROJECT}

    def test_vbw_branch_worktree_add_beside_the_nested_project_passes(self):
        self.assertEqual(self.bash(NESTED, VBW_ADD), PASSED)


class WorktreeRemoveAndPruneKeepTodaysDecisions(WorktreeHome):
    projects = {REPO: PROJECT, BESIDE: Repo(PROJECT.files, shares=REPO), REPO + "/inner": Repo(PROJECT.files, shares=REPO)}

    def test_forced_remove_of_a_temp_worktree_passes(self):
        self.assertEqual(self.bash(REPO, "git worktree remove --force " + shlex.quote(str(self.root / BESIDE))), PASSED)

    def test_forced_remove_of_a_worktree_inside_the_project_passes(self):
        self.assertEqual(self.bash(REPO, "git worktree remove --force " + INNER), PASSED)

    def test_prune_passes(self):
        self.assertEqual(self.bash(REPO, "git worktree prune"), PASSED)


if __name__ == "__main__":
    unittest.main()
