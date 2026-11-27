import json
import time
import unittest

from after_commit_home import CLEAN_TEXT, FLAGGED_TEXT, NOTES, AfterCommitCase, commit_project
from after_run_home import CAUGHT, CLAUDE, CODEX, CONFIG, GATE, LET_THROUGH, OLD_TEST, Project, identity, project_files
from fake_repo_home import SIZE_TEST


REPO = "repo"
WINDOW = "window"
FETCHED = "refs/remotes/origin/main"
TOPIC = "refs/heads/topic-"
KEEP = "refs/heads/keep"
OLD = 1
RUN = "bash tools/run.sh"
DIRTY_TEST = "tests/test_dirty.py"
CHANGED_TEST = SIZE_TEST + "X = 1\n"
DASHED_NOTES = "Old notes stay short \u2014 and plain.\n"
HELD_THEN_RELEASED = {"later_commit": (CAUGHT, CAUGHT, CAUGHT), "reset_away": (CAUGHT, LET_THROUGH, None)}
OPEN_WINDOW = {CONFIG: json.dumps({"tests_allow_until": int(time.time()) + 3600})}


class MovedHeadCase(AfterCommitCase):
    projects = {
        REPO: commit_project(),
        WINDOW: Project(project_files({NOTES: "Old.\n", **OPEN_WINDOW})),
    }

    def move_to_unreached(self, files):
        def moved():
            commit = self.branch_commit(REPO, FETCHED, files, time=OLD)
            self.write_files(REPO, files)
            self.point(REPO, {"HEAD": commit})
        return moved

    def move_to_known(self, files, dirty):
        commit = self.branch_commit(REPO, KEEP, files)

        def moved():
            self.write_files(REPO, {**files, **dirty})
            self.point(REPO, {"HEAD": commit})
        return moved

    def moved_rules(self, run, files):
        self.start(REPO, run=run)
        self.pair(REPO, "git checkout FETCH_HEAD", self.move_to_unreached(files), run=run)
        return self.blocked_rules(run)


class UnreachedMovesAreJudgedInFull(MovedHeadCase):
    def test_a_move_to_a_commit_outside_every_tip_blocks_its_test_change_and_its_new_prose(self):
        """The test fails when a real bug enters the code it exercises."""
        rules = {"test": self.moved_rules("test", {OLD_TEST: CHANGED_TEST}),
                 "prose": self.moved_rules("prose", {NOTES: DASHED_NOTES})}
        self.assertEqual(rules, {"test": [GATE], "prose": ["banned_dash"]})


class KnownMovesJudgeOnlyDirtyPaths(MovedHeadCase):
    def test_a_move_to_a_commit_a_branch_already_had_judges_only_the_paths_left_dirty(self):
        """Feed input, check the return value."""
        self.change(REPO, OLD_TEST, SIZE_TEST + "X = 0\n")
        moved = self.move_to_known({OLD_TEST: CHANGED_TEST}, {DIRTY_TEST: CHANGED_TEST})
        self.start(REPO)
        self.pair(REPO, "git checkout keep", moved)
        self.assertEqual(self.finding_paths(GATE, REPO), [DIRTY_TEST])


def new_test(run):
    return "tests/test_new_" + run + ".py"


def side_note(run):
    return "docs/side_" + run + ".md"


class CommittedTestChanges(MovedHeadCase):
    def committed_test(self, name=REPO, command=RUN, agent=None, run=""):
        self.start(name, run=run)
        effect = self.committing(name, {new_test(run): SIZE_TEST})
        _, verdict, _ = self.pair(name, command, effect, before=agent, run=run)
        return verdict, self.stop(name, run=run)

    def held_after(self, host, follow, run):
        self.start(REPO, host, run=run)
        _, first, _ = self.pair(REPO, RUN, self.committing(REPO, {new_test(run): SIZE_TEST}), host=host, run=run)
        _, later, _ = self.pair(REPO, RUN, follow, call="c2", host=host, run=run)
        return first, later, self.stop(REPO, host, run=run)

    def reset_away(self, run):
        def reset():
            parent = self.tip(REPO).parent
            self.point(REPO, {"HEAD": parent, "main": parent, "refs/heads/main": parent})
            self.remove(REPO, new_test(run))
        return reset

    def held_steps(self, host):
        return {
            "later_commit": self.held_after(host, self.committing(REPO, {NOTES: CLEAN_TEXT}), host + "later"),
            "reset_away": self.held_after(host, self.reset_away(host + "reset"), host + "reset"),
        }

    def test_a_commit_that_adds_a_test_blocks_an_ordinary_agent(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.committed_test(), (CAUGHT, CAUGHT))

    def test_the_test_writer_an_open_window_an_exempt_command_and_a_replay_pass_a_committed_test(self):
        """Use a parameterized test for several inputs."""
        verdicts = {
            "test_writer": self.committed_test(agent=identity("adw-test-writer"), run="writer"),
            "window": self.committed_test(WINDOW, run="window"),
            "merge": self.committed_test(command="git merge topic", run="merge"),
            "rebase": self.committed_test(command="git rebase main", run="rebase"),
            "pull": self.committed_test(command="git pull", run="pull"),
        }
        self.assertEqual(verdicts, dict.fromkeys(verdicts, (LET_THROUGH, None)))

    def test_on_claude_a_held_test_commit_stays_while_a_branch_reaches_it_and_goes_when_reset_away(self):
        """Use a parameterized test for several inputs."""
        self.assertEqual(self.held_steps(CLAUDE), HELD_THEN_RELEASED)

    def test_on_codex_a_held_test_commit_stays_while_a_branch_reaches_it_and_goes_when_reset_away(self):
        """Use a parameterized test for several inputs."""
        self.assertEqual(self.held_steps(CODEX), HELD_THEN_RELEASED)


class SideBranchContent(MovedHeadCase):
    def side_steps(self, host, follow, run):
        self.start(REPO, host, run=run)
        flagged = lambda: self.branch_commit(REPO, TOPIC + run, {side_note(run): FLAGGED_TEXT})
        _, first, _ = self.pair(REPO, RUN, flagged, host=host, run=run)
        _, later, _ = self.pair(REPO, RUN, follow, call="c2", host=host, run=run)
        return first, later, self.stop(REPO, host, run=run)

    def fixed_on_branch(self, run):
        return lambda: self.branch_commit(REPO, TOPIC + run, {side_note(run): CLEAN_TEXT})

    def test_a_side_branch_finding_holds_until_the_branch_fixes_it_on_claude_and_codex(self):
        """Use a parameterized test for several inputs."""
        steps = {
            (CLAUDE, "unfixed"): self.side_steps(CLAUDE, lambda: None, "cu"),
            (CLAUDE, "fixed"): self.side_steps(CLAUDE, self.fixed_on_branch("cf"), "cf"),
            (CODEX, "unfixed"): self.side_steps(CODEX, lambda: None, "xu"),
            (CODEX, "fixed"): self.side_steps(CODEX, self.fixed_on_branch("xf"), "xf"),
        }
        held, released = (CAUGHT, CAUGHT, CAUGHT), (CAUGHT, LET_THROUGH, None)
        self.assertEqual(steps, {(CLAUDE, "unfixed"): held, (CLAUDE, "fixed"): released,
                                 (CODEX, "unfixed"): held, (CODEX, "fixed"): released})


if __name__ == "__main__":
    unittest.main()
