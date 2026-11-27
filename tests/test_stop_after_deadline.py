import sys
import time
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

import adw_light
from lib import blocker_state, end_turn
from lib.config import payload_hook_config

EXPIRED = 0.0
OPEN = float("inf")
SETUP_SPENT = 3600.0
PREVIEW_LIMIT = 3
SCRATCH_COUNT = 100
FLAGGED_PROSE = "We utilize this tool for the build.\n"
FLAGGED_RULE = "utilize"
CLEAN_PROSE = "The build reads the plan and writes the report.\n"
DUPLICATE_PROSE = "The build reads the plan file, checks each step, and writes one report per run.\n" * 4
DUPLICATE_RULE = "duplicate_file_content"
PENDING_MARKER = "held_check_marker"
MISSING_NAME = "null"
FIRST = "alpha-agent"
SECOND = "bravo-agent"
THIRD = "charlie-agent"


class StopAfterDeadline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        root = Path(workspace.name).resolve()
        cls.state = str(root / "state")
        cls.work = root / "work"
        scratch = root / "scratch"
        cls.work.mkdir()
        scratch.mkdir()
        cls.clean = cls.work / "clean.md"
        cls.clean.write_text(CLEAN_PROSE, encoding="utf-8")
        cls.flagged = [cls.work / f"flagged{index}.md" for index in range(4)]
        cls.single = cls.work / "single.md"
        cls.copies = [cls.work / "copy_a.md", cls.work / "copy_b.md"]
        cls.scratch = scratch / "probe.md"
        cls.missing = root / "gone" / MISSING_NAME
        cls.probes = [scratch / f"probe{index}.md" for index in range(SCRATCH_COUNT)]
        list(map(lambda path: path.write_text(FLAGGED_PROSE, encoding="utf-8"), [*cls.flagged, cls.single, cls.scratch, *cls.probes]))
        list(map(lambda path: path.write_text(DUPLICATE_PROSE, encoding="utf-8"), cls.copies))

    def payload(self, agent=""):
        session = "s" + sha256(self.id().encode()).hexdigest()[:16]
        return {"session_id": session, "cwd": str(self.work), "agent_id": agent}

    def config(self):
        return payload_hook_config({"state_root": self.state}, self.payload())

    def scope(self, agent):
        return blocker_state.BlockerScope(self.payload()["session_id"], agent, self.state)

    def track(self, agent, paths):
        blocker_state.touch_paths(self.scope(agent), [str(path) for path in paths])

    def queued(self, agent):
        return blocker_state.queued_scopes(self.payload()["session_id"], self.state).get(agent)

    def foreign(self, until, clock=None):
        return end_turn.foreign_outcome(self.payload(), self.config(), "", until=until, clock=clock)

    def clock_running_out_after_first_scope_settles(self, until):
        def now():
            first = self.queued(FIRST)
            return until if first is None or not first.paths else until - 1.0

        return now

    def names_in(self, reason, paths):
        return [path.name for path in paths if path.name in reason]

    def test_scopes_after_the_clock_runs_out_report_their_queued_files(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.track(FIRST, [self.clean])
        self.track(SECOND, self.flagged)
        self.track(THIRD, [self.single])
        until = time.monotonic() + SETUP_SPENT

        reason = self.foreign(until, self.clock_running_out_after_first_scope_settles(until)).reason

        self.assertIn(f"{SECOND}: {len(self.flagged)}", reason)
        self.assertIn(f"{THIRD}: 1", reason)
        self.assertEqual(PREVIEW_LIMIT, len(self.names_in(reason, self.flagged)))
        self.assertIn(self.single.name, reason)
        self.assertNotIn(FIRST, reason)
        self.assertNotIn(FLAGGED_RULE, reason)

    def test_deferred_scope_keeps_its_paths_tracked(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.track(SECOND, self.flagged)

        self.foreign(EXPIRED)

        self.assertEqual([str(path) for path in self.flagged], self.queued(SECOND).paths)

    def test_deferred_paths_are_scanned_at_the_next_stop(self):
        """The test fails when a real bug enters the code it exercises."""
        self.track(SECOND, self.flagged)
        self.foreign(EXPIRED)

        reason = self.foreign(OPEN).reason

        self.assertIn(FLAGGED_RULE, reason)
        self.assertIn(SECOND, reason)

    def test_pending_only_scope_is_listed_and_keeps_its_blocker(self):
        """Feed input, check the return value."""
        blocker_state.set_pending(self.scope(SECOND), "<turn>", PENDING_MARKER)

        reason = self.foreign(EXPIRED).reason

        self.assertIn(SECOND, reason)
        self.assertTrue(self.queued(SECOND).has_pending)

    def test_pending_only_blocker_is_reported_at_the_next_stop(self):
        """Write a unit test for domain logic and algorithms."""
        blocker_state.set_pending(self.scope(SECOND), "<turn>", PENDING_MARKER)
        self.foreign(EXPIRED)

        reason = self.foreign(OPEN).reason

        self.assertIn(PENDING_MARKER, reason)

    def test_parent_stop_after_the_deadline_lists_its_own_and_subagent_files(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.track("", [self.single])
        self.track(SECOND, self.flagged)
        self.track(THIRD, [self.clean])

        result = adw_light.run("Stop", self.payload(), {"state_root": self.state}, started_at=time.monotonic() - SETUP_SPENT)

        self.assertEqual("block", result["decision"])
        self.assertIn(self.single.name, result["reason"])
        self.assertIn(SECOND, result["reason"])
        self.assertIn(THIRD, result["reason"])
        self.assertNotIn(FLAGGED_RULE, result["reason"])

    def test_subagent_stop_after_the_deadline_lists_its_files_and_keeps_them(self):
        """Use output based if the code returns a value."""
        self.track(SECOND, self.flagged)

        outcome = end_turn.stop_outcome(self.payload(SECOND), self.config(), hook="subagent_stop", until=EXPIRED)

        self.assertEqual(PREVIEW_LIMIT, len(self.names_in(outcome.reason, self.flagged)))
        self.assertEqual([str(path) for path in self.flagged], self.queued(SECOND).paths)

    def test_duplicate_check_left_by_the_deadline_blocks_at_the_next_stop(self):
        """Unit test domain code."""
        self.track(SECOND, self.copies)
        blocker_state.set_pending(self.scope(SECOND), blocker_state.BATCH_KEY, PENDING_MARKER)
        self.foreign(EXPIRED)

        reason = self.foreign(OPEN).reason

        self.assertIn(DUPLICATE_RULE, reason)

    def test_deferred_preview_leaves_out_scratch_and_missing_files(self):
        """Compute expected values independently."""
        self.track(SECOND, [self.scratch, self.missing, self.single])

        reason = self.foreign(EXPIRED).reason

        self.assertIn(f"{SECOND}: 1", reason)
        self.assertIn(self.single.name, reason)
        self.assertNotIn(self.scratch.name, reason)
        self.assertNotIn(MISSING_NAME, reason)

    def test_deferred_scope_holding_only_scratch_files_is_not_listed(self):
        """Keep one act."""
        self.track(SECOND, [self.scratch, self.missing])

        reason = self.foreign(EXPIRED).reason

        self.assertEqual("", reason)

    def test_deferred_scope_with_many_scratch_and_missing_files_is_not_listed(self):
        """Name the bug."""
        self.track(SECOND, [*self.probes[:4], self.missing])

        reason = self.foreign(EXPIRED).reason

        self.assertEqual("", reason)

    def test_deferred_count_covers_only_real_files_among_many_scratch_files(self):
        """Use precomputed results from the domain."""
        real = [self.single, self.clean, self.flagged[0]]
        self.track(SECOND, [*self.probes, *real])

        reason = self.foreign(EXPIRED).reason

        self.assertIn(f"{SECOND}: {len(real)}", reason)
        self.assertNotIn(self.probes[0].name, reason)


if __name__ == "__main__":
    unittest.main()
