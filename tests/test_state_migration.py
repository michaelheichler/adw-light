import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "adw_light.py"
SCRUBBED_ENV = ("CLAUDE_PLUGIN_ROOT", "ADW_STATE_ROOT", "ADW_CODEX_HOOK", "ADW_PYTHON", "ADW_ALLOW_PROTECTED_EDIT")
LEGACY_ROW = {"hook": "reply_scan", "event": "Stop", "rule": "legacy_row", "outcome": "would_block"}
TARGET_ROW = {"hook": "reply_scan", "event": "Stop", "rule": "target_row", "outcome": "would_block"}
LEGACY_NAMES = ("ledger", "notes.txt", "sessions")


def isolated_env(home):
    env = {key: value for key, value in os.environ.items() if key not in SCRUBBED_ENV}
    return {**env, "HOME": str(home)}


def restore_mode(folder):
    with contextlib.suppress(FileNotFoundError):
        folder.chmod(0o755)


def ledger_rows(state):
    ledger = state / "ledger" / "ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]
    return [row["rule"] for row in rows if row["hook"] == "reply_scan"]


class StateDirMigrationTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.home = self.scratch / "home"
        self.project = self.scratch / "project"
        self.project.mkdir(parents=True)
        self.legacy = self.home / ".local" / "state" / "adw-lite"
        self.target = self.home / ".adw-lite" / "state"

    def seed_ledger(self, where, row):
        self.seed_entry(where / "ledger" / "ledger.jsonl", json.dumps(row) + "\n")

    def seed_legacy(self, where):
        self.seed_ledger(where, LEGACY_ROW)

    def seed_entry(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def lock_legacy(self):
        self.legacy.chmod(0o555)
        self.addCleanup(restore_mode, self.legacy)

    def link_legacy_to(self, real):
        self.legacy.parent.mkdir(parents=True)
        self.legacy.symlink_to(real, target_is_directory=True)

    def stop_command(self, state):
        return [sys.executable, "-ISB", str(ENTRY), "Stop", "--host", "claude", "--state-dir", str(state)]

    def stop_payload(self, session):
        return json.dumps({"session_id": session, "cwd": str(self.project), "hook_event_name": "Stop",
                           "last_assistant_message": "We utilize this tool for the build."})

    def stop(self, state):
        return subprocess.run(self.stop_command(state), input=self.stop_payload("migration-case"),
                              capture_output=True, text=True, check=False,
                              cwd=str(self.project), env=isolated_env(self.home), timeout=60)

    def stop_twice_at_once(self, state):
        hooks = [subprocess.Popen(self.stop_command(state), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, cwd=str(self.project),
                                  env=isolated_env(self.home)) for _ in range(2)]
        outputs = [hook.communicate(self.stop_payload(f"migration-race-{index}"), timeout=60)[0]
                   for index, hook in enumerate(hooks)]
        return [(hook.returncode, output) for hook, output in zip(hooks, outputs)]

    def test_a_default_path_call_moves_the_legacy_folder_away(self):
        self.seed_legacy(self.legacy)
        self.stop(self.target)
        self.assertFalse(self.legacy.exists())

    def test_a_default_path_call_keeps_the_legacy_rows_and_appends_the_new_row(self):
        self.seed_legacy(self.legacy)
        self.stop(self.target)
        self.assertEqual(ledger_rows(self.target), ["legacy_row", "utilize"])

    def test_a_default_path_call_moves_every_legacy_entry_into_the_new_folder(self):
        self.seed_legacy(self.legacy)
        self.seed_entry(self.legacy / "sessions" / "one.json", "{\"session\": 1}\n")
        self.seed_entry(self.legacy / "notes.txt", "legacy notes\n")
        self.stop(self.target)
        self.assertEqual(((self.target / "sessions" / "one.json").read_text(encoding="utf-8"),
                          (self.target / "notes.txt").read_text(encoding="utf-8")),
                         ("{\"session\": 1}\n", "legacy notes\n"))

    def test_a_new_folder_without_a_ledger_takes_in_the_legacy_rows(self):
        self.seed_legacy(self.legacy)
        self.seed_entry(self.target / "cutover" / "marker", "target\n")
        self.stop(self.target)
        self.assertEqual((ledger_rows(self.target), self.legacy.exists()), (["legacy_row", "utilize"], False))

    def test_an_entry_already_in_the_new_folder_wins_over_the_legacy_entry_of_the_same_name(self):
        self.seed_legacy(self.legacy)
        self.seed_entry(self.legacy / "cutover" / "marker", "legacy\n")
        self.seed_entry(self.target / "cutover" / "marker", "target\n")
        self.stop(self.target)
        self.assertEqual(((self.target / "cutover" / "marker").read_text(encoding="utf-8"),
                          (self.legacy / "cutover" / "marker").read_text(encoding="utf-8"),
                          sorted(entry.name for entry in self.legacy.iterdir())),
                         ("target\n", "legacy\n", ["cutover"]))

    def test_a_new_folder_with_a_ledger_keeps_both_ledgers_apart_and_takes_the_other_legacy_entries(self):
        self.seed_legacy(self.legacy)
        self.seed_entry(self.legacy / "notes.txt", "legacy notes\n")
        self.seed_ledger(self.target, TARGET_ROW)
        self.stop(self.target)
        self.assertEqual((ledger_rows(self.legacy), ledger_rows(self.target),
                          (self.target / "notes.txt").read_text(encoding="utf-8"),
                          os.path.lexists(self.legacy / "notes.txt")),
                         (["legacy_row"], ["target_row", "utilize"], "legacy notes\n", False))

    def test_a_read_only_legacy_folder_keeps_the_ledger_and_takes_the_new_row(self):
        self.seed_legacy(self.legacy)
        self.lock_legacy()
        result = self.stop(self.target)
        self.assertEqual((result.returncode, type(json.loads(result.stdout)), bool(result.stderr),
                          os.path.lexists(self.target / "ledger"), ledger_rows(self.legacy)),
                         (0, dict, True, False, ["legacy_row", "utilize"]))

    def test_a_legacy_folder_made_writable_again_moves_on_the_next_call(self):
        self.seed_legacy(self.legacy)
        self.seed_entry(self.legacy / "notes.txt", "legacy notes\n")
        self.lock_legacy()
        self.stop(self.target)
        self.legacy.chmod(0o755)
        self.stop(self.target)
        self.assertEqual((ledger_rows(self.target), (self.target / "notes.txt").read_text(encoding="utf-8"),
                          self.legacy.exists()),
                         (["legacy_row", "utilize", "utilize"], "legacy notes\n", False))

    def test_a_call_after_the_finished_move_writes_nothing_to_stderr(self):
        self.seed_legacy(self.legacy)
        self.lock_legacy()
        self.stop(self.target)
        self.legacy.chmod(0o755)
        self.stop(self.target)
        result = self.stop(self.target)
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_a_relative_legacy_link_becomes_an_absolute_link_in_the_new_place(self):
        real = self.scratch / "elsewhere"
        self.seed_legacy(real)
        self.legacy.parent.mkdir(parents=True)
        self.legacy.symlink_to(os.path.relpath(real, self.legacy.parent), target_is_directory=True)
        self.stop(self.target)
        link = os.readlink(self.target)
        self.assertEqual((os.path.isabs(link), Path(link).resolve(), os.path.lexists(self.legacy)),
                         (True, real, False))

    def test_a_legacy_cutover_stamp_missing_in_the_new_folder_moves(self):
        self.seed_legacy(self.legacy)
        self.seed_entry(self.legacy / "cutover" / "stamp-old" / "marker", "legacy\n")
        self.seed_entry(self.target / "cutover" / "stamp-new" / "marker", "target\n")
        self.stop(self.target)
        self.assertEqual(((self.target / "cutover" / "stamp-old" / "marker").read_text(encoding="utf-8"),
                          (self.target / "cutover" / "stamp-new" / "marker").read_text(encoding="utf-8"),
                          os.path.lexists(self.legacy / "cutover" / "stamp-old")),
                         ("legacy\n", "target\n", False))

    def test_a_cutover_stamp_present_in_both_folders_stays_in_legacy(self):
        self.seed_legacy(self.legacy)
        self.seed_entry(self.legacy / "cutover" / "stamp" / "marker", "legacy\n")
        self.seed_entry(self.target / "cutover" / "stamp" / "marker", "target\n")
        self.stop(self.target)
        self.assertEqual(((self.legacy / "cutover" / "stamp" / "marker").read_text(encoding="utf-8"),
                          (self.target / "cutover" / "stamp" / "marker").read_text(encoding="utf-8"),
                          sorted(entry.name for entry in (self.target / "cutover" / "stamp").iterdir())),
                         ("legacy\n", "target\n", ["marker"]))

    def test_a_legacy_file_whose_name_exists_in_the_new_folder_stays_in_legacy(self):
        self.seed_legacy(self.legacy)
        self.seed_entry(self.legacy / "notes.txt", "legacy notes\n")
        self.seed_entry(self.target / "notes.txt", "target notes\n")
        self.stop(self.target)
        self.assertEqual(((self.legacy / "notes.txt").read_text(encoding="utf-8"),
                          (self.target / "notes.txt").read_text(encoding="utf-8")),
                         ("legacy notes\n", "target notes\n"))

    def test_a_symlinked_legacy_folder_becomes_the_new_folder_link(self):
        real = self.scratch / "elsewhere"
        self.seed_legacy(real)
        self.link_legacy_to(real)
        self.stop(self.target)
        self.assertEqual((self.target.is_symlink(), self.target.resolve(), os.path.lexists(self.legacy),
                          ledger_rows(real)),
                         (True, real, False, ["legacy_row", "utilize"]))

    def test_a_symlinked_legacy_folder_empties_into_an_existing_new_folder(self):
        real = self.scratch / "elsewhere"
        self.seed_legacy(real)
        self.link_legacy_to(real)
        self.seed_entry(self.target / "cutover" / "marker", "target\n")
        self.stop(self.target)
        self.assertEqual((ledger_rows(self.target), os.path.lexists(self.legacy), real.is_dir(),
                          list(real.iterdir())),
                         (["legacy_row", "utilize"], False, True, []))

    def test_two_hooks_started_together_move_each_legacy_entry_once(self):
        self.seed_legacy(self.legacy)
        self.seed_entry(self.legacy / "sessions" / "one.json", "{\"session\": 1}\n")
        self.seed_entry(self.legacy / "notes.txt", "legacy notes\n")
        results = self.stop_twice_at_once(self.target)
        self.assertEqual(([code for code, _ in results], [type(json.loads(out)) for _, out in results],
                          ledger_rows(self.target).count("legacy_row"),
                          sorted(entry.name for entry in self.target.iterdir() if entry.name in LEGACY_NAMES),
                          (self.target / "sessions" / "one.json").read_text(encoding="utf-8"),
                          self.legacy.exists()),
                         ([0, 0], [dict, dict], 1, sorted(LEGACY_NAMES), "{\"session\": 1}\n", False))

    def test_an_explicit_other_state_dir_leaves_the_legacy_folder_untouched(self):
        self.seed_legacy(self.legacy)
        other = self.scratch / "other-state"
        self.stop(other)
        self.assertEqual((ledger_rows(self.legacy), ledger_rows(other), (self.home / ".adw-lite").exists()),
                         (["legacy_row"], ["utilize"], False))

class WatcherStateWriteTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.home = self.scratch / "home"
        self.home.mkdir()

    def write_decision(self, relative):
        payload = {"session_id": "state-write-case", "cwd": str(self.scratch), "hook_event_name": "PreToolUse",
                   "tool_name": "Write", "tool_input": {"file_path": str(self.home / relative), "content": "{}\n"}}
        result = subprocess.run([sys.executable, "-ISB", str(ENTRY), "PreToolUse", "--host", "claude",
                                 "--state-dir", str(self.scratch / "state")], input=json.dumps(payload),
                                capture_output=True, text=True, check=False, cwd=str(self.scratch),
                                env=isolated_env(self.home), timeout=60)
        output = json.loads(result.stdout).get("hookSpecificOutput", {})
        return output.get("permissionDecision"), "state_mutation" in output.get("permissionDecisionReason", "")

    def blocked_rules(self, relative):
        decision, _ = self.write_decision(relative)
        ledger = self.scratch / "state" / "ledger" / "ledger.jsonl"
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]
        return decision, [row["rule"] for row in rows if row.get("outcome") == "block"]

    def test_a_write_into_the_new_install_root_is_denied_as_install_surface(self):
        self.assertEqual(self.blocked_rules(".adw-lite/install/adw_light.py"), ("deny", ["watcher_install_surface"]))

    def test_a_write_into_the_legacy_install_root_stays_denied_as_install_surface(self):
        self.assertEqual(self.blocked_rules(".local/share/adw-light/adw_light.py"),
                         ("deny", ["watcher_install_surface"]))

    def test_a_write_into_the_new_state_folder_is_denied_as_state_mutation(self):
        self.assertEqual(self.write_decision(".adw-lite/state/ledger/ledger.jsonl"), ("deny", True))

    def test_a_write_into_the_new_cache_folder_passes_because_the_cache_holds_no_state(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.write_decision(".adw-lite/cache/entry.json"), (None, False))

    def test_a_write_into_the_legacy_state_folder_is_denied_as_state_mutation(self):
        self.assertEqual(self.write_decision(".local/state/adw-lite/ledger/ledger.jsonl"), ("deny", True))

    def test_a_write_beside_the_watcher_folders_is_allowed(self):
        self.assertEqual(self.write_decision("notes/entry.json"), (None, False))


if __name__ == "__main__":
    unittest.main()
