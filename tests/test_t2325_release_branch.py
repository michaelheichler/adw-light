import json
import shutil
import sys
import unittest
from pathlib import Path
from typing import NamedTuple

from t2325_fixtures import (RUNTIME_TOP, ModuleFixture, Scratch, all_paths, commit_version, copy_source_repo,
                            next_version, shipped_leaks, small_payload_probe, source_version)

AGENT_FILES = {"CLAUDE.md": "Project notes.\n", "engine/AGENTS.md": "Agent notes.\n",
               ".claude/settings.json": "{}\n"}
MALFORMED_MARKETPLACE = ".claude-plugin/marketplace.json"
DECOY_BUILD = 'mkdir -p "$ADW_RELEASE"\nprintf "9.9.9\\n" > "$ADW_RELEASE/VERSION"\n'


class ReleaseSource(NamedTuple):
    scratch: Scratch
    repo: Path
    bare: Path
    baseline: str

    @classmethod
    def build(cls, register_cleanup):
        scratch = Scratch(register_cleanup)
        repo = scratch.root / "repo"
        baseline = copy_source_repo(scratch, repo, AGENT_FILES)
        bare = scratch.root / "remote.git"
        scratch.git(scratch.root, "init", "-q", "--bare", bare.name)
        return cls(scratch, repo, bare, baseline)

    def reset(self):
        for name in self.scratch.git(self.repo, "remote").split():
            self.scratch.git(self.repo, "remote", "remove", name)
        self.scratch.git(self.repo, "reset", "-q", "--hard", self.baseline)
        for repo in (self.repo, self.bare):
            refs = self.scratch.git(repo, "for-each-ref", "--format=%(refname)", "refs/heads/release", "refs/tags")
            for ref in refs.split():
                self.scratch.git(repo, "update-ref", "-d", ref)
        return self


SOURCE = ModuleFixture(ReleaseSource.build)


def release_tool(scratch, repo, *args):
    return scratch.run(["bash", repo / "tools" / "release_branch.sh", *args], cwd=repo).returncode


def released_commit(scratch, repo, *args):
    result = scratch.run(["bash", repo / "tools" / "release_branch.sh", *args], cwd=repo)
    return result.returncode, result.stdout.strip().split("\n")[-1]


def local_tags(scratch, repo):
    return scratch.git(repo, "tag", "--list").split()


def reject_pushes(bare):
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    return hook


def remote_refs(scratch, bare):
    listed = scratch.git(bare, "for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split() for line in listed.splitlines())


class ReleaseBranchCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo, cls.bare, cls.baseline = SOURCE.get()

    def setUp(self):
        SOURCE.get().reset()

    def has_release(self):
        return self.scratch.run(["git", "-C", self.repo, "rev-parse", "--verify", "--quiet",
                                 "refs/heads/release"]).returncode == 0

    def add_bare_remote(self, name):
        self.scratch.git(self.repo, "remote", "add", name, str(self.bare))
        return self.bare

    def refused(self, *args):
        return release_tool(self.scratch, self.repo, *args) != 0, self.has_release()


class ReleaseToolRefusesOtherBranchesTests(ReleaseBranchCase):
    def test_branch_main_is_refused_and_no_release_ref_appears(self):
        self.assertEqual(self.refused("--branch", "main"), (True, False))

    def test_any_branch_name_other_than_release_is_refused(self):
        self.assertEqual(self.refused("--branch", "feature"), (True, False))

    def test_an_unknown_argument_is_refused(self):
        self.assertEqual(self.refused("--force"), (True, False))


class ReleaseToolRefusesOutsideTheRootTests(ReleaseBranchCase):
    def test_a_run_from_a_repository_subfolder_with_its_own_build_tool_is_refused_and_no_release_ref_appears(self):
        decoy = self.repo / "decoy-root"
        self.addCleanup(shutil.rmtree, decoy)
        (decoy / "tools").mkdir(parents=True)
        (decoy / "tools" / "build_release.sh").write_text(DECOY_BUILD, encoding="utf-8")
        code = self.scratch.run(["bash", self.repo / "tools" / "release_branch.sh"], cwd=decoy).returncode
        self.assertEqual((code != 0, self.has_release()), (True, False))


class ReleaseToolRefusesUnsafeRemotesTests(ReleaseBranchCase):
    def test_push_without_any_remote_is_refused(self):
        self.assertEqual(self.refused("--push"), (True, False))

    def test_push_with_a_configured_remote_but_no_remote_choice_is_refused_and_pushes_nothing(self):
        bare = self.add_bare_remote("origin")
        self.assertEqual((*self.refused("--push"), remote_refs(self.scratch, bare)), (True, False, {}))

    def test_push_to_a_mirror_remote_is_refused_and_pushes_nothing(self):
        bare = self.add_bare_remote("origin")
        self.scratch.git(self.repo, "config", "remote.origin.mirror", "true")
        self.assertEqual((*self.refused("--push", "--remote", "origin"), remote_refs(self.scratch, bare)),
                         (True, False, {}))

    def test_a_mirror_remote_named_without_push_is_refused_and_tags_nothing(self):
        self.add_bare_remote("origin")
        self.scratch.git(self.repo, "config", "remote.origin.mirror", "true")
        refused = release_tool(self.scratch, self.repo, "--remote", "origin") != 0
        self.assertEqual((refused, local_tags(self.scratch, self.repo)), (True, []))

    def test_push_to_a_remote_that_is_not_configured_is_refused(self):
        self.add_bare_remote("origin")
        self.assertEqual(self.refused("--push", "--remote", "elsewhere"), (True, False))

    def test_a_source_commit_other_than_main_is_refused_before_any_release_ref_appears(self):
        self.assertEqual(self.refused("--source-commit", "0" * 40), (True, False))


class PushRefspecRemoteTests(ReleaseBranchCase):
    def test_a_remote_push_refspec_does_not_stop_the_push_and_only_the_release_refs_arrive(self):
        bare = self.add_bare_remote("origin")
        self.scratch.git(self.repo, "config", "remote.origin.push", "refs/heads/*:refs/heads/*")
        code, made = released_commit(self.scratch, self.repo, "--push", "--remote", "origin")
        expected = {"refs/heads/release": made, "refs/tags/v" + source_version(self.repo): made}
        self.assertEqual((code, remote_refs(self.scratch, bare), self.has_release()), (0, expected, False))


class RejectedPushTests(ReleaseBranchCase):
    def test_a_rejected_push_leaves_no_local_tag_and_no_remote_ref(self):
        bare = self.add_bare_remote("origin")
        self.addCleanup(reject_pushes(bare).unlink)
        code = release_tool(self.scratch, self.repo, "--push", "--remote", "origin")
        self.assertEqual((code != 0, local_tags(self.scratch, self.repo), remote_refs(self.scratch, bare)),
                         (True, [], {}))

    def test_a_retry_after_a_rejected_push_publishes_the_branch_and_tag(self):
        bare = self.add_bare_remote("origin")
        hook = reject_pushes(bare)
        release_tool(self.scratch, self.repo, "--push", "--remote", "origin")
        hook.unlink()
        code, made = released_commit(self.scratch, self.repo, "--push", "--remote", "origin")
        tag = "v" + source_version(self.repo)
        self.assertEqual((code, remote_refs(self.scratch, bare), local_tags(self.scratch, self.repo)),
                         (0, {"refs/heads/release": made, "refs/tags/" + tag: made}, [tag]))


class LeftoverTagTests(ReleaseBranchCase):
    def test_a_leftover_local_tag_on_the_remote_branch_is_pushed_as_the_release_commit(self):
        bare = self.add_bare_remote("origin")
        _, first = released_commit(self.scratch, self.repo, "--push", "--remote", "origin")
        commit_version(self.scratch, self.repo, next_version(source_version(self.repo)))
        tag = "v" + source_version(self.repo)
        _, leftover = released_commit(self.scratch, self.repo, "--remote", "origin")
        code, made = released_commit(self.scratch, self.repo, "--push", "--remote", "origin")
        pushed = remote_refs(self.scratch, bare)
        parent = self.scratch.git(self.repo, "rev-parse", leftover + "^")
        self.assertEqual((code, made, pushed["refs/heads/release"], pushed["refs/tags/" + tag], parent),
                         (0, leftover, leftover, leftover, first))


class MalformedManifestTests(ReleaseBranchCase):
    def test_a_marketplace_manifest_that_is_not_an_object_is_refused_before_the_push(self):
        bare = self.add_bare_remote("origin")
        (self.repo / MALFORMED_MARKETPLACE).write_text("[]\n", encoding="utf-8")
        self.scratch.commit_all(self.repo, "break the marketplace manifest")
        code = release_tool(self.scratch, self.repo, "--push", "--remote", "origin")
        self.assertEqual((code != 0, remote_refs(self.scratch, bare)), (True, {}))


class TagNamingAnotherReleaseTests(ReleaseBranchCase):
    def test_a_main_change_under_an_unchanged_version_is_refused_and_keeps_the_first_tag(self):
        bare = self.add_bare_remote("origin")
        first_code = release_tool(self.scratch, self.repo, "--push", "--remote", "origin")
        published = remote_refs(self.scratch, bare)
        (self.repo / "skills" / "t2325-marker.txt").write_text("changed\n", encoding="utf-8")
        self.scratch.commit_all(self.repo, "change main without a version bump")
        second_code = release_tool(self.scratch, self.repo, "--push", "--remote", "origin")
        self.assertEqual((first_code, second_code != 0, remote_refs(self.scratch, bare)), (0, True, published))


class ReleaseBranchPushTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo, cls.bare, _ = SOURCE.get().reset()
        cls.scratch.git(cls.repo, "remote", "add", "origin", str(cls.bare))
        cls.tag = "v" + source_version(cls.repo)
        push = ("--push", "--remote", "origin")
        cls.push_code, cls.first = released_commit(cls.scratch, cls.repo, *push)
        cls.remote_after_push = remote_refs(cls.scratch, cls.bare)
        cls.repeat_code, cls.repeat = released_commit(cls.scratch, cls.repo)
        cls.next_tag = "v" + next_version(source_version(cls.repo))
        commit_version(cls.scratch, cls.repo, next_version(source_version(cls.repo)))
        cls.change_code, cls.changed = released_commit(cls.scratch, cls.repo, *push)
        cls.changed_parent = cls.scratch.git(cls.repo, "rev-parse", cls.changed + "^")
        cls.remote_after_change = remote_refs(cls.scratch, cls.bare)
        cls.local_branches = cls.scratch.git(cls.repo, "for-each-ref", "--format=%(refname)", "refs/heads").split()
        cls.release_top = cls.scratch.git(cls.repo, "ls-tree", "--name-only", cls.changed).split("\n")
        cls.release_names = cls.scratch.git(cls.repo, "ls-tree", "-r", "--name-only", cls.changed).split("\n")
        cls.tagged_record = json.loads(cls.scratch.git(cls.repo, "show", cls.next_tag + ":engine/RELEASE.json"))
        cls.main = cls.scratch.git(cls.repo, "rev-parse", "refs/heads/main")
        cls.checkout = cls.scratch.root / "checkout"
        cls.checkout.mkdir()
        archive = cls.scratch.run(["git", "-C", cls.repo, "archive", "-o", cls.scratch.root / "release.tar",
                                   cls.changed])
        assert archive.returncode == 0, archive.stderr
        cls.scratch.run(["tar", "-xf", cls.scratch.root / "release.tar", "-C", cls.checkout])

    def test_no_local_release_branch_exists_after_the_releases(self):
        self.assertEqual(self.local_branches, ["refs/heads/main"])

    def test_every_run_exits_zero(self):
        self.assertEqual((self.push_code, self.repeat_code, self.change_code), (0, 0, 0))

    def test_push_sends_the_release_branch_and_its_version_tag_at_the_same_commit(self):
        self.assertEqual(self.remote_after_push, {"refs/heads/release": self.first, "refs/tags/" + self.tag: self.first})

    def test_release_branch_top_level_holds_exactly_the_runtime_entries(self):
        self.assertEqual(set(self.release_top), RUNTIME_TOP)

    def test_release_branch_holds_no_test_tool_doc_agent_or_dot_path(self):
        self.assertEqual(shipped_leaks(self.release_names), [])

    def test_a_repeated_run_with_main_unchanged_keeps_the_release_commit(self):
        self.assertEqual(self.repeat, self.first)

    def test_a_version_bump_adds_a_release_commit_on_top_and_pushes_its_new_tag_beside_the_old_one(self):
        expected = {"refs/heads/release": self.changed, "refs/tags/" + self.tag: self.first,
                    "refs/tags/" + self.next_tag: self.changed}
        self.assertEqual((self.changed_parent, self.remote_after_change), (self.first, expected))

    def test_the_new_tag_records_the_main_commit_and_version_it_was_built_from(self):
        self.assertEqual(self.tagged_record, {"version": self.next_tag[1:], "source": self.main})

    def test_the_release_tool_runs_no_plugin_install_and_leaves_the_user_config_untouched(self):
        calls = self.scratch.fakes.calls("claude") + self.scratch.fakes.calls("codex")
        self.assertEqual((calls, all_paths(self.scratch.config_dir, self.scratch.codex_home)), ([], []))

    def test_a_checkout_of_the_release_branch_passes_the_smoke_probe(self):
        """The test runs quickly."""
        result = self.scratch.run([sys.executable, "-c", small_payload_probe(), self.checkout])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
