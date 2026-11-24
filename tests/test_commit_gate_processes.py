import time
import unittest

from commit_gate_home import CLEAN_LIB, HIDDEN_REFERENCE, PROSE_COMMIT, SCANNED, SHEBANG, CommitGateHome


SOURCE_LIB = ". ./lib/x.sh\n"
RERUN_THROUGH_HELPER = 'if [ -z "$AGAIN" ]; then AGAIN=1 bash ./t.sh "$0"; fi\n' + SOURCE_LIB
RUN_FIRST_ARGUMENT = 'bash "$1"\n'
SEEDED_RERUN = "if [ -z \"$INNER\" ]; then INNER=1 bash a.sh; fi; bash b.sh; echo 'git commit -m x' > b.sh\n"
TOUCH_THEN_SOURCE = "touch ./lib/x.sh\n" + SOURCE_LIB
GO_BRANCH = 'if [ "$1" = go ]; then\n  ' + PROSE_COMMIT + 'else\n  bash "$0" go\nfi\n'
FALLBACK_DEFINITIONS = "f() { echo 0; }\nf() { " + PROSE_COMMIT.strip() + "; }\nf\n"
ALIAS_DEFINITIONS = "f() { alias g=ls; }; f() { :; }; f"
DYNAMIC_REFERENCE = '"$(echo git)" commit -m msg'
UNRESOLVED_PATH = 'bash "$UNSET_DIR/h.sh"'
FAN_OUT = (
    "".join(f"f{level}() {{ echo | xargs bash -c f{level + 1} bash -c f{level + 1}; }}\n" for level in range(5))
    + "f5() { " + PROSE_COMMIT.strip() + "; }\nexport -f f0 f1 f2 f3 f4 f5\nf0\n"
)
FAN_OUT_SECONDS = 5.0


class SelfRerunningScripts(CommitGateHome):
    def rerun_through_helper(self, lib):
        self.place("t.sh", SHEBANG + RUN_FIRST_ARGUMENT)
        self.place("lib/x.sh", lib)
        return self.run_script(RERUN_THROUGH_HELPER)

    def test_script_rerun_through_a_helper_passes_a_clean_lib(self):
        self.assertEqual({}, self.rerun_through_helper(CLEAN_LIB))

    def test_script_rerun_through_a_helper_scans_its_lib(self):
        self.assertEqual(SCANNED, self.message_finding(self.rerun_through_helper(PROSE_COMMIT)))

    def test_seeded_rerun_that_rewrites_a_script_it_runs_is_refused_as_stale(self):
        self.place("lib/x.sh", CLEAN_LIB)
        self.place("b.sh", SHEBANG + CLEAN_LIB)
        expected = self.refusal_of_script("reference.sh", TOUCH_THEN_SOURCE)

        refusal = self.refusal_of_script("a.sh", SEEDED_RERUN)

        self.assertEqual(expected, refusal)

    def test_script_rerun_with_a_go_argument_scans_the_go_branch(self):
        self.assertEqual(SCANNED, self.message_finding(self.run_script(GO_BRANCH)))


class PositionalShapes(CommitGateHome):
    def test_shift_inside_an_if_block_that_returns_finds_the_commit(self):
        response = self.gate('f() { if [ "$1" = t ]; then shift 2; "$@"; return; fi; }; f t x git commit -m "We utilize this."')

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_shift_that_may_not_run_refuses_the_call_as_a_dynamic_program(self):
        expected = self.deny_reason(self.gate('"$(echo git)" commit -m msg'))

        refusal = self.deny_reason(self.gate('f() { if [ -n "$1" ]; then shift; fi; "$@"; }; f x git commit -m msg'))

        self.assertEqual(expected, refusal)

    def test_set_dashdash_reset_inside_a_function_finds_the_commit(self):
        response = self.gate('f() { set -- git; $1 commit -m "We utilize this."; }; f')

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_single_quoted_eval_of_an_argument_finds_the_commit(self):
        response = self.gate("f() { eval '$1'; }; f 'git commit -m \"We utilize this.\"'")

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_inner_sh_c_payload_that_takes_git_as_an_argument_is_not_allowed(self):
        response = self.gate("f() { sh -c '$1 commit -m \"We utilize this.\"' sh git; }; f")

        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])


class FunctionDefinitions(CommitGateHome):
    def test_fallback_definition_that_commits_is_scanned(self):
        self.assertEqual(SCANNED, self.message_finding(self.run_script(FALLBACK_DEFINITIONS)))

    def test_program_set_differently_by_two_definitions_is_refused_as_a_dynamic_program(self):
        expected = self.deny_reason(self.gate(DYNAMIC_REFERENCE))

        refusal = self.deny_reason(self.gate("f() { X=git; }; f() { X=echo; }; f; $X commit -m x"))

        self.assertEqual(expected, refusal)

    def test_script_path_set_differently_by_two_definitions_is_refused_as_unresolvable(self):
        self.place("a.sh", SHEBANG + CLEAN_LIB)
        self.place("b.sh", SHEBANG + CLEAN_LIB)
        expected = self.deny_reason(self.gate(UNRESOLVED_PATH))

        refusal = self.deny_reason(self.gate('f() { X=a.sh; }; f() { X=b.sh; }; f; bash "$X"'))

        self.assertEqual(expected, refusal)

    def test_definitions_that_leave_different_folders_are_refused_as_ambiguous(self):
        self.place("sub/.keep", "")
        expected = self.deny_reason(self.gate(ALIAS_DEFINITIONS))

        self.assertEqual(expected, self.deny_reason(self.gate("f() { cd sub; }; f() { :; }; f")))


class SymlinkedFolders(CommitGateHome):
    def through_link(self, prefix, pwd):
        self.place("h.sh", SHEBANG + CLEAN_LIB)
        self.place("real/h.sh", SHEBANG + PROSE_COMMIT)
        self.place("real/deep/.keep", "")
        (self.root / "lnk").symlink_to("real/deep")
        return self.gate(f'{prefix}bash "$(cd "{self.root}/lnk/.." && {pwd})/h.sh"')

    def test_logical_cd_through_a_symlink_reads_the_logical_parent_script(self):
        self.assertEqual({}, self.through_link("", "pwd"))

    def test_physical_pwd_after_a_logical_cd_reads_the_logical_parent_script(self):
        self.assertEqual({}, self.through_link("", "pwd -P"))

    def test_physical_mode_is_refused_rather_than_read_as_the_logical_script(self):
        expected = self.deny_reason(self.gate(UNRESOLVED_PATH))

        self.assertEqual(expected, self.deny_reason(self.through_link("set -P\n", "pwd")))


class CommandRunnerFanOut(CommitGateHome):
    def test_five_level_xargs_fan_out_finds_the_commit_in_time(self):
        expected = self.refusal_of_script("reference.sh", HIDDEN_REFERENCE)
        started = time.monotonic()

        refusal = self.refusal_of_script("s.sh", FAN_OUT)

        self.assertEqual(expected, refusal)
        self.assertLess(time.monotonic() - started, FAN_OUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
