import hashlib
import subprocess
import threading
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

from after_run_home import GATE

git_port = import_module("lib.git_port")
git_cli_view = import_module("lib.git_cli_view")
parallel_calls = import_module("lib.parallel_calls")
shell_snapshot = import_module("lib.shell_snapshot")
commit_test_changes = import_module("lib.commit_test_changes")

ROOT = Path("/work/repo")
FOREVER = float("inf")
SINCE = 5000
SKEW = 5
PLAIN_RUST = "pub fn one() -> i32 {\n    1\n}\n"
INLINE_RUST = PLAIN_RUST + "#[cfg(test)]\nmod tests {\n    #[test]\n    fn works() {\n        assert_eq!(1 + 1, 2);\n    }\n}\n"
BASE_TREE = {"notes.md": "old\n", "tests/test_old.py": "def test_a():\n    pass\n", "tests/test_gone.py": "def test_b():\n    pass\n",
             "src/app.py": "def helper():\n    return 1\n", "src/lib.rs": PLAIN_RUST}
CHANGED = {"tests/test_old.py": "def test_a():\n    assert True\n", "tests/test_new.py": "def test_c():\n    pass\n",
           "src/app.py": "def helper():\n    return 2\n", "src/lib.rs": INLINE_RUST}
COMMIT_TREE = {**{path: text for path, text in BASE_TREE.items() if path != "tests/test_gone.py"}, **CHANGED}
FAR_TREE = {**BASE_TREE, "notes.md": "new\n", "added.md": "x\n"}
SHA_A, SHA_B, SHA_C, SHA_TAG = "a" * 40, "b" * 40, "c" * 40, "d" * 40


def history_view():
    revisions = {"base": BASE_TREE, "mid": BASE_TREE, "tip": BASE_TREE, "fresh": FAR_TREE, "far": FAR_TREE,
                 "commit": COMMIT_TREE, "other": BASE_TREE}
    parents = {"base": [], "mid": ["base"], "tip": ["mid"], "fresh": ["base"], "far": ["base"],
               "commit": ["base"], "other": ["base"]}
    times = {"base": 100, "mid": 150, "tip": 200, "fresh": SINCE + 100, "far": 50, "commit": 300, "other": 300}
    refs = {"HEAD": "tip", "main": "tip", "refs/heads/main": "tip", "refs/tags/v1": "base"}
    return git_port.FakeGitView({"roots": {str(ROOT): {"revisions": revisions, "parents": parents, "times": times, "refs": refs}}})


def heads_before():
    return {"heads": {str(ROOT): {"head": "base", "tips": ["tip"], "since": SINCE}}}


def heads_after(head):
    return {str(ROOT): {"head": head, "tips": ["tip"]}}, True


def moved(head):
    with git_port.using_view(history_view()):
        return commit_test_changes.moved_sources(ROOT, heads_before(), heads_after(head), FOREVER, SKEW)


def written(parents):
    blobs = {path: text.encode() for path, text in CHANGED.items()}
    with git_port.using_view(history_view()):
        return commit_test_changes.written_test_rows(ROOT, "commit", parents, blobs, FOREVER)


def tip_line(sha, kind, name, peeled="", peeled_kind=""):
    return " ".join((sha, kind, peeled, peeled_kind, name))


class FakeGit:
    def __init__(self, root_refs):
        self.root_refs = root_refs

    def run(self, args, **options):
        words = args[1:]
        if "--include-root-refs" in words and not self.root_refs:
            raise subprocess.CalledProcessError(129, args)
        lines = [tip_line(SHA_A, "commit", "refs/heads/main"), tip_line(SHA_TAG, "tag", "refs/tags/v1", SHA_C, "commit")]
        if "--include-root-refs" in words:
            lines.insert(0, tip_line(SHA_B, "commit", "HEAD"))
        output = SHA_B + "\n" if words[:1] == ["rev-parse"] else "\n".join(lines) + "\n"
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")


def cli_head_tips(root_refs):
    fake = FakeGit(root_refs)
    with mock.patch.object(git_cli_view.subprocess, "run", fake.run), mock.patch.object(git_cli_view, "ROOT_REFS", [True]):
        return git_cli_view.CliGitView().head_tips(ROOT)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class MovedSources(unittest.TestCase):
    def test_no_sources_come_back_for_the_same_head_a_reachable_head_or_a_commit_inside_the_since_window(self):
        """Use a parameterized test for several inputs."""
        heads = {"same": "base", "reachable": "mid", "inside_window": "fresh"}
        self.assertEqual({label: moved(head) for label, head in heads.items()}, dict.fromkeys(heads, {}))

    def test_an_older_unreached_head_returns_old_head_sources_and_absent_for_added_files(self):
        """Feed input, check the return value."""
        expected = {"notes.md": (digest("old\n"), "old\n"), "added.md": (shell_snapshot.ABSENT, None)}
        self.assertEqual(moved("far"), expected)


class WrittenTestRows(unittest.TestCase):
    def test_added_changed_and_deleted_test_paths_and_inline_tests_a_commit_adds_are_flagged(self):
        """Feed input, check the return value."""
        flagged = sorted((row["rule"], row["path"]) for row in written(["base"]))
        expected = [(GATE, str(ROOT / path)) for path in ("src/lib.rs", "tests/test_gone.py", "tests/test_new.py", "tests/test_old.py")]
        self.assertEqual(flagged, expected)

    def test_a_merge_commit_flags_nothing(self):
        """Feed input, check the return value."""
        self.assertEqual(written(["base", "other"]), [])


class FakeViewContracts(unittest.TestCase):
    def test_head_tips_return_the_head_commit_and_every_full_ref(self):
        """Feed input, check the return value."""
        self.assertEqual(history_view().head_tips(ROOT), ("tip", {"refs/heads/main": "tip", "refs/tags/v1": "base"}))

    def test_commit_removed_paths_list_the_files_the_commit_deleted_from_its_parent(self):
        """Feed input, check the return value."""
        self.assertEqual(history_view().commit_removed_paths(ROOT, "commit"), ["tests/test_gone.py"])

    def test_tree_changes_list_changed_added_and_removed_paths_and_refuse_an_unknown_revision(self):
        """Use a parameterized test for several inputs."""
        view = history_view()
        changes = {"known": view.tree_changes(ROOT, "commit", "far"), "unknown": view.tree_changes(ROOT, "base", "missing")}
        expected = ["added.md", "notes.md", "src/app.py", "src/lib.rs", "tests/test_gone.py", "tests/test_new.py", "tests/test_old.py"]
        self.assertEqual(changes, {"known": expected, "unknown": None})

    def test_revision_blobs_return_only_the_paths_present_in_that_revision(self):
        """Feed input, check the return value."""
        blobs = history_view().revision_blobs(ROOT, "far", ["added.md", "tests/test_new.py"])
        self.assertEqual(blobs, {"added.md": b"x\n"})

    def test_revision_blobs_of_an_unknown_repository_are_unavailable(self):
        """Feed input, check the return value."""
        self.assertIsNone(history_view().revision_blobs(Path("/elsewhere"), "far", ["added.md"]))


class CliHeadTips(unittest.TestCase):
    def test_head_comes_from_root_refs_and_from_the_fallback_when_the_flag_fails(self):
        """Use a parameterized test for several inputs."""
        expected = (SHA_B, {"refs/heads/main": SHA_A, "refs/tags/v1": SHA_C})
        found = {"root_refs": cli_head_tips(True), "fallback": cli_head_tips(False)}
        self.assertEqual(found, dict.fromkeys(found, expected))


class GatheredCalls(unittest.TestCase):
    def test_results_keep_the_call_order_when_a_later_call_finishes_first(self):
        """Feed input, check the return value."""
        finished = threading.Event()

        def first():
            finished.wait(5)
            return "first"

        def second():
            finished.set()
            return "second"

        self.assertEqual(parallel_calls.gathered([first, second]), ["first", "second"])

    def test_each_thread_sees_the_git_view_set_by_the_caller(self):
        """Feed input, check the return value."""
        view = history_view()
        with git_port.using_view(view):
            seen = parallel_calls.gathered([git_port.view, git_port.view, git_port.view])
        self.assertEqual(seen, [view, view, view])

    def test_attempted_calls_hand_back_their_listed_error_as_a_value(self):
        """Feed input, check the return value."""
        def broken():
            raise OSError("down")

        results = parallel_calls.gathered([parallel_calls.attempted(broken, (OSError,)),
                                           parallel_calls.attempted(lambda: 1, (OSError,))])
        self.assertEqual([type(result) for result in results], [OSError, int])


if __name__ == "__main__":
    unittest.main()
