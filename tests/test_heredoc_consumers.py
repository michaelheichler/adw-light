import unittest

from fake_repo_home import OLD_TEST, PASSED, REFUSED, SIZE_TEST, FakeRepoCase, Repo


PROJECT = "project"
WRITE_TEST = "echo 'def test(): pass' > tests/test_x.py"
PYTHON_COMPARE = "print(1 > 0)"
PYTHON_PATH_COMPARE = "flag = 'b' > 'tests/test_x.py'\nprint(flag)"
DEPLOY_STAMP = 'import sys\nstamp = {"ran": 1}\nsys.exit(not (stamp["ran"] > 0))'


def heredoc(head, body, tag="EOF"):
    return head + " <<'" + tag + "'\n" + body + "\n" + tag


class HeredocHome(FakeRepoCase):
    projects = {PROJECT: Repo({OLD_TEST: SIZE_TEST})}

    def gate(self, command):
        return self.bash(PROJECT, command)


class PythonReadingItsBodyAsTheProgram(HeredocHome):
    def test_python_stdin_program_with_a_comparison_passes(self):
        self.assertEqual(self.gate(heredoc("python3 -", PYTHON_COMPARE)), PASSED)

    def test_python_stdin_program_comparing_a_string_with_a_test_path_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.gate(heredoc("python3 -", PYTHON_PATH_COMPARE)), PASSED)


class DeployStampScriptAndProgramsThatReadAnotherSource(HeredocHome):
    def test_isolated_python_stamp_check_with_an_argument_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(heredoc('python3 -I - "$commit"', DEPLOY_STAMP, "PY")), PASSED)

class CatDataAndShellConsumers(HeredocHome):
    def test_cat_reading_a_body_as_data_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(heredoc("cat", WRITE_TEST)), PASSED)

    def test_sh_reading_the_body_is_refused(self):
        self.assertEqual(self.gate(heredoc("sh", WRITE_TEST)), REFUSED)

    def test_bash_reading_the_body_is_refused(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(heredoc("bash", WRITE_TEST)), REFUSED)

if __name__ == "__main__":
    unittest.main()
