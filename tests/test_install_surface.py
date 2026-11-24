import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "adw_light.py"
BUILD = ROOT / "tools" / "build_release.sh"
SCRUBBED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CONFIG_DIR", "ADW_STATE_ROOT", "ADW_CODEX_HOOK", "ADW_PYTHON",
                "ADW_ALLOW_PROTECTED_EDIT", "ADW_RELEASE")
DENY = "deny"
INSTALL_SURFACE = "watcher_install_surface"
STATE_MUTATION = "state_mutation"


def isolated_env(home, **extra):
    env = {key: value for key, value in os.environ.items() if key not in SCRUBBED_ENV}
    return {**env, "HOME": str(home), **extra}


class GuardCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.home = self.scratch / "home"
        self.work = self.scratch / "work"
        self.state = self.scratch / "state"
        self.plugin_root = self.scratch / "plugin-root"
        self.home.mkdir()
        self.work.mkdir()

    def pre_tool(self, tool_name, tool_input, **env):
        payload = {"session_id": "guard-case", "cwd": str(self.work), "hook_event_name": "PreToolUse",
                   "tool_name": tool_name, "tool_input": tool_input}
        result = subprocess.run([sys.executable, "-ISB", str(ENTRY), "PreToolUse", "--host", "claude",
                                 "--state-dir", str(self.state)], input=json.dumps(payload), capture_output=True,
                                text=True, check=False, cwd=str(self.work), env=isolated_env(self.home, **env),
                                timeout=60)
        return result.returncode, json.loads(result.stdout)

    def write_tool(self, path, **env):
        return self.pre_tool("Write", {"file_path": str(path), "content": "Short text.\n"}, **env)

    def bash_write(self, path, **env):
        return self.pre_tool("Bash", {"command": "printf 'x\\n' > " + shlex.quote(str(path))}, **env)

    def blocked_rules(self):
        ledger = self.state / "ledger" / "ledger.jsonl"
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        return {row["rule"] for row in rows if row.get("outcome") == "block"}

    def verdict(self, outcome):
        code, response = outcome
        decision = response.get("hookSpecificOutput", {}).get("permissionDecision")
        return code, decision, self.blocked_rules()

    def assert_allowed(self, outcome):
        self.assertEqual(outcome, (0, {}))

    def assert_denied_by(self, outcome, rule):
        self.assertEqual(self.verdict(outcome), (0, DENY, {rule}))


class SourceFoldersStayWritableTests(GuardCase):
    def test_write_tool_into_the_adw_light_source_folder_is_allowed(self):
        self.assert_allowed(self.write_tool(self.home / "Development/projects/adw-light/notes.md"))

    def test_bash_write_into_the_adw_light_source_folder_is_allowed(self):
        self.assert_allowed(self.bash_write(self.home / "Development/projects/adw-light/notes.md"))

    def test_write_tool_into_the_adw_light_plugin_folder_is_allowed(self):
        self.assert_allowed(self.write_tool(self.home / "Development/projects/adw-light-plugin/notes.md"))

    def test_bash_write_into_the_adw_light_plugin_folder_is_allowed(self):
        self.assert_allowed(self.bash_write(self.home / "Development/projects/adw-light-plugin/notes.md"))

    def test_write_tool_beside_an_unset_plugin_root_is_allowed(self):
        self.assert_allowed(self.write_tool(self.plugin_root / "notes.md"))


class PluginCopiesStayProtectedTests(GuardCase):
    def test_write_tool_into_the_claude_plugin_cache_is_denied(self):
        self.assert_denied_by(self.write_tool(self.home / ".claude/plugins/cache/adw-light/adw-light/1/notes.md"),
                              INSTALL_SURFACE)

    def test_bash_write_into_the_claude_plugin_cache_is_denied(self):
        self.assert_denied_by(self.bash_write(self.home / ".claude/plugins/cache/adw-light/adw-light/1/notes.md"),
                              INSTALL_SURFACE)

    def test_write_tool_into_the_claude_marketplace_copy_is_denied(self):
        self.assert_denied_by(self.write_tool(self.home / ".claude/plugins/marketplaces/adw-light/notes.md"),
                              INSTALL_SURFACE)

    def test_bash_write_into_the_claude_marketplace_copy_is_denied(self):
        self.assert_denied_by(self.bash_write(self.home / ".claude/plugins/marketplaces/adw-light/notes.md"),
                              INSTALL_SURFACE)

    def test_write_tool_into_the_config_dir_plugin_cache_is_denied(self):
        self.assert_denied_by(self.write_tool(self.home / ".config/claude-code/plugins/cache/adw-light/notes.md"),
                              INSTALL_SURFACE)

    def test_bash_write_into_the_config_dir_plugin_cache_is_denied(self):
        self.assert_denied_by(self.bash_write(self.home / ".config/claude-code/plugins/cache/adw-light/notes.md"),
                              INSTALL_SURFACE)

    def test_write_tool_into_the_config_dir_marketplace_copy_is_denied(self):
        self.assert_denied_by(
            self.write_tool(self.home / ".config/claude-code/plugins/marketplaces/adw-light/notes.md"), INSTALL_SURFACE)

    def test_bash_write_into_the_config_dir_marketplace_copy_is_denied(self):
        self.assert_denied_by(
            self.bash_write(self.home / ".config/claude-code/plugins/marketplaces/adw-light/notes.md"), INSTALL_SURFACE)


class ConfigDirPluginCopiesStayProtectedTests(GuardCase):
    def setUp(self):
        super().setUp()
        self.config_dir = self.scratch / "cfg"

    def with_config_dir(self):
        return {"CLAUDE_CONFIG_DIR": str(self.config_dir)}

    def test_write_tool_into_the_custom_config_plugin_cache_is_denied(self):
        self.assert_denied_by(self.write_tool(self.config_dir / "plugins/cache/adw-light/adw-light/1/notes.md",
                                              **self.with_config_dir()), INSTALL_SURFACE)

    def test_bash_write_into_the_custom_config_plugin_cache_is_denied(self):
        self.assert_denied_by(self.bash_write(self.config_dir / "plugins/cache/adw-light/adw-light/1/notes.md",
                                              **self.with_config_dir()), INSTALL_SURFACE)

    def test_write_tool_into_the_custom_config_marketplace_copy_is_denied(self):
        self.assert_denied_by(self.write_tool(self.config_dir / "plugins/marketplaces/adw-light/notes.md",
                                              **self.with_config_dir()), INSTALL_SURFACE)

    def test_bash_write_into_the_custom_config_marketplace_copy_is_denied(self):
        self.assert_denied_by(self.bash_write(self.config_dir / "plugins/marketplaces/adw-light/notes.md",
                                              **self.with_config_dir()), INSTALL_SURFACE)

    def test_write_tool_into_the_home_claude_cache_stays_denied_beside_a_custom_config(self):
        self.assert_denied_by(self.write_tool(self.home / ".claude/plugins/cache/adw-light/adw-light/1/notes.md",
                                              **self.with_config_dir()), INSTALL_SURFACE)

    def test_bash_write_into_the_home_claude_marketplace_stays_denied_beside_a_custom_config(self):
        self.assert_denied_by(self.bash_write(self.home / ".claude/plugins/marketplaces/adw-light/notes.md",
                                              **self.with_config_dir()), INSTALL_SURFACE)

    def test_write_tool_into_the_home_config_dir_cache_stays_denied_beside_a_custom_config(self):
        self.assert_denied_by(self.write_tool(self.home / ".config/claude-code/plugins/cache/adw-light/notes.md",
                                              **self.with_config_dir()), INSTALL_SURFACE)

    def test_bash_write_into_the_home_config_dir_marketplace_stays_denied_beside_a_custom_config(self):
        self.assert_denied_by(
            self.bash_write(self.home / ".config/claude-code/plugins/marketplaces/adw-light/notes.md",
                            **self.with_config_dir()), INSTALL_SURFACE)


class RunningInstallStaysProtectedTests(GuardCase):
    def test_write_tool_into_the_plugin_root_is_denied(self):
        self.assert_denied_by(self.write_tool(self.plugin_root / "notes.md", CLAUDE_PLUGIN_ROOT=str(self.plugin_root)),
                              INSTALL_SURFACE)

    def test_bash_write_into_the_plugin_root_is_denied(self):
        self.assert_denied_by(self.bash_write(self.plugin_root / "notes.md", CLAUDE_PLUGIN_ROOT=str(self.plugin_root)),
                              INSTALL_SURFACE)

    def test_write_tool_into_the_local_share_install_is_denied(self):
        self.assert_denied_by(self.write_tool(self.home / ".local/share/adw-light/notes.md"), INSTALL_SURFACE)

    def test_bash_write_into_the_local_share_install_is_denied(self):
        self.assert_denied_by(self.bash_write(self.home / ".local/share/adw-light/notes.md"), INSTALL_SURFACE)

    def test_write_tool_into_the_engine_tree_is_denied(self):
        self.assert_denied_by(self.write_tool(ROOT / "engine" / f"probe-{uuid.uuid4().hex}.md"), INSTALL_SURFACE)


class StateFoldersStayProtectedTests(GuardCase):
    def test_write_tool_into_the_state_dir_is_denied(self):
        self.assert_denied_by(self.write_tool(self.state / "ledger" / "ledger.jsonl"), STATE_MUTATION)

    def test_bash_write_into_the_state_dir_is_denied(self):
        self.assert_denied_by(self.bash_write(self.state / "ledger" / "ledger.jsonl"), STATE_MUTATION)

    def test_write_tool_into_the_default_state_folder_is_denied(self):
        self.assert_denied_by(self.write_tool(self.home / ".local/state/adw-lite/notes.json"), STATE_MUTATION)

    def test_bash_write_into_the_default_state_folder_is_denied(self):
        self.assert_denied_by(self.bash_write(self.home / ".local/state/adw-lite/notes.json"), STATE_MUTATION)


class ReleaseTargetRefusalTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.home = self.scratch / "home"
        self.home.mkdir()

    def build(self, **env):
        return subprocess.run(["bash", str(BUILD)], capture_output=True, text=True, check=False, timeout=120,
                              env=isolated_env(self.home, **env)).returncode

    def test_build_without_adw_release_exits_non_zero(self):
        self.assertNotEqual(self.build(), 0)

    def test_build_into_an_existing_target_exits_non_zero_and_leaves_it_empty(self):
        target = self.scratch / "existing"
        target.mkdir()
        code = self.build(ADW_RELEASE=str(target))
        self.assertEqual((code != 0, list(target.iterdir())), (True, []))

    def refused_inside_kept_out(self, kept_out, parent):
        parent.mkdir(parents=True)
        code = self.build(ADW_RELEASE=str(parent / "release"), ADW_KEEP_OUT=kept_out)
        return code != 0, list(parent.iterdir())

    def test_build_under_the_folder_adw_keep_out_names_exits_non_zero_and_creates_nothing(self):
        kept_out = self.scratch / "kept-out"
        self.assertEqual(self.refused_inside_kept_out(str(kept_out), kept_out / "builds"), (True, []))

    def test_build_under_the_second_of_several_adw_keep_out_folders_exits_non_zero_and_creates_nothing(self):
        kept_out = self.scratch / "second"
        listed = str(self.scratch / "first") + ":" + str(kept_out)
        self.assertEqual(self.refused_inside_kept_out(listed, kept_out / "builds"), (True, []))

    def test_build_under_a_folder_reached_through_a_link_named_in_adw_keep_out_exits_non_zero(self):
        real = self.scratch / "real-kept-out"
        link = self.scratch / "kept-out-link"
        real.mkdir()
        link.symlink_to(real, target_is_directory=True)
        self.assertEqual(self.refused_inside_kept_out(str(link), real / "builds"), (True, []))

    def test_build_inside_the_source_tree_exits_non_zero_and_creates_nothing(self):
        link = self.scratch / "link"
        link.symlink_to(ROOT, target_is_directory=True)
        target = link / f".release-probe-{uuid.uuid4().hex}"
        self.addCleanup(shutil.rmtree, target, True)
        code = self.build(ADW_RELEASE=str(target))
        self.assertEqual((code != 0, target.exists()), (True, False))


if __name__ == "__main__":
    unittest.main()
