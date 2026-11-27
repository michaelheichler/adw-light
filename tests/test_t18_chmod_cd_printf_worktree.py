from fake_repo_home import OLD_TEST, PASSED, README, REFUSED, SIZE_TEST, FakeRepoCase, Repo, loose
import unittest


PROJECT = "repo"
SCRATCH = "scratch"
CACHE = "cache"
OUTSIDE = "outside/sub"
FEATURE_WORKTREE = " && git worktree add ../wt -b feat"


class ShellWritesAroundTheTestsFolder(FakeRepoCase):
    projects = {
        PROJECT: Repo({OLD_TEST: SIZE_TEST, "docs/guide.md": "Guide.\n", **README}, branches={"docs-fix": [{"docs/guide.md": "Guide, revised.\n"}]}),
        SCRATCH: Repo(README),
        CACHE: loose({"test_a.log": "log\n"}),
    }

    @classmethod
    def arrange(cls):
        (cls.root / OUTSIDE).mkdir(parents=True)
        (cls.root / SCRATCH / "sub").mkdir()
        (cls.root / PROJECT / "link").symlink_to(cls.root / OUTSIDE, target_is_directory=True)
        (cls.root / PROJECT / "docs/x").symlink_to(cls.root / SCRATCH / "sub", target_is_directory=True)

    def from_tests(self, command):
        return self.bash(PROJECT, command, cwd=self.root / PROJECT / "tests")

    def from_project(self, command):
        return self.bash(PROJECT, command)

    def into_scratch(self):
        return "cd " + str(self.root / SCRATCH)

    def test_recursive_chmod_then_remove_of_a_scratch_folder_holding_a_test_named_log_passes(self):
        folder = str(self.root / CACHE)

        self.assertEqual(self.from_tests("chmod -R u+rw " + folder + " && rm -rf " + folder), PASSED)

    def test_chmod_on_a_tracked_test_followed_by_printf_into_it_is_refused(self):
        self.assertEqual(self.from_tests("chmod u+w test_old.py && printf a > test_old.py"), REFUSED)

    def test_printf_after_cd_into_docs_writes_outside_the_tests_folder_and_passes(self):
        self.assertEqual(self.from_project("cd docs && printf x > test_new.py"), PASSED)

    def test_printf_after_cd_into_tests_is_refused(self):
        self.assertEqual(self.from_project("cd tests && printf a > f"), REFUSED)

    def test_direct_printf_into_the_tests_folder_is_refused(self):
        self.assertEqual(self.from_project("printf a > tests/f"), REFUSED)

    def test_printf_after_cd_through_a_link_that_leaves_the_project_is_refused_for_the_tracked_test(self):
        self.assertEqual(self.from_project("cd link/.. && printf a > " + OLD_TEST), REFUSED)

    def test_printf_after_cd_into_a_fresh_scratch_repo_passes(self):
        for name, form in ((".gitmodules", "x"), ("mods.cfg", "x"), (".gitmodules", "'[submodule]\\n'")):
            with self.subTest(name=name, form=form):
                self.assertEqual(self.from_tests(self.into_scratch() + " && printf " + form + " > " + name), PASSED)

    def test_printf_after_parent_cd_passes(self):
        self.assertEqual(self.from_tests("cd .. && printf 'x\\n' > mods.cfg"), PASSED)

    def test_quoted_printf_after_cd_into_tests_is_refused(self):
        self.assertEqual(self.from_project("cd tests && printf 'a\\n' > test_new.py"), REFUSED)

    def test_chmod_then_redirect_into_the_tests_folder_is_refused(self):
        self.assertEqual(self.from_project("chmod -R u+rw tests && printf 'a\\n' > tests/test_new.py"), REFUSED)

    def test_revert_of_source_only_commit_passes(self):
        self.assertEqual(self.from_project("git revert --no-edit docs-fix"), PASSED)

    def test_worktree_add_beside_an_independent_scratch_repo_passes(self):
        self.assertEqual(self.from_tests(self.into_scratch() + FEATURE_WORKTREE), PASSED)

    def test_worktree_add_beside_the_project_passes(self):
        self.assertEqual(self.from_tests("cd " + str(self.root / PROJECT) + FEATURE_WORKTREE), PASSED)

    def test_worktree_add_after_cd_into_the_project_through_a_symlinked_scratch_path_passes(self):
        self.assertEqual(self.from_tests("cd " + str(self.root / PROJECT / "docs/x/..") + FEATURE_WORKTREE), PASSED)


if __name__ == "__main__":
    unittest.main()
