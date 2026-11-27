import sys
import time
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from lib import blocker_state, end_turn
from lib.config import payload_hook_config
from lib.stop_stall import StopOutcome

EXPIRED = 0.0
OPEN = float("inf")
SETUP_SPENT = 3600.0
CLEAN_PROSE = "The build reads the plan and writes the report.\n"
PENDING_MARKER = "held_check_marker"
FIRST = "alpha-agent"
SECOND = "bravo-agent"
THIRD = "charlie-agent"


class StopScopeRotation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        root = Path(workspace.name).resolve()
        cls.state = str(root / "state")
        cls.work = root / "work"
        cls.work.mkdir()
        cls.clean = cls.work / "clean.md"
        cls.clean.write_text(CLEAN_PROSE, encoding="utf-8")
        cls.deleted = cls.work / "deleted.md"

    def payload(self):
        session = "s" + sha256(self.id().encode()).hexdigest()[:16]
        return {"session_id": session, "cwd": str(self.work), "agent_id": ""}

    def config(self):
        return payload_hook_config({"state_root": self.state}, self.payload())

    def scope(self, agent):
        return blocker_state.BlockerScope(self.payload()["session_id"], agent, self.state)

    def track(self, agent, paths):
        blocker_state.touch_paths(self.scope(agent), [str(path) for path in paths])

    def track_clean(self, *agents):
        list(map(lambda agent: self.track(agent, [self.clean]), agents))

    def holding_paths(self):
        queued = blocker_state.queued_scopes(self.payload()["session_id"], self.state)
        return sum(1 for scope in queued.values() if scope.paths)

    def closing_after(self, settled):
        until = time.monotonic() + SETUP_SPENT
        closes_at = self.holding_paths() - settled

        def now():
            return until if self.holding_paths() <= closes_at else until - 1.0

        return until, now

    def foreign(self, until, clock=None):
        return end_turn.foreign_outcome(self.payload(), self.config(), "", until=until, clock=clock)

    def stop_closing_after(self, settled):
        until, clock = self.closing_after(settled)
        return self.foreign(until, clock)

    def repeated_deferral(self, stops):
        self.track_clean(SECOND)
        outcomes = [self.foreign(EXPIRED) for _ in range(stops)]
        return outcomes[-1]

    def test_deferred_scope_with_only_deleted_paths_still_lists_its_held_blocker(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.track(SECOND, [self.deleted])
        blocker_state.set_pending(self.scope(SECOND), "<turn>", PENDING_MARKER)

        reason = self.foreign(EXPIRED).reason

        self.assertIn(SECOND, reason)

    def test_held_blocker_of_a_deleted_only_scope_blocks_at_the_next_stop(self):
        """The test fails when a real bug enters the code it exercises."""
        self.track(SECOND, [self.deleted])
        blocker_state.set_pending(self.scope(SECOND), "<turn>", PENDING_MARKER)
        self.foreign(EXPIRED)

        reason = self.foreign(OPEN).reason

        self.assertIn(PENDING_MARKER, reason)

    def test_next_stop_starts_with_the_scope_after_a_first_scope_that_ran_out(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.track_clean(FIRST, SECOND, THIRD)
        self.stop_closing_after(1)
        self.track_clean(FIRST)

        reason = self.stop_closing_after(1).reason

        self.assertNotIn(SECOND, reason)
        self.assertIn(FIRST, reason)
        self.assertIn(THIRD, reason)

    def test_next_stop_starts_with_the_scope_that_was_cut(self):
        """Resistance to refactoring is not negotiable, because a test that fails on every refactor trains the team to ignore failures."""
        self.track_clean(FIRST, SECOND, THIRD)
        self.stop_closing_after(2)
        self.track_clean(FIRST)

        reason = self.stop_closing_after(1).reason

        self.assertNotIn(THIRD, reason)
        self.assertIn(FIRST, reason)

    def test_identical_deferred_line_still_blocks_at_the_second_stop(self):
        """Use output based if the code returns a value."""
        outcome = self.repeated_deferral(2)

        self.assertIn(SECOND, outcome.reason)

    def test_identical_deferred_line_releases_with_a_notice_at_the_third_stop(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        outcome = self.repeated_deferral(3)

        self.assertEqual("", outcome.reason)
        self.assertNotEqual("", outcome.notice)

    def test_identical_deferred_line_stays_silent_after_its_release(self):
        """Feed input, check the return value."""
        outcome = self.repeated_deferral(4)

        self.assertEqual(StopOutcome("", ""), outcome)

    def test_foreign_outcome_returns_a_reason_and_a_notice(self):
        """Stay on the public API."""
        blocker_state.set_pending(self.scope(SECOND), "<turn>", PENDING_MARKER)

        outcome = self.foreign(OPEN)

        self.assertIsInstance(outcome, StopOutcome)
        self.assertIn(PENDING_MARKER, outcome.reason)
        self.assertEqual("", outcome.notice)


if __name__ == "__main__":
    unittest.main()
