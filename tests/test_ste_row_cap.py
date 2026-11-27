import json
import os
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView


NO_REPOSITORY = FakeGitView({"roots": {}})

def dangling_list(count):
    return "".join("- The cache keeps row %d and\n" % number for number in range(1, count + 1))


def nominalized_paragraphs(count):
    return "".join("Job %d performs an analysis of the log.\n\n" % number for number in range(1, count + 1))


class SteRowCapThroughWriteHook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name)
        cls.state = cls.root / ".adw-state"
        (cls.root / "home").mkdir()
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)

    def hook(self, event, **payload):
        base = {"cwd": str(self.root), "session_id": self._testMethodName, "hook_event_name": event}
        return run_hook(event, {**base, **payload}, state_dir=self.state, git_view=NO_REPOSITORY)

    def write_attempt(self, content):
        self.hook("SessionStart", source="startup")
        return self.hook("PreToolUse", tool_name="Write",
                         tool_input={"file_path": str(self.root / "notes.md"), "content": content})

    def ledger_outcomes(self, rule):
        rows = (json.loads(line) for line in (self.state / "ledger" / "ledger.jsonl").read_text().splitlines() if line.strip())
        mine = (row for row in rows if row["session_id"] == self._testMethodName)
        return Counter(row["outcome"] for row in mine if row["rule"] == rule)

    def test_sixty_dangling_list_items_leave_fifty_observe_rows(self):
        self.write_attempt(dangling_list(60))
        self.assertEqual({"would_block": 50}, self.ledger_outcomes("ste_dangling_conjunction"))

    def test_sixty_dangling_list_items_do_not_deny_the_write(self):
        response = self.write_attempt(dangling_list(60))
        self.assertIsNone(response["hookSpecificOutput"].get("permissionDecision"))

    def test_sixty_nominalizations_leave_sixty_block_rows(self):
        self.write_attempt(nominalized_paragraphs(60))
        self.assertEqual({"block": 60}, self.ledger_outcomes("ste_nominalization"))

    def test_sixty_nominalizations_deny_the_write(self):
        response = self.write_attempt(nominalized_paragraphs(60))
        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])


if __name__ == "__main__":
    unittest.main()
