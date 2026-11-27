import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from ast import Assign, Name, literal_eval, parse
from pathlib import Path

from t2325_fixtures import small_payload_probe

ROOT = Path(__file__).resolve().parents[1]
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
CODEX_HOOKS_JSON = ROOT / "hooks" / "codex-hooks.json"
PLUGIN_MANIFEST = ROOT / ".claude-plugin" / "plugin.json"
ENTRY = ROOT / "adw_light.py"
BUILD = ROOT / "tools" / "build_release.sh"

CLAUDE_EVENTS = frozenset({
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolBatch",
    "PostToolUseFailure", "SubagentStart", "SubagentStop", "Stop", "SessionEnd",
})
CODEX_EVENTS = CLAUDE_EVENTS - {"PostToolBatch", "PostToolUseFailure"}
CLAUDE_CONFIG_EVENT = "UserPromptExpansion"
CODEX_CONFIG_EVENT = "UserPromptSubmit"
POST_TOOL_MATCHER = "^(Write|Edit|MultiEdit|NotebookEdit|apply_patch|Bash|mcp__.*)$"
EXPECTED_MATCHERS_AND_TIMEOUTS = {
    "SessionStart": ("startup|resume|clear|compact", [10]),
    "UserPromptSubmit": (None, [10]),
    "PreToolUse": (None, [10]),
    "PostToolUse": (POST_TOOL_MATCHER, [10]),
    "PostToolBatch": (None, [10]),
    "PostToolUseFailure": (None, [10]),
    "SubagentStart": (None, [10]),
    "SubagentStop": (None, [10]),
    "Stop": (None, [10]),
    "SessionEnd": (None, [10]),
    "UserPromptExpansion": ("^adw-light:config$", [10]),
}
RELEASE_EXCLUDED_NAMES = frozenset({".git", ".state", ".agents", ".claude", ".gitnexus", ".caliber", "tests"})
SCRUBBED_ENV = ("CLAUDE_PLUGIN_ROOT", "ADW_STATE_ROOT", "ADW_CODEX_HOOK", "ADW_PYTHON", "ADW_ALLOW_PROTECTED_EDIT")

BRANCH_GUARD = ("PreToolUse", "Bash", {"type": "command", "command": "/Users/sample/.claude/hooks/branch-guard.sh"})
CLAUDE_SPEAK = ("Stop", None, {"type": "command", "command": '"/Users/sample/Development/projects/claude-speak/hooks/run.sh" Stop'})
IMPECCABLE = ("PostToolUse", "Write|Edit", {"type": "command", "command": "node /Users/sample/.claude/skills/impeccable/hooks/check.mjs"})
CALIBER = ("SessionStart", None, {"type": "command", "command": "caliber learn observe"})
PROMPT_HOOK = ("UserPromptSubmit", None, {"type": "prompt", "prompt": "Reject requests that widen the scope."})
FOREIGN = (BRANCH_GUARD, CLAUDE_SPEAK, IMPECCABLE, CALIBER, PROMPT_HOOK)
LEGACY_INTERPRETER = ("Stop", None, {"type": "command", "command": "python3 -I -S -B /Users/sample/Development/projects/adw-light/adw_light.py Stop --host claude"})
STALE_RUN_SH = ("PreToolUse", None, {"type": "command", "command": '"/Users/sample/Development/projects/adw-light/hooks/run.sh" PreToolUse'})
PLUGIN_CACHE_RUN_SH = Path.home().joinpath(".host", "plugins", "cache", "adw-light", "adw-light", "0.1.2", "hooks", "run.sh")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def entry_events():
    tree = parse(ENTRY.read_text(encoding="utf-8"))
    node = next(item for item in tree.body
                if type(item) is Assign and any(type(t) is Name and t.id == "EVENTS" for t in item.targets))
    return frozenset(literal_eval(node.value))


def registered_commands(config):
    return {event: [handler["command"] for group in groups for handler in group["hooks"]]
            for event, groups in config["hooks"].items()}


def run_sh_events(config):
    return {event for event, commands in registered_commands(config).items()
            if any("/hooks/run.sh " in command for command in commands)}


def matchers_and_timeouts(config):
    return {event: (groups[0].get("matcher"), [handler["timeout"] for group in groups for handler in group["hooks"]])
            for event, groups in config["hooks"].items()}


def isolated_env(home, **extra):
    env = {key: value for key, value in os.environ.items() if key not in SCRUBBED_ENV}
    return {**env, "HOME": str(home), **extra}


def mixed_settings():
    stop_group = {"hooks": [CLAUDE_SPEAK[2], LEGACY_INTERPRETER[2]]}
    return {
        "model": "opus",
        "enabledPlugins": {"impeccable@impeccable": True},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [BRANCH_GUARD[2]]}, {"hooks": [STALE_RUN_SH[2]]}],
            "Stop": [stop_group],
            "PostToolUse": [{"matcher": "Write|Edit", "hooks": [IMPECCABLE[2]]}],
            "SessionStart": [{"hooks": [CALIBER[2]]}],
            "UserPromptSubmit": [{"hooks": [PROMPT_HOOK[2]]}],
        },
    }


def handler_rows(data):
    return {(event, group.get("matcher"), json.dumps(handler, sort_keys=True))
            for event, groups in data.get("hooks", {}).items() for group in groups for handler in group["hooks"]}


def rows_of(entries):
    return {(event, matcher, json.dumps(handler, sort_keys=True)) for event, matcher, handler in entries}


def command_pairs(data):
    return {(event, handler.get("command")) for event, groups in data.get("hooks", {}).items()
            for group in groups for handler in group["hooks"]}


class PluginManifestWiringTests(unittest.TestCase):
    def test_claude_hooks_register_the_ten_engine_events_and_the_config_expansion(self):
        self.assertEqual(set(read_json(HOOKS_JSON)["hooks"]), CLAUDE_EVENTS | {CLAUDE_CONFIG_EVENT})

    def test_every_claude_command_runs_its_hook_script_with_its_own_event(self):
        expected = {event: ['"${CLAUDE_PLUGIN_ROOT}"/hooks/run.sh ' + event] for event in CLAUDE_EVENTS}
        expected[CLAUDE_CONFIG_EVENT] = ['"${CLAUDE_PLUGIN_ROOT}"/hooks/adw-config ' + CLAUDE_CONFIG_EVENT]
        self.assertEqual(registered_commands(read_json(HOOKS_JSON)), expected)

    def test_every_event_routed_to_run_sh_is_dispatched_by_the_entry_script(self):
        self.assertEqual(run_sh_events(read_json(HOOKS_JSON)) - entry_events(), set())

    def test_claude_matchers_and_timeouts_match_the_expected_table(self):
        self.assertEqual(matchers_and_timeouts(read_json(HOOKS_JSON)), EXPECTED_MATCHERS_AND_TIMEOUTS)

    def test_plugin_manifest_lets_commit_sha_updates_and_auto_discovery_work(self):
        manifest = read_json(PLUGIN_MANIFEST)
        self.assertNotIn("version", manifest)
        self.assertNotIn("hooks", manifest)

    def test_codex_hooks_register_the_eight_events_through_the_skill_dir_placeholder(self):
        expected = {event: [f'ADW_CODEX_HOOK=1 "__SKILL_DIR__"/hooks/run.sh {event}'] for event in CODEX_EVENTS}
        expected[CODEX_CONFIG_EVENT].append(f'ADW_CODEX_HOOK=1 "__SKILL_DIR__"/hooks/adw-config {CODEX_CONFIG_EVENT}')
        self.assertEqual(registered_commands(read_json(CODEX_HOOKS_JSON)), expected)


STUB_PYTHON = '#!/bin/sh\nif [ "$4" = "-c" ]; then exit 0; fi\nprintf "%s\\n" "$@"\n'
OLD_PYTHON = '#!/bin/sh\nif [ "$4" = "-c" ]; then exit 1; fi\nprintf "%s\\n" "$@"\n'


class RunScriptTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.plugin = self.scratch / "plugin"
        shutil.copytree(ROOT / "hooks", self.plugin / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ENTRY, self.plugin / "adw_light.py")
        shutil.copy2(ROOT / ".python-version", self.plugin / ".python-version")
        self.home = self.scratch / "home"
        self.home.mkdir()

    def interpreter(self, name, body):
        path = self.scratch / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    def run_sh(self, event, **env):
        return subprocess.run([str(self.plugin / "hooks" / "run.sh"), event], input="", capture_output=True,
                              text=True, check=False, env=isolated_env(self.home, **env), timeout=60)

    def expected_argv(self, event, host, state):
        return ["-I", "-S", "-B", str(self.plugin / "adw_light.py"), event, "--host", host, "--state-dir", str(state)]

    def test_run_sh_passes_the_default_state_dir_under_home(self):
        result = self.run_sh("Stop", ADW_PYTHON=self.interpreter("python3", STUB_PYTHON))
        self.assertEqual(result.stdout.splitlines(), self.expected_argv("Stop", "claude", self.home / ".adw-lite/state"))

    def test_run_sh_passes_the_state_root_override(self):
        state = self.scratch / "state"
        result = self.run_sh("PreToolUse", ADW_PYTHON=self.interpreter("python3", STUB_PYTHON), ADW_STATE_ROOT=str(state))
        self.assertEqual(result.stdout.splitlines(), self.expected_argv("PreToolUse", "claude", state))

    def test_run_sh_selects_the_codex_host_and_still_passes_the_state_dir(self):
        result = self.run_sh("Stop", ADW_PYTHON=self.interpreter("python3", STUB_PYTHON), ADW_CODEX_HOOK="1")
        self.assertEqual(result.stdout.splitlines(), self.expected_argv("Stop", "codex", self.home / ".adw-lite/state"))

    def seed_legacy_ledger(self):
        shutil.copytree(ROOT / "engine", self.plugin / "engine", ignore=shutil.ignore_patterns("__pycache__"))
        ledger = self.home / ".local/state/adw-lite/ledger/ledger.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{"hook": "stop", "rule": ""}\n', encoding="utf-8")
        return ledger

    def test_run_sh_without_a_state_root_moves_the_legacy_ledger_into_the_new_state_dir(self):
        self.seed_legacy_ledger()
        self.run_sh("Stop")
        self.assertEqual(((self.home / ".local/state/adw-lite").exists(),
                          (self.home / ".adw-lite/state/ledger/ledger.jsonl").is_file()), (False, True))

    def test_run_sh_with_a_state_root_leaves_the_legacy_ledger_untouched(self):
        ledger = self.seed_legacy_ledger()
        self.run_sh("Stop", ADW_STATE_ROOT=str(self.scratch / "state"))
        self.assertEqual((ledger.read_bytes(), (self.home / ".adw-lite").exists()), (b'{"hook": "stop", "rule": ""}\n', False))

    def test_run_sh_exits_2_when_the_interpreter_does_not_exist(self):
        result = self.run_sh("Stop", ADW_PYTHON="/nonexistent")
        self.assertEqual((result.returncode, result.stdout, (self.home / ".adw-lite").exists()), (2, "", False))

    def test_run_sh_exits_2_without_running_an_interpreter_below_the_floor(self):
        result = self.run_sh("Stop", ADW_PYTHON=self.interpreter("python3", OLD_PYTHON))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_a_state_dir_inside_the_plugin_tree_exits_2_and_creates_nothing(self):
        state = self.plugin / ".state"
        result = self.run_sh("Stop", ADW_STATE_ROOT=str(state))
        self.assertEqual((result.returncode, result.stdout), (2, ""), result.stderr)
        self.assertFalse(state.exists())


class MergeContract:
    script: Path
    target_flag: str
    events: frozenset
    config_event: str
    prefix: str
    retained_on_install: set

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.target = Path(scratch.name) / "settings.json"
        self.original = (json.dumps(mixed_settings(), indent=2) + "\n").encode()
        self.target.write_bytes(self.original)

    def merge(self, *flags, skill_dir=True):
        skill = ["--skill-dir", str(ROOT)] if skill_dir else []
        return subprocess.run([sys.executable, "-I", str(self.script), self.target_flag, str(self.target), *skill, *flags],
                              capture_output=True, text=True, check=False, timeout=60)

    def merged(self):
        return json.loads(self.target.read_text(encoding="utf-8"))

    def installed_pairs(self):
        run_sh = shlex.quote(str(ROOT / "hooks" / "run.sh"))
        adw_config = shlex.quote(str(ROOT / "hooks" / "adw-config"))
        config_pair = (self.config_event, f"{self.prefix}{adw_config} {self.config_event}")
        return {(event, f"{self.prefix}{run_sh} {event}") for event in self.events} | {config_pair}

    def test_install_keeps_every_foreign_handler_with_its_matcher(self):
        self.merge()
        self.assertEqual(rows_of(FOREIGN) - handler_rows(self.merged()), set())

    def test_install_keeps_top_level_settings(self):
        self.merge()
        self.assertEqual((self.merged()["model"], self.merged()["enabledPlugins"]), ("opus", {"impeccable@impeccable": True}))

    def test_install_replaces_stale_adw_handlers_with_the_skill_dir_routes(self):
        self.merge()
        foreign = command_pairs({"hooks": {event: [{"hooks": [handler]}] for event, _, handler in FOREIGN}})
        self.assertEqual(command_pairs(self.merged()) - foreign, self.installed_pairs() | self.retained_on_install)

    def test_a_second_install_leaves_the_file_byte_identical(self):
        self.merge()
        once = self.target.read_bytes()
        self.merge()
        self.assertEqual(self.target.read_bytes(), once)

    def test_install_swaps_in_a_new_file_and_leaves_no_backup_or_temp_file_beside_it(self):
        before = self.target.stat().st_ino
        self.merge()
        names = sorted(path.name for path in self.target.parent.iterdir())
        replaced = (self.target.stat().st_ino != before, self.target.read_bytes() != self.original)
        self.assertEqual((names, replaced), (["settings.json"], (True, True)))

    def test_install_keeps_the_file_mode(self):
        self.target.chmod(0o640)
        self.merge()
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)

    def test_a_merge_without_skill_dir_is_refused_and_leaves_the_file_alone(self):
        result = self.merge(skill_dir=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_remove_legacy_without_skill_dir_is_refused_and_leaves_the_file_alone(self):
        result = self.merge("--remove-legacy", skill_dir=False)
        self.assertEqual((result.returncode, self.target.read_bytes()), (2, self.original))

    def test_strip_only_without_skill_dir_drops_plugin_cache_handlers_and_keeps_the_foreign_ones(self):
        settings = mixed_settings()
        cached = {"type": "command", "command": f"{self.prefix}{shlex.quote(str(PLUGIN_CACHE_RUN_SH))} SessionEnd"}
        settings["hooks"]["SessionEnd"] = [{"hooks": [cached]}]
        self.target.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        result = self.merge("--strip-only", skill_dir=False)
        self.assertEqual((result.returncode, handler_rows(self.merged())), (0, rows_of(FOREIGN)))

    def test_remove_legacy_drops_only_the_interpreter_handler(self):
        self.merge("--remove-legacy")
        self.assertEqual(handler_rows(self.merged()), handler_rows(mixed_settings()) - rows_of([LEGACY_INTERPRETER]))

    def test_strip_only_leaves_exactly_the_foreign_handlers(self):
        self.merge("--strip-only")
        self.assertEqual(handler_rows(self.merged()), rows_of(FOREIGN))


class ClaudeSettingsMergeTests(MergeContract, unittest.TestCase):
    script = ROOT / "hooks" / "merge-claude-settings.py"
    target_flag = "--settings"
    events = CLAUDE_EVENTS
    config_event = CLAUDE_CONFIG_EVENT
    prefix = ""
    retained_on_install = set()


class CodexHooksMergeTests(MergeContract, unittest.TestCase):
    script = ROOT / "hooks" / "merge-codex-hooks.py"
    target_flag = "--hooks-json"
    events = CODEX_EVENTS
    config_event = CODEX_CONFIG_EVENT
    prefix = "ADW_CODEX_HOOK=1 "
    retained_on_install = {(LEGACY_INTERPRETER[0], LEGACY_INTERPRETER[2]["command"])}


class ReleaseBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(scratch.cleanup)
        cls.scratch = Path(scratch.name).resolve()
        cls.home = cls.scratch / "home"
        cls.home.mkdir()
        cls.release = cls.scratch / "adw-release"
        build = subprocess.run(["bash", str(BUILD)], capture_output=True, text=True, check=False, timeout=600, cwd=str(ROOT),
                               env=isolated_env(cls.home, ADW_RELEASE=str(cls.release)))
        assert build.returncode == 0, build.stdout + build.stderr

    def pre_tool_write(self, path):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        payload = {"session_id": "release-case", "cwd": str(self.scratch), "hook_event_name": "PreToolUse",
                   "tool_name": "Write", "tool_input": {"file_path": str(path), "content": "Short text.\n"}}
        result = subprocess.run([sys.executable, "-I", str(self.release / "adw_light.py"), "PreToolUse", "--host", "claude",
                                 "--state-dir", state.name], input=json.dumps(payload), capture_output=True, text=True,
                                check=False, cwd=str(self.scratch), env=isolated_env(self.home), timeout=60)
        return json.loads(result.stdout)

    def test_release_tree_omits_every_dev_only_directory(self):
        leaked = sorted(str(path.relative_to(self.release)) for path in self.release.rglob("*")
                        if path.name in RELEASE_EXCLUDED_NAMES)
        self.assertEqual(leaked, [])

    def test_release_tree_passes_the_smoke_probe(self):
        """The test runs quickly."""
        result = subprocess.run([sys.executable, "-c", small_payload_probe(), str(self.release)], capture_output=True,
                                text=True, check=False, env=isolated_env(self.home), timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_protection_denies_a_write_under_the_release_root_in_other_letter_case(self):
        swapped = self.release.parent / self.release.name.swapcase() / "notes.md"
        decision = self.pre_tool_write(swapped)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("watcher_install_surface", decision["permissionDecisionReason"])

    def test_protection_allows_the_same_write_beside_the_release_root(self):
        self.assertEqual(self.pre_tool_write(self.scratch / "elsewhere" / "notes.md"), {})


if __name__ == "__main__":
    unittest.main()
