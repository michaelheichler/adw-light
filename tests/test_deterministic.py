import json
import shlex
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"


class DeterministicChecks(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)

    def hook(self, event, *, host="claude", **payload):
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", host, "--state-dir", str(self.root / "state")],
            input=json.dumps({"cwd": str(self.root), "session_id": "adw-lite-test",
                              "hook_event_name": event, **payload}),
            text=True, capture_output=True, check=True,
        )
        return json.loads(call.stdout)

    def reply_scan_rows(self):
        ledger = self.root / "state" / "ledger" / "ledger.jsonl"
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]
        return [(row["event"], row["rule"], row["outcome"]) for row in rows if row["hook"] == "reply_scan"]

    def capture_post_use(self, *rows):
        path = self.root / "events.jsonl"
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return self.hook("PostToolUse", host="codex", tool_name="Bash", tool_input={
            "command": f"codex exec --json > {shlex.quote(str(path))}",
        })

    def test_reply_uses_original_prose_rules(self):
        result = self.hook("Stop", last_assistant_message="We utilize this tool for the build.")

        self.assertEqual({}, result)
        self.assertEqual([("Stop", "utilize", "would_block")], self.reply_scan_rows())

    def test_dash_rules_preserve_a_mathematical_minus(self):
        self.assertEqual({}, self.hook("Stop", last_assistant_message="The result is 2\u22121."))
        self.assertEqual([], self.reply_scan_rows())

        results = [self.hook("Stop", last_assistant_message=f"Cache invalidation {dash} restart the service.")
                   for dash in ("\u2013", "\u2014")]

        self.assertEqual([{}, {}], results)
        self.assertEqual([("Stop", "banned_dash", "would_block")] * 2, self.reply_scan_rows())

    def test_native_edit_blocks_new_comments_and_allows_plain_code(self):
        path = self.root / "build-config.py"
        path.write_text("# Existing source header\ncache_size = 10000\n")
        edit = {"file_path": str(path), "old_string": "cache_size = 10000"}
        result = self.hook("PreToolUse", tool_name="Edit",
                           tool_input={**edit, "new_string": "# Explain the DNS cache\ncache_size = 20000"})
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        result = self.hook("PreToolUse", tool_name="Edit",
                           tool_input={**edit, "new_string": "cache_size = 20000"})
        self.assertEqual({}, result)
        result = self.hook("PreToolUse", tool_name="Edit",
                           tool_input={**edit, "new_string": "# DNS cache\ncache_size = 20000"})
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        result = self.hook("PreToolUse", tool_name="Edit", tool_input={
            **edit, "new_string": "# Because the upstream TTL expires sooner.\ncache_size = 20000",
        })
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        result = self.hook("PreToolUse", tool_name="Edit", tool_input={
            **edit, "new_string": "cache_size = 20000#DNS cache",
        })
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])

    def test_edit_preserves_an_existing_inline_comment(self):
        path = self.root / "cache.py"
        path.write_text("cache_size = 10000  # Existing cache limit\n")
        result = self.hook("PreToolUse", tool_name="Edit", tool_input={
            "file_path": str(path), "old_string": "cache_size = 10000", "new_string": "cache_size = 20000",
        })
        self.assertEqual({}, result)

    def test_post_edit_reports_inherited_prose_at_its_original_line(self):
        path = self.root / "handoff.md"
        inherited = "We utilize this tool for the build."
        path.write_text(f"{inherited}\n\nThe build starts.\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", path.name], cwd=self.root, check=True)
        subprocess.run([
            "git", "-c", "user.name=ADW Test", "-c", "user.email=adw-test@example.invalid",
            "-c", "commit.gpgSign=false", "commit", "-qm", "Record baseline",
        ], cwd=self.root, check=True)
        path.write_text(f"{inherited}\n\nThe build passes.\n", encoding="utf-8")

        result = self.hook("PostToolUse", tool_name="Edit", tool_input={
            "file_path": str(path), "old_string": "The build starts.", "new_string": "The build passes.",
        })

        notice = result["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("decision", result)
        self.assertIn(f"{path.resolve()}:1 ", notice)
        self.assertIn("(utilize)", notice)
        self.assertEqual({}, self.hook("Stop", last_assistant_message="Done."))

    def test_post_use_accepts_comment_syntax_inside_jsonl_strings(self):
        record = json.dumps({
            "type": "item.completed",
            "item": {"aggregated_output": "Log entry # Returns the result.\nSecond\u2028line."},
        }, ensure_ascii=False)

        result = self.capture_post_use(record, '{"type": "turn.completed"}')

        self.assertEqual({}, result)

    def test_post_use_reports_raw_source_in_malformed_jsonl(self):
        result = self.capture_post_use('{"type": "turn.completed"}', "# Returns the result.")

        notice = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"{self.root.resolve() / 'events.jsonl'}:2 ", notice)
        self.assertIn("(what_comment)", notice)

    def test_new_docstrings_are_blocked_and_literal_hashes_are_allowed(self):
        def write(content):
            return self.hook("PreToolUse", tool_name="Write",
                             tool_input={"file_path": "cache.py", "content": content})
        result = write('def cache_size():\n    """Size."""\n    return 20000\n')
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.assertEqual({}, write('label = "# DNS cache"\n'))

    def test_shell_rejects_inline_write_and_accepts_shell_read(self):
        result = self.hook("PreToolUse", tool_name="Bash",
                           tool_input={"command": """python3 -c 'open("a.py", "w").write("x")'"""})
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.assertEqual({}, self.hook("PreToolUse", tool_name="Bash",
                                      tool_input={"command": "git status --short"}))
