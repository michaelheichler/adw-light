import importlib.util
import io
import json
import shlex
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from in_memory_hook import DENY, InMemoryHookCase

ROOT = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = ROOT / "hooks" / "adw-config"
CONFIG_NAME = ".agent-discipline.json"
SLASH = "UserPromptExpansion"
PROMPT = "UserPromptSubmit"
COMMAND = "adw-light:config"
CODEX_PREFIX = "$adw-config"
NOW = 1_800_000_000
REFUSED = "refused"
BLOCK = "block"
SET_FILLER = "rule filler enforce"
SEED = {"tests": "deny", "max_rows": 5, "rule_gates": {"test_builds_repo": "enforce"}}


def load_hook():
    spec = importlib.util.spec_from_file_location("adw_config_hook", ROOT / "hooks" / "adw_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOOK = load_hook()
config = HOOK.config
tests_policy = importlib.import_module("lib.tests_policy")
git_port = importlib.import_module("lib.git_port")


def slash(arguments, cwd="", **overrides):
    payload = {"hook_event_name": SLASH, "expansion_type": "slash_command", "command_source": "plugin",
               "command_name": COMMAND, "command_args": arguments, "cwd": cwd}
    return {**payload, **overrides}


def codex(prompt, cwd="", **overrides):
    return {"hook_event_name": PROMPT, "prompt": prompt, "cwd": cwd, **overrides}


def seeded_project(root, name):
    folder = root / name
    folder.mkdir()
    (folder / CONFIG_NAME).write_text(json.dumps(SEED), encoding="utf-8")
    return folder


class ConfigCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        root = Path(workspace.name).resolve()
        cls.gate_project = seeded_project(root, "gate")
        cls.nested = cls.gate_project / "pkg" / "deep"
        cls.nested.mkdir(parents=True)
        cls.window_project = seeded_project(root, "window")
        cls.guarded_project = seeded_project(root, "guarded")
        cls.worktree = root / "worktree"
        cls.worktree_nested = cls.worktree / "src" / "pkg"
        cls.worktree_nested.mkdir(parents=True)
        cls.main_git = root / "main" / ".git"
        cls.loose = root / "loose"
        cls.loose.mkdir()

    def window_left(self, arguments, elapsed):
        try:
            path = HOOK.apply(arguments, str(self.window_project), NOW)
        except ValueError:
            return REFUSED
        return tests_policy.window_seconds_left(config.load_project_config(path).settings, NOW + elapsed)

    def hook_outcome(self, event, payload):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            response = HOOK.main(event)
        stored = json.loads((self.guarded_project / CONFIG_NAME).read_text(encoding="utf-8"))
        return response.get("decision"), stored == SEED

    def test_exact_user_invocation_only(self):
        cases = {
            "claude_user": (SLASH, slash(SET_FILLER)),
            "codex_user": (PROMPT, codex(f"{CODEX_PREFIX} {SET_FILLER}")),
            "other_command_name": (SLASH, slash(SET_FILLER, command_name="config")),
            "project_command_source": (SLASH, slash(SET_FILLER, command_source="project")),
            "non_slash_expansion": (SLASH, slash(SET_FILLER, expansion_type="mcp_prompt")),
            "event_mismatch": (PROMPT, slash(SET_FILLER)),
            "codex_prefix_after_text": (PROMPT, codex(f"please run {CODEX_PREFIX} {SET_FILLER}")),
            "codex_prefix_glued": (PROMPT, codex(f"{CODEX_PREFIX}{SET_FILLER}")),
        }
        arguments = {name: HOOK.user_arguments(event, payload) for name, (event, payload) in cases.items()}
        expected = {name: None for name in cases} | {"claude_user": SET_FILLER, "codex_user": SET_FILLER}
        self.assertEqual(arguments, expected)

    def test_gate_updates_nearest_config_without_losing_keys(self):
        written = HOOK.apply(SET_FILLER, str(self.nested), NOW)
        stored = json.loads(written.read_text(encoding="utf-8"))
        expected = {**SEED, "rule_gates": {"test_builds_repo": "enforce", "filler": "enforce"}}
        self.assertEqual((written, stored, (self.nested / CONFIG_NAME).exists()),
                         (self.gate_project / CONFIG_NAME, expected, False))

    def written_without_config(self, cwd, roots):
        with git_port.using_view(git_port.FakeGitView({"roots": roots})):
            written = HOOK.apply(SET_FILLER, str(cwd), NOW)
        return written, json.loads(written.read_text(encoding="utf-8"))

    def test_new_config_lands_at_the_git_top_level_from_a_nested_worktree_folder(self):
        roots = {str(self.worktree): {"common_dir": str(self.main_git)}}
        found = self.written_without_config(self.worktree_nested, roots)
        self.assertEqual((found, (self.worktree_nested / CONFIG_NAME).exists()),
                         ((self.worktree / CONFIG_NAME, {"rule_gates": {"filler": "enforce"}}), False))

    def test_new_config_lands_in_the_cwd_outside_git(self):
        found = self.written_without_config(self.loose, {})
        self.assertEqual(found, (self.loose / CONFIG_NAME, {"rule_gates": {"filler": "enforce"}}))

    def test_window_expires_and_rejects_more_than_eight_hours(self):
        cases = {("30m", 0): 1800, ("30m", 1800): 0, ("8h", 28799): 1, ("480m", 0): 28800,
                 ("9h", 0): REFUSED, ("481m", 0): REFUSED}
        left = {(span, elapsed): self.window_left(f"tests allow --for {span}", elapsed) for span, elapsed in cases}
        self.assertEqual(left, cases)

    def test_model_routes_and_protected_rules_are_refused(self):
        cwd = str(self.guarded_project)
        cases = {
            "subagent_slash": (SLASH, slash(SET_FILLER, cwd, agent_id="a1", agent_type="general-purpose")),
            "subagent_codex": (PROMPT, codex(f"{CODEX_PREFIX} {SET_FILLER}", cwd, agent_type="worker")),
            "self_protection_rule": (SLASH, slash("rule config_seal off", cwd)),
            "test_gate_rule": (SLASH, slash("rule test_write_gate observe", cwd)),
            "fixed_observe_rule": (SLASH, slash("rule file_length_warning enforce", cwd)),
            "unknown_rule": (SLASH, slash("rule state_root off", cwd)),
        }
        outcomes = {name: self.hook_outcome(event, payload) for name, (event, payload) in cases.items()}
        expected = {name: (BLOCK, True) for name in cases} | {"subagent_slash": (None, True), "subagent_codex": (None, True)}
        self.assertEqual(outcomes, expected)


class ConfigHookShellRouteTests(InMemoryHookCase):
    def test_agent_shell_call_of_the_config_hook_is_denied(self):
        decision, rules = self.bash(f"{shlex.quote(str(HOOK_SCRIPT))} {SLASH} < payload.json")
        self.assertEqual((decision, "config_seal" in rules), (DENY, True))

    def test_hook_reached_through_a_variable_is_denied(self):
        decision, rules = self.bash(f'"$CONFIG_HOOK" {SLASH} < payload.json')
        self.assertEqual((decision, "config_seal" in rules), (DENY, True))


if __name__ == "__main__":
    unittest.main()
