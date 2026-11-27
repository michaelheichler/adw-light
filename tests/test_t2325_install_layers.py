import json
import shlex
import unittest
from pathlib import Path

from t2325_fixtures import (CODEX_ADW_HOOK_EVENTS, CODEX_EVENTS, DEFAULT_REPO, FORK_REPO, PLUGIN_ID, RUNTIME_TOP, SMOKE_FAILURE, SMOKE_PROBE,
                            ProcessFixture, Scratch, adw_hook_events, all_paths, built_version, commit_version,
                            copy_source_repo, cut_release, hook_commands, installed_plugins, link_github, marketplaces,
                            install_flow_probe, next_version, no_venv_python, publish, recorded_smoke_runs, shipped_leaks,
                            source_version, stand_in_github, tree_names)

FOREIGN_CODEX = "echo foreign-codex"
FOREIGN_CLAUDE = "echo foreign-claude"
UNPUBLISHED_TAG = "v9.9.9"
UNREACHABLE_TAG_EXIT = 69


def claude_calls(slug, tag):
    return [["claude", "plugin", "marketplace", "list", "--json"],
            ["claude", "plugin", "marketplace", "add", f"{slug}#{tag}", "--scope", "user"],
            ["claude", "plugin", "install", PLUGIN_ID, "--scope", "user"], ["claude", "plugin", "list", "--json"]]


def codex_calls(slug, tag):
    return [["codex", "plugin", "marketplace", "list", "--json"],
            ["codex", "plugin", "marketplace", "add", slug, "--ref", tag, "--json"],
            ["codex", "plugin", "add", PLUGIN_ID, "--json"]]


def command_group(command):
    return {"hooks": [{"type": "command", "command": command}]}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class PublishedRelease:
    def __init__(self, register_cleanup, probe=None):
        self.scratch = Scratch(register_cleanup)
        self.repo = self.scratch.root / "repo"
        copy_source_repo(self.scratch, self.repo, {SMOKE_PROBE: probe} if probe else None, skip_top=("tests",))
        self.installer = self.repo / "tools" / "install_layers.sh"
        stand_in_github(self.scratch)
        self.version = source_version(self.repo)
        self.tag = cut_release(self.scratch, self.repo)
        self.next_version = next_version(self.version)
        commit_version(self.scratch, self.repo, self.next_version)
        self.next_tag = cut_release(self.scratch, self.repo)
        publish(self.scratch, self.repo, DEFAULT_REPO, f"refs/tags/{self.next_tag}:refs/heads/release", "refs/tags/" + self.tag,
                "refs/tags/" + self.next_tag)
        link_github(self.scratch, FORK_REPO)

    def host(self, register_cleanup):
        return Scratch(register_cleanup, self.scratch.fakes.github)

    def fail_smoke(self, case):
        marker = self.repo.parent / SMOKE_FAILURE
        marker.touch()
        case.addCleanup(marker.unlink)

    def smoke_runs_from(self, home_root):
        return [(Path(run["tree"]).resolve(), Path(run["home"]).resolve() == home_root / "home")
                for run in recorded_smoke_runs(self.repo) if Path(run["tree"]).resolve().is_relative_to(home_root)]


def small_probe_release(register_cleanup):
    return PublishedRelease(register_cleanup, install_flow_probe())


RECORDED_RELEASE = ProcessFixture(PublishedRelease)
SMALL_PROBE_RELEASE = ProcessFixture(small_probe_release)


class InstallCase(unittest.TestCase):
    release_fixture = RECORDED_RELEASE

    @classmethod
    def setUpClass(cls):
        cls.release = cls.release_fixture.get()

    @classmethod
    def fresh_scratch(cls):
        return cls.release.host(cls.addClassCleanup)

    @classmethod
    def install(cls, scratch, *args, **env):
        env = {"ADW_PYTHON": no_venv_python(scratch), **env}
        return scratch.run(["bash", cls.release.installer, *args], timeout=600, **env).returncode

    @staticmethod
    def claude_cache(scratch, version):
        return scratch.config_dir / "plugins" / "cache" / "adw-light" / "adw-light" / version

    @staticmethod
    def codex_cache(scratch, version):
        return scratch.codex_home / "plugins" / "cache" / "adw-light" / "adw-light" / version

    @staticmethod
    def plugin_installs(scratch):
        installs = [call for call in scratch.fakes.calls("claude") if call[1:3] == ["plugin", "install"]]
        return installs + [call for call in scratch.fakes.calls("codex") if call[1:3] == ["plugin", "add"]]

    @staticmethod
    def codex_runners(scratch):
        return {(event, *shlex.split(command)[1:2]) for event, command in hook_commands(scratch.codex_home / "hooks.json")
                if "run.sh" in command}


class InstallRefusalTests(InstallCase):
    def setUp(self):
        self.scratch = self.release.host(self.addCleanup)

    def refused(self, *args):
        code = self.install(self.scratch, *args)
        written = all_paths(self.scratch.home, self.scratch.config_dir, self.scratch.codex_home)
        return code != 0, written, self.scratch.fakes.calls("claude") + self.scratch.fakes.calls("codex")

    def test_no_layer_flag_exits_non_zero_and_writes_nothing(self):
        self.assertEqual(self.refused("--tag", self.release.tag), (True, [], []))

    def test_no_tag_exits_non_zero_and_writes_nothing(self):
        self.assertEqual(self.refused("--claude", "--codex"), (True, [], []))

    def test_a_tag_without_the_v_prefix_exits_non_zero_and_writes_nothing(self):
        self.assertEqual(self.refused("--claude", "--codex", "--tag", self.release.version), (True, [], []))

    def test_a_release_folder_argument_exits_non_zero_and_writes_nothing(self):
        self.assertEqual(self.refused("--claude", "--codex", "--tag", self.release.tag, self.scratch.root), (True, [], []))

    def test_a_repository_slug_that_names_a_local_folder_exits_non_zero_and_writes_nothing(self):
        (self.scratch.root / FORK_REPO).mkdir(parents=True)
        self.assertEqual(self.refused("--claude", "--codex", "--repo", FORK_REPO, "--tag", self.release.tag),
                         (True, [], []))

    def test_a_tag_missing_on_github_exits_with_the_unreachable_status_and_writes_nothing(self):
        code = self.install(self.scratch, "--claude", "--codex", "--tag", UNPUBLISHED_TAG)
        self.assertEqual((code, all_paths(self.scratch.home, self.scratch.config_dir, self.scratch.codex_home)),
                         (UNREACHABLE_TAG_EXIT, []))


class KeptOutPluginCacheTests(InstallCase):
    def test_a_claude_plugin_cache_inside_an_adw_keep_out_folder_stops_the_install_before_codex(self):
        scratch = self.release.host(self.addCleanup)
        code = self.install(scratch, "--claude", "--codex", "--tag", self.release.tag, ADW_KEEP_OUT=str(scratch.config_dir))
        self.assertEqual((code != 0, scratch.fakes.calls("codex")), (True, []))


class FailingCacheSmokeTests(InstallCase):
    def test_a_claude_cache_that_fails_smoke_stops_the_install_before_codex(self):
        scratch = self.release.host(self.addCleanup)
        self.release.fail_smoke(self)
        code = self.install(scratch, "--claude", "--codex", "--tag", self.release.tag)
        self.assertEqual((code != 0, scratch.fakes.calls("codex")), (True, []))


class SeededInstallCase(InstallCase):
    @classmethod
    def seed(cls, scratch):
        development = scratch.home / "Development"
        kept_source = development / "projects" / "adw-light"
        kept_source.mkdir(parents=True)
        stale_runner = shlex.quote(str(development / "agent-tools" / "adw-light-plugin" / "20261120T174612Z-ab"
                                       / "hooks" / "run.sh"))
        legacy = "python3 -I " + shlex.quote(str(kept_source / "adw_light.py")) + " Stop --host codex"
        write_json(scratch.codex_home / "hooks.json", {"hooks": {
            "Stop": [command_group("ADW_CODEX_HOOK=1 " + stale_runner + " Stop"), command_group(legacy),
                     command_group(FOREIGN_CODEX)]}})
        write_json(scratch.config_dir / "settings.json", {"hooks": {
            "Stop": [command_group(shlex.quote(str(kept_source / "hooks" / "run.sh")) + " Stop"),
                     command_group(FOREIGN_CLAUDE)]}})


class FirstInstallTests(SeededInstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scratch = cls.fresh_scratch()
        cls.seed(cls.scratch)
        cls.code = cls.install(cls.scratch, "--claude", "--codex", "--tag", cls.release.tag)
        cls.cache = cls.codex_cache(cls.scratch, cls.release.version)

    def test_install_exits_zero(self):
        self.assertEqual(self.code, 0)

    def test_claude_adds_the_default_github_repo_at_the_tag_then_installs_the_plugin_once(self):
        self.assertEqual(self.scratch.fakes.calls("claude"), claude_calls(DEFAULT_REPO, self.release.tag))

    def test_codex_adds_the_default_github_repo_at_the_tag_then_adds_the_plugin_once(self):
        self.assertEqual(self.scratch.fakes.calls("codex"), codex_calls(DEFAULT_REPO, self.release.tag))

    def test_both_layers_install_the_tagged_version(self):
        installed = (installed_plugins(self.scratch, "claude"), installed_plugins(self.scratch, "codex"))
        self.assertEqual(installed, ({PLUGIN_ID: self.release.version},) * 2)

    def test_the_claude_cache_holds_exactly_the_runtime_entries(self):
        cache = self.claude_cache(self.scratch, self.release.version)
        self.assertEqual({path.name for path in cache.iterdir()}, RUNTIME_TOP)

    def test_the_claude_cache_holds_no_test_tool_doc_agent_or_dot_path(self):
        self.assertEqual(shipped_leaks(tree_names(self.claude_cache(self.scratch, self.release.version))), [])

    def test_the_claude_cache_ships_the_engine_license_at_the_top(self):
        cache = self.claude_cache(self.scratch, self.release.version)
        self.assertEqual((cache / "LICENSE").read_bytes(), (self.release.repo / "engine" / "LICENSE").read_bytes())

    def test_the_claude_plugin_manifest_carries_the_version_file_value(self):
        self.assertEqual(built_version(self.claude_cache(self.scratch, self.release.version)), self.release.version)

    def test_the_codex_plugin_points_its_hooks_at_a_file_with_no_hooks(self):
        manifest = json.loads((self.cache / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        hooks = json.loads((self.cache / manifest["hooks"]).read_text(encoding="utf-8"))
        self.assertEqual(hooks, {"hooks": {}})

    def test_codex_hooks_run_only_from_the_codex_plugin_cache(self):
        expected = {(event, str(self.cache / "hooks" / "run.sh")) for event in CODEX_EVENTS}
        self.assertEqual(self.codex_runners(self.scratch), expected)

    def test_no_hook_command_in_either_layer_points_into_development(self):
        commands = hook_commands(self.scratch.codex_home / "hooks.json") + hook_commands(
            self.scratch.config_dir / "settings.json")
        self.assertEqual([command for _, command in commands if "/Development/" in command], [])

    def test_foreign_hooks_survive_in_both_layers(self):
        kept = {command for _, command in hook_commands(self.scratch.codex_home / "hooks.json")
                + hook_commands(self.scratch.config_dir / "settings.json")}
        self.assertEqual({FOREIGN_CODEX, FOREIGN_CLAUDE} - kept, set())

    def test_claude_settings_keep_only_the_foreign_hook(self):
        self.assertEqual(hook_commands(self.scratch.config_dir / "settings.json"), [("Stop", FOREIGN_CLAUDE)])

    def test_each_cache_gets_a_throwaway_home_smoke_then_a_user_home_smoke(self):
        claude = self.claude_cache(self.scratch, self.release.version)
        self.assertEqual(self.release.smoke_runs_from(self.scratch.root),
                         [(claude, False), (claude, True), (self.cache, False), (self.cache, True)])


class UpgradeInstallTests(SeededInstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scratch = cls.fresh_scratch()
        cls.seed(cls.scratch)
        cls.first_code = cls.install(cls.scratch, "--claude", "--codex", "--tag", cls.release.tag)
        first_runs = {name: len(cls.scratch.fakes.calls(name)) for name in ("claude", "codex")}
        cls.second_code = cls.install(cls.scratch, "--claude", "--codex", "--tag", cls.release.next_tag)
        cls.second_calls = {name: cls.scratch.fakes.calls(name)[count:] for name, count in first_runs.items()}

    def test_both_runs_exit_zero(self):
        self.assertEqual((self.first_code, self.second_code), (0, 0))

    def test_the_second_install_removes_the_claude_marketplace_and_adds_it_at_the_new_tag(self):
        expected = claude_calls(DEFAULT_REPO, self.release.next_tag)
        self.assertEqual(self.second_calls["claude"],
                         [expected[0], ["claude", "plugin", "marketplace", "remove", "adw-light"], *expected[1:]])

    def test_the_second_install_removes_the_codex_marketplace_and_adds_it_at_the_new_tag(self):
        expected = codex_calls(DEFAULT_REPO, self.release.next_tag)
        self.assertEqual(self.second_calls["codex"],
                         [expected[0], ["codex", "plugin", "marketplace", "remove", "adw-light"], *expected[1:]])

    def test_codex_hooks_hold_exactly_one_adw_hook_set_after_two_installs(self):
        self.assertEqual(adw_hook_events(self.scratch.codex_home / "hooks.json"), CODEX_ADW_HOOK_EVENTS)

    def test_codex_hooks_point_at_the_new_version_only(self):
        runner = str(self.codex_cache(self.scratch, self.release.next_version) / "hooks" / "run.sh")
        self.assertEqual(self.codex_runners(self.scratch), {(event, runner) for event in CODEX_EVENTS})

    def test_the_foreign_codex_hook_survives_both_installs(self):
        commands = [command for _, command in hook_commands(self.scratch.codex_home / "hooks.json")]
        self.assertEqual(commands.count(FOREIGN_CODEX), 1)


class RepoOverrideInstallTests(InstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scratch = cls.fresh_scratch()
        cls.code = cls.install(cls.scratch, "--claude", "--codex", "--repo", FORK_REPO, "--tag", cls.release.tag)

    def test_claude_adds_the_named_repository_at_the_tag(self):
        self.assertEqual((self.code, self.scratch.fakes.calls("claude")), (0, claude_calls(FORK_REPO, self.release.tag)))

    def test_codex_adds_the_named_repository_at_the_tag(self):
        self.assertEqual(self.scratch.fakes.calls("codex"), codex_calls(FORK_REPO, self.release.tag))

    def test_both_marketplaces_record_the_named_repository(self):
        self.assertEqual((marketplaces(self.scratch, "claude"), marketplaces(self.scratch, "codex")),
                         ({"adw-light": (FORK_REPO, self.release.tag)},) * 2)


class UnpublishedUpgradeTests(InstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scratch = cls.fresh_scratch()
        cls.first_code = cls.install(cls.scratch, "--claude", "--codex", "--tag", cls.release.tag)
        cls.calls_before = len(cls.scratch.fakes.calls("claude")) + len(cls.scratch.fakes.calls("codex"))
        cls.second_code = cls.install(cls.scratch, "--claude", "--codex", "--tag", UNPUBLISHED_TAG)

    def test_an_unpublished_tag_stops_before_either_marketplace_is_touched(self):
        calls = len(self.scratch.fakes.calls("claude")) + len(self.scratch.fakes.calls("codex"))
        self.assertEqual((self.first_code, self.second_code, calls), (0, UNREACHABLE_TAG_EXIT, self.calls_before))

    def test_both_marketplaces_keep_the_installed_tag(self):
        self.assertEqual((marketplaces(self.scratch, "claude"), marketplaces(self.scratch, "codex")),
                         ({"adw-light": (DEFAULT_REPO, self.release.tag)},) * 2)


if __name__ == "__main__":
    unittest.main()
