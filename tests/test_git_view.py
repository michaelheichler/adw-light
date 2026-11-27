import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView, using_view, view

REPO = Path("/repo")
ALT_INDEX = REPO / ".git" / "alt-index"


def repo_map():
    return {"roots": {str(REPO): {
        "revisions": {
            "c1": {"src/app.py": "v1\n"},
            "c2": {"src/app.py": "v2\n", "docs/guide.md": "one\r\ntwo\r\n", "lib/util.py": "needle = 1\n"},
            "c3": {"src/app.py": "v1\n", "extra.py": "e\n"},
        },
        "parents": {"c1": [], "c2": ["c1"], "c3": ["c1"]},
        "refs": {"HEAD": "c2", "refs/heads/main": "c2", "refs/heads/side": "c3"},
        "times": {"c1": 100, "c2": 200},
        "index": {"src/app.py": "v3\n", "docs/guide.md": "one\r\ntwo\r\n", "lib/util.py": "needle = 1\n",
                  "bin/data": {"base64": "AG5lZWRsZQ=="}},
        "indexes": {str(ALT_INDEX): {"alt.py": "alt = needle\n"}},
        "disk": {"src/app.py": "v3\n", "docs/guide.md": "one\r\ntwo\r\n", "lib/util.py": "needle = 1\n",
                 "notes.txt": "n\n", "build/out.o": "o", "debug.log": "l", "keep.log": "k"},
        "ignore": [{"source": ".gitignore", "pattern": "build/"}, {"source": ".gitignore", "pattern": "*.log"},
                   {"source": ".gitignore", "pattern": "!keep.log"}],
        "state": {"MERGE_HEAD": ["c3"]},
        "config": {"core.hooksPath": "hooks", "remote.origin.url": "ssh-origin"},
        "config_files": {".gitmodules": {"submodule.vendor.path": "vendor"}},
    }}}


def root_state(**fields):
    return {"roots": {str(REPO): fields}}


class FakeGitViewQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.git = FakeGitView(repo_map())

    def test_toplevel_finds_the_enclosing_root_and_nothing_outside_it(self):
        found = (self.git.toplevel(REPO / "src" / "deep"), self.git.toplevel(Path("/other")),
                 self.git.has_work_tree(REPO / "src"), self.git.has_work_tree(Path("/other")))
        self.assertEqual((REPO, None, True, False), found)

    def test_common_dir_sits_under_the_root_only_inside_a_repo(self):
        found = (self.git.common_dir(REPO / "src"), self.git.common_dir(Path("/other")))
        self.assertEqual((REPO / ".git", None), found)

    def test_state_names_lists_recorded_heads_and_empty_for_absent_ones(self):
        found = (self.git.state_names(REPO, "MERGE_HEAD"), self.git.state_names(REPO, "CHERRY_PICK_HEAD"))
        self.assertEqual((["c3"], []), found)

    def test_tracked_paths_filters_by_prefix_glob_and_literal_pathspecs(self):
        found = (self.git.tracked_paths(REPO), self.git.tracked_paths(REPO, ["src"]),
                 self.git.tracked_paths(REPO, ["*.md"]), self.git.tracked_paths(REPO, [":(literal)*.md"]),
                 self.git.tracked_paths(Path("/other")))
        expected = (["bin/data", "docs/guide.md", "lib/util.py", "src/app.py"], ["src/app.py"],
                    ["docs/guide.md"], [], None)
        self.assertEqual(expected, found)

    def test_disk_paths_keeps_tracked_and_negated_files_but_drops_ignored_ones(self):
        expected = ["bin/data", "docs/guide.md", "keep.log", "lib/util.py", "notes.txt", "src/app.py"]
        self.assertEqual(expected, self.git.disk_paths(REPO))

    def test_index_paths_reads_the_default_or_a_named_index(self):
        found = (self.git.index_paths(REPO), self.git.index_paths(REPO, ALT_INDEX))
        self.assertEqual((["bin/data", "docs/guide.md", "lib/util.py", "src/app.py"], ["alt.py"]), found)

    def test_tree_paths_lists_a_revision_and_none_for_an_unknown_one(self):
        found = (self.git.tree_paths(REPO, "HEAD", []), self.git.tree_paths(REPO, "HEAD^", ["src"]),
                 self.git.tree_paths(REPO, "missing", []))
        self.assertEqual((["docs/guide.md", "lib/util.py", "src/app.py"], ["src/app.py"], None), found)

    def test_matched_paths_adds_untracked_disk_files_that_match(self):
        self.assertEqual(["debug.log", "keep.log"], self.git.matched_paths(REPO, ["*.log"]))

    def test_is_tracked_separates_index_entries_from_disk_only_files(self):
        found = (self.git.is_tracked(REPO, "src/app.py"), self.git.is_tracked(REPO, "notes.txt"))
        self.assertEqual((True, False), found)

    def test_ignore_match_reports_the_last_matching_rule_and_skips_tracked_files(self):
        found = (self.git.ignore_match(REPO, "debug.log"), self.git.ignore_match(REPO, "keep.log"),
                 self.git.ignore_match(REPO, "src/app.py"))
        self.assertEqual(((".gitignore", "*.log"), (".gitignore", "!keep.log"), None), found)

    def test_ignored_paths_excludes_files_rescued_by_a_negated_rule(self):
        found = self.git.ignored_paths(REPO, ["debug.log", "keep.log", "notes.txt", "build/out.o"])
        self.assertEqual({"debug.log", "build/out.o"}, found)

    def test_is_unchanged_compares_the_disk_with_head(self):
        found = (self.git.is_unchanged(REPO, "src/app.py"), self.git.is_unchanged(REPO, "docs/guide.md"))
        self.assertEqual((False, True), found)

    def test_staged_paths_lists_index_entries_that_differ_from_head(self):
        found = (self.git.staged_paths(REPO), self.git.staged_paths(Path("/other")))
        self.assertEqual((["bin/data", "src/app.py"], None), found)

    def test_diff_paths_uses_the_merge_base_for_a_three_dot_span(self):
        found = (self.git.diff_paths(REPO, ["c3", "c2"]),
                 self.git.diff_paths(REPO, ["refs/heads/side...refs/heads/main"]),
                 self.git.diff_paths(REPO, ["c1", "c2", "c3"]))
        expected = (["docs/guide.md", "extra.py", "lib/util.py", "src/app.py"],
                    ["docs/guide.md", "lib/util.py", "src/app.py"], None)
        self.assertEqual(expected, found)

    def test_commit_parents_follows_refs_and_rejects_unknown_commits(self):
        found = (self.git.commit_parents(REPO, "HEAD"), self.git.commit_parents(REPO, "c1"),
                 self.git.commit_parents(REPO, "missing"))
        self.assertEqual((["c1"], [], None), found)

    def test_head_text_normalizes_line_endings_and_reads_older_revisions(self):
        found = (self.git.head_text(REPO, "docs/guide.md"), self.git.head_text(REPO, "src/app.py", "HEAD^"),
                 self.git.head_text(REPO, "missing.py"))
        self.assertEqual(("one\ntwo\n", "v1\n", None), found)

    def test_index_bytes_decodes_binary_blobs_and_honours_the_limit(self):
        found = (self.git.index_bytes(REPO, "bin/data"), self.git.index_bytes(REPO, "bin/data", limit=1),
                 self.git.index_bytes(REPO, "missing.py", limit=5))
        self.assertEqual((b"\x00needle", b"\x00", b""), found)

    def test_index_bytes_fails_like_git_show_for_a_missing_entry(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.git.index_bytes(REPO, "missing.py")

    def test_index_bytes_rejects_a_negative_limit(self):
        with self.assertRaises(ValueError):
            self.git.index_bytes(REPO, "bin/data", limit=-1)

    def test_index_text_reads_staged_text_and_none_when_absent(self):
        found = (self.git.index_text(REPO, "src/app.py"), self.git.index_text(REPO, "missing.py"))
        self.assertEqual(("v3\n", None), found)

    def test_commit_time_resolves_refs_and_returns_none_without_a_time(self):
        found = (self.git.commit_time(REPO, "HEAD"), self.git.commit_time(REPO, "c3"))
        self.assertEqual((200, None), found)

    def test_resolve_commit_walks_first_parents_and_stops_at_the_root(self):
        found = (self.git.resolve_commit(REPO, "HEAD"), self.git.resolve_commit(REPO, "HEAD^"),
                 self.git.resolve_commit(REPO, "HEAD^^"), self.git.resolve_commit(REPO, "refs/heads/side"),
                 self.git.resolve_commit(REPO, "missing"))
        self.assertEqual(("c2", "c1", None, "c3", None), found)

    def test_refs_containing_returns_the_first_branch_that_holds_the_commit(self):
        found = (self.git.refs_containing(REPO, "c1", []), self.git.refs_containing(REPO, "c3", []),
                 self.git.refs_containing(REPO, "c3", ["refs/heads/main"]))
        self.assertEqual((["refs/heads/main"], ["refs/heads/side"], []), found)

    def test_is_ancestor_follows_the_parent_chain(self):
        found = (self.git.is_ancestor(REPO, "c1"), self.git.is_ancestor(REPO, "c3"),
                 self.git.is_ancestor(REPO, "c1", "refs/heads/side"))
        self.assertEqual((True, False, True), found)

    def test_config_value_prefers_command_line_overrides(self):
        found = (self.git.config_value(REPO, "remote.origin.url"),
                 self.git.config_value(REPO, "core.hooksPath", flags=["-c", "core.hooksPath=other"]),
                 self.git.config_value(REPO, "core.hooksPath", flags=["--config-env=core.hooksPath=HOOKS"],
                                       env={"HOOKS": "from-env"}))
        self.assertEqual(("ssh-origin", "other", "from-env"), found)

    def test_config_value_rejects_a_flag_the_fake_cannot_model(self):
        with self.assertRaises(ValueError):
            self.git.config_value(REPO, "core.hooksPath", flags=["--bogus"])

    def test_config_entries_match_keys_in_the_repo_or_a_named_file(self):
        found = (self.git.config_entries(REPO, r"^remote\."),
                 self.git.config_entries(REPO, "path$", file=".gitmodules"),
                 self.git.config_entries(REPO, "^absent\\."), self.git.config_entries(REPO, ".", file="missing"))
        self.assertEqual((["remote.origin.url\nssh-origin"], ["submodule.vendor.path\nvendor"], None, None), found)

    def test_grep_tracked_searches_head_content(self):
        self.assertEqual(["lib/util.py"], self.git.grep_tracked(REPO, "needle"))

    def test_grep_index_skips_binary_blobs_and_reads_named_indexes(self):
        found = (self.git.grep_index(REPO, "needle"), self.git.grep_index(REPO, "needle", ALT_INDEX))
        self.assertEqual((["lib/util.py"], ["alt.py"]), found)


class FakeGitViewValidation(unittest.TestCase):
    def test_rejects_a_relative_repository_root(self):
        with self.assertRaises(ValueError):
            FakeGitView({"roots": {"repo": {}}})

    def test_rejects_an_unknown_repository_field(self):
        with self.assertRaises(ValueError):
            FakeGitView(root_state(stash={}))

    def test_rejects_a_commit_time_that_is_not_an_integer(self):
        with self.assertRaises(ValueError):
            FakeGitView(root_state(times={"c1": "100"}))


class ActiveViewScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.git = FakeGitView(repo_map())
        cls.workspace = TemporaryDirectory()
        cls.addClassCleanup(cls.workspace.cleanup)
        cls.folder = Path(cls.workspace.name)

    def test_using_view_activates_the_fake_only_inside_the_block(self):
        with using_view(self.git):
            inside = view()
        self.assertEqual((True, False), (inside is self.git, view() is self.git))

    def test_using_view_without_a_view_replaces_an_outer_fake(self):
        with using_view(self.git), using_view(None):
            inner = view()
        self.assertIsNot(self.git, inner)

    def test_run_hook_leaves_no_fake_active_after_it_returns(self):
        run_hook("Stop", {"session_id": "scope", "cwd": str(self.folder)}, state_dir=self.folder / "scope",
                 git_view=self.git)
        self.assertIsNot(self.git, view())


class InProcessEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = TemporaryDirectory()
        cls.addClassCleanup(cls.workspace.cleanup)
        cls.folder = Path(cls.workspace.name)

    def write_payload(self):
        return {"session_id": "entry", "cwd": str(self.folder), "hook_event_name": "PreToolUse",
                "tool_name": "Write", "tool_input": {"file_path": str(self.folder / "sample.py"),
                                                     "content": "# Loop over the rows and add them\ntotal = 1\n"}}

    def test_run_hook_answers_a_write_like_the_command_line(self):
        payload = self.write_payload()
        command = [sys.executable, "-ISB", str(PROJECT / "adw_light.py"), "PreToolUse",
                   "--state-dir", str(self.folder / "cli")]
        cli = json.loads(subprocess.run(command, input=json.dumps(payload), text=True,
                                        capture_output=True, check=True).stdout)
        in_process = run_hook("PreToolUse", payload, state_dir=self.folder / "in-process")
        decisions = [response["hookSpecificOutput"]["permissionDecision"] for response in (cli, in_process)]
        self.assertEqual(["deny", "deny"], decisions)


if __name__ == "__main__":
    unittest.main()
