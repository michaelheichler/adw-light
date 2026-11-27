import unittest

from after_run_home import (
    CAUGHT, LET_THROUGH, OLD_TEST, TEST_WRITER, AfterRunCase, Project, identity, project_files,
)
from fake_repo_home import SIZE_TEST


REPO = "repo"
FLAGGED = "We utilize this tool for the build.\n"
NOTES = "docs/new.md"
OLD_NOTES = "docs/old.md"
SOURCE = "src/total.py"
NARRATED = "def total(items):\n    # Loop over the items and add them up\n    return sum(items)\n"
APPEND = "printf 'More.\\n' >> \"$(cat target.txt)\""
DIRTY = SIZE_TEST.replace("2]", "3]")
EDITED_AGAIN = SIZE_TEST.replace("2]", "4]")


class NewContentFindings(AfterRunCase):
    projects = {REPO: Project(project_files({OLD_NOTES: FLAGGED}))}

    def shell_rules(self, relative, content):
        self.after_run(REPO, APPEND, lambda: self.change(REPO, relative, content), before=identity(TEST_WRITER))
        return self.blocked_rules()

    def test_new_prose_written_through_a_shell_command_blocks_the_next_step(self):
        """The test fails when a real bug enters the code it exercises."""
        verdict = self.after_run(REPO, APPEND, lambda: self.change(REPO, NOTES, FLAGGED), before=identity(TEST_WRITER))
        self.assertEqual(verdict, CAUGHT)

    def test_shell_prose_gets_the_same_rules_as_a_write_of_the_same_text(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.shell_rules(NOTES, FLAGGED), self.write_rules(REPO, "docs/other.md", FLAGGED, run="write"))

    def test_new_narrating_comment_written_through_a_shell_command_blocks_the_next_step(self):
        """Feed input, check the return value."""
        verdict = self.after_run(REPO, APPEND, lambda: self.change(REPO, SOURCE, NARRATED), before=identity(TEST_WRITER))
        self.assertEqual(verdict, CAUGHT)

    def test_shell_comment_gets_the_same_rules_as_a_write_of_the_same_text(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.shell_rules(SOURCE, NARRATED), self.write_rules(REPO, "src/other.py", NARRATED, run="write"))

    def test_clean_line_appended_beside_an_inherited_finding_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        effect = lambda: self.change(REPO, OLD_NOTES, FLAGGED + "More.\n")
        self.assertEqual(self.after_run(REPO, APPEND, effect, before=identity(TEST_WRITER)), LET_THROUGH)


class AlreadyDirtyFiles(AfterRunCase):
    projects = {REPO: Project(project_files())}

    def test_main_agent_edit_of_a_test_file_that_was_already_dirty_is_caught(self):
        """The test fails when a real bug enters the code it exercises."""
        self.change(REPO, OLD_TEST, DIRTY)
        self.assertEqual(self.after_run(REPO, APPEND, lambda: self.change(REPO, OLD_TEST, EDITED_AGAIN)), CAUGHT)


if __name__ == "__main__":
    unittest.main()
