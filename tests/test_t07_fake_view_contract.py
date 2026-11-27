import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from lib.git_port import FakeGitView


ROOT = Path("/work/repo")
TIMES = {"a": 100, "b": 200, "c": 300, "d": 400, "e": 50, "f": 250, "g": 350}
PARENTS = {"a": [], "b": ["a"], "c": ["b"], "d": ["b"], "e": ["a"], "f": ["a"], "g": ["c"]}
REFS = {
    "HEAD": "g", "main": "c", "refs/heads/main": "c", "refs/heads/side": "d",
    "refs/remotes/origin/main": "e", "refs/tags/v1": "f",
}


def contract_view():
    revisions = {name: {"notes.md": name + "\n"} for name in PARENTS}
    state = {"revisions": revisions, "parents": PARENTS, "refs": REFS, "times": TIMES}
    return FakeGitView({"roots": {str(ROOT): state}})


def fresh(tips, known, since=0, limit=10):
    return sorted(contract_view().new_commits(ROOT, tips, known, since=since, limit=limit))


class RefTips(unittest.TestCase):
    def test_ref_tips_list_every_full_ref_and_leave_out_head_and_short_names(self):
        """Feed input, check the return value."""
        expected = {"refs/heads/main": "c", "refs/heads/side": "d", "refs/remotes/origin/main": "e", "refs/tags/v1": "f"}
        self.assertEqual(contract_view().ref_tips(ROOT), expected)


class NewCommits(unittest.TestCase):
    def test_new_commits_are_those_the_tips_reach_and_the_known_commits_do_not(self):
        """Feed input, check the return value."""
        self.assertEqual(fresh(["c", "d"], ["a"]), ["b", "c", "d"])

    def test_known_tips_leave_no_new_commits(self):
        """Feed input, check the return value."""
        self.assertEqual(fresh(["c", "d"], ["c", "d"]), [])

    def test_time_filter_keeps_only_commits_dated_at_or_after_the_start(self):
        """Feed input, check the return value."""
        self.assertEqual(fresh(["c", "d"], ["a"], since=TIMES["c"]), ["c", "d"])

    def test_limit_caps_the_number_of_listed_commits(self):
        """Feed input, check the return value."""
        self.assertEqual(len(fresh(["c", "d"], ["a"], limit=2)), 2)


class CommitReachable(unittest.TestCase):
    def test_commit_counts_as_reachable_only_from_a_local_branch_or_head(self):
        """Use a parameterized test for several inputs."""
        found = {name: contract_view().commit_reachable(ROOT, name) for name in ("b", "d", "e", "f", "g")}
        self.assertEqual(found, {"b": True, "d": True, "e": False, "f": False, "g": True})


if __name__ == "__main__":
    unittest.main()
