import unittest

from after_commit_home import CLEAN_TEXT, FLAGGED_MESSAGE, NOTES, AfterCommitCase, commit_project
from after_run_home import CAUGHT, LET_THROUGH
from fake_repo_home import OLD_TEST, SIZE_TEST, FakeRepoCase, Repo
from lib.commit_inputs import STALE_SCRIPT
from pre_commit import STAGE_SEPARATELY


PROJECT = "project"
RUN_SCRIPT = "bash tools/run.sh"
COMMITTING_SCRIPT = "#!/bin/bash\ngit commit -m x\n"
UNCHANGED_CHECK = "git diff --quiet HEAD -- tools/run.sh"
STAMP_COMPARE = 'import sys\nstamp = {"ran": 1}\nsys.exit(not (stamp["ran"] > 0))'
TEMP_TREE_ADD = 'git --git-dir=.git --work-tree="$work/tree" add -A -f .'
ALLOWED = (None, [], None)
SCANNED_AND_BLOCKED = (LET_THROUGH, CAUGHT, CAUGHT)
SCANNED_AND_RELEASED = (LET_THROUGH, LET_THROUGH, None)


def then_run(command):
    return command + "; " + RUN_SCRIPT


def after_check(command):
    return then_run(command + "; " + UNCHANGED_CHECK)


def heredoc_then_run(head, body):
    return head + " <<'PY'\n" + body + "\nPY\n" + RUN_SCRIPT


class ScriptHome(FakeRepoCase):
    projects = {PROJECT: Repo({OLD_TEST: SIZE_TEST, "tools/run.sh": COMMITTING_SCRIPT})}

    def gate(self, command):
        folder = self.root / PROJECT
        self.respond("SessionStart", {"cwd": str(folder), "source": "startup"})
        payload = {"cwd": str(folder), "tool_name": "Bash", "tool_input": {"command": command}}
        output = self.respond("PreToolUse", payload).get("hookSpecificOutput", {})
        reason = output.get("permissionDecisionReason") or ""
        cause = next((known for known in (STALE_SCRIPT, STAGE_SEPARATELY) if known in reason), None)
        return output.get("permissionDecision"), self.reported_rules(), cause


class ReadOnlyQueriesOfTheScriptFolder(ScriptHome):
    def test_ls_files_listing_the_script_folder_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(then_run("git -C . ls-files -v -- tools")), ALLOWED)

    def test_ls_files_listing_untracked_files_in_the_script_folder_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(then_run("git ls-files --others -- tools")), ALLOWED)

    def test_find_searching_the_script_folder_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(then_run("find tools -name x")), ALLOWED)

    def test_tar_archiving_the_script_folder_to_stdout_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(then_run("tar -cf - -C . tools/x")), ALLOWED)

    def test_copying_the_script_elsewhere_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(then_run("cp tools/run.sh other")), ALLOWED)

    def test_required_variable_guard_whose_message_names_the_folder_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(then_run("a=${v:?Give a tools directory}")), ALLOWED)

class UnchangedScriptCheck(ScriptHome):
    def test_diff_check_with_nothing_written_earlier_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(then_run(UNCHANGED_CHECK)), ALLOWED)

    def test_diff_check_after_shift_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(after_check("shift")), ALLOWED)

    def test_diff_check_after_break_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(after_check("break")), ALLOWED)

    def test_diff_check_after_getopts_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(after_check("getopts ab opt")), ALLOWED)


class StatusQueries(ScriptHome):
    def test_status_without_optional_locks_passes(self):
        """Feed input, check the return value."""
        command = "git --no-optional-locks status --porcelain --untracked-files=all"

        self.assertEqual(self.gate(then_run(command)), ALLOWED)


class FreshTempFolders(ScriptHome):
    def test_write_inside_a_fresh_temp_folder_before_the_diff_check_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(after_check('work=$(mktemp -d); printf x > "$work/a"')), ALLOWED)

    def test_staging_from_a_temp_work_tree_on_its_own_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate("work=$(mktemp -d); " + TEMP_TREE_ADD), ALLOWED)

class StdinPythonBeforeTheScript(ScriptHome):
    def test_isolated_stamp_compare_before_the_script_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.gate(heredoc_then_run('python3 -I -S - "$commit"', STAMP_COMPARE)), ALLOWED)


class CommitsTheScriptMakes(AfterCommitCase):
    projects = {PROJECT: commit_project({"tools/run.sh": COMMITTING_SCRIPT})}

    def flagged_commit(self, command):
        return self.run_and_stop(PROJECT, command, self.committing(PROJECT, message=FLAGGED_MESSAGE))

    def clean_commit(self, command):
        return self.run_and_stop(PROJECT, command, self.committing(PROJECT, {NOTES: CLEAN_TEXT}))

    def test_redirect_into_the_script_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run("printf x > tools/run.sh")), SCANNED_AND_BLOCKED)

    def test_redirect_into_the_script_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run("printf x > tools/run.sh")), SCANNED_AND_RELEASED)

    def test_copy_over_the_script_after_making_its_folder_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run("mkdir -p tools; cp a.sh tools/run.sh")), SCANNED_AND_BLOCKED)

    def test_copy_over_the_script_after_making_its_folder_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run("mkdir -p tools; cp a.sh tools/run.sh")), SCANNED_AND_RELEASED)

    def test_unknown_program_given_the_script_as_operand_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run('"$w" tools/run.sh')), SCANNED_AND_BLOCKED)

    def test_unknown_program_given_the_script_as_operand_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run('"$w" tools/run.sh')), SCANNED_AND_RELEASED)

    def test_sort_output_flag_naming_the_script_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run("sort -o tools/run.sh x")), SCANNED_AND_BLOCKED)

    def test_sort_output_flag_naming_the_script_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run("sort -o tools/run.sh x")), SCANNED_AND_RELEASED)

    def test_copy_with_the_script_folder_as_target_directory_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run("cp --target-directory=tools a")), SCANNED_AND_BLOCKED)

    def test_copy_with_the_script_folder_as_target_directory_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run("cp --target-directory=tools a")), SCANNED_AND_RELEASED)

    def test_git_diff_output_into_the_script_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run("git diff --output=tools/run.sh")), SCANNED_AND_BLOCKED)

    def test_git_diff_output_into_the_script_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run("git diff --output=tools/run.sh")), SCANNED_AND_RELEASED)

    def test_git_show_output_into_the_script_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run("git show --output=tools/run.sh")), SCANNED_AND_BLOCKED)

    def test_git_show_output_into_the_script_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run("git show --output=tools/run.sh")), SCANNED_AND_RELEASED)

    def test_git_restore_into_the_script_folder_work_tree_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run("git --work-tree=tools restore -- run.sh")), SCANNED_AND_BLOCKED)

    def test_git_restore_into_the_script_folder_work_tree_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run("git --work-tree=tools restore -- run.sh")), SCANNED_AND_RELEASED)

    def test_diff_check_after_a_textconv_config_change_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run("git config diff.x.textconv ./w && " + UNCHANGED_CHECK)), SCANNED_AND_BLOCKED)

    def test_diff_check_after_a_textconv_config_change_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run("git config diff.x.textconv ./w && " + UNCHANGED_CHECK)), SCANNED_AND_RELEASED)

    def test_diff_check_after_appending_to_the_global_git_config_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(after_check("printf '[diff]' >> \"$HOME/.gitconfig\"")), SCANNED_AND_BLOCKED)

    def test_diff_check_after_appending_to_the_global_git_config_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(after_check("printf '[diff]' >> \"$HOME/.gitconfig\"")), SCANNED_AND_RELEASED)

    def test_diff_check_after_writing_global_git_attributes_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(after_check('printf x > "$HOME/.config/git/attributes"')), SCANNED_AND_BLOCKED)

    def test_diff_check_after_writing_global_git_attributes_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(after_check('printf x > "$HOME/.config/git/attributes"')), SCANNED_AND_RELEASED)

    def test_diff_check_after_writing_a_quote_split_repository_config_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(after_check('printf x >> .gi""t/config')), SCANNED_AND_BLOCKED)

    def test_diff_check_after_writing_a_quote_split_repository_config_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(after_check('printf x >> .gi""t/config')), SCANNED_AND_RELEASED)

    def test_diff_check_after_writing_a_file_holding_a_script_write_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(after_check("printf 'printf x > tools/run.sh' > w")), SCANNED_AND_BLOCKED)

    def test_diff_check_after_writing_a_file_holding_a_script_write_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(after_check("printf 'printf x > tools/run.sh' > w")), SCANNED_AND_RELEASED)

    def test_plain_status_then_a_file_write_before_the_diff_check_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(after_check("git status --porcelain; printf x > w")), SCANNED_AND_BLOCKED)

    def test_plain_status_then_a_file_write_before_the_diff_check_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(after_check("git status --porcelain; printf x > w")), SCANNED_AND_RELEASED)

    def test_fetch_then_rev_parse_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run("git fetch origin; git -C . rev-parse HEAD")), SCANNED_AND_BLOCKED)

    def test_fetch_then_rev_parse_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run("git fetch origin; git -C . rev-parse HEAD")), SCANNED_AND_RELEASED)

    def test_staging_from_a_temp_work_tree_then_running_the_script_passes_and_its_flagged_commit_blocks_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.flagged_commit(then_run("work=$(mktemp -d); " + TEMP_TREE_ADD)), SCANNED_AND_BLOCKED)

    def test_staging_from_a_temp_work_tree_then_running_the_script_passes_and_its_clean_commit_releases(self):
        """Feed input, check the return value."""
        self.assertEqual(self.clean_commit(then_run("work=$(mktemp -d); " + TEMP_TREE_ADD)), SCANNED_AND_RELEASED)


if __name__ == "__main__":
    unittest.main()
