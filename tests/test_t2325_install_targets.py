import json
import shlex
import subprocess
import sys
import unittest

from t2325_fixtures import ROOT, Scratch, copy_source_repo

ENTRY = ROOT / "adw_light.py"
DENY = "deny"
INSTALL_SURFACE = "watcher_install_surface"
STATE_MUTATION = "state_mutation"
HOST_LOCATION_ENV = frozenset({"CODEX_HOME", "CLAUDE_CONFIG_DIR"})


def pre_tool(scratch, cwd, tool_name, tool_input, **host_env):
    payload = {"session_id": "t2325-hook", "cwd": str(cwd), "hook_event_name": "PreToolUse",
               "tool_name": tool_name, "tool_input": tool_input}
    env = {key: value for key, value in scratch.env().items() if key not in HOST_LOCATION_ENV} | host_env
    result = subprocess.run([sys.executable, "-ISB", str(ENTRY), "PreToolUse", "--host", "claude",
                             "--state-dir", str(scratch.root / "state")], input=json.dumps(payload),
                            capture_output=True, text=True, check=False, cwd=str(cwd), env=env, timeout=120)
    return result.returncode, json.loads(result.stdout)


def blocked_rules(scratch):
    ledger = scratch.root / "state" / "ledger" / "ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["rule"] for row in rows if row.get("outcome") == "block"}


class InstalledCopiesStayProtectedTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Scratch(self.addCleanup)

    def write_verdict(self, path):
        code, response = pre_tool(self.scratch, self.scratch.root, "Write",
                                  {"file_path": str(path), "content": "Short text.\n"})
        return code, response.get("hookSpecificOutput", {}).get("permissionDecision"), blocked_rules(self.scratch)

    def test_write_into_the_default_codex_plugin_cache_is_denied_as_an_install_surface(self):
        target = self.scratch.home / ".codex" / "plugins" / "cache" / "adw-light" / "adw-light" / "1" / "notes.md"
        self.assertEqual(self.write_verdict(target), (0, DENY, {INSTALL_SURFACE}))

    def test_write_into_the_marketplace_source_in_the_adw_lite_home_is_denied_as_an_install_surface(self):
        target = self.scratch.home / ".adw-lite" / "install" / "source" / "hooks" / "run.sh"
        self.assertEqual(self.write_verdict(target), (0, DENY, {INSTALL_SURFACE}))

    def test_write_into_the_adw_lite_state_folder_stays_denied_as_state(self):
        target = self.scratch.home / ".adw-lite" / "state" / "reports" / "notes.md"
        self.assertEqual(self.write_verdict(target), (0, DENY, {STATE_MUTATION}))

    def test_write_into_the_state_dir_given_to_the_hook_is_denied_as_state(self):
        target = self.scratch.root / "state" / "reports" / "notes.md"
        self.assertEqual(self.write_verdict(target), (0, DENY, {STATE_MUTATION}))

    def test_write_into_the_adw_light_source_folder_under_development_is_allowed(self):
        target = self.scratch.home / "Development" / "projects" / "adw-light" / "notes.md"
        self.assertEqual(self.write_verdict(target)[:2], (0, None))


class CodexHomePluginCopiesStayProtectedTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Scratch(self.addCleanup)
        self.codex_link = self.scratch.root / "codex-link"
        self.codex_link.symlink_to(self.scratch.codex_home, target_is_directory=True)

    def verdict(self, tool_name, tool_input, codex_home):
        code, response = pre_tool(self.scratch, self.scratch.root, tool_name, tool_input, CODEX_HOME=str(codex_home))
        return code, response.get("hookSpecificOutput", {}).get("permissionDecision"), blocked_rules(self.scratch)

    def write_verdict(self, target, codex_home):
        return self.verdict("Write", {"file_path": str(target), "content": "Short text.\n"}, codex_home)

    def test_write_into_the_plugin_cache_under_codex_home_is_denied_as_an_install_surface(self):
        target = self.scratch.codex_home / "plugins" / "cache" / "adw-light" / "adw-light" / "1" / "notes.md"
        self.assertEqual(self.write_verdict(target, self.scratch.codex_home), (0, DENY, {INSTALL_SURFACE}))

    def test_write_into_the_marketplace_copy_under_codex_home_is_denied_as_an_install_surface(self):
        target = self.scratch.codex_home / "plugins" / "marketplaces" / "adw-light" / "notes.md"
        self.assertEqual(self.write_verdict(target, self.scratch.codex_home), (0, DENY, {INSTALL_SURFACE}))

    def test_shell_redirect_into_the_plugin_cache_under_codex_home_is_denied_as_an_install_surface(self):
        target = self.scratch.codex_home / "plugins" / "cache" / "adw-light" / "adw-light" / "1" / "notes.md"
        command = f"echo text > {shlex.quote(str(target))}"
        self.assertEqual(self.verdict("Bash", {"command": command}, self.scratch.codex_home),
                         (0, DENY, {INSTALL_SURFACE}))

    def test_write_through_the_real_path_of_a_linked_codex_home_is_denied_as_an_install_surface(self):
        target = self.scratch.codex_home / "plugins" / "cache" / "adw-light" / "adw-light" / "1" / "notes.md"
        self.assertEqual(self.write_verdict(target, self.codex_link), (0, DENY, {INSTALL_SURFACE}))

    def test_write_through_a_link_to_codex_home_is_denied_as_an_install_surface(self):
        target = self.codex_link / "plugins" / "cache" / "adw-light" / "adw-light" / "1" / "notes.md"
        self.assertEqual(self.write_verdict(target, self.scratch.codex_home), (0, DENY, {INSTALL_SURFACE}))

    def test_write_beside_the_plugin_copies_under_codex_home_is_allowed(self):
        target = self.scratch.codex_home / "plugins" / "cache" / "other-plugin" / "notes.md"
        self.assertEqual(self.write_verdict(target, self.scratch.codex_home)[:2], (0, None))


class ReleaseToolCommandsPassTheCommitGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = Scratch(cls.addClassCleanup)
        cls.repo = cls.shared.root / "repo"
        copy_source_repo(cls.shared, cls.repo)

    def run_in_repo(self, command):
        return pre_tool(Scratch(self.addCleanup), self.repo, "Bash", {"command": command})

    def test_local_deploy_command_is_allowed(self):
        self.assertEqual(self.run_in_repo("bash tools/deploy.sh"), (0, {}))

    def test_remote_deploy_command_is_allowed(self):
        self.assertEqual(self.run_in_repo("bash tools/deploy.sh --host deploy@deploy-target.example"), (0, {}))

    def test_release_branch_build_command_is_allowed(self):
        self.assertEqual(self.run_in_repo("bash tools/release_branch.sh"), (0, {}))

    def test_release_branch_push_command_is_allowed(self):
        self.assertEqual(self.run_in_repo("bash tools/release_branch.sh --push"), (0, {}))


if __name__ == "__main__":
    unittest.main()
