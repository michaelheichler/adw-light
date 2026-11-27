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
from lib import blocker_state

FLAGGED_PROSE = "We utilize this tool for the build.\n"
FLAGGED_RULE = "utilize"
SUBAGENT = "helper-agent"
SETUP_SPENT = 3600.0


class NestedProjectStopCases:
    event = "Stop"
    agent = ""

    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        root = Path(workspace.name).resolve()
        project = root / "tproj"
        cls.nested = project / "sub"
        cls.nested.mkdir(parents=True)
        (project / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        cls.doc = project / "doc.md"
        cls.other = project / "other.md"
        cls.scratch = root / "scratch.md"
        list(map(lambda path: path.write_text(FLAGGED_PROSE, encoding="utf-8"), (cls.doc, cls.other, cls.scratch)))
        cls.settings = {"state_root": str(root / "state"), "ledger_root": str(root / "ledger")}

    def payload(self):
        session = "s" + sha256(self.id().encode()).hexdigest()[:16]
        return {"session_id": session, "cwd": str(self.nested), "agent_id": self.agent}

    def stop(self, paths, started_at, tracked_by=None):
        payload = self.payload()
        owner = self.agent if tracked_by is None else tracked_by
        scope = blocker_state.BlockerScope(payload["session_id"], owner, self.settings["state_root"])
        blocker_state.touch_paths(scope, [str(path) for path in paths])
        return adw_light.run(self.event, payload, self.settings, started_at=started_at)

    def test_nested_cwd_scans_the_tracked_parent_project_file(self):
        """The test fails when a real bug enters the code it exercises."""
        result = self.stop([self.doc], time.monotonic())

        self.assertIn(self.doc.name, result["reason"])
        self.assertIn(FLAGGED_RULE, result["reason"])

    def test_nested_cwd_skips_a_sibling_scratch_file_outside_the_project(self):
        """A test must verify what the code does for its client, never how it does it."""
        result = self.stop([self.scratch], time.monotonic())

        self.assertNotIn("decision", result)

    def test_setup_that_spent_the_budget_leaves_the_file_unscanned(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        result = self.stop([self.doc], time.monotonic() - SETUP_SPENT)

        self.assertIn(self.doc.name, result["reason"])
        self.assertNotIn(FLAGGED_RULE, result["reason"])

    def test_file_left_unscanned_at_timeout_is_scanned_at_the_next_stop(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.stop([self.doc], time.monotonic() - SETUP_SPENT)

        result = adw_light.run(self.event, self.payload(), self.settings, started_at=time.monotonic())

        self.assertIn(FLAGGED_RULE, result["reason"])


class StopInNestedTempProject(NestedProjectStopCases, unittest.TestCase):
    def test_later_subagent_scope_gets_no_fresh_deadline(self):
        """Write a unit test for domain logic and algorithms."""
        result = self.stop([self.doc], time.monotonic() - SETUP_SPENT, tracked_by=SUBAGENT)

        self.assertIn(self.doc.name, result["reason"])
        self.assertNotIn(FLAGGED_RULE, result["reason"])


class SubagentStopInNestedTempProject(NestedProjectStopCases, unittest.TestCase):
    event = "SubagentStop"
    agent = SUBAGENT


if __name__ == "__main__":
    unittest.main()
