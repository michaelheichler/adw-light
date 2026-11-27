import unittest

from after_run_home import (
    DENY, LET_THROUGH, OLD_TEST, TEST_WRITER, AfterRunCase, Project, identity, project_files,
)
from fake_repo_home import SIZE_TEST


REPO = "repo"
NEW_TEST = "tests/test_added.py"
EDITED = SIZE_TEST.replace("2]", "3]")
ADDED = SIZE_TEST.replace("test_size", "test_added")
HIDDEN_SHAPES = {
    "python": "python3 -c \"import pathlib; pathlib.Path('tes' + 'ts/test_added.py').write_text('x')\"",
    "sed": "sed -i '' 's/2]/3]/' $(cat list.txt)",
    "bash": "bash -c \"python3 -c 'import os; os.remove(chr(116) + \\\"ests/test_old.py\\\")'\"",
}
NAMED_SHAPES = {
    "sed": "sed -i '' 's/2]/3]/' tests/test_old.py",
    "bash": "bash -c 'rm -f tests/test_old.py'",
}


class ShellShapes(AfterRunCase):
    projects = {REPO: Project(project_files())}

    def effect(self, kind):
        return {
            "python": lambda: self.change(REPO, NEW_TEST, ADDED),
            "sed": lambda: self.change(REPO, OLD_TEST, EDITED),
            "bash": lambda: self.remove(REPO, OLD_TEST),
        }[kind]

    def verdicts(self, before, after):
        return {kind: self.after_run(REPO, command, self.effect(kind), before=before, after=after, run=kind)
                for kind, command in HIDDEN_SHAPES.items()}

    def pre_run_verdicts(self):
        self.start(REPO)
        return {kind: self.pre(REPO, command, call=kind) for kind, command in NAMED_SHAPES.items()}

    def test_identity_saved_before_the_run_lets_the_test_writer_through_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.verdicts(identity(TEST_WRITER), identity("")), dict.fromkeys(HIDDEN_SHAPES, LET_THROUGH))

    def test_named_test_paths_in_sed_and_bash_stay_refused_before_the_run(self):
        """Feed input, check the return value."""
        self.assertEqual(self.pre_run_verdicts(), dict.fromkeys(NAMED_SHAPES, DENY))


if __name__ == "__main__":
    unittest.main()
