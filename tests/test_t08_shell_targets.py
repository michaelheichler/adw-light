import os
import unittest
from unittest import mock

from adw_light import run_hook
from after_run_home import (
    CAUGHT, CLAUDE, CODEX, DENY, GATE, LET_THROUGH, OLD_TEST, AfterRunCase, Project, project_files,
)
from fake_repo_home import PLAIN_MODULE, SIZE_TEST


REPO = "repo"
MIRROR = "mirror"
SCRATCH = "scratch"
INLINE_RUST = "src/inline.rs"
RUST_TESTS = "#[cfg(test)]\nmod tests {\n    #[test]\n    fn works() {\n        assert_eq!(1 + 1, 2);\n    }\n}\n"
SOURCES = {"src/app.py": PLAIN_MODULE, "src/lib.rs": "pub fn one() -> i32 {\n    1\n}\n", INLINE_RUST: RUST_TESTS}
OPAQUE_WRITES = {
    "mktemp": 'd=$(mktemp -d); cp src/app.py "$d/test_copy.py"',
    "bash_c": 'bash -c "$WRITE"',
}


class ShellTargetCase(AfterRunCase):
    projects = {
        REPO: Project(project_files(SOURCES)),
        MIRROR: Project({}, git=False),
        SCRATCH: Project({"a.py": PLAIN_MODULE}, git=False),
    }

    def edit_old_test(self, kind):
        return lambda: self.change(REPO, OLD_TEST, SIZE_TEST + "X = " + repr(kind) + "\n")

    def decisions(self, commands):
        self.start(REPO)
        return {name: self.pre(REPO, command, call=name) for name, command in commands.items()}


class OpaqueWritesAreJudgedAfterTheRun(ShellTargetCase):
    def claude_steps(self, kind):
        self.start(REPO, run=kind)
        decision, batched, _ = self.pair(REPO, OPAQUE_WRITES[kind], self.edit_old_test(kind), run=kind)
        return decision, batched, self.stop(REPO, run=kind)

    def codex_steps(self, kind):
        self.start(REPO, CODEX, run=kind)
        decision, posted, _ = self.pair(REPO, OPAQUE_WRITES[kind], self.edit_old_test(kind), host=CODEX, run=kind)
        return decision, posted, self.stop(REPO, CODEX, run=kind)

    def blocked_paths(self, kind):
        self.shell(REPO, OPAQUE_WRITES[kind], self.edit_old_test(kind), run=kind)
        return self.finding_paths(GATE, REPO, run=kind)

    def test_unknown_mktemp_and_bash_c_writes_pass_before_the_run(self):
        """Feed input, check the return value."""
        self.assertEqual(self.decisions(OPAQUE_WRITES), dict.fromkeys(OPAQUE_WRITES, LET_THROUGH))

    def test_claude_batch_and_stop_block_a_test_change_by_an_opaque_write(self):
        """The test fails when a real bug enters the code it exercises."""
        verdicts = {kind: self.claude_steps(kind) for kind in OPAQUE_WRITES}
        self.assertEqual(verdicts, dict.fromkeys(OPAQUE_WRITES, (LET_THROUGH, CAUGHT, CAUGHT)))

    def test_codex_post_tool_use_and_stop_block_a_test_change_by_an_opaque_write(self):
        """The test fails when a real bug enters the code it exercises."""
        verdicts = {kind: self.codex_steps(kind) for kind in OPAQUE_WRITES}
        self.assertEqual(verdicts, dict.fromkeys(OPAQUE_WRITES, (LET_THROUGH, CAUGHT, CAUGHT)))

    def test_the_after_run_block_names_the_changed_test_path(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        paths = {kind: self.blocked_paths(kind) for kind in OPAQUE_WRITES}
        self.assertEqual(paths, dict.fromkeys(OPAQUE_WRITES, [OLD_TEST]))

    def test_unknown_delete_that_also_names_a_test_is_refused_before_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.decisions({"mixed": "rm -rf $UNKNOWN " + OLD_TEST}), {"mixed": DENY})


class SymbolicLinksCountOnlyTheLinkPath(ShellTargetCase):
    def passing_links(self):
        return {
            "tests_mirror": "ln -s " + str(self.folder(REPO) / "tests") + " " + str(self.folder(MIRROR) / "tests"),
            "venv": "ln -s " + str(self.home / ".local/venv") + " .venv",
        }

    def test_links_whose_link_path_is_no_test_pass_before_the_run(self):
        """Feed input, check the return value."""
        links = self.passing_links()
        self.assertEqual(self.decisions(links), dict.fromkeys(links, LET_THROUGH))

    def test_a_link_at_a_test_path_and_a_write_through_a_fresh_alias_are_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        commands = {"test_link": "ln -s src/app.py tests/test_link.py", "alias": "ln -s tests t && echo x > t/test_old.py"}
        self.assertEqual(self.decisions(commands), dict.fromkeys(commands, DENY))


class WritesResolveAgainstTheLandedDirectory(ShellTargetCase):
    def decision_from_tests(self, name, command):
        body = {"session_id": self.session(name), "hook_event_name": "PreToolUse", "cwd": str(self.folder(REPO) / "tests"),
                "tool_name": "Bash", "tool_use_id": name, "tool_input": {"command": command}}
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, environment, clear=True):
            response = run_hook("PreToolUse", body, host=CLAUDE, state_dir=self.state, git_view=self.view())
        return response.get("hookSpecificOutput", {}).get("permissionDecision")

    def decisions_from_tests(self, commands):
        return {name: self.decision_from_tests(name, command) for name, command in commands.items()}

    def test_removals_and_copies_after_landing_outside_the_project_pass(self):
        """Feed input, check the return value."""
        scratch = str(self.folder(SCRATCH))
        commands = {
            "rm_in_tmp": "cd /tmp && rm -rf tests",
            "cp_in_scratch": "cd " + scratch + " && cp a.py test_a.py",
            "mv_in_scratch": "cd " + scratch + " && mv a.py tests/test_b.py",
        }
        self.assertEqual(self.decisions_from_tests(commands), dict.fromkeys(commands, LET_THROUGH))

    def test_named_test_writes_after_cd_and_in_a_subshell_loop_are_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        commands = {
            "after_cd": "cd .. && cp src/app.py tests/test_new.py",
            "subshell_loop": "(cd .. && for f in src/app.py; do cp $f " + OLD_TEST + "; done)",
        }
        self.assertEqual(self.decisions_from_tests(commands), dict.fromkeys(commands, DENY))


class CopiesOfUnknownContent(ShellTargetCase):
    def test_a_missing_copy_source_onto_a_plain_rust_file_passes_before_the_run(self):
        """Feed input, check the return value."""
        self.assertEqual(self.decisions({"rust": "cp missing.rs src/lib.rs"}), {"rust": LET_THROUGH})

    def test_a_named_rust_test_destination_and_an_inline_test_deletion_are_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        commands = {"destination": "cp missing.rs tests/integration.rs", "inline_delete": "rm " + INLINE_RUST}
        self.assertEqual(self.decisions(commands), dict.fromkeys(commands, DENY))


class StashPathspecsAreJudged(ShellTargetCase):
    def test_engine_only_pathspecs_bare_stash_and_test_paths_in_the_message_pass(self):
        """Use a parameterized test for several inputs."""
        commands = {
            "engine_paths": "git stash push engine/a.py engine/b.py",
            "bare": "git stash",
            "message_names_a_test": "git stash push -m " + OLD_TEST + " engine/a.py",
            "long_message_names_a_test": "git stash push --message " + OLD_TEST + " -- engine/a.py",
        }
        self.assertEqual(self.decisions(commands), dict.fromkeys(commands, LET_THROUGH))

    def test_stash_push_of_a_test_pathspec_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        commands = {"plain": "git stash push " + OLD_TEST, "after_separator": "git stash push -m x -- " + OLD_TEST}
        self.assertEqual(self.decisions(commands), dict.fromkeys(commands, DENY))


if __name__ == "__main__":
    unittest.main()
