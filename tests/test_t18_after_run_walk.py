import unittest

from after_run_home import (
    CAUGHT, LET_THROUGH, OLD_TEST, TEST_WRITER, AfterRunCase, Project, identity, project_files,
)
from fake_repo_home import SIZE_TEST


LOOSE = "loose"
HIDDEN_WRITE = "python3 -c \"import pathlib; pathlib.Path('tes' + 'ts/x.py').write_text('x')\""
ADDED = SIZE_TEST.replace("test_size", "test_added")
EDITED = SIZE_TEST.replace("2]", "3]")
OWN_FILES = {"tests/test_edit.py": SIZE_TEST, "tests/test_delete.py": SIZE_TEST, "tests/test_rename.py": SIZE_TEST}
CHANGES = ("add", "edit", "delete", "rename", "spaced unicode name", "dollar name")


class StatWalkOutsideGit(AfterRunCase):
    projects = {LOOSE: Project(project_files({"pyproject.toml": "[project]\nname = 'loose'\n", **OWN_FILES}), git=False)}

    def effect(self, kind):
        return {
            "add": lambda: self.change(LOOSE, "tests/test_added.py", ADDED),
            "edit": lambda: self.change(LOOSE, "tests/test_edit.py", EDITED),
            "delete": lambda: self.remove(LOOSE, "tests/test_delete.py"),
            "rename": lambda: self.rename(LOOSE, "tests/test_rename.py", "tests/test_moved.py"),
            "spaced unicode name": lambda: self.change(LOOSE, "tests/test spaced \u00fc.py", ADDED),
            "dollar name": lambda: self.change(LOOSE, "tests/test_$cost.py", ADDED),
        }[kind]

    def verdicts(self, before=None):
        return {kind: self.after_run(LOOSE, HIDDEN_WRITE, self.effect(kind), before=before, run=kind) for kind in CHANGES}

    def test_main_agent_test_changes_outside_git_are_caught_after_the_run(self):
        """Use a parameterized test for several inputs."""
        self.assertEqual(self.verdicts(), dict.fromkeys(CHANGES, CAUGHT))

    def test_test_writer_changes_outside_git_pass(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.verdicts(identity(TEST_WRITER)), dict.fromkeys(CHANGES, LET_THROUGH))

    def test_byte_identical_rewrite_of_a_test_file_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        effect = lambda: self.change(LOOSE, OLD_TEST, SIZE_TEST)
        self.assertEqual(self.after_run(LOOSE, HIDDEN_WRITE, effect), LET_THROUGH)


if __name__ == "__main__":
    unittest.main()
