from quiet_git import disable_background_maintenance
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"


def dangling_list(count):
    return "".join("- The cache keeps row %d and\n" % number for number in range(1, count + 1))


def nominalized_paragraphs(count):
    return "".join("Job %d performs an analysis of the log.\n\n" % number for number in range(1, count + 1))


class SteRowCapThroughWriteHook(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.state = self.root / ".adw-state"
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        disable_background_maintenance(self.root)

    def hook(self, event, **payload):
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps({"cwd": str(self.root), "session_id": "ste-row-cap", "hook_event_name": event, **payload}),
            text=True, capture_output=True, check=True,
        )
        return json.loads(call.stdout)

    def write_attempt(self, content):
        self.hook("SessionStart", source="startup")
        return self.hook("PreToolUse", tool_name="Write",
                         tool_input={"file_path": str(self.root / "notes.md"), "content": content})

    def ledger_outcomes(self, rule):
        rows = (json.loads(line) for line in (self.state / "ledger" / "ledger.jsonl").read_text().splitlines() if line.strip())
        return Counter(row["outcome"] for row in rows if row["rule"] == rule)

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
