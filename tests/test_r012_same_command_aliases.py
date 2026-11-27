from fake_repo_home import PASSED, README, REFUSED, SIZE_TEST, FakeRepoCase, Repo
import unittest


SAMPLE_TEST = "tests/test_a.py"
REPO = "repo"
SHELL_REMOVAL = "'!rm " + SAMPLE_TEST + "'"
GIT_REMOVAL = "'rm " + SAMPLE_TEST + "'"
SUBSTITUTION_ALIAS = 'git config alias.zap "$(cat body)" && git zap'


class SameCommandAliasHome(FakeRepoCase):
    projects = {REPO: Repo({SAMPLE_TEST: SIZE_TEST, **README})}

    def gate(self, command):
        return self.bash(REPO, command)


class HarmlessSameCommandAliases(SameCommandAliasHome):
    def test_alias_defined_by_git_config_for_status_passes(self):
        self.assertEqual(self.gate("git config alias.st status && git st"), PASSED)

    def test_alias_given_by_git_c_for_status_passes(self):
        self.assertEqual(self.gate("git -c alias.st=status st"), PASSED)


class AliasesThatRemoveATest(SameCommandAliasHome):
    def test_shell_alias_defined_by_git_config_gets_the_test_write_deny(self):
        self.assertEqual(self.gate("git config alias.zap " + SHELL_REMOVAL + " && git zap"), REFUSED)

    def test_shell_alias_given_by_git_c_gets_the_test_write_deny(self):
        self.assertEqual(self.gate("git -c alias.zap=" + SHELL_REMOVAL + " zap"), REFUSED)

    def test_git_rm_alias_defined_by_git_config_gets_the_test_write_deny(self):
        self.assertEqual(self.gate("git config alias.zap " + GIT_REMOVAL + " && git zap"), REFUSED)

    def test_git_rm_alias_given_by_git_c_gets_the_test_write_deny(self):
        self.assertEqual(self.gate("git -c alias.zap=" + GIT_REMOVAL + " zap"), REFUSED)

    def test_shell_alias_given_by_git_c_that_overwrites_a_test_gets_the_test_write_deny(self):
        self.assertEqual(self.gate("git -c alias.zap='!echo hi > " + SAMPLE_TEST + "' zap"), REFUSED)


class UnreadableSameCommandAliases(SameCommandAliasHome):
    def test_alias_body_from_a_substitution_passes_before_the_run(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(SUBSTITUTION_ALIAS), PASSED)


if __name__ == "__main__":
    unittest.main()
