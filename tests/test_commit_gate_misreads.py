import unittest

from commit_view_home import (
    CLEAN_LIB, EMPTY, HIDDEN_REFERENCE, PROSE_COMMIT, SCANNED, CommitViewHome, deep_chain, script,
)


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
RUN = "bash ./s.sh"
CONSTRUCTS = {
    "arithmetic_inner_parentheses": "n=$(( (5 - 2) / 1 ))",
    "arithmetic_base_prefix": "m=$((10#$n))",
    "arithmetic_command": "(( n > 1 )) && echo hi",
    "c_style_for": 'for ((i=0; i<2; i++)); do echo "$i"; done',
    "case_parent_patterns": 'case "$p" in ..|../*|*/..|*/../*) exit 1 ;; esac',
    "local_glob_array": "f() { local g=('*' '~' '[ok]'); }\nf",
    "array_append": 'a+=("$x")',
    "regex_group": "[[ $x =~ ^(a|b)$ ]]",
}
HIDDEN_SCRIPTS = {
    "reference.sh": script(HIDDEN_REFERENCE),
    "find_parentheses.sh": script(r'find . \( -name x \) -exec git commit -m "We utilize this." \;' + "\n"),
    "xargs_shell.sh": script("xargs sh -c 'git commit -m \"We utilize this.\"'\n"),
    "find_shell.sh": script(r"find . -exec sh -c 'git commit -m x' \;" + "\n"),
}


class MisreadHome(CommitViewHome):
    projects = {
        "jq_clean": {"s.sh": script(JQ_THEN_SOURCE), "lib/x.sh": CLEAN_LIB},
        "jq_prose": {"s.sh": script(JQ_THEN_SOURCE), "lib/x.sh": PROSE_COMMIT},
        "tool_folder_clean": {"s.sh": script(TOOL_THEN_FOLDER_LIB), "docker/env.sh": CLEAN_LIB},
        "tool_folder_prose": {"s.sh": script(TOOL_THEN_FOLDER_LIB), "docker/env.sh": PROSE_COMMIT},
        "tool_argument": {"lib/x.sh": CLEAN_LIB, "compose/env.sh": CLEAN_LIB, "reference.sh": script(TOUCH_THEN_SOURCE),
                          "s.sh": script(TOOL_THEN_ARGUMENT_LIB)},
        "main_clean": {"s.sh": script(PARENT_MAIN), "b.sh": script(CHILD_MAIN)},
        "main_commit": {"s.sh": script(PARENT_MAIN), "b.sh": script(CHILD_MAIN_COMMIT)},
        "deep": deep_chain(),
        "tool_clean": {"tool.sh": script(CLEAN_LIB)},
        "tool_prose": {"tool.sh": script(PROSE_COMMIT)},
        "folder_sub_prose": {"s.sh": script(FOLDER_DEFAULT), "lib/x.sh": CLEAN_LIB, "sub/lib/x.sh": PROSE_COMMIT},
        "folder_sub_clean": {"s.sh": script(FOLDER_DEFAULT), "lib/x.sh": PROSE_COMMIT, "sub/lib/x.sh": CLEAN_LIB},
        "empty_default_argument": {"s.sh": script(EMPTY_DEFAULT), "lib/x.sh": CLEAN_LIB, "lib/x2.sh": PROSE_COMMIT},
        "folder_default_prose": {"s.sh": script(FOLDER_DEFAULT), "lib/x.sh": PROSE_COMMIT},
        "folder_default_clean": {"s.sh": script(FOLDER_DEFAULT), "lib/x.sh": CLEAN_LIB},
        "empty_default_bare": {"s.sh": script(EMPTY_DEFAULT), "lib/x.sh": PROSE_COMMIT},
        **{name: {"s.sh": script(construct + "\n" + MISSING_AFTER_LIB), "lib/x.sh": CLEAN_LIB}
           for name, construct in CONSTRUCTS.items()},
        "hidden": HIDDEN_SCRIPTS,
    }


class JqFiltersAreNotWrites(MisreadHome):
    def test_lib_sourced_after_a_jq_filter_passes(self):
        self.assertEqual({}, self.gate("jq_clean", RUN))

    def test_lib_sourced_after_a_jq_filter_is_scanned_for_the_commit_message(self):
        self.assertEqual(SCANNED, self.message_finding("jq_prose", RUN))


class ProgramNamesAreNotWrites(MisreadHome):
    def test_lib_in_a_folder_named_after_an_earlier_tool_passes(self):
        self.assertEqual({}, self.gate("tool_folder_clean", RUN))

    def test_lib_in_a_folder_named_after_an_earlier_tool_is_scanned(self):
        self.assertEqual(SCANNED, self.message_finding("tool_folder_prose", RUN))

    def test_lib_in_a_folder_named_by_a_tool_argument_is_refused_as_stale(self):
        expected = self.refusal("tool_argument", "bash ./reference.sh")

        self.assertEqual(expected, self.refusal("tool_argument", RUN))


class FunctionRecursionStaysInOneProcess(MisreadHome):
    def test_main_that_runs_a_script_with_its_own_main_passes(self):
        self.assertEqual({}, self.gate("main_clean", RUN))

    def test_commit_in_the_child_script_main_is_scanned_for_the_commit_message(self):
        self.assertEqual(SCANNED, self.message_finding("main_commit", RUN))

    def test_function_that_calls_itself_is_refused_like_deep_nesting(self):
        expected = self.refusal("deep", "bash ./deep1.sh")

        self.assertEqual(expected, self.refusal("deep", "f() { f; }; f"))


class ShiftedArguments(MisreadHome):
    def test_commit_after_an_unconditional_shift_is_scanned_for_the_commit_message(self):
        command = 'f() { shift; "$@"; }; f x git commit -m "We utilize this."'

        self.assertEqual(SCANNED, self.message_finding(EMPTY, command))

    def test_wrapper_that_shifts_off_a_folder_runs_a_clean_script_without_refusal(self):
        self.assertEqual({}, self.gate("tool_clean", SHIFT_WRAPPER))

    def test_wrapper_that_shifts_off_a_folder_scans_the_script_it_runs(self):
        self.assertEqual(SCANNED, self.message_finding("tool_prose", SHIFT_WRAPPER))


class DefaultParameterForms(MisreadHome):
    def test_folder_argument_selects_the_lib_under_that_folder(self):
        self.assertEqual(SCANNED, self.message_finding("folder_sub_prose", RUN + " sub"))

    def test_folder_argument_leaves_the_default_lib_unread(self):
        self.assertEqual({}, self.gate("folder_sub_clean", RUN + " sub"))

    def test_empty_default_takes_the_argument_value(self):
        self.assertEqual(SCANNED, self.message_finding("empty_default_argument", RUN + " 2"))

    def test_missing_folder_argument_reads_the_default_lib(self):
        self.assertEqual(SCANNED, self.message_finding("folder_default_prose", RUN))

    def test_missing_folder_argument_passes_a_clean_default_lib(self):
        self.assertEqual({}, self.gate("folder_default_clean", RUN))

    def test_empty_default_without_an_argument_reads_the_bare_name(self):
        self.assertEqual(SCANNED, self.message_finding("empty_default_bare", RUN))


class ConstructsThatAreNotCommands(MisreadHome):
    def test_arithmetic_expansion_with_inner_parentheses_passes(self):
        self.assertEqual({}, self.gate("arithmetic_inner_parentheses", RUN))

    def test_arithmetic_expansion_with_a_base_prefix_passes(self):
        self.assertEqual({}, self.gate("arithmetic_base_prefix", RUN))

    def test_arithmetic_command_passes(self):
        self.assertEqual({}, self.gate("arithmetic_command", RUN))

    def test_c_style_for_loop_passes(self):
        self.assertEqual({}, self.gate("c_style_for", RUN))

    def test_case_patterns_naming_parent_folders_pass(self):
        self.assertEqual({}, self.gate("case_parent_patterns", RUN))

    def test_local_array_of_glob_and_tilde_items_passes(self):
        self.assertEqual({}, self.gate("local_glob_array", RUN))

    def test_array_append_passes(self):
        self.assertEqual({}, self.gate("array_append", RUN))

    def test_regex_match_with_a_group_passes(self):
        self.assertEqual({}, self.gate("regex_group", RUN))


class CommitsInsideConstructs(MisreadHome):
    def test_commit_in_an_arithmetic_command_substitution_is_scanned(self):
        self.assertEqual(SCANNED, self.message_finding(EMPTY, '(( $(git commit -m "We utilize this.") ))'))

    def test_commit_in_an_arithmetic_expansion_substitution_is_scanned(self):
        command = 'x=$(( $(git commit -m "We utilize this." >/dev/null; echo 1) + 1 ))'

        self.assertEqual(SCANNED, self.message_finding(EMPTY, command))

    def test_commit_in_a_case_arm_that_matches_its_word_is_scanned(self):
        self.assertEqual(SCANNED, self.message_finding(EMPTY, 'case x in x) git commit -m "We utilize this." ;; esac'))

    def test_commit_in_a_case_on_an_unset_variable_is_scanned(self):
        self.assertEqual(SCANNED, self.message_finding(EMPTY, 'case "$v" in y) git commit -m "We utilize this." ;; esac'))

    def test_commit_after_an_array_assignment_is_scanned(self):
        self.assertEqual(SCANNED, self.message_finding(EMPTY, 'arr=(a b); git commit -m "We utilize this."'))

    def test_commit_in_a_c_style_for_loop_is_scanned(self):
        command = 'for ((i=0;i<1;i++)); do git commit -m "We utilize this."; done'

        self.assertEqual(SCANNED, self.message_finding(EMPTY, command))


class CommitsThroughOtherPrograms(MisreadHome):
    def test_find_with_escaped_parentheses_running_git_commit_is_refused_as_hidden(self):
        expected = self.refusal("hidden", "bash ./reference.sh")

        self.assertEqual(expected, self.refusal("hidden", "bash ./find_parentheses.sh"))

    def test_xargs_running_a_shell_that_commits_is_refused_as_hidden(self):
        expected = self.refusal("hidden", "bash ./reference.sh")

        self.assertEqual(expected, self.refusal("hidden", "bash ./xargs_shell.sh"))

    def test_find_exec_running_a_shell_that_commits_is_refused_as_hidden(self):
        expected = self.refusal("hidden", "bash ./reference.sh")

        self.assertEqual(expected, self.refusal("hidden", "bash ./find_shell.sh"))


if __name__ == "__main__":
    unittest.main()
