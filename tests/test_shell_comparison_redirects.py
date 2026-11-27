import unittest

from fake_repo_home import OLD_TEST, PASSED, REFUSED, SIZE_TEST, FakeRepoCase, Repo


PROJECT = "project"
WRITE_TEST = "echo 'def test(): pass' > tests/test_x.py"
VERSION_SCRIPT = """best=''
for (( round = 0; round < rounds; round++ )); do
  if (( major > 3 || (major == 3 && minor >= 11) )); then best=yes; fi
  if (( version >= 311 && version > best_version )); then best_version=$version; fi
done
echo "$best"
"""
SCRIPTS = {
    "version.sh": VERSION_SCRIPT,
    "arithmetic_redirect.sh": "(( x )) > tests/test_x.py\n",
    "conditional_redirect.sh": "[[ a > b ]] > tests/test_x.py\n",
    "expansion_redirect.sh": "echo $(( 1 > 0 )) > tests/test_x.py\n",
    "compare_then_write.sh": "[[ a > b ]]\n" + WRITE_TEST + "\n",
}


class ComparisonHome(FakeRepoCase):
    projects = {PROJECT: Repo({OLD_TEST: SIZE_TEST}, disk=SCRIPTS)}

    def gate(self, command):
        return self.bash(PROJECT, command)


class ComparisonOperatorsAreNotRedirects(ComparisonHome):
    def test_version_compare_loop_shaped_like_an_install_script_passes(self):
        self.assertEqual(self.gate(VERSION_SCRIPT), PASSED)

    def test_let_with_a_quoted_greater_or_equal_passes(self):
        self.assertEqual(self.gate('let "x = a >= b"'), PASSED)

    def test_double_bracket_less_or_equal_passes(self):
        self.assertEqual(self.gate("[[ a <= b ]]"), PASSED)

    def test_double_bracket_greater_than_passes(self):
        self.assertEqual(self.gate("[[ a > b ]]"), PASSED)

    def test_arithmetic_expansion_greater_than_passes(self):
        self.assertEqual(self.gate("echo $(( 1 > 0 ))"), PASSED)

    def test_script_with_version_compares_passes(self):
        self.assertEqual(self.gate("bash version.sh"), PASSED)


class RedirectsNextToComparisonsStillWrite(ComparisonHome):
    def test_single_bracket_greater_than_writes_the_test_file(self):
        self.assertEqual(self.gate("[ a > tests/test_x.py ]"), REFUSED)

    def test_arithmetic_command_redirected_into_a_test_file_is_refused(self):
        self.assertEqual(self.gate("(( x )) > tests/test_x.py"), REFUSED)

    def test_unspaced_arithmetic_command_redirected_into_a_test_file_is_refused(self):
        self.assertEqual(self.gate("((x))>tests/test_x.py"), REFUSED)

    def test_arithmetic_expansion_redirected_into_a_test_file_is_refused(self):
        self.assertEqual(self.gate("echo $(( 1 > 0 )) > tests/test_x.py"), REFUSED)

    def test_double_bracket_redirected_into_a_test_file_is_refused(self):
        self.assertEqual(self.gate("[[ a > b ]] > tests/test_x.py"), REFUSED)

    def test_command_substitution_inside_arithmetic_that_writes_a_test_file_is_refused(self):
        self.assertEqual(self.gate("(( $(" + WRITE_TEST + ") ))"), REFUSED)

    def test_script_redirecting_an_arithmetic_command_into_a_test_file_is_refused(self):
        self.assertEqual(self.gate("bash arithmetic_redirect.sh"), REFUSED)

    def test_script_redirecting_a_double_bracket_into_a_test_file_is_refused(self):
        self.assertEqual(self.gate("bash conditional_redirect.sh"), REFUSED)

    def test_script_redirecting_an_arithmetic_expansion_into_a_test_file_is_refused(self):
        self.assertEqual(self.gate("bash expansion_redirect.sh"), REFUSED)

    def test_script_writing_a_test_file_after_a_comparison_is_refused(self):
        self.assertEqual(self.gate("bash compare_then_write.sh"), REFUSED)


if __name__ == "__main__":
    unittest.main()
