from fake_repo_home import OLD_TEST, PASSED, README, REFUSED, SIZE_TEST, FakeRepoCase, Repo
import unittest


PROJECT = "repo"
REAL_TEST = "(cd docs && printf 'def test_a():\\n    assert 1\\n' > test_x.py)"


class QuotedAndSubshellCdFixture(FakeRepoCase):
    projects = {PROJECT: Repo({OLD_TEST: SIZE_TEST, "docs/guide.md": "Guide.\n", **README})}

    @classmethod
    def arrange(cls):
        project = cls.root / PROJECT
        (project / "my docs").mkdir()
        (project / "tests/sub dir").mkdir()
        (project / "tests/cdp").mkdir()
        (project / "tests/sub").mkdir()
        (project / "docs/tl").symlink_to("../tests", target_is_directory=True)
        (project / "docs/deep").symlink_to("../tests/sub dir", target_is_directory=True)

    def from_project(self, command):
        return self.bash(PROJECT, command)


class PartlyQuotedCdAndSubshellScopes(QuotedAndSubshellCdFixture):
    def test_write_whose_cd_or_subshell_lands_in_tests_is_refused(self):
        """Use a parameterized test for several inputs."""
        for command in (
            "cd docs/'tl' && printf a > f",
            "cd 'tests'/cdp && printf a > f",
            "cd do\"cs\"/tl && printf a > f",
            "cd \"docs\"/tl; printf a > f",
            "cd doc\\s/tl && printf a > f",
            "bash -c \"cd docs/'tl' && printf a > f\"",
            "cd docs && cd 't'l && printf a > f",
            "cd tests/sub' 'dir && printf a > f",
            "(cd docs/tl && printf a > f)",
            "(cd docs; cd tl; printf a > f)",
            "cd tests && (printf a > f)",
            "(cd docs && (cd tl && printf a > f))",
            "cd docs && (cd tl; printf a > f)",
            "(cd 'docs'/tl && printf a > f)",
            "cd tests 2>/dev/null && printf a > f",
            "pushd tests >/dev/null && printf a > f && popd",
            "cd docs/tl >/dev/null && printf a > f",
            "pushd docs/tl && printf a > f",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), REFUSED)

    def test_write_whose_cd_or_subshell_stays_outside_tests_passes(self):
        """Use a parameterized test for several inputs."""
        for command in (
            "cd my' 'docs && printf a > notes.txt",
            "cd \"do\"cs && printf a > f.txt",
            "cd my\\ docs && printf a > notes.txt",
            "cd do'cs' && printf a > f.txt",
            "(cd docs && ls); printf a > f.txt",
            "(cd docs && ls) && printf a > f.txt",
            "echo a > f.txt; (cd docs; ls)",
            "(ls); cd docs && printf a > f.txt",
            "(cd tests && ls); printf a > f.txt",
            "(cd tests); printf a > f.txt",
            "(cd docs/tl && ls) > f.txt",
            "( cd docs && printf a > f.txt )",
            "cd docs 2>/dev/null && printf a > f.txt",
            "pushd docs >/dev/null && printf a > f.txt && popd",
            "cd docs >/dev/null && printf a > f.txt",
            "pushd docs && printf a > f.txt",
            "cd docs 2>&1 && printf a > f.txt",
            "for d in a; do cd tests; done; printf a > f.txt",
            "cd $HOME && printf a > f.txt",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), PASSED)

    def test_real_test_content_written_from_a_subshell_cd_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.from_project(REAL_TEST), REFUSED)


class PartlyQuotedCdUnderCdpath(QuotedAndSubshellCdFixture):
    def hook_environment(self):
        return {"CDPATH": str(self.root / PROJECT / "tests")}

    def test_partly_quoted_cd_that_cdpath_resolves_into_tests_is_refused(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd 'cd'p && printf a > f", "(cd c\\dp && printf a > f)"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), REFUSED)


del QuotedAndSubshellCdFixture


if __name__ == "__main__":
    unittest.main()
