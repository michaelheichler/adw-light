import json
import shlex
import shutil
import subprocess
import unittest

from t2325_fixtures import (CODEX_EVENTS, PLUGIN_ID, ROOT, Scratch, all_paths, built_version, hook_commands)

INSTALL = ROOT / "tools" / "install_layers.sh"
BUILD = ROOT / "tools" / "build_release.sh"
FOREIGN_CODEX = "echo foreign-codex"
FOREIGN_CLAUDE = "echo foreign-claude"


def command_group(command):
    return {"hooks": [{"type": "command", "command": command}]}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class BuiltRelease:
    def __init__(self, register_cleanup):
        self.scratch = Scratch(register_cleanup)
        self.path = self.scratch.root / "release"
        result = self.scratch.run(["bash", BUILD], ADW_RELEASE=str(self.path))
        assert result.returncode == 0, result.stdout + result.stderr
        self.version = built_version(self.path)

    def copy_with_version(self, version):
        target = self.scratch.root / ("release-" + version)
        shutil.copytree(self.path, target, ignore=shutil.ignore_patterns("__pycache__"))
        manifest = target / ".claude-plugin" / "plugin.json"
        write_json(manifest, {**json.loads(manifest.read_text(encoding="utf-8")), "version": version})
        return target


class InstallCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = BuiltRelease(cls.addClassCleanup)

    @classmethod
    def fresh_scratch(cls):
        return Scratch(cls.addClassCleanup)

    @staticmethod
    def install(scratch, *args):
        return scratch.run(["bash", INSTALL, *args], timeout=600).returncode

    @staticmethod
    def codex_cache_root(scratch):
        return scratch.codex_home / "plugins" / "cache" / "adw-light" / "adw-light"

    @staticmethod
    def claude_install_path(scratch):
        listed = scratch.run(["claude", "plugin", "list", "--json"], timeout=120)
        rows = [row for row in json.loads(listed.stdout) if row.get("id") == PLUGIN_ID]
        return [row.get("installPath") for row in rows]

    @staticmethod
    def codex_runners(scratch):
        return {(event, *shlex.split(command)[1:2]) for event, command in hook_commands(scratch.codex_home / "hooks.json")
                if "run.sh" in command}


class InstallRefusalTests(InstallCase):
    def setUp(self):
        self.scratch = Scratch(self.addCleanup)

    def written(self):
        return all_paths(self.scratch.home, self.scratch.config_dir, self.scratch.codex_home)

    def test_no_layer_flag_exits_non_zero_and_writes_nothing(self):
        code = self.install(self.scratch, self.release.path)
        self.assertEqual((code != 0, self.written()), (True, []))

    def test_no_release_folder_exits_non_zero_and_writes_nothing(self):
        code = self.install(self.scratch, "--claude", "--codex")
        self.assertEqual((code != 0, self.written()), (True, []))

    def test_an_unbuilt_release_folder_exits_non_zero_and_writes_nothing(self):
        empty = self.scratch.root / "not-a-release"
        empty.mkdir()
        code = self.install(self.scratch, "--claude", "--codex", empty)
        self.assertEqual((code != 0, self.written()), (True, []))

    def test_unknown_option_exits_non_zero_and_writes_nothing(self):
        code = self.install(self.scratch, "--claude", "--everything", self.release.path)
        self.assertEqual((code != 0, self.written()), (True, []))


class RetireRefusalTests(InstallCase):
    def setUp(self):
        self.scratch = Scratch(self.addCleanup)

    def retire_outcome(self, folder, entry="adw_light.py", **env):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / entry).write_text("", encoding="utf-8")
        code = self.scratch.run(["bash", INSTALL, "--claude", "--codex", "--retire", folder, self.release.path],
                                **env).returncode
        return code != 0, folder.is_dir(), all_paths(self.scratch.codex_home)

    def refused_before_install(self, folder, entry="adw_light.py"):
        return (*self.retire_outcome(folder, entry), all_paths(self.scratch.config_dir),
                (self.scratch.home / ".adw-lite").exists())

    def test_retire_of_a_folder_not_named_like_adw_light_is_refused_before_install(self):
        self.assertEqual(self.refused_before_install(self.scratch.root / "sample" / "notes"),
                         (True, True, [], [], False))

    def test_retire_of_an_adw_light_folder_without_an_adw_lite_copy_is_refused_before_install(self):
        self.assertEqual(self.refused_before_install(self.scratch.root / "sample" / "adw-light", "notes.txt"),
                         (True, True, [], [], False))

    def test_retire_of_an_adw_light_source_checkout_with_a_git_folder_is_refused_before_install(self):
        folder = self.scratch.root / "sample" / "adw-light"
        (folder / ".git").mkdir(parents=True)
        self.assertEqual(self.refused_before_install(folder), (True, True, [], [], False))

    def test_retire_of_an_adw_light_linked_worktree_with_a_git_file_is_refused_before_install(self):
        folder = self.scratch.root / "sample" / "adw-light"
        folder.mkdir(parents=True)
        (folder / ".git").write_text("gitdir: " + str(self.scratch.root / "repo" / ".git" / "worktrees" / "adw-light")
                                     + "\n", encoding="utf-8")
        self.assertEqual(self.refused_before_install(folder), (True, True, [], [], False))

    def test_retire_of_an_adw_light_folder_inside_the_adw_lite_home_is_refused_before_install(self):
        folder = self.scratch.home / ".adw-lite" / "adw-light"
        self.assertEqual((*self.retire_outcome(folder), all_paths(self.scratch.config_dir),
                          (self.scratch.home / ".adw-lite" / "install").exists()), (True, True, [], [], False))

    def test_retire_of_an_adw_light_folder_that_holds_the_claude_config_dir_is_refused_before_install(self):
        folder = self.scratch.root / "sample" / "adw-light"
        config_dir = folder / "claude-config"
        self.assertEqual((*self.retire_outcome(folder, CLAUDE_CONFIG_DIR=str(config_dir)), config_dir.exists(),
                          (self.scratch.home / ".adw-lite").exists()), (True, True, [], False, False))


class SymlinkedHostFolderRetireTests(InstallCase):
    @staticmethod
    def linked(scratch, name, real):
        real.mkdir(parents=True, exist_ok=True)
        link = scratch.root / name
        link.symlink_to(real, target_is_directory=True)
        return link

    def retire_refused(self, scratch, folder, env):
        folder.mkdir(parents=True)
        (folder / "adw_light.py").write_text("", encoding="utf-8")
        result = subprocess.run(["bash", str(INSTALL), "--claude", "--codex", "--retire", str(folder),
                                 str(self.release.path)], capture_output=True, text=True, check=False,
                                cwd=str(scratch.root), env=env, timeout=600)
        return result.returncode != 0, folder.is_dir(), (scratch.home / ".adw-lite").exists()

    def test_retire_inside_a_claude_config_dir_reached_through_a_link_is_refused_before_install(self):
        scratch = Scratch(self.addCleanup)
        link = self.linked(scratch, "claude-link", scratch.root / "real" / ".claude")
        folder = link / "plugins" / "marketplaces" / "adw-light"
        self.assertEqual(self.retire_refused(scratch, folder, scratch.env(CLAUDE_CONFIG_DIR=str(link))),
                         (True, True, False))

    def test_retire_inside_a_codex_home_reached_through_a_link_is_refused_before_install(self):
        scratch = Scratch(self.addCleanup)
        link = self.linked(scratch, "codex-link", scratch.root / "real" / ".codex")
        folder = link / "plugins" / "marketplaces" / "adw-light"
        self.assertEqual(self.retire_refused(scratch, folder, scratch.env(CODEX_HOME=str(link))), (True, True, False))

    def test_retire_inside_the_default_claude_folder_of_a_linked_home_is_refused_before_install(self):
        scratch = Scratch(self.addCleanup)
        link = self.linked(scratch, "home-link", scratch.home)
        folder = link / ".claude" / "plugins" / "marketplaces" / "adw-light"
        env = {key: value for key, value in scratch.env(HOME=str(link)).items()
               if key not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        self.assertEqual(self.retire_refused(scratch, folder, env), (True, True, False))


class RetireWithAbsentHostFoldersTests(InstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scratch = cls.fresh_scratch()
        cls.retired = cls.scratch.root / "sample" / "old-copies" / "adw-light"
        cls.retired.mkdir(parents=True)
        (cls.retired / "adw_light.py").write_text("", encoding="utf-8")
        cls.code = cls.scratch.run(["bash", INSTALL, "--claude", "--retire", cls.retired, cls.release.path],
                                   CLAUDE_CONFIG_DIR=str(cls.scratch.root / "absent-claude"),
                                   CODEX_HOME=str(cls.scratch.root / "absent-codex")).returncode

    def test_a_named_old_copy_outside_every_guarded_folder_is_retired_when_the_host_folders_are_absent(self):
        self.assertEqual((self.code, self.retired.exists()), (0, False))


class KeptOutPluginCacheTests(InstallCase):
    def test_a_claude_plugin_cache_inside_an_adw_keep_out_folder_stops_the_install_before_codex(self):
        scratch = Scratch(self.addCleanup)
        code = scratch.run(["bash", INSTALL, "--claude", "--codex", self.release.path],
                           ADW_KEEP_OUT=str(scratch.config_dir)).returncode
        self.assertEqual((code != 0, all_paths(scratch.codex_home)), (True, []))


class FailingReleaseSmokeTests(InstallCase):
    def broken_copy(self, suffix):
        broken = self.release.copy_with_version(self.release.version + suffix)
        (broken / "adw_light.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
        return broken

    def test_a_release_that_fails_smoke_is_refused_before_anything_is_placed(self):
        scratch = Scratch(self.addCleanup)
        code = self.install(scratch, "--claude", "--codex", self.broken_copy(".broken-first"))
        self.assertEqual((code != 0, (scratch.home / ".adw-lite").exists(),
                          all_paths(scratch.config_dir, scratch.codex_home)), (True, False, []))

    def test_a_release_that_fails_smoke_leaves_the_installed_release_in_place(self):
        scratch = Scratch(self.addCleanup)
        first = self.install(scratch, "--claude", "--codex", self.release.path)
        second = self.install(scratch, "--claude", "--codex", self.broken_copy(".broken-upgrade"))
        source = scratch.home / ".adw-lite" / "install" / "source"
        cached = sorted(path.name for path in self.codex_cache_root(scratch).iterdir())
        self.assertEqual((first, second != 0, built_version(source), cached),
                         (0, True, self.release.version, [self.release.version]))


class SeededInstallCase(InstallCase):
    @classmethod
    def seed(cls, scratch):
        development = scratch.home / "Development"
        retired = scratch.root / "sample" / "old-copies" / "adw-light"
        kept_source = development / "projects" / "adw-light"
        for folder in (retired, kept_source):
            folder.mkdir(parents=True)
            (folder / "adw_light.py").write_text("", encoding="utf-8")
        stale_runner = shlex.quote(str(development / "agent-tools" / "adw-light-plugin" / "20261120T174612Z-ab"
                                       / "hooks" / "run.sh"))
        legacy = "python3 -I " + shlex.quote(str(retired / "adw_light.py")) + " Stop --host codex"
        write_json(scratch.codex_home / "hooks.json", {"hooks": {
            "Stop": [command_group("ADW_CODEX_HOOK=1 " + stale_runner + " Stop"), command_group(legacy),
                     command_group(FOREIGN_CODEX)]}})
        write_json(scratch.config_dir / "settings.json", {"hooks": {
            "Stop": [command_group(shlex.quote(str(kept_source / "hooks" / "run.sh")) + " Stop"),
                     command_group(FOREIGN_CLAUDE)]}})
        return retired, kept_source


class SeededInstallTests(SeededInstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scratch = cls.fresh_scratch()
        cls.retired, cls.kept_source = cls.seed(cls.scratch)
        cls.code = cls.install(cls.scratch, "--claude", "--codex", "--retire", cls.retired, cls.release.path)
        cls.cache = cls.codex_cache_root(cls.scratch) / cls.release.version

    def test_install_exits_zero(self):
        self.assertEqual(self.code, 0)

    def test_release_is_placed_as_the_marketplace_source_in_the_adw_lite_home(self):
        source = self.scratch.home / ".adw-lite" / "install" / "source"
        self.assertEqual(built_version(source), self.release.version)

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

    def test_the_named_old_copy_is_retired_and_the_unnamed_source_folder_is_kept(self):
        self.assertEqual((self.retired.exists(), self.kept_source.is_dir()), (False, True))

    def test_claude_runs_the_plugin_from_its_config_dir_cache(self):
        expected = str(self.scratch.config_dir / "plugins" / "cache" / "adw-light" / "adw-light" / self.release.version)
        self.assertEqual(self.claude_install_path(self.scratch), [expected])


class ReinstallTests(SeededInstallCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scratch = cls.fresh_scratch()
        cls.seed(cls.scratch)
        cls.first_code = cls.install(cls.scratch, "--claude", "--codex", cls.release.path)
        cls.second_version = cls.release.version + ".second"
        cls.second_code = cls.install(cls.scratch, "--claude", "--codex",
                                      cls.release.copy_with_version(cls.second_version))

    def test_both_runs_exit_zero(self):
        self.assertEqual((self.first_code, self.second_code), (0, 0))

    def test_codex_cache_holds_only_the_new_version(self):
        self.assertEqual(sorted(path.name for path in self.codex_cache_root(self.scratch).iterdir()),
                         [self.second_version])

    def test_codex_hooks_point_at_the_new_version_only(self):
        runner = str(self.codex_cache_root(self.scratch) / self.second_version / "hooks" / "run.sh")
        self.assertEqual(self.codex_runners(self.scratch), {(event, runner) for event in CODEX_EVENTS})

    def test_claude_runs_the_new_version(self):
        expected = str(self.scratch.config_dir / "plugins" / "cache" / "adw-light" / "adw-light" / self.second_version)
        self.assertEqual(self.claude_install_path(self.scratch), [expected])


if __name__ == "__main__":
    unittest.main()
