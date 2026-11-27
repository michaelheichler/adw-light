from fake_repo_home import OLD_TEST, PASSED, README, REFUSED, SIZE_TEST, TEST_WRITER, FakeRepoCase, Repo
import unittest


PROJECT = "repo"


class LeadingCdLandsInItsRealFolder(FakeRepoCase):
    projects = {PROJECT: Repo({OLD_TEST: SIZE_TEST, "docs/guide.md": "Guide.\n", **README})}

    @classmethod
    def arrange(cls):
        project = cls.root / PROJECT
        (project / "my docs").mkdir()
        (project / "tests/sub dir").mkdir()
        (project / "tests/cdp").mkdir()
        (project / "docs/tl").symlink_to("../tests", target_is_directory=True)
        (project / "docs/deep").symlink_to("../tests/sub dir", target_is_directory=True)

    def from_project(self, command):
        return self.bash(PROJECT, command)

    def from_tests(self, command):
        return self.bash(PROJECT, command, cwd=self.root / PROJECT / "tests")

    def as_test_writer(self, command):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "agent_type": TEST_WRITER}
        return self.hook(PROJECT, payload)


class WritesOutsideTheTestsFolder(LeadingCdLandsInItsRealFolder):
    def test_write_after_cd_into_a_missing_folder_passes(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd missing && printf x > f.txt", "cd missing && printf x > test_a.py",
                        "cd missing; printf x > f.txt", 'cd "new dir" && printf x > f.txt'):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), PASSED)

    def test_write_after_cd_into_a_folder_name_with_spaces_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        for command in ('cd "my docs" && printf x > notes.txt', "cd 'my docs' && printf 'a\\n' > notes.txt",
                        'cd "my docs"; echo x > notes.txt', 'cd -L "my docs" && cat ../README.md >> notes.txt'):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), PASSED)

    def test_write_after_cd_into_docs_passes_in_every_form(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd docs; printf x > notes.txt", "cd -P docs && printf x > notes.txt",
                        "cd docs && printf x >> test_new.py", "cd docs && echo x > test_new.py"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), PASSED)

    def test_logical_cd_through_a_link_and_back_lands_in_docs_and_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        self.assertEqual(self.from_project("cd docs/deep/.. && printf a > f"), PASSED)

    def test_write_after_cd_home_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.from_project("cd ~ && printf x > notes.txt"), PASSED)

    def test_write_after_cd_from_tests_to_its_parent_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.from_tests("cd .. && printf 'x\\n' > mods.cfg"), PASSED)

    def test_test_writer_may_write_after_cd_into_a_tests_folder_with_spaces(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.as_test_writer('cd "tests/sub dir" && echo a >> f'), PASSED)


class WritesThatReachTheTestsFolder(LeadingCdLandsInItsRealFolder):
    def test_write_after_cd_into_tests_is_refused_in_every_form(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd tests; printf a > f", "cd tests && echo a >> test_old.py", "cd tests; cat ../README.md >> f",
                        "cd -L tests; echo a >> f", "cd -P tests && printf a > f", 'cd "tests/sub dir" && printf a > f',
                        "cd tests/new && printf a > f", 'cd tests && printf a > "my f"', "cd tests && printf a >| f"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), REFUSED)

    def test_write_through_a_link_into_tests_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        for command in ("cd docs/tl; echo a >> f", 'cd "docs/deep" && echo a > f', "cd -P docs/deep/.. && printf a > f",
                        "cd -P docs/deep/..; cat README.md > f", "cd docs && printf a > tl/f",
                        "cd docs/tl && cp ../../README.md f", "cd docs/tl; touch test_z.py"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), REFUSED)

    def test_redirect_that_climbs_from_a_folder_with_spaces_into_a_tracked_test_is_refused(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.from_project('cd "my docs"; echo a >> ../tests/test_old.py'), REFUSED)


class LeadingCdUnderCdpath(LeadingCdLandsInItsRealFolder):
    def hook_environment(self):
        return {"CDPATH": str(self.root / PROJECT / "tests")}

    def test_cd_into_a_local_folder_missing_from_cdpath_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        self.assertEqual(self.from_project("cd docs && printf a > f.txt"), PASSED)

    def test_cd_that_only_cdpath_resolves_into_tests_is_refused(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd cdp; printf a > f", "cd cdp && echo a >> f", "cd -L cdp && cat README.md > f"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), REFUSED)


del LeadingCdLandsInItsRealFolder


if __name__ == "__main__":
    unittest.main()
