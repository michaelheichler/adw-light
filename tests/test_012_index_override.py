import json
import os
import sys
import unittest
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView

PASSED = (None, [])
REFUSED = ("deny", ["test_write_gate"])
SIZE_TEST = "def test_size():\n    assert len([1, 2]) == 2\n"

PROJECT = {"a.py": "def helper():\n    return 1\n", "tests/test_a.py": SIZE_TEST}
BAD_PROSE = {"notes.md": "We utilize this.\n"}
CLEAN_NOTE = {"notes.md": "Short.\n"}
RELATIVE_INDEX = ".git/alt-index"
COMMIT = " git commit -m x"
UNSCANNED_REFUSAL = ("deny", [])
PROSE_FINDING = ("deny", ["utilize"])
STAGED = {"plain": {}, "clean": CLEAN_NOTE, "bad": BAD_PROSE}


def repository(folder, staged):
    disk = {**PROJECT, **{path: CLEAN_NOTE["notes.md"] for path in staged}}
    for relative, content in disk.items():
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_text(content, encoding="utf-8")
    return {"index": PROJECT, "indexes": {str(folder / RELATIVE_INDEX): {**PROJECT, **staged}}, "disk": disk,
            "revisions": {"base": PROJECT}, "refs": {"HEAD": "base", "refs/heads/main": "base", "refs/heads/other": "base"}}


class AlternateIndexHome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        (cls.root / "home").mkdir()
        cls.git = FakeGitView({"roots": {str(cls.root / name): repository(cls.root / name, staged)
                                         for name, staged in STAGED.items()}})
        environment = {"HOME": str(cls.root / "home"), "PATH": os.environ.get("PATH", ""),
                       "IDX": str(cls.root / "clean" / RELATIVE_INDEX)}
        patch = mock.patch.dict(os.environ, environment, clear=True)
        patch.start()
        cls.addClassCleanup(patch.stop)
        cls.calls = count()

    def reports(self):
        return {report: json.loads(report.read_text(encoding="utf-8")) for report in (self.state / "reports").glob("*.json")}

    def hook(self, session, event, payload):
        before = self.reports()
        body = {"session_id": session, "hook_event_name": event, **payload}
        response = run_hook(event, body, state_dir=self.state, git_view=self.git)
        rows = [row for report, batch in self.reports().items() for row in batch[len(before.get(report, [])):]]
        return response.get("hookSpecificOutput", {}).get("permissionDecision"), sorted({row["rule"] for row in rows})

    def bash(self, name, command):
        session = f"index-{next(self.calls)}"
        repo = self.root / name
        self.hook(session, "SessionStart", {"cwd": str(repo), "source": "startup"})
        return self.hook(session, "PreToolUse", {"cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": command}})

    def with_literal_index(self, name, command):
        return self.bash(name, f"GIT_INDEX_FILE={self.root / name / RELATIVE_INDEX} {command}")


class StagingIntoAnAlternateIndex(AlternateIndexHome):
    def test_rm_cached_of_a_module_passes(self):
        self.assertEqual(self.with_literal_index("plain", "git rm --cached a.py"), PASSED)

    def test_rm_of_a_tracked_test_from_the_work_tree_is_refused(self):
        self.assertEqual(self.with_literal_index("plain", "git rm tests/test_a.py"), REFUSED)

    def test_add_of_a_module_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.with_literal_index("plain", "git add a.py"), PASSED)

    def test_update_index_add_of_a_module_passes(self):
        self.assertEqual(self.with_literal_index("plain", "git update-index --add a.py"), PASSED)

    def test_checkout_of_the_tests_folder_from_the_alternate_index_is_refused(self):
        self.assertEqual(self.with_literal_index("plain", "git checkout -- tests"), REFUSED)


class CommittingFromALiteralAlternateIndex(AlternateIndexHome):
    def test_commit_of_clean_content_passes(self):
        self.assertEqual(self.with_literal_index("clean", COMMIT.strip()), PASSED)

    def test_commit_of_bad_prose_held_only_in_the_alternate_index_reports_its_finding(self):
        self.assertEqual(self.with_literal_index("bad", COMMIT.strip()), PROSE_FINDING)

    def test_commit_of_bad_prose_through_a_relative_index_path_reports_its_finding(self):
        self.assertEqual(self.bash("bad", "GIT_INDEX_FILE=" + RELATIVE_INDEX + COMMIT), PROSE_FINDING)


class CommittingFromADynamicIndex(AlternateIndexHome):
    def test_index_from_a_variable_is_refused_without_a_scan(self):
        self.assertEqual(self.bash("clean", "GIT_INDEX_FILE=$IDX" + COMMIT), UNSCANNED_REFUSAL)

    def test_index_from_a_quoted_variable_is_refused_without_a_scan(self):
        self.assertEqual(self.bash("clean", 'GIT_INDEX_FILE="$IDX"' + COMMIT), UNSCANNED_REFUSAL)

    def test_index_from_a_command_substitution_is_refused_without_a_scan(self):
        self.assertEqual(self.bash("clean", "GIT_INDEX_FILE=$(mktemp)" + COMMIT), UNSCANNED_REFUSAL)


class CommittingWithAnotherLocationVariable(AlternateIndexHome):
    def repository_move(self, variable, relative):
        return self.bash("clean", f"{variable}={self.root / 'clean' / relative}{COMMIT}")

    def test_git_dir_is_refused(self):
        self.assertEqual(self.repository_move("GIT_DIR", ".git"), UNSCANNED_REFUSAL)

    def test_git_work_tree_is_refused(self):
        self.assertEqual(self.repository_move("GIT_WORK_TREE", "."), UNSCANNED_REFUSAL)

    def test_git_common_dir_is_refused(self):
        self.assertEqual(self.repository_move("GIT_COMMON_DIR", ".git"), UNSCANNED_REFUSAL)

    def test_git_object_directory_is_refused(self):
        self.assertEqual(self.repository_move("GIT_OBJECT_DIRECTORY", ".git/objects"), UNSCANNED_REFUSAL)


if __name__ == "__main__":
    unittest.main()
