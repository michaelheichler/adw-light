import hashlib
import time
import unittest

from commit_view_home import (
    CLEAN_LIB, DENY, EMPTY, HIDDEN_REFERENCE, PROSE_COMMIT, SCANNED, CommitViewHome, script,
)


SOURCE_LIB = ". ./lib/x.sh\n"
RERUN_THROUGH_HELPER = 'if [ -z "$AGAIN" ]; then AGAIN=1 bash ./t.sh "$0"; fi\n' + SOURCE_LIB
RUN_FIRST_ARGUMENT = 'bash "$1"\n'
SEEDED_RERUN = "if [ -z \"$INNER\" ]; then INNER=1 bash a.sh; fi; bash b.sh; echo 'git commit -m x' > b.sh\n"
TOUCH_THEN_SOURCE = "touch ./lib/x.sh\n" + SOURCE_LIB
GO_BRANCH = 'if [ "$1" = go ]; then\n  ' + PROSE_COMMIT + 'else\n  bash "$0" go\nfi\n'
FALLBACK_DEFINITIONS = "f() { echo 0; }\nf() { " + PROSE_COMMIT.strip() + "; }\nf\n"
ALIAS_DEFINITIONS = "f() { alias g=ls; }; f() { :; }; f"
DYNAMIC_REFERENCE = '"$(echo git)" commit -m msg'
FAN_OUT = (
    "".join(f"f{level}() {{ echo | xargs bash -c f{level + 1} bash -c f{level + 1}; }}\n" for level in range(5))
    + "f5() { " + PROSE_COMMIT.strip() + "; }\nexport -f f0 f1 f2 f3 f4 f5\nf0\n"
)
FAN_OUT_SECONDS = 5.0
RUN = "bash ./s.sh"
TWO_DEFINITIONS = 'f() { X=a.sh; }; f() { X=b.sh; }; f; bash "$X"'


def rerun_through_helper(lib):
    return {"t.sh": script(RUN_FIRST_ARGUMENT), "lib/x.sh": lib, "s.sh": script(RERUN_THROUGH_HELPER)}


class ProcessHome(CommitViewHome):
    projects = {
        "rerun_clean": rerun_through_helper(CLEAN_LIB),
        "rerun_prose": rerun_through_helper(PROSE_COMMIT),
        "seeded": {"lib/x.sh": CLEAN_LIB, "b.sh": script(CLEAN_LIB), "reference.sh": script(TOUCH_THEN_SOURCE),
                   "a.sh": script(SEEDED_RERUN)},
        "go": {"s.sh": script(GO_BRANCH)},
        "fallback": {"s.sh": script(FALLBACK_DEFINITIONS)},
        "two_scripts": {"a.sh": script(CLEAN_LIB), "b.sh": script(CLEAN_LIB)},
        "sub_folder": {"sub/.keep": ""},
        "linked": {"h.sh": script(CLEAN_LIB), "real/h.sh": script(PROSE_COMMIT), "real/deep/.keep": ""},
        "fan_out": {"reference.sh": script(HIDDEN_REFERENCE), "s.sh": script(FAN_OUT)},
    }
    links = {("linked", "lnk"): "real/deep"}

    def session(self):
        return "s" + hashlib.sha256(self.id().encode()).hexdigest()[:20]

    def through_link(self, prefix, pwd):
        return f'{prefix}bash "$(cd "{self.project("linked")}/lnk/.." && {pwd})/h.sh"'


class SelfRerunningScripts(ProcessHome):
    def test_script_rerun_through_a_helper_passes_a_clean_lib(self):
        self.assertEqual({}, self.gate("rerun_clean", RUN))

    def test_script_rerun_through_a_helper_scans_its_lib(self):
        self.assertEqual(SCANNED, self.message_finding("rerun_prose", RUN))

    def test_seeded_rerun_that_rewrites_a_script_it_runs_is_refused_as_stale(self):
        expected = self.refusal("seeded", "bash ./reference.sh")

        self.assertEqual(expected, self.refusal("seeded", "bash ./a.sh"))

    def test_script_rerun_with_a_go_argument_scans_the_go_branch(self):
        self.assertEqual(SCANNED, self.message_finding("go", RUN))


class PositionalShapes(ProcessHome):
    def test_shift_inside_an_if_block_that_returns_finds_the_commit(self):
        command = 'f() { if [ "$1" = t ]; then shift 2; "$@"; return; fi; }; f t x git commit -m "We utilize this."'

        self.assertEqual(SCANNED, self.message_finding(EMPTY, command))

    def test_shift_that_may_not_run_refuses_the_call_as_a_dynamic_program(self):
        expected = self.refusal(EMPTY, DYNAMIC_REFERENCE)

        refusal = self.refusal(EMPTY, 'f() { if [ -n "$1" ]; then shift; fi; "$@"; }; f x git commit -m msg')

        self.assertEqual(expected, refusal)

    def test_set_dashdash_reset_inside_a_function_finds_the_commit(self):
        command = 'f() { set -- git; $1 commit -m "We utilize this."; }; f'

        self.assertEqual(SCANNED, self.message_finding(EMPTY, command))

    def test_single_quoted_eval_of_an_argument_finds_the_commit(self):
        command = "f() { eval '$1'; }; f 'git commit -m \"We utilize this.\"'"

        self.assertEqual(SCANNED, self.message_finding(EMPTY, command))

    def test_inner_sh_c_payload_that_takes_git_as_an_argument_is_not_allowed(self):
        command = "f() { sh -c '$1 commit -m \"We utilize this.\"' sh git; }; f"

        self.assertEqual(DENY, self.refusal(EMPTY, command)[0])


class FunctionDefinitions(ProcessHome):
    def test_fallback_definition_that_commits_is_scanned(self):
        self.assertEqual(SCANNED, self.message_finding("fallback", RUN))

    def test_program_set_differently_by_two_definitions_is_refused_as_a_dynamic_program(self):
        """The test fails when a real bug enters the code it exercises."""
        expected = self.refusal(EMPTY, DYNAMIC_REFERENCE)

        self.assertEqual(expected, self.refusal(EMPTY, "f() { X=git; }; f() { X=echo; }; f; $X commit -m x"))

    def test_definitions_that_leave_different_folders_are_refused_as_ambiguous(self):
        expected = self.refusal("sub_folder", ALIAS_DEFINITIONS)

        self.assertEqual(expected, self.refusal("sub_folder", "f() { cd sub; }; f() { :; }; f"))


class SymlinkedFolders(ProcessHome):
    def test_logical_cd_through_a_symlink_reads_the_logical_parent_script(self):
        self.assertEqual({}, self.gate("linked", self.through_link("", "pwd")))

    def test_physical_pwd_after_a_logical_cd_reads_the_logical_parent_script(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual({}, self.gate("linked", self.through_link("", "pwd -P")))


class CommandRunnerFanOut(ProcessHome):
    def test_five_level_xargs_fan_out_finds_the_commit_in_time(self):
        expected = self.refusal("fan_out", "bash ./reference.sh")
        started = time.monotonic()

        refusal = self.refusal("fan_out", RUN)

        self.assertEqual((expected, True), (refusal, time.monotonic() - started < FAN_OUT_SECONDS))


if __name__ == "__main__":
    unittest.main()
