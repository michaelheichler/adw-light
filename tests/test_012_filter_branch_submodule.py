from fake_repo_home import OLD_TEST, PASSED, REFUSED, SIZE_TEST, FakeRepoCase, Repo
import unittest


PROJECT = {".gitignore": ".claude/tmp/\n", "tests/test_a.py": SIZE_TEST, "docs/a.md": "Doc.\n"}
VENDOR = "vendor/up"
SCRATCH = ".claude/tmp/up"
TESTS = "tests/up"
REPO = "repo"
UPSTREAM = "upstream"


def registered(relative):
    modules = '[submodule "' + relative + '"]\n\tpath = ' + relative + "\n\turl = ../upstream\n"
    entries = {".gitmodules": {"submodule." + relative + ".path": relative}}
    return Repo({**PROJECT, ".gitmodules": modules}, fields={"config_files": entries})


class FilterBranchHome(FakeRepoCase):
    projects = {REPO: Repo(PROJECT)}

    def rewrite(self, filters):
        return self.bash(REPO, "git filter-branch " + filters + " HEAD")


class FilterBranchOnNonTestPaths(FilterBranchHome):
    def test_tree_filter_that_deletes_a_doc_passes(self):
        self.assertEqual(self.rewrite("--tree-filter 'rm -f docs/a.md'"), PASSED)

    def test_index_filter_that_unstages_a_doc_passes(self):
        self.assertEqual(self.rewrite("--index-filter 'git rm --cached --ignore-unmatch docs/a.md'"), PASSED)

    def test_message_filter_alone_passes(self):
        self.assertEqual(self.rewrite("--msg-filter 'sed s/a/b/'"), PASSED)


class FilterBranchThatTouchesTests(FilterBranchHome):
    def test_tree_filter_that_deletes_a_test_file_gets_the_test_write_deny(self):
        self.assertEqual(self.rewrite("--tree-filter 'rm -f tests/test_a.py'"), REFUSED)

    def test_tree_filter_that_deletes_the_tests_folder_gets_the_test_write_deny(self):
        self.assertEqual(self.rewrite("--tree-filter 'rm -rf tests'"), REFUSED)

    def test_index_filter_that_unstages_a_test_file_gets_the_test_write_deny(self):
        self.assertEqual(self.rewrite("--index-filter 'git rm --cached --ignore-unmatch tests/test_a.py'"), REFUSED)

    def test_message_filter_beside_a_tree_filter_that_deletes_a_test_gets_the_test_write_deny(self):
        self.assertEqual(self.rewrite("--msg-filter 'sed s/a/b/' --tree-filter 'rm -f tests/test_a.py'"), REFUSED)

    def test_subdirectory_filter_that_drops_the_tests_folder_gets_the_test_write_deny(self):
        """Feed input, check the return value."""
        self.assertEqual(self.rewrite("--subdirectory-filter docs"), REFUSED)


class CloneReference(FakeRepoCase):
    projects = {REPO: Repo(PROJECT), UPSTREAM: Repo({OLD_TEST: SIZE_TEST})}

    def test_clone_into_a_project_folder_outside_tests_passes(self):
        self.assertEqual(self.bash(REPO, f"git clone {self.root / UPSTREAM} {VENDOR}"), PASSED)

    def test_clone_into_the_tests_folder_gets_the_test_write_deny(self):
        self.assertEqual(self.bash(REPO, f"git clone {self.root / UPSTREAM} {TESTS}"), REFUSED)


class SubmoduleFirstCheckoutReadAsClone(FakeRepoCase):
    projects = {"vendor": registered(VENDOR), "scratch": registered(SCRATCH), "tests": registered(TESTS)}

    @classmethod
    def arrange(cls):
        for project, relative in (("vendor", VENDOR), ("scratch", SCRATCH), ("tests", TESTS)):
            (cls.root / project / relative).mkdir(parents=True)

    def test_init_of_a_submodule_outside_tests_passes(self):
        self.assertEqual(self.bash("vendor", "git submodule update --init " + VENDOR), PASSED)

    def test_init_of_a_submodule_under_an_ignored_scratch_folder_passes(self):
        self.assertEqual(self.bash("scratch", "git submodule update --init " + SCRATCH), PASSED)

    def test_init_of_every_submodule_when_none_sits_in_tests_passes(self):
        self.assertEqual(self.bash("vendor", "git submodule update --init"), PASSED)

    def test_init_of_a_submodule_inside_the_tests_folder_gets_the_test_write_deny(self):
        self.assertEqual(self.bash("tests", "git submodule update --init " + TESTS), REFUSED)

    def test_init_after_a_double_dash_of_a_submodule_inside_the_tests_folder_gets_the_test_write_deny(self):
        self.assertEqual(self.bash("tests", "git submodule update --init -- " + TESTS), REFUSED)

    def test_init_of_every_submodule_when_one_sits_in_tests_gets_the_test_write_deny(self):
        self.assertEqual(self.bash("tests", "git submodule update --init"), REFUSED)


class SubmoduleUpdateOfACheckedOutClone(FakeRepoCase):
    projects = {REPO: registered(VENDOR), REPO + "/" + VENDOR: Repo({OLD_TEST: SIZE_TEST})}

    def test_update_of_a_checked_out_submodule_that_tracks_tests_gets_the_test_write_deny(self):
        self.assertEqual(self.bash(REPO, "git submodule update " + VENDOR), REFUSED)

    def test_update_of_every_checked_out_submodule_that_tracks_tests_gets_the_test_write_deny(self):
        self.assertEqual(self.bash(REPO, "git submodule update"), REFUSED)


if __name__ == "__main__":
    unittest.main()
