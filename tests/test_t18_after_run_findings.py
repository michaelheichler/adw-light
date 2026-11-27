import unittest

from after_run_home import (
    LET_THROUGH, TEST_WRITER, AfterRunCase, Project, identity, project_files,
)


REPO = "repo"
FLAGGED = "We utilize this tool for the build.\n"
OLD_NOTES = "docs/old.md"
APPEND = "printf 'More.\\n' >> \"$(cat target.txt)\""


class NewContentFindings(AfterRunCase):
    projects = {REPO: Project(project_files({OLD_NOTES: FLAGGED}))}

    def test_clean_line_appended_beside_an_inherited_finding_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        effect = lambda: self.change(REPO, OLD_NOTES, FLAGGED + "More.\n")
        self.assertEqual(self.after_run(REPO, APPEND, effect, before=identity(TEST_WRITER)), LET_THROUGH)


if __name__ == "__main__":
    unittest.main()
