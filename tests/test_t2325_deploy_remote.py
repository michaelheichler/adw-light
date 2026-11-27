import json
import shlex
import unittest
from pathlib import Path

from t2325_fixtures import (CODEX_ADW_HOOK_EVENTS, DEFAULT_REPO, PLUGIN_ID, adw_hook_events, all_paths, hook_commands,
                            installed_plugins, marketplaces)
from test_t2325_deploy import DeployCase

REMOTE_HOST = "deploy@deploy-target.example"
ENV_HOST = "builder@env-target.example"
FOREIGN_CODEX = "echo foreign-codex"


def remote_script(scratch):
    return shlex.split(scratch.fakes.calls("ssh")[0][-1])[2].splitlines()


def first_line(lines, start):
    return next(index for index, line in enumerate(lines) if line.startswith(start))


def calls_named(scratch, name, *words):
    return [call for call in scratch.fakes.calls(name) if call[1:1 + len(words)] == list(words)]


def seed_foreign_codex_hook(scratch):
    path = scratch.codex_home / "hooks.json"
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": FOREIGN_CODEX}]}]}}),
                    encoding="utf-8")


class RemoteDeployTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        cls.tag = cls.tag_of(cls.repo)
        cls.code = cls.deploy(cls.scratch, cls.repo, ADW_DEPLOY_HOST=ENV_HOST)
        cls.remote = cls.scratch.fakes

    def test_deploy_exits_zero(self):
        self.assertEqual(self.code, 0)

    def test_adw_deploy_host_names_the_only_ssh_target_and_the_call_runs_in_batch_mode(self):
        self.assertEqual([call[1:4] for call in self.scratch.fakes.calls("ssh")], [["-o", "BatchMode=yes", ENV_HOST]])

    def test_the_remote_adds_the_github_tag_in_both_layers(self):
        self.assertEqual((marketplaces(self.scratch, "claude", self.remote.remote_config("claude")),
                          marketplaces(self.scratch, "codex", self.remote.remote_config("codex"))),
                         ({"adw-light": (DEFAULT_REPO, self.tag)},) * 2)

    def test_the_remote_installs_the_tagged_version_in_both_layers(self):
        self.assertEqual((installed_plugins(self.scratch, "claude", self.remote.remote_config("claude")),
                          installed_plugins(self.scratch, "codex", self.remote.remote_config("codex"))),
                         ({PLUGIN_ID: self.tag[1:]},) * 2)

    def test_the_remote_codex_runs_one_adw_hook_set_from_its_own_plugin_cache(self):
        hooks = self.remote.remote_config("codex") / "hooks.json"
        cache = self.remote.remote_config("codex") / "plugins" / "cache"
        runners = {Path(shlex.split(command)[1]).is_relative_to(cache) for _, command in hook_commands(hooks)}
        self.assertEqual((adw_hook_events(hooks), runners), (CODEX_ADW_HOOK_EVENTS, {True}))

    def test_the_mac_installs_the_same_tag(self):
        self.assertEqual(marketplaces(self.scratch, "claude"), {"adw-light": (DEFAULT_REPO, self.tag)})

    def test_the_remote_staging_folder_is_removed(self):
        self.assertEqual(all_paths(self.remote.remote_home / "tmp"), [])

    def test_without_a_remote_temp_folder_the_script_creates_its_stage_right_after_the_shell_options(self):
        self.assertEqual(remote_script(self.scratch)[:2], ["set -euo pipefail", "stage=$(mktemp -d)"])


class RemoteTempFolderTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        cls.folder = cls.scratch.fakes.remote_home / "tmp" / "stage area"
        cls.code = cls.deploy(cls.scratch, cls.repo, "--host", REMOTE_HOST, ADW_REMOTE_TMPDIR=str(cls.folder))
        cls.lines = remote_script(cls.scratch)

    def test_deploy_with_a_remote_temp_folder_exits_zero_and_leaves_the_created_folder_empty(self):
        self.assertEqual((self.code, self.folder.is_dir(), all_paths(self.folder)), (0, True, []))

    def test_the_remote_script_exports_the_quoted_folder_as_tmpdir_before_it_creates_the_stage(self):
        export = first_line(self.lines, "export TMPDIR=")
        order = export < first_line(self.lines, "stage=$(mktemp -d)")
        self.assertEqual((shlex.split(self.lines[export]), order), (["export", f"TMPDIR={self.folder}"], True))

    def test_the_remote_script_creates_the_quoted_folder_before_it_exports_it(self):
        made = first_line(self.lines, "mkdir -p ")
        order = made < first_line(self.lines, "export TMPDIR=")
        self.assertEqual((shlex.split(self.lines[made]), order), (["mkdir", "-p", str(self.folder)], True))


class RefusedRemoteTempFolderTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        cls.remote_tmp = str(cls.scratch.fakes.remote_home / "tmp")

    def refused(self, folder):
        result = self.scratch.run(["bash", self.repo / "tools" / "deploy.sh", "--host", REMOTE_HOST], cwd=self.repo,
                                  timeout=900, ADW_REMOTE_TMPDIR=folder)
        named = "ADW_REMOTE_TMPDIR" in result.stderr
        return result.returncode != 0, named, self.scratch.fakes.calls("ssh"), self.plugin_calls(self.scratch)

    def test_a_relative_remote_temp_folder_is_refused_before_any_ssh_or_plugin_call(self):
        self.assertEqual(self.refused("tmp/stage"), (True, True, [], []))

    def test_a_remote_temp_folder_with_a_newline_is_refused_before_any_ssh_or_plugin_call(self):
        self.assertEqual(self.refused(self.remote_tmp + "\nexport X=1"), (True, True, [], []))


class KeepOutForwardingTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        remote_claude = cls.scratch.fakes.remote_config("claude")
        keep_out = str(cls.scratch.root / "kept out" / "notes") + ":" + str(remote_claude)
        cls.code = cls.deploy(cls.scratch, cls.repo, "--host", REMOTE_HOST, ADW_KEEP_OUT=keep_out)

    def test_a_keep_out_folder_with_spaces_stops_the_remote_claude_layer_and_fails_the_deploy(self):
        adds = (len(calls_named(self.scratch, "claude", "plugin", "marketplace", "add")),
                len(calls_named(self.scratch, "codex", "plugin", "marketplace", "add")))
        self.assertEqual((self.code != 0, adds), (True, (2, 1)))

    def test_the_mac_is_rolled_back_after_the_kept_out_remote_failure(self):
        self.assertEqual((marketplaces(self.scratch, "claude"), installed_plugins(self.scratch, "claude")), ({}, {}))


class RemoteFailureRollsTheMacBackTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        seed_foreign_codex_hook(cls.scratch)
        cls.scratch.fakes.fail_on("ssh")
        cls.code = cls.deploy(cls.scratch, cls.repo, "--host", REMOTE_HOST)

    def test_the_deploy_exits_non_zero_after_the_mac_install_passed(self):
        self.assertEqual((self.code != 0, len(calls_named(self.scratch, "codex", "plugin", "add"))), (True, 1))

    def test_no_plugin_and_no_marketplace_remain_on_the_mac(self):
        self.assertEqual((installed_plugins(self.scratch, "claude"), marketplaces(self.scratch, "claude"),
                          installed_plugins(self.scratch, "codex"), marketplaces(self.scratch, "codex")), ({},) * 4)

    def test_the_mac_codex_hooks_lose_every_adw_entry_and_keep_the_foreign_hook(self):
        self.assertEqual(hook_commands(self.scratch.codex_home / "hooks.json"), [("Stop", FOREIGN_CODEX)])


class UnstampedRemoteDeployTests(DeployCase):
    def test_a_remote_deploy_without_a_suite_stamp_makes_no_ssh_call_and_installs_nothing(self):
        scratch, repo = self.fixture(self.addCleanup, stamped=False)
        code = self.deploy(scratch, repo, "--host", REMOTE_HOST)
        self.assertEqual((code != 0, scratch.fakes.calls("ssh"), self.plugin_calls(scratch)), (True, [], []))


if __name__ == "__main__":
    unittest.main()
