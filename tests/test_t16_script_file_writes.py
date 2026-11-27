from fake_repo_home import DENY, PASSED, REFUSED, SIZE_TEST, FakeRepoCase, Repo
import unittest


TEST_FILE = "tests/test_a.py"
PLAIN_FILE = "prices.py"
SHELL_APPEND = f"echo 'X = 1' >> {TEST_FILE}\n"
SHELL_PLAIN = f"echo 'TAX = 2' >> {PLAIN_FILE}\n"
SHELL_READ = f"cat {PLAIN_FILE}\n"
PYTHON_APPEND = f"with open('{TEST_FILE}', 'a') as handle:\n    handle.write('X = 1')\n"
PYTHON_PLAIN = f"with open('{PLAIN_FILE}', 'a') as handle:\n    handle.write('TAX = 2')\n"
PYTHON_READ = f"print(open('{PLAIN_FILE}').read())\n"
NODE_APPEND = f"require('fs').appendFileSync('{TEST_FILE}', 'X = 1');\n"
NODE_PLAIN = f"require('fs').appendFileSync('{PLAIN_FILE}', 'TAX = 2');\n"
NODE_READ = f"console.log(require('fs').readFileSync('{PLAIN_FILE}', 'utf8'));\n"
SCRIPT_CAP_BYTES = 512 * 1024
PADDING = "#\n" * SCRIPT_CAP_BYTES
DEEP_CHAIN = 40
CHAIN = {f"deep{level}.sh": f"sh deep{level + 1}.sh\n" for level in range(1, DEEP_CHAIN)}
DEPLOY_SCRIPT = "tools/deploy.sh"
BASE = {TEST_FILE: SIZE_TEST, PLAIN_FILE: "PRICE = 1\n"}
NEW = "new"
MODIFIED = "modified"
COMMITTED = "committed"
NEW_SCRIPTS = {
    "add.sh": SHELL_APPEND, "plain.sh": SHELL_PLAIN, "add.py": PYTHON_APPEND, "add.js": NODE_APPEND,
    "plain.py": PYTHON_PLAIN, "plain.js": NODE_PLAIN, "read.py": PYTHON_READ, "read.js": NODE_READ,
    "big.sh": SHELL_PLAIN + PADDING, "big.py": PYTHON_READ + PADDING, "big.js": NODE_READ + "//\n" * SCRIPT_CAP_BYTES,
    "outer.sh": "sh inner.sh\n", "inner.sh": SHELL_APPEND, "plain_outer.sh": "sh plain.sh\n",
    **CHAIN, f"deep{DEEP_CHAIN}.sh": SHELL_PLAIN,
}
COMMITTED_READS = {"add.sh": SHELL_READ, "plain.sh": SHELL_READ, "add.py": PYTHON_READ, "add.js": NODE_READ}
CHANGED_SCRIPTS = {"add.sh": SHELL_APPEND, "plain.sh": SHELL_PLAIN, "add.py": PYTHON_APPEND, "add.js": NODE_APPEND}
COMMITTED_SCRIPTS = {
    "add.sh": SHELL_APPEND, "add.py": PYTHON_APPEND, "add.js": NODE_APPEND, "plain.py": PYTHON_PLAIN,
    "plain.js": NODE_PLAIN, DEPLOY_SCRIPT: SHELL_APPEND, "big.sh": SHELL_PLAIN + PADDING, "inner.sh": SHELL_APPEND,
}


class ScriptFileHome(FakeRepoCase):
    projects = {
        NEW: Repo(BASE, NEW_SCRIPTS),
        MODIFIED: Repo({**BASE, **COMMITTED_READS}, CHANGED_SCRIPTS),
        COMMITTED: Repo({**BASE, **COMMITTED_SCRIPTS}, {"outer.sh": "sh inner.sh\n"}),
    }


class NewShellScripts(ScriptFileHome):
    def test_sh_running_a_new_script_that_appends_to_a_test_file_is_refused_as_a_test_write(self):
        self.assertEqual(REFUSED, self.bash(NEW, "sh add.sh"))

    def test_bash_running_a_new_script_that_appends_to_a_test_file_is_refused_as_a_test_write(self):
        self.assertEqual(REFUSED, self.bash(NEW, "bash add.sh"))

    def test_bash_running_a_new_dot_slash_script_that_appends_to_a_test_file_is_refused_as_a_test_write(self):
        self.assertEqual(REFUSED, self.bash(NEW, "bash ./add.sh"))

    def test_sh_running_a_new_script_that_writes_only_a_plain_file_passes(self):
        self.assertEqual(PASSED, self.bash(NEW, "sh plain.sh"))

    def test_bash_running_a_new_script_that_writes_only_a_plain_file_passes(self):
        self.assertEqual(PASSED, self.bash(NEW, "bash plain.sh"))


class ModifiedShellScripts(ScriptFileHome):
    def test_sh_running_a_committed_script_changed_to_append_to_a_test_file_is_refused_as_a_test_write(self):
        self.assertEqual(REFUSED, self.bash(MODIFIED, "sh add.sh"))

    def test_bash_running_a_committed_script_changed_to_append_to_a_test_file_is_refused_as_a_test_write(self):
        self.assertEqual(REFUSED, self.bash(MODIFIED, "bash add.sh"))

    def test_bash_running_a_committed_script_changed_to_write_only_a_plain_file_passes(self):
        self.assertEqual(PASSED, self.bash(MODIFIED, "bash plain.sh"))


class CommittedShellScripts(ScriptFileHome):
    def test_sh_running_a_committed_unchanged_script_that_appends_to_a_test_file_runs_as_today(self):
        self.assertEqual(PASSED, self.bash(COMMITTED, "sh add.sh"))

    def test_bash_running_a_committed_unchanged_script_that_appends_to_a_test_file_runs_as_today(self):
        self.assertEqual(PASSED, self.bash(COMMITTED, "bash add.sh"))

    def test_bash_running_a_committed_unchanged_dot_slash_script_that_appends_to_a_test_file_runs_as_today(self):
        self.assertEqual(PASSED, self.bash(COMMITTED, "bash ./add.sh"))

    def test_bash_running_the_committed_unchanged_deploy_script_runs_as_today(self):
        self.assertEqual(PASSED, self.bash(COMMITTED, f"bash {DEPLOY_SCRIPT}"))

    def test_bash_running_a_committed_unchanged_script_over_the_size_cap_runs_as_today(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(PASSED, self.bash(COMMITTED, "bash big.sh"))


class NewInterpreterScripts(ScriptFileHome):
    def test_python_running_a_new_script_that_only_reads_passes(self):
        self.assertEqual(PASSED, self.bash(NEW, "python3 read.py"))

    def test_node_running_a_new_script_that_only_reads_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(PASSED, self.bash(NEW, "node read.js"))


class CommittedInterpreterScripts(ScriptFileHome):
    def test_python_running_a_committed_unchanged_script_that_appends_to_a_test_file_runs_as_today(self):
        self.assertEqual(PASSED, self.bash(COMMITTED, "python3 add.py"))

    def test_node_running_a_committed_unchanged_script_that_appends_to_a_test_file_runs_as_today(self):
        self.assertEqual(PASSED, self.bash(COMMITTED, "node add.js"))

    def test_python_running_a_committed_unchanged_script_that_writes_only_a_plain_file_runs_as_today(self):
        self.assertEqual(PASSED, self.bash(COMMITTED, "python3 plain.py"))

    def test_node_running_a_committed_unchanged_script_that_writes_only_a_plain_file_runs_as_today(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(PASSED, self.bash(COMMITTED, "node plain.js"))


class NestedScriptFiles(ScriptFileHome):
    def test_new_script_that_runs_a_new_script_appending_to_a_test_file_is_refused_as_a_test_write(self):
        self.assertEqual(REFUSED, self.bash(NEW, "sh outer.sh"))

    def test_new_script_that_runs_a_new_script_writing_only_a_plain_file_passes(self):
        self.assertEqual(PASSED, self.bash(NEW, "sh plain_outer.sh"))

    def test_new_script_that_runs_a_committed_unchanged_script_appending_to_a_test_file_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(PASSED, self.bash(COMMITTED, "sh outer.sh"))


if __name__ == "__main__":
    unittest.main()
