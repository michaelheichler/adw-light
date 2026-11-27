from fake_repo_home import OLD_TEST, PASSED, README, REFUSED, SIZE_TEST, FakeRepoCase, Repo
from lib.shell_text import shell_words
import unittest


PROJECT = "repo"


class SeveralCdLandInTheirRealFolder(FakeRepoCase):
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


class EscapedSpaceInACdOperand(SeveralCdLandInTheirRealFolder):
    def test_escaped_space_keeps_the_folder_name_one_word(self):
        """Feed input, check the return value."""
        self.assertEqual(shell_words("cd new\\ dir && printf x > f.txt")[:2], ("cd", "new\\ dir"))

    def test_write_after_cd_into_a_folder_with_an_escaped_space_passes(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd new\\ dir && printf x > f.txt", "cd my\\ docs && printf x > notes.txt",
                        "cd my\\ docs; echo x >> notes.txt"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), PASSED)

    def test_write_after_cd_into_a_tests_folder_with_an_escaped_space_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.from_project("cd tests/sub\\ dir && printf a > f"), REFUSED)


class SecondCdAndBetweenCdWrites(SeveralCdLandInTheirRealFolder):
    def test_write_that_reaches_tests_after_or_between_cd_commands_is_refused(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd docs; cd tl; printf a > f", "cd docs && cd tl && echo a >> f",
                        "cd docs; cd ../tests; printf a > f", "cd tests; printf a > f; cd ../docs",
                        "cd docs && printf a > notes.txt && cd tl && touch test_z.py",
                        "cd missing; cd tests; printf a > f"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), REFUSED)

    def test_write_that_stays_outside_tests_after_or_between_cd_commands_passes(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd tests; cd ../docs; printf a > f", "cd docs; printf a > f; cd ../tests",
                        "cd docs/tl; cd ../../docs; echo a >> notes.txt"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), PASSED)


class CdInsideABashCBody(SeveralCdLandInTheirRealFolder):
    def test_bash_c_write_that_reaches_tests_is_refused(self):
        """Use a parameterized test for several inputs."""
        for command in ("bash -c 'cd tests && printf a > f'", "bash -c 'cd docs/tl && printf a > f'",
                        "cd docs && bash -c 'cd tl && printf a > f'", "bash -c 'cd docs; cd tl; echo a >> f'"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), REFUSED)

    def test_bash_c_write_that_stays_outside_tests_passes(self):
        """Use a parameterized test for several inputs."""
        for command in ("bash -c 'cd docs && printf a > f'", "cd docs && bash -c 'printf a > f'",
                        "bash -c 'cd tests; cd ../docs; printf a > f'"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), PASSED)


class LogicalAndPhysicalParentOfALink(SeveralCdLandInTheirRealFolder):
    def test_logical_parent_write_through_the_link_into_tests_is_refused(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd docs/tl; cd ..; printf a > tl/f", "cd docs/tl && cd .. && echo a >> tl/f",
                        "bash -c 'cd docs/tl; cd ..; printf a > tl/f'"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), REFUSED)

    def test_physical_parent_write_that_misses_tests_passes(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd docs/tl; cd -P ..; printf a > tl/f", "cd docs/tl && cd -P .. && echo a >> tl/f",
                        "bash -c 'cd docs/tl; cd -P ..; printf a > tl/f'", "cd docs/tl; cd ..; printf a > guide.md"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), PASSED)


class SeveralCdUnderCdpath(SeveralCdLandInTheirRealFolder):
    def hook_environment(self):
        return {"CDPATH": str(self.root / PROJECT / "tests")}

    def test_second_cd_or_bash_c_cd_that_cdpath_resolves_into_tests_is_refused(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd docs; cd cdp; printf a > f", "cd docs && cd cdp && echo a >> f",
                        "bash -c 'cd cdp && printf a > f'", "cd docs && bash -c 'cd cdp; printf a > f'"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), REFUSED)

    def test_second_cd_or_bash_c_cd_that_stays_outside_tests_passes(self):
        """Use a parameterized test for several inputs."""
        for command in ("cd cdp; cd ../../docs; printf a > f", "cd docs; cd ..; printf a > f.txt",
                        "bash -c 'cd docs && printf a > f'"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), PASSED)


class EveryReachableCdLanding(SeveralCdLandInTheirRealFolder):
    def test_write_after_any_cd_landing_that_can_reach_tests_is_refused(self):
        """Use a parameterized test for several inputs."""
        exported = f"export CDPATH={self.root / PROJECT / 'tests'}; cd docs && cd sub && printf a > f"
        for command in ("cd missing || cd docs/tl && printf a > f", "cd docs || true; cd tl; printf a > f",
                        "true && cd docs && cd tl && printf a > f", "echo go; cd docs; cd tl; printf a > f",
                        "cd docs && cd tl || true; printf a > f", "cd docs & cd docs/tl && printf a > f",
                        "cd docs && ls | cat && cd tl && printf a > f", "bash -c 'cd docs || true; cd tl; printf a > f'",
                        "cd docs && CDPATH=../tests cd sub && printf a > f", exported,
                        "printf a > tests/new\\ file.py"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), REFUSED)

    def test_write_that_no_cd_landing_brings_into_tests_passes(self):
        """Use a parameterized test for several inputs."""
        for command in ("printf a > new\\ file.txt", "cd missing && cd tests && printf a > f",
                        "printf a | cd tests; printf a > f"):
            with self.subTest(command=command):
                self.assertEqual(self.from_project(command), PASSED)


del SeveralCdLandInTheirRealFolder


if __name__ == "__main__":
    unittest.main()
