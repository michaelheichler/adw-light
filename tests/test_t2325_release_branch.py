import json
import sys
import unittest

from t2325_fixtures import PLUGIN_ID, RUNTIME_TOP, ROOT, Scratch, copy_source_repo, shipped_leaks

SMOKE = ROOT / "tools" / "smoke_probe.py"
AGENT_FILES = {"CLAUDE.md": "Project notes.\n", "engine/AGENTS.md": "Agent notes.\n",
               ".claude/settings.json": "{}\n"}


class ReleaseBranchCase(unittest.TestCase):
    def setUp(self):
        self.scratch = Scratch(self.addCleanup)
        self.repo = self.scratch.root / "repo"
        copy_source_repo(self.scratch, self.repo, AGENT_FILES)

    def release_tool(self, scratch, repo, *args):
        return scratch.run(["bash", repo / "tools" / "release_branch.sh", *args], cwd=repo).returncode

    def has_release(self):
        return self.scratch.run(["git", "-C", self.repo, "rev-parse", "--verify", "--quiet",
                                 "refs/heads/release"]).returncode == 0

    def add_bare_remote(self, name):
        bare = self.scratch.root / (name + ".git")
        self.scratch.git(self.scratch.root, "init", "-q", "--bare", bare.name)
        self.scratch.git(self.repo, "remote", "add", name, str(bare))
        return bare

    def remote_refs(self, bare):
        return self.scratch.git(bare, "for-each-ref", "--format=%(refname)").split()

    def refused(self, *args):
        return self.release_tool(self.scratch, self.repo, *args) != 0, self.has_release()


class ReleaseToolRefusesOtherBranchesTests(ReleaseBranchCase):
    def test_branch_main_is_refused_and_no_release_ref_appears(self):
        self.assertEqual(self.refused("--branch", "main"), (True, False))

    def test_any_branch_name_other_than_release_is_refused(self):
        self.assertEqual(self.refused("--branch", "feature"), (True, False))

    def test_an_unknown_argument_is_refused(self):
        self.assertEqual(self.refused("--force"), (True, False))


class ReleaseToolRefusesUnsafeRemotesTests(ReleaseBranchCase):
    def test_push_without_any_remote_is_refused(self):
        self.assertEqual(self.refused("--push"), (True, False))

    def test_push_with_two_remotes_and_no_choice_is_refused_and_pushes_nothing(self):
        first, second = self.add_bare_remote("origin"), self.add_bare_remote("backup")
        self.assertEqual((*self.refused("--push"), self.remote_refs(first), self.remote_refs(second)),
                         (True, False, [], []))

    def test_push_to_a_mirror_remote_is_refused_and_pushes_nothing(self):
        bare = self.add_bare_remote("origin")
        self.scratch.git(self.repo, "config", "remote.origin.mirror", "true")
        self.assertEqual((*self.refused("--push"), self.remote_refs(bare)), (True, False, []))

    def test_push_to_a_remote_with_a_push_refspec_is_refused_and_pushes_nothing(self):
        bare = self.add_bare_remote("origin")
        self.scratch.git(self.repo, "config", "remote.origin.push", "refs/heads/*:refs/heads/*")
        self.assertEqual((*self.refused("--push"), self.remote_refs(bare)), (True, False, []))

    def test_push_to_a_remote_that_is_not_configured_is_refused(self):
        self.add_bare_remote("origin")
        self.assertEqual(self.refused("--push", "--remote", "elsewhere"), (True, False))


class ReleaseBranchPushTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch(cls.addClassCleanup)
        cls.repo = cls.scratch.root / "repo"
        copy_source_repo(cls.scratch, cls.repo, AGENT_FILES)
        cls.bare = cls.scratch.root / "origin.git"
        cls.scratch.git(cls.scratch.root, "init", "-q", "--bare", cls.bare.name)
        cls.scratch.git(cls.repo, "remote", "add", "origin", str(cls.bare))
        tool = ["bash", cls.repo / "tools" / "release_branch.sh"]
        cls.push_code = cls.scratch.run([*tool, "--push"], cwd=cls.repo).returncode
        cls.first = cls.scratch.git(cls.repo, "rev-parse", "refs/heads/release")
        cls.remote_after_push = cls.scratch.git(cls.bare, "for-each-ref", "--format=%(refname) %(objectname)")
        cls.repeat_code = cls.scratch.run(tool, cwd=cls.repo).returncode
        cls.repeat = cls.scratch.git(cls.repo, "rev-parse", "refs/heads/release")
        (cls.repo / "skills" / "t2325-marker.txt").write_text("changed\n", encoding="utf-8")
        cls.scratch.commit_all(cls.repo, "change main")
        cls.change_code = cls.scratch.run([*tool, "--push"], cwd=cls.repo).returncode
        cls.changed = cls.scratch.git(cls.repo, "rev-parse", "refs/heads/release")
        cls.changed_parent = cls.scratch.git(cls.repo, "rev-parse", "refs/heads/release^")
        cls.remote_after_change = cls.scratch.git(cls.bare, "rev-parse", "refs/heads/release")
        cls.checkout = cls.scratch.root / "checkout"
        cls.checkout.mkdir()
        archive = cls.scratch.run(["git", "-C", cls.repo, "archive", "-o", cls.scratch.root / "release.tar",
                                   "refs/heads/release"])
        assert archive.returncode == 0, archive.stderr
        cls.scratch.run(["tar", "-xf", cls.scratch.root / "release.tar", "-C", cls.checkout])

    def test_every_run_exits_zero(self):
        self.assertEqual((self.push_code, self.repeat_code, self.change_code), (0, 0, 0))

    def test_push_sends_only_the_release_branch_to_the_remote(self):
        self.assertEqual(self.remote_after_push, "refs/heads/release " + self.first)

    def test_release_branch_top_level_holds_exactly_the_six_runtime_entries(self):
        names = self.scratch.git(self.repo, "ls-tree", "--name-only", "refs/heads/release").split("\n")
        self.assertEqual(set(names), RUNTIME_TOP)

    def test_release_branch_holds_no_test_tool_doc_agent_or_dot_path(self):
        names = self.scratch.git(self.repo, "ls-tree", "-r", "--name-only", "refs/heads/release").split("\n")
        self.assertEqual(shipped_leaks(names), [])

    def test_a_repeated_run_with_main_unchanged_keeps_the_release_commit(self):
        self.assertEqual(self.repeat, self.first)

    def test_a_run_after_main_changes_adds_a_release_commit_on_top_of_the_previous_one(self):
        self.assertEqual((self.changed != self.first, self.changed_parent, self.remote_after_change),
                         (True, self.first, self.changed))

    def plugin_cache_from_checkout(self):
        self.scratch.run(["claude", "plugin", "marketplace", "add", self.checkout, "--scope", "user"], timeout=120)
        self.scratch.run(["claude", "plugin", "install", PLUGIN_ID, "--scope", "user"], timeout=120)
        listed = json.loads(self.scratch.run(["claude", "plugin", "list", "--json"], timeout=120).stdout)
        return [row["installPath"] for row in listed if row.get("id") == PLUGIN_ID]

    def test_a_claude_plugin_install_from_the_release_branch_passes_the_smoke_probe(self):
        caches = self.plugin_cache_from_checkout()
        smoked = [self.scratch.run([sys.executable, SMOKE, cache]).returncode for cache in caches]
        self.assertEqual(smoked, [0])

    def test_a_checkout_of_the_release_branch_passes_the_smoke_probe(self):
        result = self.scratch.run([sys.executable, SMOKE, self.checkout])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
