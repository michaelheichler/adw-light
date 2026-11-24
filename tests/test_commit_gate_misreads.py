import unittest

from commit_gate_home import CLEAN_LIB, HIDDEN_REFERENCE, PROSE_COMMIT, SCANNED, SHEBANG, CommitGateHome


SOURCE_LIB = ". ./lib/x.sh\n"
JQ_THEN_SOURCE = "jq '.' <<< \"{}\"\n" + SOURCE_LIB
PARENT_MAIN = "main() { bash ./b.sh; }\nmain\n"
CHILD_MAIN = "main() { echo b; }\nmain\n"
CHILD_MAIN_COMMIT = 'main() { git commit -m "We utilize this."; }\nmain\n'
SHIFT_WRAPPER = 'run() { local d="$1"; shift; command "$@"; }; run somedir bash ./tool.sh; bash ./tool.sh'
FOLDER_DEFAULT = '. "${1:-.}/lib/x.sh"\n'
EMPTY_DEFAULT = '. "./lib/x${1:-}.sh"\n'
MISSING_AFTER_LIB = SOURCE_LIB + "bash ./missing.sh\n"
TOOL_THEN_FOLDER_LIB = "docker compose up -d\n. ./docker/env.sh\n"
TOOL_THEN_ARGUMENT_LIB = "docker compose up -d\n. ./compose/env.sh\n"
TOUCH_THEN_SOURCE = "touch ./lib/x.sh\n" + SOURCE_LIB


class JqFiltersAreNotWrites(CommitGateHome):
    def test_lib_sourced_after_a_jq_filter_passes(self):
        self.place("lib/x.sh", CLEAN_LIB)

        self.assertEqual({}, self.run_script(JQ_THEN_SOURCE))

    def test_lib_sourced_after_a_jq_filter_is_scanned_for_the_commit_message(self):
        self.place("lib/x.sh", PROSE_COMMIT)

        self.assertEqual(SCANNED, self.message_finding(self.run_script(JQ_THEN_SOURCE)))


class ProgramNamesAreNotWrites(CommitGateHome):
    def test_lib_in_a_folder_named_after_an_earlier_tool_passes(self):
        self.place("docker/env.sh", CLEAN_LIB)

        self.assertEqual({}, self.run_script(TOOL_THEN_FOLDER_LIB))

    def test_lib_in_a_folder_named_after_an_earlier_tool_is_scanned(self):
        self.place("docker/env.sh", PROSE_COMMIT)

        self.assertEqual(SCANNED, self.message_finding(self.run_script(TOOL_THEN_FOLDER_LIB)))

    def test_lib_in_a_folder_named_by_a_tool_argument_is_refused_as_stale(self):
        self.place("lib/x.sh", CLEAN_LIB)
        self.place("compose/env.sh", CLEAN_LIB)
        expected = self.refusal_of_script("reference.sh", TOUCH_THEN_SOURCE)

        refusal = self.refusal_of_script("s.sh", TOOL_THEN_ARGUMENT_LIB)

        self.assertEqual(expected, refusal)


class FunctionRecursionStaysInOneProcess(CommitGateHome):
    def test_main_that_runs_a_script_with_its_own_main_passes(self):
        self.place("b.sh", SHEBANG + CHILD_MAIN)

        self.assertEqual({}, self.run_script(PARENT_MAIN))

    def test_commit_in_the_child_script_main_is_scanned_for_the_commit_message(self):
        self.place("b.sh", SHEBANG + CHILD_MAIN_COMMIT)

        self.assertEqual(SCANNED, self.message_finding(self.run_script(PARENT_MAIN)))

    def test_function_that_calls_itself_is_refused_like_deep_nesting(self):
        expected = self.deep_nesting_refusal()

        self.assertEqual(expected, self.deny_reason(self.gate("f() { f; }; f")))


class ShiftedArguments(CommitGateHome):
    def test_commit_after_an_unconditional_shift_is_scanned_for_the_commit_message(self):
        response = self.gate('f() { shift; "$@"; }; f x git commit -m "We utilize this."')

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_wrapper_that_shifts_off_a_folder_runs_a_clean_script_without_refusal(self):
        self.place("tool.sh", SHEBANG + CLEAN_LIB)

        self.assertEqual({}, self.gate(SHIFT_WRAPPER))

    def test_wrapper_that_shifts_off_a_folder_scans_the_script_it_runs(self):
        self.place("tool.sh", SHEBANG + PROSE_COMMIT)

        self.assertEqual(SCANNED, self.message_finding(self.gate(SHIFT_WRAPPER)))


class DefaultParameterForms(CommitGateHome):
    def test_folder_argument_selects_the_lib_under_that_folder(self):
        self.place("lib/x.sh", CLEAN_LIB)
        self.place("sub/lib/x.sh", PROSE_COMMIT)

        self.assertEqual(SCANNED, self.message_finding(self.run_script(FOLDER_DEFAULT, "sub")))

    def test_folder_argument_leaves_the_default_lib_unread(self):
        self.place("lib/x.sh", PROSE_COMMIT)
        self.place("sub/lib/x.sh", CLEAN_LIB)

        self.assertEqual({}, self.run_script(FOLDER_DEFAULT, "sub"))

    def test_empty_default_takes_the_argument_value(self):
        self.place("lib/x.sh", CLEAN_LIB)
        self.place("lib/x2.sh", PROSE_COMMIT)

        self.assertEqual(SCANNED, self.message_finding(self.run_script(EMPTY_DEFAULT, "2")))

    def test_missing_folder_argument_reads_the_default_lib(self):
        self.place("lib/x.sh", PROSE_COMMIT)

        self.assertEqual(SCANNED, self.message_finding(self.run_script(FOLDER_DEFAULT)))

    def test_missing_folder_argument_passes_a_clean_default_lib(self):
        self.place("lib/x.sh", CLEAN_LIB)

        self.assertEqual({}, self.run_script(FOLDER_DEFAULT))

    def test_empty_default_without_an_argument_reads_the_bare_name(self):
        self.place("lib/x.sh", PROSE_COMMIT)

        self.assertEqual(SCANNED, self.message_finding(self.run_script(EMPTY_DEFAULT)))


class ConstructsThatAreNotCommands(CommitGateHome):
    def after_construct(self, construct):
        self.place("lib/x.sh", CLEAN_LIB)
        return self.run_script(construct + "\n" + MISSING_AFTER_LIB)

    def test_arithmetic_expansion_with_inner_parentheses_passes(self):
        self.assertEqual({}, self.after_construct("n=$(( (5 - 2) / 1 ))"))

    def test_arithmetic_expansion_with_a_base_prefix_passes(self):
        self.assertEqual({}, self.after_construct("m=$((10#$n))"))

    def test_arithmetic_command_passes(self):
        self.assertEqual({}, self.after_construct("(( n > 1 )) && echo hi"))

    def test_c_style_for_loop_passes(self):
        self.assertEqual({}, self.after_construct('for ((i=0; i<2; i++)); do echo "$i"; done'))

    def test_case_patterns_naming_parent_folders_pass(self):
        self.assertEqual({}, self.after_construct('case "$p" in ..|../*|*/..|*/../*) exit 1 ;; esac'))

    def test_local_array_of_glob_and_tilde_items_passes(self):
        self.assertEqual({}, self.after_construct("f() { local g=('*' '~' '[ok]'); }\nf"))

    def test_array_append_passes(self):
        self.assertEqual({}, self.after_construct('a+=("$x")'))

    def test_regex_match_with_a_group_passes(self):
        self.assertEqual({}, self.after_construct("[[ $x =~ ^(a|b)$ ]]"))


class CommitsInsideConstructs(CommitGateHome):
    def test_commit_in_an_arithmetic_command_substitution_is_scanned(self):
        response = self.gate('(( $(git commit -m "We utilize this.") ))')

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_commit_in_an_arithmetic_expansion_substitution_is_scanned(self):
        response = self.gate('x=$(( $(git commit -m "We utilize this." >/dev/null; echo 1) + 1 ))')

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_commit_in_a_case_arm_that_matches_its_word_is_scanned(self):
        response = self.gate('case x in x) git commit -m "We utilize this." ;; esac')

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_commit_in_a_case_on_an_unset_variable_is_scanned(self):
        response = self.gate('case "$v" in y) git commit -m "We utilize this." ;; esac')

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_commit_after_an_array_assignment_is_scanned(self):
        response = self.gate('arr=(a b); git commit -m "We utilize this."')

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_commit_in_a_c_style_for_loop_is_scanned(self):
        response = self.gate('for ((i=0;i<1;i++)); do git commit -m "We utilize this."; done')

        self.assertEqual(SCANNED, self.message_finding(response))


class CommitsThroughOtherPrograms(CommitGateHome):
    def test_find_with_escaped_parentheses_running_git_commit_is_refused_as_hidden(self):
        expected = self.refusal_of_script("reference.sh", HIDDEN_REFERENCE)

        refusal = self.refusal_of_script("s.sh", r'find . \( -name x \) -exec git commit -m "We utilize this." \;' + "\n")

        self.assertEqual(expected, refusal)

    def test_xargs_running_a_shell_that_commits_is_refused_as_hidden(self):
        expected = self.refusal_of_script("reference.sh", HIDDEN_REFERENCE)

        refusal = self.refusal_of_script("s.sh", "xargs sh -c 'git commit -m \"We utilize this.\"'\n")

        self.assertEqual(expected, refusal)

    def test_find_exec_running_a_shell_that_commits_is_refused_as_hidden(self):
        expected = self.refusal_of_script("reference.sh", HIDDEN_REFERENCE)

        refusal = self.refusal_of_script("s.sh", r"find . -exec sh -c 'git commit -m x' \;" + "\n")

        self.assertEqual(expected, refusal)


if __name__ == "__main__":
    unittest.main()
