import json
import unittest
from pathlib import Path
from typing import NamedTuple

from t2325_fixtures import (CODEX_ADW_HOOK_EVENTS, CODEX_EVENTS, DEFAULT_REPO, FORK_REPO, PLUGIN_ID, SMOKE_FAILURE, Scratch, ProcessFixture,
                            adw_hook_events, all_paths, copy_source_repo, cut_release, github_refs, github_url,
                            hook_commands, installed_plugins, marketplaces, no_venv_python, publish, source_version,
                            stand_in_github, write_suite_stamp)

MARKER_TEST = """import unittest


class DeployFixtureTests(unittest.TestCase):
    def test_two_plus_two_is_four(self):
        self.assertEqual(2 + 2, 4)
"""


class SourceRepo(NamedTuple):
    scratch: Scratch
    repo: Path
    baseline: str

    @classmethod
    def build(cls, register_cleanup):
        scratch = Scratch(register_cleanup)
        repo = scratch.root / "repo"
        baseline = copy_source_repo(scratch, repo, {"tests/test_fixture.py": MARKER_TEST}, skip_top=("tests",))
        scratch.git(repo, "remote", "add", "origin", github_url(DEFAULT_REPO))
        return cls(scratch, repo, baseline)

    def restore(self):
        self.scratch.git(self.repo, "reset", "-q", "--hard", self.baseline)
        self.scratch.git(self.repo, "remote", "set-url", "origin", github_url(DEFAULT_REPO))
        refs = self.scratch.git(self.repo, "for-each-ref", "--format=%(refname)", "refs/heads/release", "refs/tags")
        for ref in refs.split():
            self.scratch.git(self.repo, "update-ref", "-d", ref)
        return self.repo


SOURCE_REPO = ProcessFixture(SourceRepo.build)


def release_record(scratch, tag, slug=DEFAULT_REPO):
    return json.loads(scratch.git(scratch.fakes.github_repo(slug), "show", tag + ":engine/RELEASE.json"))


class DeployCase(unittest.TestCase):
    @staticmethod
    def fixture(register_cleanup, stamped=True, slugs=(DEFAULT_REPO,)):
        scratch = Scratch(register_cleanup)
        (scratch.home / "Development").mkdir()
        repo = SOURCE_REPO.get().restore()
        for slug in slugs:
            stand_in_github(scratch, slug)
        if stamped:
            DeployCase.stamp(scratch, repo)
        return scratch, repo

    @staticmethod
    def stamp(scratch, repo):
        commit = scratch.git(repo, "rev-parse", "HEAD")
        write_suite_stamp(scratch.home, commit)
        return commit

    @staticmethod
    def deploy(scratch, repo, *args, **env):
        env = {"ADW_PYTHON": no_venv_python(scratch), **env}
        return scratch.run(["bash", repo / "tools" / "deploy.sh", *args], cwd=repo, timeout=900, **env).returncode

    @staticmethod
    def tag_of(repo):
        return "v" + source_version(repo)

    @staticmethod
    def plugin_calls(scratch):
        return scratch.fakes.calls("claude") + scratch.fakes.calls("codex")


class DeployArgumentTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup, slugs=(DEFAULT_REPO, FORK_REPO))

    def refused(self, *args):
        code = self.deploy(self.scratch, self.repo, *args)
        published = (github_refs(self.scratch), github_refs(self.scratch, FORK_REPO))
        return code != 0, published, self.plugin_calls(self.scratch), self.scratch.fakes.calls("ssh")

    def test_an_unknown_argument_is_refused_before_anything_is_published_or_installed(self):
        self.assertEqual(self.refused("--everywhere"), (True, ({}, {}), [], []))

    def test_host_without_a_target_is_refused_before_anything_is_published_or_installed(self):
        self.assertEqual(self.refused("--host"), (True, ({}, {}), [], []))

    def test_a_host_that_starts_with_a_dash_is_refused_before_anything_is_published_or_installed(self):
        self.assertEqual(self.refused("--host", "-oProxyCommand=true"), (True, ({}, {}), [], []))

    def test_a_repository_that_is_no_owner_and_name_pair_is_refused_before_anything_is_published(self):
        self.assertEqual(self.refused("--repo", "adw-light"), (True, ({}, {}), [], []))

    def test_a_push_remote_for_another_repository_is_refused_before_anything_is_published(self):
        self.assertEqual(self.refused("--repo", FORK_REPO), (True, ({}, {}), [], []))


class UnstampedDeployTests(DeployCase):
    def test_a_deploy_without_a_suite_stamp_publishes_and_installs_nothing(self):
        scratch, repo = self.fixture(self.addCleanup, stamped=False)
        code = self.deploy(scratch, repo)
        self.assertEqual((code != 0, github_refs(scratch), self.plugin_calls(scratch)), (True, {}, []))


class FailingSmokeProbeTests(DeployCase):
    def test_a_failing_smoke_probe_stops_the_deploy_before_anything_is_published_or_installed(self):
        scratch, repo = self.fixture(self.addCleanup)
        failure = repo.parent / SMOKE_FAILURE
        failure.touch()
        self.addCleanup(failure.unlink, missing_ok=True)
        code = self.deploy(scratch, repo)
        self.assertEqual((code != 0, github_refs(scratch), self.plugin_calls(scratch)), (True, {}, []))


class PublishingDeployTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        cls.commit = cls.scratch.git(cls.repo, "rev-parse", "HEAD")
        cls.tag = cls.tag_of(cls.repo)
        cls.code = cls.deploy(cls.scratch, cls.repo)
        cls.published = github_refs(cls.scratch)

    def test_deploy_exits_zero(self):
        self.assertEqual(self.code, 0)

    def test_deploy_publishes_the_release_branch_and_the_version_tag_at_one_commit(self):
        self.assertEqual((sorted(self.published), len(set(self.published.values()))),
                         (["refs/heads/release", "refs/tags/" + self.tag], 1))

    def test_the_published_tag_records_the_stamped_commit_and_the_version(self):
        self.assertEqual(release_record(self.scratch, self.tag), {"version": self.tag[1:], "source": self.commit})

    def test_both_layers_add_the_default_repository_at_the_new_tag(self):
        self.assertEqual((marketplaces(self.scratch, "claude"), marketplaces(self.scratch, "codex")),
                         ({"adw-light": (DEFAULT_REPO, self.tag)},) * 2)

    def test_both_layers_install_the_tagged_version(self):
        self.assertEqual((installed_plugins(self.scratch, "claude"), installed_plugins(self.scratch, "codex")),
                         ({PLUGIN_ID: self.tag[1:]},) * 2)

    def test_codex_runs_every_hook_from_its_plugin_cache(self):
        cache = self.scratch.codex_home / "plugins" / "cache" / "adw-light" / "adw-light"
        runners = {(event, command.split()[1].startswith(str(cache) + "/"))
                   for event, command in hook_commands(self.scratch.codex_home / "hooks.json")}
        self.assertEqual(runners, {(event, True) for event in CODEX_EVENTS})

    def test_deploy_creates_nothing_under_home_development_or_the_temp_dir(self):
        self.assertEqual(all_paths(self.scratch.home / "Development", self.scratch.tmp), [])

    def test_a_deploy_without_a_host_makes_no_ssh_call(self):
        self.assertEqual(self.scratch.fakes.calls("ssh"), [])


class ExistingTagDeployTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        cls.tag = cut_release(cls.scratch, cls.repo)
        publish(cls.scratch, cls.repo, DEFAULT_REPO, f"refs/tags/{cls.tag}:refs/heads/release", "refs/tags/" + cls.tag)
        cls.before = github_refs(cls.scratch)
        cls.code = cls.deploy(cls.scratch, cls.repo)

    def test_a_tag_with_matching_provenance_installs_without_moving_any_published_ref(self):
        self.assertEqual((self.code, github_refs(self.scratch)), (0, self.before))

    def test_both_layers_install_the_existing_tag(self):
        self.assertEqual((marketplaces(self.scratch, "claude"), marketplaces(self.scratch, "codex")),
                         ({"adw-light": (DEFAULT_REPO, self.tag)},) * 2)


class WrongProvenanceDeployTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        (cls.repo / "skills" / "t2325-other-release.txt").write_text("other\n", encoding="utf-8")
        cls.scratch.commit_all(cls.repo, "an unstamped release under the same version")
        tag = cut_release(cls.scratch, cls.repo)
        publish(cls.scratch, cls.repo, DEFAULT_REPO, f"refs/tags/{tag}:refs/heads/release", "refs/tags/" + tag)
        cls.before = github_refs(cls.scratch)
        SOURCE_REPO.get().restore()
        cls.stamp(cls.scratch, cls.repo)
        cls.code = cls.deploy(cls.scratch, cls.repo)

    def test_a_tag_built_from_another_commit_is_refused_and_nothing_is_installed(self):
        self.assertEqual((self.code != 0, self.plugin_calls(self.scratch)), (True, []))

    def test_the_refused_deploy_moves_no_published_ref(self):
        self.assertEqual(github_refs(self.scratch), self.before)


class RepoOverrideDeployTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup, slugs=(FORK_REPO,))
        cls.scratch.git(cls.repo, "remote", "set-url", "origin", github_url(FORK_REPO))
        cls.tag = cls.tag_of(cls.repo)
        cls.code = cls.deploy(cls.scratch, cls.repo, "--repo", FORK_REPO)

    def test_deploy_publishes_the_tag_to_the_named_repository(self):
        self.assertEqual((self.code, sorted(github_refs(self.scratch, FORK_REPO))),
                         (0, ["refs/heads/release", "refs/tags/" + self.tag]))

    def test_both_layers_add_the_named_repository_at_the_tag(self):
        self.assertEqual((marketplaces(self.scratch, "claude"), marketplaces(self.scratch, "codex")),
                         ({"adw-light": (FORK_REPO, self.tag)},) * 2)

    def test_codex_holds_one_adw_hook_set(self):
        self.assertEqual(adw_hook_events(self.scratch.codex_home / "hooks.json"), CODEX_ADW_HOOK_EVENTS)


if __name__ == "__main__":
    unittest.main()
