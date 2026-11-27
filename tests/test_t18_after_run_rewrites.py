import unittest

from after_run_home import CAUGHT, OLD_TEST, AfterRunCase, Project, project_files
from fake_repo_home import SIZE_TEST


REPO = "repo"
APPENDED = SIZE_TEST + "X = 1\n"
INLINE_WRITES = {
    "tests/test_inline_a.py": "python3 -c \"import pathlib; pathlib.Path('tests/test_inline_a.py').write_text('x')\"",
    "tests/test_inline_b.py": "python3 -c 'import pathlib; pathlib.Path(\"tests/test_inline_b.py\").write_text(\"x\")'",
}
STDIN_PROGRAMS = {
    "tests/test_stdin_python.py": "python3 - <<'EOF'\nimport os\nos.system(\"echo x > tests/test_stdin_python.py\")\nEOF",
    "tests/test_stdin_perl.py": "perl /dev/stdin <<'EOF'\nopen(my $f, \">\", \"tests/test_stdin_perl.py\"); print $f \"x\";\nEOF",
    "tests/test_stdin_node.py": "node /dev/stdin <<'EOF'\nrequire(\"fs\").writeFileSync(\"tests/test_stdin_node.py\", \"x\");\nEOF",
    "tests/test_stdin_argument.py": "python3 - x <<'PY'\nimport os\nos.system(\"echo x > tests/test_stdin_argument.py\")\nPY",
}
SCRIPT_RUNS = ("python3 add.py", "node add.js")


class TestWritesCaughtAfterTheRun(AfterRunCase):
    projects = {REPO: Project(project_files())}

    def new_test_verdicts(self, commands):
        return {path: self.shell(REPO, command, lambda path=path: self.change(REPO, path, SIZE_TEST), run=path)
                for path, command in commands.items()}

    def script_verdicts(self):
        return {command: self.shell(REPO, command, lambda: self.change(REPO, OLD_TEST, APPENDED + command), run=command)
                for command in SCRIPT_RUNS}

    def test_inline_python_that_writes_into_a_tests_file_runs_and_is_caught_after_the_run(self):
        """Use a parameterized test for several inputs."""
        self.assertEqual(self.new_test_verdicts(INLINE_WRITES), dict.fromkeys(INLINE_WRITES, (None, CAUGHT)))

    def test_stdin_programs_that_write_a_test_file_run_and_are_caught_after_the_run(self):
        """Use a parameterized test for several inputs."""
        self.assertEqual(self.new_test_verdicts(STDIN_PROGRAMS), dict.fromkeys(STDIN_PROGRAMS, (None, CAUGHT)))

    def test_interpreter_scripts_that_append_to_a_test_file_run_and_are_caught_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.script_verdicts(), dict.fromkeys(SCRIPT_RUNS, (None, CAUGHT)))


if __name__ == "__main__":
    unittest.main()
