import unittest

from fake_repo_home import DENY, PASSED, SIZE_TEST, FakeRepoCase, Repo


TEST_FILE = "tests/test_a.py"
APPEND = f"echo 'X = 1' >> {TEST_FILE}\n"
CLEAN = "echo 1\n"
BASE = {TEST_FILE: SIZE_TEST}
KEEP = {"work/keep.txt": "1\n"}
TEST_WRITE = (DENY, ["test_write_gate"])
MISSING = "missing"
APPENDING = "appending"
PARENT_CLEAN = "parent_clean"
PARENT_APPEND = "parent_append"


class GuardHome(FakeRepoCase):
    projects = {
        MISSING: Repo(BASE, KEEP),
        APPENDING: Repo(BASE, {"run.sh": APPEND}),
        PARENT_CLEAN: Repo(BASE, {"tools/run.sh": CLEAN, **KEEP}),
        PARENT_APPEND: Repo(BASE, {"tools/run.sh": APPEND, **KEEP}),
    }

    def missing(self, command):
        return self.bash(MISSING, command)

    def from_work(self, project, command):
        return self.bash(project, command, self.root / project / "work")


class GuardedMissingScriptPasses(GuardHome):
    def test_bracket_test_joined_by_and_passes(self):
        self.assertEqual(PASSED, self.missing("[ -f run.sh ] && bash run.sh"))

    def test_test_builtin_joined_by_and_passes(self):
        self.assertEqual(PASSED, self.missing("test -f run.sh && bash run.sh"))

    def test_if_block_with_a_bracket_test_passes(self):
        self.assertEqual(PASSED, self.missing("if [ -f run.sh ]; then bash run.sh; fi"))

    def test_if_block_with_an_earlier_command_in_the_then_branch_passes(self):
        self.assertEqual(PASSED, self.missing("if test -f run.sh; then echo 1; bash run.sh; fi"))

    def test_double_bracket_test_passes(self):
        self.assertEqual(PASSED, self.missing("[[ -f run.sh ]] && bash run.sh"))

    def test_guarded_call_with_output_redirects_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(PASSED, self.missing("[ -f run.sh ] && bash run.sh > out.log 2>&1"))


class GuardedExistingScriptIsStillRead(GuardHome):
    def test_guarded_new_script_that_appends_to_a_test_file_is_refused_as_a_test_write(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(TEST_WRITE, self.bash(APPENDING, "[ -f run.sh ] && bash run.sh"))


class LiteralParentFolder(GuardHome):
    def test_cd_into_the_parent_runs_a_clean_script(self):
        self.assertEqual(PASSED, self.from_work(PARENT_CLEAN, "cd .. && bash tools/run.sh"))

    def test_cd_into_the_parent_reads_a_script_that_appends_to_a_test_file(self):
        self.assertEqual(TEST_WRITE, self.from_work(PARENT_APPEND, "cd .. && bash tools/run.sh"))


if __name__ == "__main__":
    unittest.main()
