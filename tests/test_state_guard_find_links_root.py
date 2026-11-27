from fake_repo_home import DENY, PASSED, FakeRepoCase, Repo, README
import shlex
import unittest


PROJECT = "project"
HOME_STATE = ".adw-lite/state"
LEGACY_STATE = ".local/state/adw-lite"
LINKER = "linker"
LIVE_LINK = LINKER + "/live"
FRESH_LINK = LINKER + "/fresh"
DELETION = "state_deletion"
MUTATION = "state_mutation"
HOME_TOKEN = "~/"
TILDE_STATE = HOME_TOKEN + HOME_STATE


class StateGuardHome(FakeRepoCase):
    projects = {PROJECT: Repo(dict(README))}

    @classmethod
    def arrange(cls):
        (cls.home / HOME_STATE / "ledger").mkdir(parents=True)
        (cls.home / LEGACY_STATE / "ledger").mkdir(parents=True)
        (cls.state / "ledger").mkdir(parents=True, exist_ok=True)
        (cls.root / LINKER).mkdir()
        (cls.root / LIVE_LINK).symlink_to(cls.home / HOME_STATE, target_is_directory=True)

    def under_root(self, name):
        return shlex.quote(str(self.root / name))

    def under_home(self, name):
        return shlex.quote(str(self.home / name))

    def configured_state(self, name):
        return shlex.quote(str(self.state / name))

    def reports(self, command, rule):
        decision, rules = self.bash(PROJECT, command)
        return decision, rule in rules


class FindDeletesOfHomeStateRefused(StateGuardHome):
    def test_find_delete_of_tilde_state_is_refused_as_state_deletion(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.reports("find " + TILDE_STATE + " -delete", DELETION), (DENY, True))

    def test_find_type_file_delete_of_dollar_home_adw_lite_is_refused_as_state_deletion(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.reports("find $HOME/.adw-lite -type f -delete", DELETION), (DENY, True))

    def test_find_delete_of_absolute_state_is_refused_as_state_deletion(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.reports("find " + self.under_home(HOME_STATE) + " -delete", DELETION), (DENY, True))

    def test_find_type_file_delete_of_absolute_adw_lite_is_refused_as_state_deletion(self):
        """The test fails when a real bug enters the code it exercises."""
        command = "find " + self.under_home(".adw-lite") + " -type f -delete"
        self.assertEqual(self.reports(command, DELETION), (DENY, True))


class LinksIntoHomeState(StateGuardHome):
    def test_creating_a_link_to_the_state_folder_passes(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.bash(PROJECT, "ln -s " + TILDE_STATE + " " + self.under_root(FRESH_LINK)), PASSED)

    def test_a_write_through_a_link_made_in_the_same_command_is_refused_as_state_mutation(self):
        """The test fails when a real bug enters the code it exercises."""
        link = self.under_root(FRESH_LINK)
        command = "ln -s " + TILDE_STATE + " " + link + " && printf x > " + link + "/forged.json"
        self.assertEqual(self.reports(command, MUTATION), (DENY, True))

    def test_a_delete_through_a_link_made_in_the_same_command_is_refused_as_state_deletion(self):
        """The test fails when a real bug enters the code it exercises."""
        link = self.under_root(FRESH_LINK)
        command = "ln -s " + TILDE_STATE + " " + link + " && rm -rf " + link + "/ledger"
        self.assertEqual(self.reports(command, DELETION), (DENY, True))

    def test_a_write_through_an_existing_link_is_refused_as_state_mutation(self):
        """The test fails when a real bug enters the code it exercises."""
        command = "printf x > " + self.under_root(LIVE_LINK) + "/forged.json"
        self.assertEqual(self.reports(command, MUTATION), (DENY, True))

    def test_a_delete_through_an_existing_link_is_refused_as_state_deletion(self):
        """The test fails when a real bug enters the code it exercises."""
        command = "rm -rf " + self.under_root(LIVE_LINK) + "/ledger"
        self.assertEqual(self.reports(command, DELETION), (DENY, True))


class ConfiguredAndLegacyStateRefused(StateGuardHome):
    def test_a_write_into_the_configured_state_root_is_refused_as_state_mutation(self):
        """The test fails when a real bug enters the code it exercises."""
        command = "printf x > " + self.configured_state("ledger/forged.jsonl")
        self.assertEqual(self.reports(command, MUTATION), (DENY, True))

    def test_a_delete_in_the_configured_state_root_is_refused_as_state_deletion(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.reports("rm -rf " + self.configured_state("ledger"), DELETION), (DENY, True))

    def test_a_find_delete_of_the_configured_state_root_is_refused_as_state_deletion(self):
        """The test fails when a real bug enters the code it exercises."""
        command = "find " + shlex.quote(str(self.state)) + " -delete"
        self.assertEqual(self.reports(command, DELETION), (DENY, True))

    def test_a_write_into_the_legacy_state_folder_is_refused_as_state_mutation(self):
        """The test fails when a real bug enters the code it exercises."""
        command = "printf x > " + self.under_home(LEGACY_STATE + "/ledger/forged.jsonl")
        self.assertEqual(self.reports(command, MUTATION), (DENY, True))

    def test_a_delete_of_the_legacy_state_folder_is_refused_as_state_deletion(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.reports("rm -rf " + HOME_TOKEN + LEGACY_STATE, DELETION), (DENY, True))

    def test_a_find_delete_of_the_legacy_state_folder_is_refused_as_state_deletion(self):
        """The test fails when a real bug enters the code it exercises."""
        command = "find " + self.under_home(LEGACY_STATE) + " -delete"
        self.assertEqual(self.reports(command, DELETION), (DENY, True))


if __name__ == "__main__":
    unittest.main()
