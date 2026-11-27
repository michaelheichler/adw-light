import hashlib
import json
import os
import re
import shlex
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView


INHERITED = "We utilize this tool for the build."
HANDOFF_BASELINE = f"{INHERITED}\n\nThe build starts.\n"
HANDOFF_EDITED = f"{INHERITED}\n\nThe build passes.\n"
JSONL_RECORD = json.dumps({
    "type": "item.completed",
    "item": {"aggregated_output": "Log entry # Returns the result.\nSecond\u2028line."},
}, ensure_ascii=False)
PLAIN_FILES = {
    "build-config.py": "# Existing source header\ncache_size = 10000\n",
    "cache.py": "cache_size = 10000  # Existing cache limit\n",
    "events-valid.jsonl": JSONL_RECORD + "\n" + '{"type": "turn.completed"}' + "\n",
    "events-malformed.jsonl": '{"type": "turn.completed"}' + "\n" + "# Returns the result." + "\n",
    "events-oversized-valid.jsonl": (JSONL_RECORD + "\n") * 4,
    "events-oversized-broken.jsonl": (JSONL_RECORD + "\n") * 4 + "not a record\n",
}
LONG_JSONL_LOG = (JSONL_RECORD + "\n") * 700
SMALL_SCAN_LIMIT = {"ADW_MAX_SCAN_BYTES": "200"}
LISTED_RULE_RE = re.compile(r"\((\w+)\)$", re.M)


def place(folder, files):
    folder.mkdir()
    for relative, content in files.items():
        (folder / relative).write_text(content, encoding="utf-8")


class DeterministicChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.plain = cls.root / "plain"
        cls.repo = cls.root / "repo"
        place(cls.plain, PLAIN_FILES)
        place(cls.repo, {"handoff.md": HANDOFF_EDITED})
        (cls.root / "home").mkdir()
        baseline = {"handoff.md": HANDOFF_BASELINE}
        cls.git = FakeGitView({"roots": {str(cls.repo): {
            "index": baseline, "disk": {"handoff.md": HANDOFF_EDITED}, "revisions": {"base": baseline},
            "refs": {"HEAD": "base"},
        }}})
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home"), "PATH": os.environ.get("PATH", "")}, clear=True)
        environment.start()
        cls.addClassCleanup(environment.stop)

    def session(self):
        return "s" + hashlib.sha256(self.id().encode()).hexdigest()[:20]

    def hook(self, event, *, host="claude", cwd=None, **payload):
        body = {"cwd": str(cwd or self.plain), "session_id": self.session(), "hook_event_name": event, **payload}
        return run_hook(event, body, host=host, state_dir=self.root / "state", git_view=self.git)

    def reply_scan_rows(self):
        ledger = self.root / "state" / "ledger" / "ledger.jsonl"
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]
        return [(row["event"], row["rule"], row["outcome"]) for row in rows
                if row["hook"] == "reply_scan" and row["session_id"] == self.session()]

    def capture_post_use(self, name):
        return self.hook("PostToolUse", host="codex", tool_name="Bash", tool_input={
            "command": f"codex exec --json > {shlex.quote(str(self.plain / name))}",
        })

    def edit_decision(self, new_string):
        tool_input = {"file_path": str(self.plain / "build-config.py"), "old_string": "cache_size = 10000", "new_string": new_string}
        return self.hook("PreToolUse", tool_name="Edit", tool_input=tool_input).get("hookSpecificOutput", {}).get("permissionDecision")

    def test_reply_uses_original_prose_rules(self):
        result = self.hook("Stop", last_assistant_message="We utilize this tool for the build.")

        self.assertEqual(({}, [("Stop", "utilize", "would_block")]), (result, self.reply_scan_rows()))

    def test_mathematical_minus_passes_the_dash_rules(self):
        result = self.hook("Stop", last_assistant_message="The result is 2\u22121.")

        self.assertEqual(({}, []), (result, self.reply_scan_rows()))

    def test_en_and_em_dash_replies_are_logged_without_blocking(self):
        results = [self.hook("Stop", last_assistant_message=f"Cache invalidation {dash} restart the service.")
                   for dash in ("\u2013", "\u2014")]

        self.assertEqual(([{}, {}], [("Stop", "banned_dash", "would_block")] * 2), (results, self.reply_scan_rows()))

    def test_native_edit_blocks_new_comments_and_allows_plain_code(self):
        decisions = [self.edit_decision(new) for new in (
            "# Explain the DNS cache\ncache_size = 20000",
            "cache_size = 20000",
            "# DNS cache\ncache_size = 20000",
            "# Because the upstream TTL expires sooner.\ncache_size = 20000",
            "cache_size = 20000#DNS cache",
        )]

        self.assertEqual(["deny", None, "deny", "deny", "deny"], decisions)

    def test_edit_preserves_an_existing_inline_comment(self):
        result = self.hook("PreToolUse", tool_name="Edit", tool_input={
            "file_path": str(self.plain / "cache.py"), "old_string": "cache_size = 10000", "new_string": "cache_size = 20000",
        })
        self.assertEqual({}, result)

    def test_post_edit_reports_inherited_prose_at_its_original_line(self):
        path = self.repo / "handoff.md"

        result = self.hook("PostToolUse", cwd=self.repo, tool_name="Edit", tool_input={
            "file_path": str(path), "old_string": "The build starts.", "new_string": "The build passes.",
        })

        notice = result["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("decision", result)
        self.assertIn(f"{path}:1 ", notice)
        self.assertIn("(utilize)", notice)
        self.assertEqual({}, self.hook("Stop", cwd=self.repo, last_assistant_message="Done."))

    def test_post_use_accepts_comment_syntax_inside_jsonl_strings(self):
        self.assertEqual({}, self.capture_post_use("events-valid.jsonl"))

    def test_post_use_reports_raw_source_in_malformed_jsonl(self):
        result = self.capture_post_use("events-malformed.jsonl")

        notice = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"{self.plain / 'events-malformed.jsonl'}:2 ", notice)
        self.assertIn("(what_comment)", notice)

    def test_valid_jsonl_log_skips_code_rules(self):
        result = self.hook("PreToolUse", tool_name="Write",
                           tool_input={"file_path": str(self.plain / "events-long.jsonl"), "content": LONG_JSONL_LOG})

        self.assertEqual({}, result)

    def test_oversized_jsonl_requires_every_record_to_parse(self):
        with mock.patch.dict(os.environ, SMALL_SCAN_LIMIT):
            results = [self.capture_post_use(name) for name in ("events-oversized-valid.jsonl", "events-oversized-broken.jsonl")]

        notices = [result.get("hookSpecificOutput", {}).get("additionalContext", "") for result in results]
        self.assertEqual([[], ["unscannable_file"]], [LISTED_RULE_RE.findall(notice) for notice in notices])

    def test_new_docstrings_are_blocked_and_literal_hashes_are_allowed(self):
        def write(content):
            return self.hook("PreToolUse", tool_name="Write",
                             tool_input={"file_path": "cache.py", "content": content})
        result = write('def cache_size():\n    """Size."""\n    return 20000\n')
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.assertEqual({}, write('label = "# DNS cache"\n'))

    def test_shell_lets_an_inline_plain_file_write_and_a_shell_read_run_before_the_check(self):
        """Feed input, check the return value."""
        commands = ("""python3 -c 'open("a.py", "w").write("x")'""", "git status --short")
        results = [self.hook("PreToolUse", tool_name="Bash", tool_input={"command": command}) for command in commands]
        self.assertEqual([{}, {}], results)


if __name__ == "__main__":
    unittest.main()
