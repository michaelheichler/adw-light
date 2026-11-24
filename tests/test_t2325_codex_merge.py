import json
import shlex
import shutil
import sys
import unittest

from t2325_fixtures import CODEX_EVENTS, ROOT, Scratch, hook_commands

MERGER = ROOT / "hooks" / "merge-codex-hooks.py"
CLAUDE_MERGER = ROOT / "hooks" / "merge-claude-settings.py"


def command_group(command):
    return {"hooks": [{"type": "command", "command": command}]}


class CodexMergeCase(unittest.TestCase):
    def setUp(self):
        self.scratch = Scratch(self.addCleanup)
        self.skill = self.scratch.root / "skill"
        shutil.copytree(ROOT / "hooks", self.skill / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
        self.hooks_json = self.scratch.codex_home / "hooks.json"
        self.foreign = "echo foreign"

    def seed(self, stale_runner):
        stale = "ADW_CODEX_HOOK=1 " + shlex.quote(str(stale_runner)) + " Stop"
        data = {"hooks": {"Stop": [command_group(stale), command_group(self.foreign)]}}
        self.hooks_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def merge(self):
        return self.scratch.run([sys.executable, "-I", MERGER, "--hooks-json", self.hooks_json,
                                 "--skill-dir", self.skill]).returncode

    def merged_words(self):
        return sorted(shlex.split(command) for _, command in hook_commands(self.hooks_json))

    def expected_words(self, *extra):
        runner = str(self.skill / "hooks" / "run.sh")
        return sorted([["ADW_CODEX_HOOK=1", runner, event] for event in CODEX_EVENTS] + [list(words) for words in extra])

    def stamped_runner(self, *parts):
        return self.scratch.root.joinpath("x", "Development", "agent-tools", *parts, "hooks", "run.sh")


class StaleAdwRunnersAreReplacedTests(CodexMergeCase):
    def test_a_stamped_plugin_copy_runner_is_replaced_by_one_skill_dir_entry_per_codex_event(self):
        self.seed(self.stamped_runner("adw-light-plugin", "20261120T174612Z-ab"))
        self.assertEqual((self.merge(), self.merged_words()), (0, self.expected_words(["echo", "foreign"])))

    def test_an_old_marketplace_source_runner_under_the_adw_lite_home_is_replaced(self):
        self.seed(self.scratch.home / ".adw-lite" / "install" / "source" / "hooks" / "run.sh")
        self.assertEqual((self.merge(), self.merged_words()), (0, self.expected_words(["echo", "foreign"])))

    def test_a_suffixed_dev_tree_runner_is_replaced(self):
        self.seed(self.stamped_runner("adw-light-dev"))
        self.assertEqual((self.merge(), self.merged_words()), (0, self.expected_words(["echo", "foreign"])))

    def test_an_older_codex_plugin_cache_version_runner_is_replaced(self):
        self.seed(self.scratch.codex_home.joinpath("plugins", "cache", "adw-light", "adw-light", "0.0.0-build.old",
                                                   "hooks", "run.sh"))
        self.assertEqual((self.merge(), self.merged_words()), (0, self.expected_words(["echo", "foreign"])))

    def test_a_second_merge_leaves_the_hooks_file_byte_identical(self):
        self.seed(self.stamped_runner("adw-light-plugin", "20261120T174612Z-ab"))
        self.merge()
        once = self.hooks_json.read_bytes()
        self.merge()
        self.assertEqual(self.hooks_json.read_bytes(), once)


class ForeignRunnersSurviveTests(CodexMergeCase):
    def test_a_run_sh_from_a_tool_whose_name_only_starts_like_adw_light_is_kept(self):
        foreign_runner = self.stamped_runner("adw-lightning")
        self.seed(foreign_runner)
        self.merge()
        self.assertEqual(self.merged_words(), self.expected_words(["ADW_CODEX_HOOK=1", str(foreign_runner), "Stop"],
                                                                  ["echo", "foreign"]))

    def test_a_run_sh_from_an_unrelated_tool_under_development_is_kept(self):
        foreign_runner = self.stamped_runner("claude-speak")
        self.seed(foreign_runner)
        self.merge()
        self.assertEqual(self.merged_words(), self.expected_words(["ADW_CODEX_HOOK=1", str(foreign_runner), "Stop"],
                                                                  ["echo", "foreign"]))

    def test_a_run_sh_nested_deep_below_an_adw_light_folder_is_kept(self):
        foreign_runner = self.scratch.root.joinpath("x", "adw-light", "vendor", "probe", "hooks", "run.sh")
        self.seed(foreign_runner)
        self.merge()
        self.assertEqual(self.merged_words(), self.expected_words(["ADW_CODEX_HOOK=1", str(foreign_runner), "Stop"],
                                                                  ["echo", "foreign"]))


class ClaudeStripTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Scratch(self.addCleanup)
        self.settings = self.scratch.config_dir / "settings.json"

    def strip_with(self, runner):
        command = shlex.quote(str(runner)) + " Stop"
        data = {"hooks": {"Stop": [command_group(command), command_group("echo foreign")]}}
        self.settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        code = self.scratch.run([sys.executable, "-I", CLAUDE_MERGER, "--settings", self.settings,
                                 "--skill-dir", self.scratch.root / "skill", "--strip-only"]).returncode
        return code, hook_commands(self.settings), command

    def test_a_run_sh_nested_deep_below_an_adw_light_folder_is_kept(self):
        code, kept, command = self.strip_with(self.scratch.root.joinpath("x", "adw-light", "vendor", "probe", "hooks",
                                                                         "run.sh"))
        self.assertEqual((code, kept), (0, [("Stop", command), ("Stop", "echo foreign")]))

    def test_an_older_claude_plugin_cache_version_runner_is_stripped(self):
        code, kept, _ = self.strip_with(self.scratch.config_dir.joinpath("plugins", "cache", "adw-light", "adw-light",
                                                                         "0.0.0-build.old", "hooks", "run.sh"))
        self.assertEqual((code, kept), (0, [("Stop", "echo foreign")]))


if __name__ == "__main__":
    unittest.main()
