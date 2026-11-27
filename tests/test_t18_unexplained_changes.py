import unittest

from after_run_home import CAUGHT, GATE, LET_THROUGH, OLD_TEST, AfterRunCase, Project, project_files
from fake_repo_home import SIZE_TEST
from lib.git_port import FakeGitView


REPO = "repo"
README_PATH = "README.md"
GUIDE = "docs/guide.md"
NOTES = "docs/notes.md"
PROSE_RULE = "utilize"
UNEXPLAINED = "unexplained_change"
FAILED = "snapshot_failed"
FLAGGED = "We utilize this tool for the build.\n"
CHANGED_TEST = SIZE_TEST.replace("2]", "3]")
HIDDEN_WRITE = "python3 tools/bulk_write.py"
QUIET = "git status --short"
BACKGROUND = "sleep 5 &"
BETWEEN_CALLS = {README_PATH: FLAGGED, OLD_TEST: CHANGED_TEST}


class CountingView:
    def __init__(self, inner, calls):
        self.inner = inner
        self.calls = calls

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def head_blobs(self, root, paths, *, until=None):
        self.calls.append(tuple(paths))
        return self.inner.head_blobs(root, paths, until=until)


class ChangesBetweenToolCalls(AfterRunCase):
    projects = {REPO: Project(project_files({GUIDE: "Guide.\n"}))}

    def blind(self):
        return self.__dict__.get("blinded", False)

    def view(self):
        if self.blind():
            return FakeGitView({"roots": {}})
        return CountingView(super().view(), self.__dict__.setdefault("head_calls", []))

    def stop_after(self, command, relative, content, run):
        self.start(REPO, run=run)
        self.pair(REPO, command, run=run)
        self.change(REPO, relative, content)
        return self.stop(REPO, run=run)

    def quiet_outcomes(self):
        return {relative: (self.stop_after(QUIET, relative, content, relative), self.finding_paths(UNEXPLAINED, REPO, relative))
                for relative, content in BETWEEN_CALLS.items()}

    def background_outcomes(self):
        return {relative: (self.stop_after(BACKGROUND, relative, content, relative), self.blocked_rules(relative))
                for relative, content in BETWEEN_CALLS.items()}

    def blind_pre(self, call):
        self.blinded = True
        try:
            return self.pre(REPO, HIDDEN_WRITE, call)
        finally:
            self.blinded = False

    def failed_second_snapshot(self):
        self.start(REPO)
        self.pair(REPO, QUIET, call="c1")
        self.blind_pre("c2")
        self.change(REPO, OLD_TEST, CHANGED_TEST)
        self.change(REPO, NOTES, FLAGGED)
        self.post(REPO, HIDDEN_WRITE, "c2")
        return self.batch(REPO, HIDDEN_WRITE, "c2").get("decision")

    def failed_first_snapshot(self):
        self.start(REPO)
        decision = self.blind_pre("c1")
        self.change(REPO, OLD_TEST, CHANGED_TEST)
        self.post(REPO, HIDDEN_WRITE, "c1")
        return decision, self.batch(REPO, HIDDEN_WRITE, "c1").get("decision")

    def tracked_edits(self):
        self.change(REPO, README_PATH, "Longer readme.\n")
        self.change(REPO, GUIDE, "Longer guide.\n")
        self.change(REPO, OLD_TEST, CHANGED_TEST)

    def test_edit_between_calls_without_a_background_command_passes_and_is_noted_per_path(self):
        """Use a parameterized test for several inputs."""
        expected = {relative: (LET_THROUGH, [relative]) for relative in BETWEEN_CALLS}
        self.assertEqual(self.quiet_outcomes(), expected)

    def test_edit_between_calls_after_a_background_command_blocks_with_its_rule(self):
        """Use a parameterized test for several inputs."""
        expected = {README_PATH: (CAUGHT, [PROSE_RULE]), OLD_TEST: (CAUGHT, [GATE])}
        self.assertEqual(self.background_outcomes(), expected)

    def test_failed_snapshot_after_a_good_call_compares_against_the_turn_and_blocks(self):
        """The test fails when a real bug enters the code it exercises."""
        verdict = self.failed_second_snapshot()
        self.assertEqual((verdict, self.blocked_rules()), (CAUGHT, [GATE, PROSE_RULE]))

    def test_failed_first_snapshot_of_the_turn_lets_the_command_through_and_logs_the_failure(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        outcome = self.failed_first_snapshot()
        logged = {(row["event"], row["rule"]) for row in self.timing_rows() if row["rule"]}
        self.assertEqual((outcome, logged), ((None, LET_THROUGH), {("PreToolUse", FAILED), ("PostToolUse", FAILED)}))

    def test_several_tracked_edits_fetch_their_old_content_in_one_git_call_and_the_test_still_blocks(self):
        """Check the call count and the arguments of each mocked call."""
        verdict = self.after_run(REPO, HIDDEN_WRITE, self.tracked_edits)
        observed = (self.__dict__.get("head_calls"), verdict, self.blocked_rules())
        self.assertEqual(observed, ([tuple(sorted((README_PATH, GUIDE, OLD_TEST)))], CAUGHT, [GATE]))


if __name__ == "__main__":
    unittest.main()
