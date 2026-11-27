import unittest

from after_run_home import CAUGHT, LET_THROUGH, OLD_TEST, AfterRunCase, Project, project_files
from fake_repo_home import PLAIN_MODULE, SIZE_TEST


REPO = "repo"
WRITTEN_TEST = "tests/test_x.py"
UNPACKED_TEST = "tools/test_unpacked.py"
WRITE_TEST = "echo 'def test(): pass' > " + WRITTEN_TEST
REMOVE_ALL = '; rm "${files[@]}"'
EACH_NESTED_REPO = 'for d in repos/*; do git -C "$d" '
SCRIPTS = {
    "src/app.py": PLAIN_MODULE,
    "tools/run.pl": "print 1;\n",
    "tools/run.rb": "puts 1\n",
    "tools/deploy.sh": '#!/bin/bash\ncp "$x" "$y"\n',
}
UNKNOWN_DELETES = {
    "glob_array": "files=(*)" + REMOVE_ALL,
    "substituted_array": "files=($(printf a))" + REMOVE_ALL,
    "one_substituted_element": "files=(a $(printf b))" + REMOVE_ALL,
    "one_variable_element": 'files=(a "$other")' + REMOVE_ALL,
    "read_array": "read -a files" + REMOVE_ALL,
    "mapfile_array": "mapfile -t files" + REMOVE_ALL,
    "literal_then_glob": "files=(a b); files=(*)" + REMOVE_ALL,
    "literal_then_read": "files=(a b); read -a files" + REMOVE_ALL,
    "unknown_variable": "rm -rf $UNKNOWN",
    "tmpdir_glob": 'rm -rf "$TMPDIR"/repo*',
    "dot_glob": "rm -rf ./test*",
    "loop_over_glob": EACH_NESTED_REPO + "rm -r tests; done",
}
WORKTREE_REWRITES = {
    "checkout_index": "git checkout-index -a",
    "forced_checkout_index": "git checkout-index -a -f",
    "alternate_index_checkout": "GIT_INDEX_FILE=.git/alt-index git checkout-index -f -a",
    "read_tree_update": "git read-tree -u -m HEAD",
    "scratch_index_read_tree_update": "GIT_INDEX_FILE=tmp git read-tree -u HEAD",
    "hard_reset": "git reset --hard",
    "branch_checkout": "git checkout other",
    "stash": "git stash",
    "switch": "git switch topic",
    "switch_create": "git switch -c new topic",
    "unknown_folder_checkout": 'git -C "$d" checkout -- .',
    "loop_hard_reset": EACH_NESTED_REPO + "reset --hard; done",
    "loop_worktree_remove": EACH_NESTED_REPO + "worktree remove --force one; done",
    "tree_filter_variable": 'git filter-branch --tree-filter "$FILTER" HEAD',
    "index_filter_variable": 'git filter-branch --index-filter "$FILTER" HEAD',
    "nested_clone_pull": "git -C vendor/up pull",
    "borrowed_git_dir_reset": "git -C scratch/w reset --hard",
    "insecure_tar": "git archive HEAD | tar -x --insecure -C .claude/tmp/x",
    "script_with_dynamic_copy": "bash tools/deploy.sh",
}
STDIN_PROGRAMS = {
    "sh_pipe": "cat <<'EOF' | sh\n" + WRITE_TEST + "\nEOF",
    "source_stdin": ". /dev/stdin <<'EOF'\n" + WRITE_TEST + "\nEOF",
    "awk": "awk -f - <<'EOF'\n" + WRITE_TEST + "\nEOF",
    "make": "make -f - <<'EOF'\n" + WRITE_TEST + "\nEOF",
    "busybox": "busybox sh <<'EOF'\n" + WRITE_TEST + "\nEOF",
    "doas": "doas bash <<'EOF'\n" + WRITE_TEST + "\nEOF",
    "python_module": "python3 -m runner <<'EOF'\n" + WRITE_TEST + "\nEOF",
    "perl_script": "perl tools/run.pl <<'EOF'\n" + WRITE_TEST + "\nEOF",
    "ruby_script": "ruby tools/run.rb <<'EOF'\n" + WRITE_TEST + "\nEOF",
    "php_shell": "php -r 'passthru(\"sh\");' <<'EOF'\n" + WRITE_TEST + "\nEOF",
}
SPLIT_REMOVALS = {
    "comma_variable": "IFS=,; d=.,rm,-f," + OLD_TEST + "; git -C $d status",
    "comma_prefix": "IFS=, d=.,rm,-f," + OLD_TEST + "; git -C $d status",
    "comma_unset_folder": "IFS=, git -C $d status",
    "comma_inside_loop": "for d in .,rm,-f," + OLD_TEST + "; do IFS=,; git -C $d status; done",
    "comma_before_loop": "IFS=,; for d in .,rm,-f," + OLD_TEST + "; do git -C $d status; done",
    "comma_line_before_loop": "IFS=,\nfor d in .,rm,-f," + OLD_TEST + "; do git -C $d status; done",
    "exported_comma": "export IFS=,; git -C $REPO status",
    "comma_subcommand": "IFS=,; f=rm,-f," + OLD_TEST + "; git $f",
    "substituted_folder": "git -C $(printf '. rm -f " + OLD_TEST + "') worktree list",
    "spaced_variable": 'd=". rm -f ' + OLD_TEST + '"; git -C $d status',
    "spaced_substitution": "d=$(printf '. rm -f " + OLD_TEST + "'); git -C $d status",
    "spaced_read_loop": "printf '. rm -f " + OLD_TEST + "\\n' | while read d; do git -C $d status; done",
    "spaced_inside_loop": 'for d in a; do d=". rm -f ' + OLD_TEST + '"; git -C $d status; done',
    "positional_loop": "for d; do git -C $d status; done",
    "quoted_unknown_loop": 'for d in "$X"; do git -C $d status; done',
    "sourced_folder": "source x.sh; git -C $d status",
}
SPLIT_REWRITES = {
    "comma_checkout": "IFS=, ; d=.,checkout,HEAD,--," + OLD_TEST + "; git -C $d worktree list",
    "loop_word_checkout": 'for d in ". checkout HEAD -- ' + OLD_TEST + '"; do git -C $d worktree list; done',
    "comma_copy_target": "IFS=,; f=.," + OLD_TEST + "; cp src/app.py $f",
    "unset_folder_checkout": "git -C $d checkout HEAD -- " + OLD_TEST,
    "unset_folder_restore": "git -C $d restore " + OLD_TEST,
    "unset_folder_stash": "git -C $d stash",
    "loop_checkout": "for d in */; do git -C $d checkout HEAD -- " + OLD_TEST + "; done",
}


def own_test(name):
    return "tests/test_" + name + ".py"


REMOVABLE = {own_test(name): SIZE_TEST for name in (*UNKNOWN_DELETES, *SPLIT_REMOVALS)}


class UnresolvedCommandCase(AfterRunCase):
    projects = {REPO: Project(project_files({**SCRIPTS, **REMOVABLE}))}

    def edit_old_test(self, name):
        self.change(REPO, OLD_TEST, SIZE_TEST + "X = " + repr(name) + "\n")

    def remove_own_test(self, name):
        self.remove(REPO, own_test(name))

    def write_test(self, relative):
        return lambda name: self.change(REPO, relative, SIZE_TEST + "X = " + repr(name) + "\n")

    def verdicts(self, commands, effect):
        return {name: self.shell(REPO, command, lambda name=name: effect(name), run=name) for name, command in commands.items()}


class UnknownDeleteTargetsAreJudgedAfterTheRun(UnresolvedCommandCase):
    def test_deletes_of_unknown_arrays_variables_and_globs_pass_before_the_run_and_block_after_it(self):
        """Use a parameterized test for several inputs."""
        verdicts = self.verdicts(UNKNOWN_DELETES, self.remove_own_test)
        self.assertEqual(verdicts, dict.fromkeys(UNKNOWN_DELETES, (LET_THROUGH, CAUGHT)))


class WorktreeRewritesAreJudgedAfterTheRun(UnresolvedCommandCase):
    def test_git_commands_that_rewrite_tracked_tests_pass_before_the_run_and_block_after_it(self):
        """Use a parameterized test for several inputs."""
        verdicts = self.verdicts(WORKTREE_REWRITES, self.edit_old_test)
        self.assertEqual(verdicts, dict.fromkeys(WORKTREE_REWRITES, (LET_THROUGH, CAUGHT)))

    def test_archive_extracted_into_a_script_folder_passes_before_the_run_and_its_new_test_blocks_after_it(self):
        """The test fails when a real bug enters the code it exercises."""
        verdict = self.shell(REPO, "tar -xf x.tar -C tools", lambda: self.write_test(UNPACKED_TEST)("tar"))
        self.assertEqual(verdict, (LET_THROUGH, CAUGHT))


class StdinProgramsAreJudgedAfterTheRun(UnresolvedCommandCase):
    def test_heredoc_bodies_fed_to_shells_and_interpreters_pass_before_the_run_and_their_test_write_blocks_after_it(self):
        """Use a parameterized test for several inputs."""
        verdicts = self.verdicts(STDIN_PROGRAMS, self.write_test(WRITTEN_TEST))
        self.assertEqual(verdicts, dict.fromkeys(STDIN_PROGRAMS, (LET_THROUGH, CAUGHT)))


class SplittableWordsAreJudgedAfterTheRun(UnresolvedCommandCase):
    def test_words_that_can_split_into_a_test_removal_pass_before_the_run_and_block_after_it(self):
        """Use a parameterized test for several inputs."""
        verdicts = self.verdicts(SPLIT_REMOVALS, self.remove_own_test)
        self.assertEqual(verdicts, dict.fromkeys(SPLIT_REMOVALS, (LET_THROUGH, CAUGHT)))

    def test_words_that_can_split_into_a_test_rewrite_pass_before_the_run_and_block_after_it(self):
        """Use a parameterized test for several inputs."""
        verdicts = self.verdicts(SPLIT_REWRITES, self.edit_old_test)
        self.assertEqual(verdicts, dict.fromkeys(SPLIT_REWRITES, (LET_THROUGH, CAUGHT)))


if __name__ == "__main__":
    unittest.main()
