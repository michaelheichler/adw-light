import unittest

from commit_view_home import DENY, EMPTY, SCANNED, CommitViewHome, FakeGitView, committed, script


PROJECT = "project"
SUBFOLDER = "sub"
MISSING = "missing"
A_FILE = "afile"
OS_ERROR_MARK = "Errno"
ALIASES = {"alias.ci": "commit"}
PROSE = ' -m "We utilize this."'
PASSED = {}


class MissingFolderHome(CommitViewHome):
    projects = {PROJECT: {SUBFOLDER + "/keep.sh": script("echo keep\n"), A_FILE: "plain file\n"}}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        projects = {EMPTY: {}, **cls.projects}
        cls.git = FakeGitView({"roots": {str(cls.root / name): {**committed(files), "config": ALIASES} for name, files in projects.items()}})

    def reason(self, command):
        decision, rules, reason = self.refusal(PROJECT, command)
        return decision, rules, reason or ""

    def path(self, relative):
        return str(self.project(PROJECT) / relative)


class CommitIntoAMissingFolder(MissingFolderHome):
    def test_cd_into_a_missing_folder_then_commit_is_denied(self):
        decision, rules, _ = self.reason("cd missing && git commit -m x")

        self.assertEqual((DENY, frozenset()), (decision, rules))

    def test_cd_into_a_missing_folder_then_commit_names_the_folder(self):
        _, _, reason = self.reason("cd missing && git commit -m x")

        self.assertIn(self.path(MISSING), reason)

    def test_cd_into_a_missing_folder_then_commit_holds_no_os_error(self):
        _, _, reason = self.reason("cd missing && git commit -m x")

        self.assertNotIn(OS_ERROR_MARK, reason)

    def test_commit_alias_after_cd_into_a_missing_folder_is_denied(self):
        decision, rules, _ = self.reason("cd missing && git ci -m x")

        self.assertEqual((DENY, frozenset()), (decision, rules))

    def test_commit_alias_after_cd_into_a_missing_folder_names_the_folder(self):
        _, _, reason = self.reason("cd missing && git ci -m x")

        self.assertIn(self.path(MISSING), reason)

    def test_commit_alias_after_cd_into_a_missing_folder_holds_no_os_error(self):
        _, _, reason = self.reason("cd missing && git ci -m x")

        self.assertNotIn(OS_ERROR_MARK, reason)

    def test_commit_alias_with_git_c_into_a_missing_folder_is_denied(self):
        decision, rules, _ = self.reason("git -C missing ci")

        self.assertEqual((DENY, frozenset()), (decision, rules))

    def test_commit_alias_with_git_c_into_a_missing_folder_names_the_folder(self):
        _, _, reason = self.reason("git -C missing ci")

        self.assertIn(self.path(MISSING), reason)

    def test_commit_alias_with_git_c_into_a_missing_folder_holds_no_os_error(self):
        _, _, reason = self.reason("git -C missing ci")

        self.assertNotIn(OS_ERROR_MARK, reason)


class CommitIntoAFile(MissingFolderHome):
    def test_cd_into_a_file_then_commit_is_denied(self):
        decision, rules, _ = self.reason("cd afile && git commit -m x")

        self.assertEqual((DENY, frozenset()), (decision, rules))

    def test_cd_into_a_file_then_commit_names_the_file(self):
        _, _, reason = self.reason("cd afile && git commit -m x")

        self.assertIn(self.path(A_FILE), reason)

    def test_cd_into_a_file_then_commit_holds_no_os_error(self):
        _, _, reason = self.reason("cd afile && git commit -m x")

        self.assertNotIn(OS_ERROR_MARK, reason)

    def test_cd_into_a_file_gets_another_reason_than_a_missing_folder(self):
        missing = self.reason("cd missing && git commit -m x")[2].replace(self.path(MISSING), self.path(A_FILE))

        self.assertNotEqual(missing, self.reason("cd afile && git commit -m x")[2])


class CommandsThatDoNotCommit(MissingFolderHome):
    def test_listing_after_cd_into_a_missing_folder_passes(self):
        self.assertEqual(PASSED, self.gate(PROJECT, "cd missing && ls"))

    def test_status_with_git_c_into_a_missing_folder_passes(self):
        self.assertEqual(PASSED, self.gate(PROJECT, "git -C missing status"))

    def test_editor_on_the_current_folder_name_passes(self):
        self.assertEqual(PASSED, self.gate(PROJECT, "$EDITOR ${PWD##*/}"))


class CommitIntoAnExistingFolder(MissingFolderHome):
    def test_commit_in_the_project_folder_scans_the_message(self):
        self.assertEqual(SCANNED, self.message_finding(PROJECT, "git commit" + PROSE))

    def test_cd_into_an_existing_folder_then_commit_scans_the_message(self):
        self.assertEqual(SCANNED, self.message_finding(PROJECT, "cd sub && git commit" + PROSE))

    def test_commit_alias_after_cd_into_an_existing_folder_scans_the_message(self):
        self.assertEqual(SCANNED, self.message_finding(PROJECT, "cd sub && git ci" + PROSE))

    def test_commit_alias_with_git_c_into_an_existing_folder_scans_the_message(self):
        self.assertEqual(SCANNED, self.message_finding(PROJECT, "git -C sub ci" + PROSE))


if __name__ == "__main__":
    unittest.main()
