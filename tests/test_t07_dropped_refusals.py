import json
import unittest
from unittest import mock

from after_run_home import CAUGHT, LET_THROUGH, AfterRunCase, Project, project_files
from lib import temp_authorship


REPO = "repo"
DENY = "deny"
WIRING = "watcher_wiring_removal"
PROSE_RULE = "utilize"
NOTES = "docs/long.md"
PLAIN_TARGET = "docs/out.txt"
TEST_TARGET = "tests/test_out.py"
CLEAN_LONG = "The build keeps the release notes short so the reader can follow each step of the release and its checks in order."
FLAGGED = "We utilize this tool for the build."
LONG_PRINTF = "printf '%s %s\\n' '" + CLEAN_LONG + "' \"$EXTRA\" > " + NOTES
CAP = 3
FIRST_BATCH = tuple(f"scratch/first_{number}.txt" for number in range(CAP + 2))
SECOND_BATCH = tuple(f"scratch/second_{number}.txt" for number in range(2))
WIRED = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 adw_light.py Stop"}]}]}}
UNWIRED = {"hooks": {}}


def tee_into(paths):
    return "printf 'x\\n' | tee " + " ".join(paths)


class DroppedRefusalHome(AfterRunCase):
    projects = {REPO: Project(project_files())}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.home / ".claude").mkdir()
        cls.settings().write_text(json.dumps(WIRED), encoding="utf-8")

    @classmethod
    def settings(cls):
        return cls.home / ".claude" / "settings.json"

    def scratch(self, paths):
        return [str(self.root / relative) for relative in paths]

    def first_seen(self, command):
        self.start(REPO)
        return self.pre(REPO, command)

    def settings_write(self, content):
        self.start(REPO)
        tool_input = {"file_path": str(self.settings()), "content": json.dumps(content)}
        response = self.respond("PreToolUse", REPO, {"tool_name": "Write", "tool_use_id": "w1", "tool_input": tool_input})
        return response.get("hookSpecificOutput", {}).get("permissionDecision"), self.report_rules()

    def full_store_notes(self):
        ledger = self.state / "ledger" / "ledger.jsonl"
        rows = (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())
        return [row["event"] for row in rows if row.get("session_id") == self.session() and row.get("event") == "scratch_provenance_full"]


class ProvenanceStoreCap(DroppedRefusalHome):
    def fill_past_the_cap(self):
        self.start(REPO)
        self.post(REPO, tee_into(self.scratch(FIRST_BATCH)), "c1")
        later = tee_into(self.scratch(SECOND_BATCH))
        decision = self.pre(REPO, later, "c2")
        self.post(REPO, later, "c2")
        return decision

    def test_writes_past_a_full_store_pass_keep_earlier_paths_and_note_the_cap_once(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        with mock.patch.object(temp_authorship, "MAX_LINES", CAP):
            decision = self.fill_past_the_cap()
        recorded = temp_authorship.authored_temp(self.session(), self.state / "state")
        expected = (LET_THROUGH, frozenset(self.scratch(FIRST_BATCH[:CAP])), ["scratch_provenance_full"])
        self.assertEqual((decision, recorded, self.full_store_notes()), expected)


class WatcherWiring(DroppedRefusalHome):
    def test_settings_write_that_drops_the_watcher_hooks_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.settings_write(UNWIRED), (DENY, [WIRING]))

    def test_settings_write_that_keeps_the_watcher_hooks_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        self.assertEqual(self.settings_write(WIRED), (LET_THROUGH, []))


class DroppedShellWriteRefusals(DroppedRefusalHome):
    def test_base64_decode_into_a_plain_file_passes_before_the_run(self):
        """Feed input, check the return value."""
        self.assertEqual(self.first_seen("base64 -d blob.b64 > " + PLAIN_TARGET), LET_THROUGH)

    def test_heredoc_with_a_dynamic_marker_into_a_plain_file_passes_before_the_run(self):
        """Feed input, check the return value."""
        self.assertEqual(self.first_seen("cat <<$X > " + PLAIN_TARGET), LET_THROUGH)

    def test_write_to_a_path_from_a_variable_passes_before_the_run(self):
        """Feed input, check the return value."""
        self.assertEqual(self.first_seen("printf x > $OUT"), LET_THROUGH)

    def test_long_printf_into_a_markdown_file_passes_before_the_run(self):
        """Feed input, check the return value."""
        self.assertEqual(self.first_seen(LONG_PRINTF), LET_THROUGH)

    def test_prose_a_long_printf_wrote_is_scanned_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        written = lambda: self.change(REPO, NOTES, CLEAN_LONG + " " + FLAGGED + "\n")
        verdict = self.after_run(REPO, LONG_PRINTF, written)
        self.assertEqual((verdict, self.blocked_rules()), (CAUGHT, [PROSE_RULE]))

    def test_base64_decode_into_a_test_file_still_blocks(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.first_seen("base64 -d blob.b64 > " + TEST_TARGET), DENY)

    def test_heredoc_with_a_dynamic_marker_into_a_test_file_still_blocks(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.first_seen("cat <<$X > " + TEST_TARGET), DENY)


if __name__ == "__main__":
    unittest.main()
