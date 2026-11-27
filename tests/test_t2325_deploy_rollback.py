import json
import shlex
import unittest
from pathlib import Path

from t2325_fixtures import (CODEX_ADW_HOOK_EVENTS, DEFAULT_REPO, PLUGIN_ID, ModuleFixture, adw_hook_events, commit_version,
                            github_refs, hook_commands, installed_plugins, marketplaces, next_version, source_version)
from test_t2325_deploy import SOURCE_REPO, DeployCase

FOREIGN_CODEX = "echo foreign-codex"
UNPUBLISHED_PRIOR = "v0.1.9"
OLD_TAGS = ("v0.1.1", "v0.0.9")
MARKETPLACE_LIST = ["claude", "plugin", "marketplace", "list", "--json"]


def seed_claude_marketplace(scratch, **entry):
    table = scratch.config_dir / "plugins" / "known_marketplaces.json"
    table.parent.mkdir(parents=True, exist_ok=True)
    data = {"adw-light": {"name": "adw-light", **entry}}
    table.write_text(json.dumps(data), encoding="utf-8")
    return {"adw-light": (entry.get("repo"), entry.get("ref"))}


def seed_foreign_codex_hook(scratch):
    path = scratch.codex_home / "hooks.json"
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": FOREIGN_CODEX}]}]}}),
                    encoding="utf-8")


def runners(path):
    return [Path(shlex.split(command)[1]) for _, command in hook_commands(path) if "ADW_CODEX_HOOK=1" in command]


SEEDED_DEPLOY_HOME = ModuleFixture(DeployCase.fixture)


class RollbackCase(DeployCase):
    @classmethod
    def refused_with_seeded_marketplace(cls, **entry):
        cls.scratch, _ = SEEDED_DEPLOY_HOME.get()
        cls.repo = SOURCE_REPO.get().restore()
        cls.scratch.fakes.forget_calls()
        cls.seeded = seed_claude_marketplace(cls.scratch, **entry)
        cls.code = cls.deploy(cls.scratch, cls.repo)

    def stopped_before_any_marketplace_change(self):
        return (self.code != 0, self.scratch.fakes.calls("claude"), self.scratch.fakes.calls("codex"),
                marketplaces(self.scratch, "claude"))


class FailedUpgradeRollbackTests(RollbackCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        seed_foreign_codex_hook(cls.scratch)
        cls.first_tag = cls.tag_of(cls.repo)
        cls.first_code = cls.deploy(cls.scratch, cls.repo)
        cls.first_calls = len(cls.scratch.fakes.calls("claude"))
        commit_version(cls.scratch, cls.repo, next_version(source_version(cls.repo)))
        cls.stamp(cls.scratch, cls.repo)
        cls.next_tag = cls.tag_of(cls.repo)
        cls.scratch.fakes.fail_on("claude", "plugin", "marketplace", "add", f"{DEFAULT_REPO}#{cls.next_tag}")
        cls.second_code = cls.deploy(cls.scratch, cls.repo)

    def test_the_first_deploy_passes_and_the_failing_upgrade_exits_non_zero(self):
        self.assertEqual((self.first_code, self.second_code != 0), (0, True))

    def test_the_upgrade_removes_the_claude_marketplace_before_its_failing_add(self):
        upgrade = self.scratch.fakes.calls("claude")[self.first_calls:][:4]
        self.assertEqual(upgrade, [MARKETPLACE_LIST, MARKETPLACE_LIST, ["claude", "plugin", "marketplace", "remove", "adw-light"],
                                   ["claude", "plugin", "marketplace", "add", f"{DEFAULT_REPO}#{self.next_tag}",
                                    "--scope", "user"]])

    def test_both_marketplaces_return_to_the_previous_tag(self):
        self.assertEqual((marketplaces(self.scratch, "claude"), marketplaces(self.scratch, "codex")),
                         ({"adw-light": (DEFAULT_REPO, self.first_tag)},) * 2)

    def test_both_layers_run_the_previous_version_again(self):
        self.assertEqual((installed_plugins(self.scratch, "claude"), installed_plugins(self.scratch, "codex")),
                         ({PLUGIN_ID: self.first_tag[1:]},) * 2)

    def test_codex_holds_one_adw_hook_set_whose_runners_exist_and_the_foreign_hook(self):
        path = self.scratch.codex_home / "hooks.json"
        commands = [command for _, command in hook_commands(path)]
        self.assertEqual((adw_hook_events(path), [runner.is_file() for runner in runners(path)], commands.count(FOREIGN_CODEX)),
                         (CODEX_ADW_HOOK_EVENTS, [True] * len(CODEX_ADW_HOOK_EVENTS), 1))

    def test_the_new_tag_stays_published(self):
        self.assertIn("refs/tags/" + self.next_tag, github_refs(self.scratch))


class UnpublishedPriorTagTests(RollbackCase):
    @classmethod
    def setUpClass(cls):
        cls.refused_with_seeded_marketplace(source="github", repo=DEFAULT_REPO, ref=UNPUBLISHED_PRIOR)

    def test_a_previous_tag_missing_on_github_stops_before_the_marketplace_is_removed(self):
        self.assertEqual(self.stopped_before_any_marketplace_change(), (True, [MARKETPLACE_LIST], [], self.seeded))


class FailedFirstInstallTests(RollbackCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        seed_foreign_codex_hook(cls.scratch)
        cls.scratch.fakes.fail_on("claude", "plugin", "install")
        cls.code = cls.deploy(cls.scratch, cls.repo)

    def test_the_deploy_exits_non_zero(self):
        self.assertNotEqual(self.code, 0)

    def test_no_plugin_and_no_marketplace_remain_in_either_layer(self):
        self.assertEqual((installed_plugins(self.scratch, "claude"), marketplaces(self.scratch, "claude"),
                          installed_plugins(self.scratch, "codex"), marketplaces(self.scratch, "codex")), ({},) * 4)

    def test_the_codex_hooks_file_keeps_only_the_foreign_hook(self):
        self.assertEqual(hook_commands(self.scratch.codex_home / "hooks.json"), [("Stop", FOREIGN_CODEX)])


class OldRollbackTagTests(RollbackCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)

    def refused(self, tag):
        code = self.deploy(self.scratch, self.repo, "--previous-tag", tag)
        return code != 0, github_refs(self.scratch), self.plugin_calls(self.scratch)

    def test_a_previous_tag_before_v0_1_2_is_refused_before_anything_is_published(self):
        self.assertEqual(self.refused(OLD_TAGS[0]), (True, {}, []))

    def test_a_previous_tag_from_the_v0_0_series_is_refused_before_anything_is_published(self):
        self.assertEqual(self.refused(OLD_TAGS[1]), (True, {}, []))


class OldMarketplaceTagTests(RollbackCase):
    @classmethod
    def setUpClass(cls):
        cls.refused_with_seeded_marketplace(source="github", repo=DEFAULT_REPO, ref=OLD_TAGS[0])

    def test_a_marketplace_at_a_tag_before_v0_1_2_stops_before_the_marketplace_is_removed(self):
        self.assertEqual(self.stopped_before_any_marketplace_change(), (True, [MARKETPLACE_LIST], [], self.seeded))


class MarketplaceWithoutRepoTests(RollbackCase):
    @classmethod
    def setUpClass(cls):
        cls.refused_with_seeded_marketplace(source="github", ref=UNPUBLISHED_PRIOR)

    def test_a_marketplace_without_a_repo_stops_before_the_marketplace_is_removed(self):
        self.assertEqual(self.stopped_before_any_marketplace_change(), (True, [MARKETPLACE_LIST], [], self.seeded))


class MarketplaceWithoutRefTests(RollbackCase):
    @classmethod
    def setUpClass(cls):
        cls.refused_with_seeded_marketplace(source="github", repo=DEFAULT_REPO)

    def test_a_marketplace_without_a_ref_stops_before_the_marketplace_is_removed(self):
        self.assertEqual(self.stopped_before_any_marketplace_change(), (True, [MARKETPLACE_LIST], [], self.seeded))


class LocalMarketplaceMigrationTests(RollbackCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        seed_claude_marketplace(cls.scratch, source="directory", path=str(cls.scratch.home / ".adw-lite" / "install"))
        cls.code = cls.deploy(cls.scratch, cls.repo)

    def test_a_local_folder_marketplace_is_replaced_by_the_github_tag(self):
        self.assertEqual((self.code, marketplaces(self.scratch, "claude")),
                         (0, {"adw-light": (DEFAULT_REPO, self.tag_of(self.repo))}))


if __name__ == "__main__":
    unittest.main()
