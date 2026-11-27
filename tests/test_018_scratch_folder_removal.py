from fake_repo_home import DEBT_FILE, OLD_TEST, PASSED, README, REFUSED, SIZE_TEST, FakeRepoCase, Repo, loose
from lib.find_matches import MAX_ENTRIES
import shlex
import unittest


PROJECT = "project"
WORKTREE = "wt"
HOLDER = "holder"
HELD = HOLDER + "/inner"
CROWDED = "crowded"
TESTS_SCRATCH = "tests-scratch"
STATES = "states"
FAKE_HOME = "fakehome"
HOME_STATE = ".adw-lite/state"
SCRATCH_CONFIG = "qa-scratch"
LINKER = "linker"
LIVE_LINK = LINKER + "/live"
CHECKOUT = Repo({OLD_TEST: SIZE_TEST, **README})
LINKED = Repo(CHECKOUT.files, shares=PROJECT)
CROWD = {"f" + str(number) + ".txt": "x\n" for number in range(MAX_ENTRIES + 1)}
SCRATCH_FOLDERS = {
    CROWDED: loose({**CROWD, "sub/a.txt": "x\n"}),
    TESTS_SCRATCH: loose({OLD_TEST: SIZE_TEST}),
    STATES: loose({"state/a.txt": "x\n", "deep/state/b.txt": "x\n"}),
    FAKE_HOME: loose({HOME_STATE + "/ledger.jsonl": "{}\n"}),
    SCRATCH_CONFIG: loose({".agent-discipline.json": "{}\n", ".adw/ledger.jsonl": "{}\n"}),
}
STATE_REFUSED = ("deny", ["state_deletion", "state_mutation"])
LINK_WRITE_REFUSED = ("deny", ["state_mutation"])


class ScratchHome(FakeRepoCase):
    projects = {PROJECT: CHECKOUT, WORKTREE: LINKED, HELD: LINKED, **SCRATCH_FOLDERS}

    @classmethod
    def setUpClass(cls):
        return None

    @classmethod
    def arrange(cls):
        (cls.home / HOME_STATE).mkdir(parents=True)
        (cls.root / LINKER).mkdir()
        (cls.root / LIVE_LINK).symlink_to(cls.home / HOME_STATE, target_is_directory=True)

    def path(self, name):
        return shlex.quote(str(self.root / name))

    def remove(self, *names, verb="rm -rf "):
        return self.bash(PROJECT, verb + " ".join(self.path(name) for name in names))


class ScratchRemovalsPass(ScratchHome):
    def test_removing_a_scratch_folder_with_more_files_than_the_scan_limit_passes(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.remove(CROWDED), PASSED)

    def test_removing_a_subfolder_of_a_scratch_folder_passes(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.remove(CROWDED + "/sub"), PASSED)

    def test_removing_a_scratch_folder_that_holds_folders_named_state_passes(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.remove(STATES), PASSED)

    def test_removing_a_fake_home_that_holds_adw_lite_state_passes(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.remove(FAKE_HOME), PASSED)

    def test_scratch_config_and_ledger_do_not_block_removal(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.remove(SCRATCH_CONFIG + "/.agent-discipline.json", SCRATCH_CONFIG + "/.adw"), PASSED)


class WorktreeRemovalsRefused(ScratchHome):
    def test_removing_a_linked_worktree_under_the_temp_folder_is_refused(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.remove(WORKTREE), REFUSED)

    def test_removing_a_linked_worktree_with_a_trailing_slash_is_refused(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.bash(PROJECT, "rm -rf " + self.path(WORKTREE) + "/"), REFUSED)

    def test_removing_a_linked_worktree_after_a_double_dash_is_refused(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.remove(WORKTREE, verb="rm -r -- "), REFUSED)

    def test_removing_a_linked_worktree_through_sudo_is_refused(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.remove(WORKTREE, verb="sudo rm -rf "), REFUSED)

    def test_removing_a_folder_that_holds_a_linked_worktree_is_refused(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.remove(HOLDER), REFUSED)

    def test_removing_the_main_checkout_from_inside_a_worktree_is_refused(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.bash(WORKTREE, "rm -rf " + self.path(PROJECT)), REFUSED)

    def test_removing_a_scratch_folder_together_with_a_worktree_is_refused(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.remove(STATES, WORKTREE), REFUSED)


class ProtectedRemovalsRefused(ScratchHome):
    def test_removing_the_adw_lite_state_folder_of_home_is_refused(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.bash(PROJECT, "rm -rf " + shlex.quote(str(self.home / HOME_STATE))), STATE_REFUSED)

    def test_link_into_live_state_blocks_writes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.bash(PROJECT, "printf x > " + self.path(LIVE_LINK + "/forged.json")), LINK_WRITE_REFUSED)

    def test_removing_a_tracked_project_test_is_refused(self):
        """Build fixtures with factory functions."""
        self.assertEqual(self.bash(PROJECT, "rm -f " + OLD_TEST), REFUSED)


def setUpModule():
    FakeRepoCase.setUpClass.__func__(ScratchHome)
    unittest.addModuleCleanup(ScratchHome.doClassCleanups)


class ScratchRemovalWhileTheProjectCarriesWordingDebt(FakeRepoCase):
    projects = {PROJECT: Repo({OLD_TEST: DEBT_FILE, **README}), TESTS_SCRATCH: SCRATCH_FOLDERS[TESTS_SCRATCH]}

    def test_removing_a_scratch_folder_that_holds_a_tests_folder_passes(self):
        self.assertEqual(self.bash(PROJECT, "rm -rf " + shlex.quote(str(self.root / TESTS_SCRATCH))), PASSED)


if __name__ == "__main__":
    unittest.main()
