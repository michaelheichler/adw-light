import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
SCREENSHOT_BYTES = 2_500_000
FLAGGED_PROSE = "We utilize this tool for the build.\n"
REPORT_LINE = re.compile(r"Full report: (\S+\.json)")
FLAGGED_WORD = "utilize"


def line_of(text, fragment):
    return text[:text.index(fragment)].count("\n") + 1


class TurnSurvivalRegressions(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.state = self.root / "state"

    def call(self, event, *, env=None, raw=None, **payload):
        body = raw if raw is not None else json.dumps({
            "cwd": str(self.root), "session_id": "turn-survival", "hook_event_name": event, **payload,
        })
        return subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.state)],
            input=body, text=True, capture_output=True, env=env,
        )

    def hook(self, event, **payload):
        return json.loads(self.call(event, **payload).stdout)

    def flagged_file(self, name):
        path = self.root / name
        path.write_text(FLAGGED_PROSE, encoding="utf-8")
        return path

    def post_write(self, path, **extra):
        return self.hook("PostToolUse", tool_name="Write", tool_use_id=f"write-{path.name}",
                         tool_input={"file_path": str(path), "content": path.read_text(encoding="utf-8")}, **extra)

    def stop(self, **extra):
        return self.hook("Stop", last_assistant_message="Done.", **extra)

    def test_post_tool_use_with_huge_bash_stdout_answers_with_json_at_exit_zero(self):
        call = self.call("PostToolUse", tool_name="Bash", tool_use_id="bash-1",
                         tool_input={"command": "cat build.log"},
                         tool_response={"stdout": "gradle line\n" * (SCREENSHOT_BYTES // 12), "stderr": ""})

        self.assertEqual(0, call.returncode, call.stderr)
        self.assertIsInstance(json.loads(call.stdout), dict)

    def test_post_tool_batch_with_screenshot_read_never_blocks_the_turn(self):
        screenshot = {"type": "image", "file": {"base64": "A" * SCREENSHOT_BYTES, "type": "image/png"}}
        call = self.call("PostToolBatch", tool_calls=[{
            "tool_name": "Read", "tool_use_id": "read-1",
            "tool_input": {"file_path": str(self.root / "screen.png")}, "tool_response": screenshot,
        }])

        self.assertEqual(0, call.returncode, call.stderr)
        self.assertNotEqual("block", json.loads(call.stdout).get("decision"))

    def test_post_tool_batch_findings_advise_and_stop_still_blocks_on_the_file(self):
        self.stop()
        path = self.flagged_file("batch-notes.md")

        batch = self.hook("PostToolBatch", tool_calls=[{
            "tool_name": "Write", "tool_use_id": "batch-write-1",
            "tool_input": {"file_path": str(path), "content": FLAGGED_PROSE}, "tool_response": {},
        }])
        stop = self.stop()
        location = f"{path.name}:{line_of(FLAGGED_PROSE, FLAGGED_WORD)}"

        self.assertNotIn("decision", batch)
        self.assertIn(location, batch["hookSpecificOutput"]["additionalContext"])
        self.assertEqual("block", stop["decision"])
        self.assertIn(location, stop["reason"])

    def test_pre_tool_use_payload_that_overflows_the_reader_is_denied_at_exit_zero(self):
        nested = "[" * 200_000 + "]" * 200_000
        body = (
            '{"cwd": %s, "session_id": "turn-survival", "hook_event_name": "PreToolUse", "tool_name": "Write", '
            '"tool_input": {"file_path": "notes.md", "content": "x", "extra": %s}}'
        ) % (json.dumps(str(self.root)), nested)

        call = self.call("PreToolUse", raw=body)

        self.assertEqual(0, call.returncode, call.stderr)
        self.assertEqual("deny", json.loads(call.stdout)["hookSpecificOutput"]["permissionDecision"])

    def test_reading_a_flagged_file_never_seeds_a_stop_blocker(self):
        path = self.flagged_file("read-only.md")
        self.hook("PostToolUse", tool_name="Read", tool_use_id="read-1",
                  tool_input={"file_path": str(path)}, tool_response={"type": "text", "file": {"content": FLAGGED_PROSE}})

        self.assertNotIn("decision", self.stop())

    def test_subagent_findings_block_the_parent_stop(self):
        path = self.flagged_file("subagent-notes.md")
        self.post_write(path, agent_id="helper-agent")

        stop = self.stop()

        self.assertEqual("block", stop["decision"])
        self.assertIn("helper-agent:", stop["reason"])
        self.assertIn(f"{path.name}:{line_of(FLAGGED_PROSE, FLAGGED_WORD)}", stop["reason"])

    def test_stop_with_a_huge_tracked_file_answers_in_budget_and_keeps_unreached_paths(self):
        env = {**os.environ, "ADW_MAX_SCAN_BYTES": "3000000"}
        big = self.root / "big.md"
        big.write_text("We utilize this tool for the build. The cache warms up.\n" * 40_000, encoding="utf-8")
        small = self.flagged_file("small.md")
        self.post_write(big)
        self.post_write(small)

        started = time.monotonic()
        first = self.call("Stop", env=env, last_assistant_message="Done.")
        elapsed = time.monotonic() - started
        second = self.hook("Stop", env=env, last_assistant_message="Done.")

        first_stop = json.loads(first.stdout)
        location = f"{small.name}:{line_of(FLAGGED_PROSE, FLAGGED_WORD)}"
        self.assertLess(elapsed, 8.0)
        self.assertEqual("block", first_stop["decision"])
        self.assertIn(small.name, first_stop["reason"])
        self.assertNotIn(location, first_stop["reason"])
        self.assertIn(location, second["reason"])

    def test_report_named_by_a_pending_blocker_survives_pruning(self):
        self.post_write(self.flagged_file("kept.md"))
        held_report = Path(REPORT_LINE.search(self.stop()["reason"]).group(1))
        reports = held_report.parent
        future = time.time() + 3600
        fillers = [reports / f"filler-{index:03d}.json" for index in range(300)]
        list(map(lambda filler: filler.write_text("[]", encoding="utf-8"), fillers))
        list(map(lambda filler: os.utime(filler, (future, future)), fillers))

        self.post_write(self.flagged_file("trigger.md"))

        self.assertTrue(held_report.exists())
        self.assertEqual(300 + 1, len(list(reports.glob("*.json"))))
