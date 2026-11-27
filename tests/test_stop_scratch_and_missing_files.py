import os
import sys
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from lib import blocker_state, end_turn
from lib.config import payload_hook_config

EXPIRED = 0.0
OPEN = float("inf")
FLAGGED_PROSE = "We utilize this tool for the build.\n"
SCRATCH_FILES = {
    "probe-payload.json": '{"session_id": "probe", "last_assistant_message": "We utilize this."}\n',
    "probe.py": "print('We utilize this tool.')\n",
    "VERSION": "0.1.3\n",
}
MISSING_NAME = "null"
SUBAGENT = "helper-agent"


class StopScopeCases:
    agent = ""
    hook = "stop"

    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        root = Path(workspace.name).resolve()
        cls.state = root / "state"
        cls.work = root / "work"
        cls.scratch = root / "scratch"
        cls.missing = root / "gone" / MISSING_NAME
        cls.work.mkdir()
        cls.scratch.mkdir()
        cls.notes = cls.work / "notes.md"
        cls.notes.write_text(FLAGGED_PROSE, encoding="utf-8")
        cls.real_null = cls.work / MISSING_NAME
        cls.real_null.write_text("plain\n", encoding="utf-8")
        cls.scratch_paths = [str(cls.scratch / name) for name in SCRATCH_FILES]
        list(map(lambda name: (cls.scratch / name).write_text(SCRATCH_FILES[name], encoding="utf-8"), SCRATCH_FILES))

    def payload(self, cwd, agent):
        session = "s" + sha256(self.id().encode()).hexdigest()[:16]
        return {"session_id": session, "cwd": cwd, "agent_id": agent}

    def track(self, payload, agent, paths):
        cfg = payload_hook_config({"state_root": str(self.state)}, payload)
        blocker_state.touch_paths(blocker_state.BlockerScope(payload["session_id"], agent, str(self.state)), paths)
        return cfg

    def outcome(self, paths, until, cwd=None):
        payload = self.payload(cwd or str(self.work), self.agent)
        cfg = self.track(payload, self.agent, paths)
        return end_turn.stop_outcome(payload, cfg, hook=self.hook, until=until)

    def test_scratch_files_under_the_system_temp_folder_are_not_scanned(self):
        """A test must verify what the code does for its client, never how it does it."""
        outcome = self.outcome(self.scratch_paths, EXPIRED)

        self.assertEqual("", outcome.reason)

    def test_scratch_files_are_not_scanned_with_a_relative_cwd(self):
        """Feed input, check the return value."""
        outcome = self.outcome(self.scratch_paths, EXPIRED, cwd=os.path.relpath(self.work))

        self.assertEqual("", outcome.reason)

    def test_written_project_file_under_a_temp_folder_stays_scanned(self):
        """The test fails when a real bug enters the code it exercises."""
        outcome = self.outcome([str(self.notes)], OPEN)

        self.assertIn(self.notes.name, outcome.reason)

    def test_missing_path_named_null_is_left_out_of_the_timeout_notice(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        outcome = self.outcome([str(self.missing), str(self.notes)], EXPIRED)

        self.assertIn(self.notes.name, outcome.reason)
        self.assertNotIn(MISSING_NAME, outcome.reason)

    def test_real_project_file_named_null_stays_in_the_timeout_notice(self):
        """Write a unit test for domain logic and algorithms."""
        outcome = self.outcome([str(self.real_null)], EXPIRED)

        self.assertIn(MISSING_NAME, outcome.reason)


class StopSkipsScratchAndMissingFiles(StopScopeCases, unittest.TestCase):
    def test_parent_stop_reaching_a_subagent_scope_after_the_deadline_skips_the_missing_path(self):
        """Unit test domain code."""
        payload = self.payload(str(self.work), "")
        cfg = self.track(payload, SUBAGENT, [str(self.missing), str(self.notes)])

        outcome = end_turn.foreign_outcome(payload, cfg, "", until=EXPIRED)

        self.assertIn(self.notes.name, outcome.reason)
        self.assertNotIn(MISSING_NAME, outcome.reason)


class SubagentStopSkipsScratchAndMissingFiles(StopScopeCases, unittest.TestCase):
    agent = SUBAGENT
    hook = "subagent_stop"


if __name__ == "__main__":
    unittest.main()
