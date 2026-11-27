import unittest

from after_run_home import (
    CAUGHT, CODEX, DENY, GATE, LET_THROUGH, OLD_TEST, TEST_WRITER, AfterRunCase, Project, identity, project_files,
)
from fake_repo_home import SIZE_TEST


REPO = "repo"
NEW_TEST = "tests/test_added.py"
SECOND_TEST = "tests/test_second.py"
EDITED = SIZE_TEST.replace("2]", "3]")
ADDED = SIZE_TEST.replace("test_size", "test_added")
HIDDEN_SHAPES = {
    "python": "python3 -c \"import pathlib; pathlib.Path('tes' + 'ts/test_added.py').write_text('x')\"",
    "sed": "sed -i '' 's/2]/3]/' $(cat list.txt)",
    "bash": "bash -c \"python3 -c 'import os; os.remove(chr(116) + \\\"ests/test_old.py\\\")'\"",
}
NAMED_SHAPES = {
    "sed": "sed -i '' 's/2]/3]/' tests/test_old.py",
    "bash": "bash -c 'rm -f tests/test_old.py'",
}


class ShellShapes(AfterRunCase):
    projects = {REPO: Project(project_files())}

    def effect(self, kind):
        return {
            "python": lambda: self.change(REPO, NEW_TEST, ADDED),
            "sed": lambda: self.change(REPO, OLD_TEST, EDITED),
            "bash": lambda: self.remove(REPO, OLD_TEST),
        }[kind]

    def verdicts(self, before, after):
        return {kind: self.after_run(REPO, command, self.effect(kind), before=before, after=after, run=kind)
                for kind, command in HIDDEN_SHAPES.items()}

    def pre_run_verdicts(self):
        self.start(REPO)
        return {kind: self.pre(REPO, command, call=kind) for kind, command in NAMED_SHAPES.items()}

    def test_main_agent_test_changes_through_python_sed_and_bash_are_caught_after_the_run(self):
        """Use a parameterized test for several inputs."""
        self.assertEqual(self.verdicts(None, None), dict.fromkeys(HIDDEN_SHAPES, CAUGHT))

    def test_identity_saved_before_the_run_lets_the_test_writer_through_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.verdicts(identity(TEST_WRITER), identity("")), dict.fromkeys(HIDDEN_SHAPES, LET_THROUGH))

    def test_test_writer_identity_claimed_only_after_the_run_is_still_caught(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.verdicts(identity(""), identity(TEST_WRITER)), dict.fromkeys(HIDDEN_SHAPES, CAUGHT))

    def test_named_test_paths_in_sed_and_bash_stay_refused_before_the_run(self):
        """Feed input, check the return value."""
        self.assertEqual(self.pre_run_verdicts(), dict.fromkeys(NAMED_SHAPES, DENY))


class HostResponses(AfterRunCase):
    projects = {REPO: Project(project_files())}

    def two_tests(self):
        self.change(REPO, NEW_TEST, ADDED)
        self.change(REPO, SECOND_TEST, ADDED)

    def test_claude_post_tool_use_only_advises_and_the_batch_blocks(self):
        """Feed input, check the return value."""
        self.start(REPO)
        _, batched, posted = self.pair(REPO, HIDDEN_SHAPES["python"], self.two_tests)
        self.assertEqual((posted.get("decision"), batched), (None, CAUGHT))

    def test_codex_post_tool_use_blocks_without_a_batch_event(self):
        """Feed input, check the return value."""
        self.assertEqual(self.after_run(REPO, HIDDEN_SHAPES["python"], self.two_tests, host=CODEX), CAUGHT)

    def test_one_block_names_every_changed_test_path(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.after_run(REPO, HIDDEN_SHAPES["python"], self.two_tests)
        self.assertEqual(self.finding_paths(GATE, REPO), [NEW_TEST, SECOND_TEST])


class DelayedWritesAtStop(AfterRunCase):
    projects = {REPO: Project(project_files())}

    def test_stop_keeps_blocking_a_delayed_test_write_until_it_is_undone(self):
        """The test fails when a real bug enters the code it exercises."""
        self.start(REPO)
        self.pair(REPO, "sleep 5 &", call="c1")
        self.change(REPO, NEW_TEST, ADDED)
        first = self.stop(REPO)
        self.pair(REPO, "git status --short", call="c2")
        second = self.stop(REPO)
        self.remove(REPO, NEW_TEST)
        self.assertEqual((first, second, self.stop(REPO)), (CAUGHT, CAUGHT, LET_THROUGH))

    def test_codex_stop_blocks_a_delayed_test_write(self):
        """Feed input, check the return value."""
        self.start(REPO, CODEX)
        self.pair(REPO, "sleep 5 &", host=CODEX)
        self.change(REPO, NEW_TEST, ADDED)
        self.assertEqual(self.stop(REPO, CODEX), CAUGHT)


if __name__ == "__main__":
    unittest.main()
