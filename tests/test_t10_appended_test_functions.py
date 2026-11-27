from fake_repo_home import PASSED, REFUSED, FakeRepoCase, Repo
import unittest


MODULE = "prices.py"
SNIPPET = "snippet.txt"
RATE_MODULE = "RATE = 2\n"
HEREDOC_REFUSED = REFUSED
HEREDOC_PASSED = PASSED
TEST_NAME = "test_rate"
PLAIN_NAME = "rate"


def function_named(name):
    return f"\ndef {name}():\n    assert RATE == 2\n"


class AppendedFunctionHome(FakeRepoCase):
    projects = {name: Repo({MODULE: RATE_MODULE, SNIPPET: function_named(name)}) for name in (TEST_NAME, PLAIN_NAME)}

    def write_tool(self, name):
        return self.write(name, MODULE, RATE_MODULE + function_named(name))

    def edit_tool(self, name):
        return self.edit(name, MODULE, RATE_MODULE, RATE_MODULE + function_named(name))

    def heredoc_append(self, name):
        return self.bash(name, f"cat >> {MODULE} <<'EOF'" + function_named(name) + "EOF")

    def printf_append(self, name):
        return self.bash(name, f"printf '%s\\n' '' 'def {name}():' '    assert RATE == 2' >> {MODULE}")

    def printf_variable_append(self, name):
        return self.bash(name, f"t={name}; printf 'def %s():\\n    assert RATE == 2\\n' \"$t\" >> {MODULE}")

    def cat_file_append(self, name):
        return self.bash(name, f"cat {SNIPPET} >> {MODULE}")

    def copy_over_module(self, name):
        return self.bash(name, f"cp {SNIPPET} {MODULE}")


class WriteToolAppend(AppendedFunctionHome):
    def test_new_content_with_a_test_function_is_refused(self):
        self.assertEqual(self.write_tool(TEST_NAME), REFUSED)

    def test_new_content_with_a_plain_function_passes(self):
        self.assertEqual(self.write_tool(PLAIN_NAME), PASSED)


class EditToolAppend(AppendedFunctionHome):
    def test_edit_appending_a_test_function_is_refused(self):
        self.assertEqual(self.edit_tool(TEST_NAME), REFUSED)

    def test_edit_appending_a_plain_function_passes(self):
        self.assertEqual(self.edit_tool(PLAIN_NAME), PASSED)


class HeredocAppend(AppendedFunctionHome):
    def test_heredoc_appending_a_test_function_reaches_the_test_gate(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.heredoc_append(TEST_NAME), HEREDOC_REFUSED)

    def test_heredoc_appending_a_plain_function_skips_the_test_gate(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.heredoc_append(PLAIN_NAME), HEREDOC_PASSED)


class PrintfAppend(AppendedFunctionHome):
    def test_printf_appending_a_test_function_is_refused(self):
        self.assertEqual(self.printf_append(TEST_NAME), REFUSED)

    def test_printf_appending_a_plain_function_passes(self):
        self.assertEqual(self.printf_append(PLAIN_NAME), PASSED)


class PrintfVariableAppend(AppendedFunctionHome):
    def test_printf_naming_a_test_function_through_a_variable_is_refused(self):
        self.assertEqual(self.printf_variable_append(TEST_NAME), REFUSED)

    def test_printf_naming_a_plain_function_through_a_variable_passes(self):
        self.assertEqual(self.printf_variable_append(PLAIN_NAME), PASSED)


class FileContentAppend(AppendedFunctionHome):
    def test_cat_appending_a_file_with_a_test_function_is_refused(self):
        self.assertEqual(self.cat_file_append(TEST_NAME), REFUSED)

    def test_cat_appending_a_file_with_a_plain_function_passes(self):
        self.assertEqual(self.cat_file_append(PLAIN_NAME), PASSED)


class CopyOverModule(AppendedFunctionHome):
    def test_copying_a_file_with_a_test_function_over_a_module_is_refused(self):
        self.assertEqual(self.copy_over_module(TEST_NAME), REFUSED)

    def test_copying_a_file_with_a_plain_function_over_a_module_passes(self):
        self.assertEqual(self.copy_over_module(PLAIN_NAME), PASSED)


if __name__ == "__main__":
    unittest.main()
