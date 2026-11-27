import itertools
import json
import math
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
from lib import blocker_state, end_turn, scan_cache, session_state, stop_scan
from lib.config import payload_hook_config

OPEN = float("inf")
SETUP_SPENT = 3600.0
REAL_PAST = 1.0
FLAGGED_PROSE = "We utilize this tool for the build.\n"
FLAGGED_RULE = "utilize"
CLEAN_PROSE = "The build reads the plan and writes the report.\n"
HEAVY_PROSE = "".join(f"Step {index} lets us utilize the cache.\n\n" for index in range(200))
PENDING_MARKER = "held_check_marker"
UPDATED_MARKER = "updated_check_marker"
ROTATED_COUNT = 12
MERGING_SCOPES = 9
SCOPE_COUNT = 21
STALL_STOPS = 20
STOP_MARGIN = 1.5
SCAN_BUDGET_LEFT = 1.0
HOOKS = PROJECT / "hooks" / "hooks.json"


def ticking(per_stop):
    base = time.monotonic() + SETUP_SPENT
    calls = itertools.count()
    return base + per_stop + 1, lambda: base + next(calls)


def hooked(clock, actions):
    calls = itertools.count(1)

    def now():
        actions.get(next(calls), lambda: None)()
        return clock()

    return now


def without_marks(bucket):
    kept = {key: value for key, value in bucket.items() if key != blocker_state.TOUCHED_KEY}
    return {**kept, "revision": bucket["revision"] + 1}


def malformed(bucket, path):
    return {
        **bucket, "scan_findings": ["row", {"path": 5}], "scan_stamps": {"x": 7, path: "short"},
        "scan_pause": [5], "scan_cursor": 42, blocker_state.TOUCHED_KEY: "bad",
    }


def stop_timeout():
    hooks = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]["Stop"]
    return hooks[0]["hooks"][0]["timeout"]


class StopRotationInsideAScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        root = Path(workspace.name).resolve()
        cls.state = str(root / "state")
        cls.work = root / "work"
        cls.work.mkdir()
        cls.flagged = [cls.work / f"flagged{index:03d}.md" for index in range(blocker_state.MAX_SCOPE_PATHS)]
        cls.clean = [cls.work / f"clean{index}.md" for index in range(4)]
        cls.fixable = cls.work / "fixable.md"
        cls.heavy = cls.work / "heavy.md"
        list(map(lambda path: path.write_text(FLAGGED_PROSE, encoding="utf-8"), cls.flagged))
        list(map(lambda path: path.write_text(CLEAN_PROSE, encoding="utf-8"), cls.clean))
        cls.heavy.write_text(HEAVY_PROSE, encoding="utf-8")

    def session(self):
        return "s" + sha256(self.id().encode()).hexdigest()[:16]

    def payload(self, agent=""):
        return {"session_id": self.session(), "cwd": str(self.work), "agent_id": agent}

    def config(self):
        return payload_hook_config({"state_root": self.state}, self.payload())

    def scope(self, agent=""):
        return blocker_state.BlockerScope(self.session(), agent, self.state)

    def track(self, paths, agent=""):
        blocker_state.touch_paths(self.scope(agent), [str(path) for path in paths])

    def hold(self, paths):
        list(map(lambda path: blocker_state.set_pending(self.scope(), str(path), PENDING_MARKER), paths))

    def stop(self, until=OPEN, clock=None):
        return end_turn.stop_outcome(self.payload(), self.config(), until=until, clock=clock)

    def ticked_stop(self, per_stop, actions=None):
        until, clock = ticking(per_stop)
        return self.stop(until, hooked(clock, actions or {}))

    def scan_state(self):
        return blocker_state.scan_state(self.scope())

    def details(self):
        return blocker_state.details(self.session(), "", self.state)

    def rewrite(self, change):
        def changed(state):
            scopes = state[blocker_state.KEY]
            return {**state, blocker_state.KEY: {**scopes, "": change(scopes[""])}}

        session_state.update_state_strict(self.session(), changed, self.state)

    def stored_rows(self):
        scopes = session_state.read_state_strict(self.session(), self.state)[blocker_state.KEY]
        return [len(bucket.get("scan_findings", [])) for bucket in scopes.values()]

    def stamped_after_bounded_stops(self, count, per_stop):
        files = self.flagged[:count]
        self.track(files)
        [self.ticked_stop(per_stop) for _ in range(math.ceil(count / per_stop))]
        return {str(path) for path in files}, set(self.scan_state().stamps)

    def touched_during_scan(self):
        self.track([*self.clean[:2], *self.flagged[:3]])
        touched = [str(self.clean[0]), str(self.flagged[0]), str(self.work / "added.md")]
        self.ticked_stop(3, {5: lambda: blocker_state.touch_paths(self.scope(), touched)})
        return self.scan_state()

    def pending_after_write(self, write):
        self.track(self.clean[:2])
        self.hold(self.clean[:2])
        self.stop(OPEN, hooked(time.monotonic, {2: write}))
        return set(self.details()[0])

    def cut_with_time_left(self, left):
        self.track(self.flagged[:2])
        until = time.monotonic() - REAL_PAST
        self.stop(until, lambda: until - left)
        return self.scan_state()

    def stops_over_unchanged_findings(self, per_stop):
        self.track(self.flagged[:ROTATED_COUNT])
        return [self.ticked_stop(per_stop) for _ in range(STALL_STOPS)]

    def test_every_file_gets_a_scan_within_the_bounded_stops_at_three_per_stop(self):
        """Write a unit test for domain logic and algorithms."""
        expected, stamped = self.stamped_after_bounded_stops(ROTATED_COUNT, 3)

        self.assertEqual(expected, stamped)

    def test_every_file_gets_a_scan_within_the_bounded_stops_at_four_per_stop(self):
        """Use a parameterized test for several inputs."""
        expected, stamped = self.stamped_after_bounded_stops(10, 4)

        self.assertEqual(expected, stamped)

    def test_clean_rescan_of_a_fixed_file_releases_its_pending_reason(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.fixable.write_text(FLAGGED_PROSE, encoding="utf-8")
        self.track([self.fixable])
        self.hold([self.fixable])
        self.stop()
        self.fixable.write_text(CLEAN_PROSE, encoding="utf-8")

        outcome = self.stop()

        self.assertEqual("", outcome.reason)

    def test_touch_during_the_scan_keeps_the_cursor(self):
        """The test fails when a real bug enters the code it exercises."""
        state = self.touched_during_scan()

        self.assertEqual(str(self.flagged[1]), state.paths[0])

    def test_touch_during_the_scan_keeps_the_touched_paths_tracked(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        state = self.touched_during_scan()

        self.assertLessEqual({str(self.clean[0]), str(self.work / "added.md")}, set(state.paths))

    def test_touch_during_the_scan_still_drops_untouched_clean_paths(self):
        """A test must verify what the code does for its client, never how it does it."""
        state = self.touched_during_scan()

        self.assertNotIn(str(self.clean[1]), state.paths)

    def test_touch_during_the_scan_keeps_the_findings_and_stamp_of_the_touched_path(self):
        """Protection against regressions."""
        state = self.touched_during_scan()

        self.assertIn(str(self.flagged[0]), state.stamps)
        self.assertIn(str(self.flagged[0]), {row["path"] for row in state.findings})

    def test_pending_set_during_the_scan_survives_while_untouched_keys_clear(self):
        """Feed input, check the return value."""
        write = lambda: blocker_state.set_pending(self.scope(), str(self.clean[0]), UPDATED_MARKER)

        kept = self.pending_after_write(write)

        self.assertEqual({str(self.clean[0])}, kept)

    def test_pending_applied_during_the_scan_survives_while_untouched_keys_clear(self):
        """Unit test domain code."""
        write = lambda: blocker_state.apply_paths(self.scope(), [], {str(self.clean[0]): UPDATED_MARKER})

        kept = self.pending_after_write(write)

        self.assertEqual({str(self.clean[0])}, kept)

    def test_older_state_without_marks_clears_no_pending_key_and_no_path(self):
        """Resistance to refactoring is not negotiable, because a test that fails on every refactor trains the team to ignore failures."""
        kept = self.pending_after_write(lambda: self.rewrite(without_marks))

        self.assertEqual({str(path) for path in self.clean[:2]}, kept)
        self.assertEqual([str(path) for path in self.clean[:2]], self.details()[1])

    def test_older_state_without_marks_keeps_a_clean_path_through_a_cut_scan(self):
        """Name the bug."""
        self.track([self.clean[0], *self.flagged[:2]])

        self.ticked_stop(1, {3: lambda: self.rewrite(without_marks)})

        self.assertIn(str(self.clean[0]), self.details()[1])

    def test_malformed_scan_cache_neither_crashes_nor_hides_findings(self):
        """Prefer output based tests."""
        self.track(self.flagged[:1])
        self.rewrite(lambda bucket: malformed(bucket, str(self.flagged[0])))

        outcome = self.stop()

        self.assertIn(FLAGGED_RULE, outcome.reason)

    def test_stuck_first_file_with_a_full_turn_left_is_paused_and_passed(self):
        """Compute expected values independently."""
        state = self.cut_with_time_left(stop_scan.FULL_TURN)

        self.assertEqual([str(self.flagged[0])], state.pause)
        self.assertEqual(str(self.flagged[1]), state.paths[0])

    def test_stuck_first_file_with_less_than_a_full_turn_left_keeps_its_turn(self):
        """Make the test fail first."""
        state = self.cut_with_time_left(stop_scan.FULL_TURN - 0.5)

        self.assertEqual([], state.pause)
        self.assertEqual(str(self.flagged[0]), state.paths[0])

    def test_scope_cap_keeps_the_cursor_path(self):
        """Stay on the public API."""
        self.track(self.flagged)
        self.ticked_stop(0)

        self.track([self.work / "extra.md"])

        self.assertIn(str(self.flagged[0]), self.details()[1])

    def test_stall_guard_releases_unchanged_findings_rotating_three_per_stop(self):
        """Unit test heavily."""
        outcomes = self.stops_over_unchanged_findings(3)

        self.assertNotEqual("", outcomes[0].reason)
        self.assertEqual("", outcomes[-1].reason)

    def test_stall_guard_releases_unchanged_findings_rotating_four_per_stop(self):
        """Use precomputed results from the domain."""
        outcomes = self.stops_over_unchanged_findings(4)

        self.assertNotEqual("", outcomes[0].reason)
        self.assertEqual("", outcomes[-1].reason)

    def test_stored_rows_stay_within_the_scope_and_session_limits(self):
        """Keep one act."""
        list(map(lambda index: self.track([self.heavy], f"agent-{index}"), range(MERGING_SCOPES)))

        end_turn.foreign_outcome(self.payload(), self.config(), "", until=OPEN)

        rows = self.stored_rows()
        self.assertLessEqual(max(rows), scan_cache.SCOPE_LIMIT)
        self.assertLessEqual(sum(rows), scan_cache.SESSION_LIMIT)

    def test_cut_scan_with_the_batch_key_pending_drops_no_clean_path(self):
        """Build fixtures with factory functions."""
        self.track([*self.clean[:2], *self.flagged[:2]])
        blocker_state.set_pending(self.scope(), blocker_state.BATCH_KEY, PENDING_MARKER)

        self.ticked_stop(2)

        self.assertLessEqual({str(path) for path in self.clean[:2]}, set(self.details()[1]))

    def test_stop_over_many_full_scopes_ends_under_the_hook_timeout(self):
        """Fast feedback."""
        agents = ["", *(f"scope-{index:02d}" for index in range(SCOPE_COUNT - 1))]
        list(map(lambda agent: self.track(self.flagged, agent), agents))
        started_at = time.monotonic() - (stop_scan.STOP_SCAN_CAP - SCAN_BUDGET_LEFT)

        adw_light.run("Stop", self.payload(), {"state_root": self.state}, started_at=started_at)

        self.assertLessEqual(time.monotonic() - started_at, stop_timeout() - STOP_MARGIN)


if __name__ == "__main__":
    unittest.main()
