import os
import re
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

import adw_light
from after_run_home import CAUGHT, CLAUDE, GATE, LET_THROUGH, OLD_TEST, AfterRunCase, Project, identity, project_files
from fake_repo_home import SIZE_TEST
from lib import blocker_state, git_port
from lib.git_cli_view import CliGitView


REPO = "repo"
NOTES = "docs/notes.md"
GUIDE = "docs/guide.md"
FLAGGED = "We utilize this tool for the build.\n"
CHANGED_TEST = SIZE_TEST.replace("2]", "3]")
DIRTY_TEST = SIZE_TEST.replace("2]", "4]")
HIDDEN_WRITE = "python3 tools/bulk_write.py"
BACKGROUND = "sleep 5 &"
DEFERRED = "after_run_deferred"
HOUR = 3600.0
HELPER = "general-purpose"
AGENTS = {"Stop": "", "SubagentStop": "a1"}
CLI_ROOT = Path("/work/repo")
BLOB_PATHS = ["notes/a b.md", "old notes.md", "empty.md", "tail.md"]
BLOB_STREAM = b"1111 blob 5\nhello\nHEAD:old notes.md missing\n2222 blob 0\n\n3333 blob 4\ntail\n"
BLOB_REQUEST = b"".join(b"HEAD:" + path.encode() + b"\n" for path in BLOB_PATHS)


class LateClockCase(AfterRunCase):
    def respond(self, event, name, payload, host=CLAUDE, run=""):
        if not self.__dict__.get("lagging"):
            return super().respond(event, name, payload, host, run)
        body = {"session_id": self.session(run), "hook_event_name": event, "cwd": str(self.folder(name)), **payload}
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", ""), "ADW_STATE_ROOT": str(self.state),
                       "ADW_CODEX_HOOK": ""}
        settings = {"adw_model": "", "state_root": str(self.state / "state"), "ledger_root": str(self.state / "ledger")}
        with mock.patch.dict(os.environ, environment, clear=True), git_port.using_view(self.view()):
            return adw_light.run(event, body, settings, started_at=time.monotonic() - HOUR)

    def late(self, action):
        self.lagging = True
        try:
            return action()
        finally:
            self.lagging = False

    def deferred_counts(self, run=""):
        return [row["deferred"] for row in self.timing_rows(run) if row["rule"] == DEFERRED]

    def tracked_paths(self, run="", agent=""):
        _, paths, _ = blocker_state.details(self.session(run), agent, str(self.state / "state"))
        folder = str(self.folder(REPO)) + "/"
        return sorted(path.removeprefix(folder) for path in paths)


class PostAfterTheDeadline(LateClockCase):
    projects = {REPO: Project(project_files())}

    def late_command(self):
        self.start(REPO)
        self.pre(REPO, HIDDEN_WRITE)
        self.change(REPO, OLD_TEST, CHANGED_TEST)
        self.change(REPO, NOTES, FLAGGED)
        self.change(REPO, GUIDE, FLAGGED)
        posted = self.late(lambda: self.post(REPO, HIDDEN_WRITE))
        verdict = self.late(lambda: self.batch(REPO, HIDDEN_WRITE)).get("decision")
        return verdict, posted

    def notice_counts(self, posted):
        return re.findall(r"\d+", posted.get("hookSpecificOutput", {}).get("additionalContext", ""))

    def test_named_test_path_still_blocks_by_name_when_the_deadline_has_passed(self):
        """The test fails when a real bug enters the code it exercises."""
        verdict, _ = self.late_command()
        self.assertEqual((verdict, self.blocked_rules(), self.finding_paths(GATE, REPO)), (CAUGHT, [GATE], [OLD_TEST]))

    def test_late_prose_paths_move_to_the_stop_check_and_are_counted(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.late_command()
        observed = (self.tracked_paths(), self.deferred_counts(), self.finding_rules())
        self.assertEqual(observed, (sorted((GUIDE, NOTES, OLD_TEST)), [3], [GATE]))

    def test_late_run_tells_the_agent_how_many_paths_were_deferred(self):
        """Feed input, check the return value."""
        _, posted = self.late_command()
        self.assertEqual(self.notice_counts(posted), ["3"])


class TouchedTestAfterTheDeadline(LateClockCase):
    projects = {REPO: Project(project_files())}

    def touch(self):
        os.utime(self.folder(REPO) / OLD_TEST, ns=(1_000_000_000, 1_000_000_000))

    def test_touched_test_blocked_by_name_is_cleared_by_the_next_batch_recheck(self):
        """The test stays green when you restructure the code without changing its behavior."""
        self.change(REPO, OLD_TEST, DIRTY_TEST)
        self.start(REPO)
        self.pre(REPO, HIDDEN_WRITE)
        self.touch()
        self.late(lambda: self.post(REPO, HIDDEN_WRITE))
        verdict = self.batch(REPO, HIDDEN_WRITE).get("decision")
        self.assertEqual((self.blocked_rules(), verdict), ([GATE], LET_THROUGH))


class StopAfterTheDeadline(LateClockCase):
    projects = {REPO: Project(project_files())}

    def late_stop(self, event, run):
        helper = identity(HELPER) if event == "SubagentStop" else {}
        self.start(REPO, run=run)
        self.pair(REPO, BACKGROUND, before=helper, run=run)
        self.change(REPO, NOTES, FLAGGED)
        payload = {"last_assistant_message": "Done.", **helper}
        return self.late(lambda: self.respond(event, REPO, payload, run=run)).get("decision")

    def outcomes(self):
        return {event: (self.late_stop(event, event), self.finding_rules(event), self.deferred_counts(event),
                        self.tracked_paths(event, agent))
                for event, agent in AGENTS.items()}

    def test_stop_and_subagent_stop_past_the_deadline_skip_the_path_scan_and_keep_the_path_tracked(self):
        """Use a parameterized test for several inputs."""
        expected = dict.fromkeys(AGENTS, (CAUGHT, [], [1], [NOTES]))
        self.assertEqual(self.outcomes(), expected)


class CliBlobBatch(unittest.TestCase):
    def fetched(self):
        done = subprocess.CompletedProcess(["git"], 0, stdout=BLOB_STREAM, stderr=b"")
        with mock.patch("lib.git_cli_view.subprocess.run", return_value=done) as run:
            blobs = CliGitView().head_blobs(CLI_ROOT, BLOB_PATHS)
        return blobs, run.call_args_list

    def test_one_batch_read_keeps_spaced_and_empty_blobs_and_skips_missing_objects(self):
        """Check the call count and the arguments of each mocked call."""
        expected_call = mock.call(["git", "cat-file", "--batch"], cwd=CLI_ROOT, input=BLOB_REQUEST,
                                  capture_output=True, timeout=mock.ANY, check=False)
        expected = {"notes/a b.md": b"hello", "empty.md": b"", "tail.md": b"tail"}
        self.assertEqual(self.fetched(), (expected, [expected_call]))


if __name__ == "__main__":
    unittest.main()
