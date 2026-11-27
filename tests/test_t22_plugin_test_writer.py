import os
import unittest

from in_memory_hook import PRINCIPLE, TEST_WRITER, InMemoryHookCase, decision_of


PLAIN_PROSE = "Plain text.\n"
PLUGIN_TEST_WRITER = "adw-light:" + TEST_WRITER
OTHER_AGENT = "general-purpose"
DEFINITION_RULE = "test_writer_definition"
TEST_GATE_RULE = "test_write_gate"
BEHAVIOR_TEST = ('def test_sum_adds_two_numbers():\n    """' + PRINCIPLE + '"""\n'
                 "    assert sum([2, 3]) == 5\n")
DEFINITION_FILE = TEST_WRITER + ".md"
NAMED_DEFINITION = "---\nname: " + TEST_WRITER + "\n---\n" + PLAIN_PROSE
TEST_FILE = "tests/test_x.py"
WORKTREE = ("worktrees", "agent-x")
CACHE_AGENTS = ("plugins", "cache", "adw-light", "adw-light", "x", "agents")


class TestWriterPluginHarness(InMemoryHookCase):
    files = {"pyproject.toml": "[project]\nname = \"fixture\"\n"}

    def write_to(self, path, content, agent_type=PLUGIN_TEST_WRITER):
        payload = {"cwd": str(self.work), "tool_name": "Write", "agent_id": "agent-1", "agent_type": agent_type,
                   "tool_input": {"file_path": str(path), "content": content}}
        return decision_of(self.respond("PreToolUse", payload)), self.ledger_rules(path)


class PluginNamedTestWriterAtTheTestGate(TestWriterPluginHarness):
    def test_plugin_named_test_writer_may_write_a_test_file(self):
        decision, rules = self.write_to(self.work / TEST_FILE, BEHAVIOR_TEST)

        self.assertIsNone(decision)
        self.assertNotIn(TEST_GATE_RULE, rules)

    def test_other_agent_is_denied_the_same_test_write(self):
        decision, rules = self.write_to(self.work / TEST_FILE, BEHAVIOR_TEST, agent_type=OTHER_AGENT)

        self.assertEqual("deny", decision)
        self.assertIn(TEST_GATE_RULE, rules)

    def test_lookalike_plugin_prefix_is_denied(self):
        decision, rules = self.write_to(self.work / TEST_FILE, BEHAVIOR_TEST, agent_type="other-plugin:" + TEST_WRITER)

        self.assertEqual("deny", decision)
        self.assertIn(TEST_GATE_RULE, rules)


class InstalledDefinitionStaysProtected(TestWriterPluginHarness):
    def assert_definition_denied(self, path, content=PLAIN_PROSE):
        decision, rules = self.write_to(path, content)

        self.assertEqual("deny", decision)
        self.assertIn(DEFINITION_RULE, rules)

    def test_claude_home_definition_is_denied(self):
        self.assert_definition_denied(self.home / ".claude" / "agents" / DEFINITION_FILE)

    def test_codex_home_definition_is_denied(self):
        self.assert_definition_denied(self.home / ".codex" / "agents" / DEFINITION_FILE)

    def test_codex_cache_definition_is_denied(self):
        self.assert_definition_denied(self.home.joinpath(".codex", *CACHE_AGENTS, DEFINITION_FILE))

    def test_worktree_project_claude_agents_definition_is_denied(self):
        checkout = self.work.joinpath(".claude", *WORKTREE)
        self.assert_definition_denied(checkout / ".claude" / "agents" / DEFINITION_FILE)

    def test_plugin_cache_definition_is_denied(self):
        cache = self.home / ".claude" / "plugins" / "cache" / "adw-light" / "adw-light" / "x"
        self.assert_definition_denied(cache / "agents" / DEFINITION_FILE)

    def test_adw_lite_home_definition_is_denied(self):
        self.assert_definition_denied(self.home / ".adw-lite" / "install" / "agents" / DEFINITION_FILE)

    def test_project_claude_folder_definition_is_denied(self):
        self.assert_definition_denied(self.work / ".claude" / "agents" / DEFINITION_FILE)

    def test_renamed_file_that_declares_the_test_writer_is_denied(self):
        self.assert_definition_denied(self.home / ".claude" / "agents" / "helper.md", NAMED_DEFINITION)


class LinkedDefinitionFolderStaysProtected(TestWriterPluginHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        installed = cls.home / ".claude" / "agents"
        installed.mkdir(parents=True)
        os.symlink(installed, cls.work / "agents")

    def test_source_agents_folder_linked_into_the_install_is_denied(self):
        decision, rules = self.write_to(self.work / "agents" / DEFINITION_FILE, PLAIN_PROSE)

        self.assertEqual("deny", decision)
        self.assertIn(DEFINITION_RULE, rules)


class HardlinkedCacheDefinitionStaysProtected(TestWriterPluginHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        installed = cls.home.joinpath(".claude", *CACHE_AGENTS, DEFINITION_FILE)
        installed.parent.mkdir(parents=True)
        installed.write_text(NAMED_DEFINITION, encoding="utf-8")
        cls.linked = cls.work.joinpath(".claude", *WORKTREE, "agents", DEFINITION_FILE)
        cls.linked.parent.mkdir(parents=True)
        os.link(installed, cls.linked)

    def test_cache_definition_hardlink_is_denied(self):
        decision, rules = self.write_to(self.linked, PLAIN_PROSE)

        self.assertEqual("deny", decision)
        self.assertIn(DEFINITION_RULE, rules)


class WorktreeSourceDefinitionIsWritable(TestWriterPluginHarness):
    def test_worktree_source_definition_passes(self):
        path = self.work.joinpath(".claude", *WORKTREE, "agents", DEFINITION_FILE)

        decision, rules = self.write_to(path, NAMED_DEFINITION)

        self.assertIsNone(decision)
        self.assertNotIn(DEFINITION_RULE, rules)


class SourceRepoDefinitionIsWritable(TestWriterPluginHarness):
    def assert_definition_allowed(self, content, agent_type=PLUGIN_TEST_WRITER):
        path = self.work / "agents" / DEFINITION_FILE

        decision, rules = self.write_to(path, content, agent_type=agent_type)

        self.assertIsNone(decision)
        self.assertNotIn(DEFINITION_RULE, rules)

    def test_source_repo_agent_file_passes(self):
        self.assert_definition_allowed(PLAIN_PROSE)

    def test_source_repo_agent_file_naming_the_test_writer_passes(self):
        self.assert_definition_allowed(NAMED_DEFINITION)

    def test_source_repo_agent_file_passes_for_any_agent(self):
        self.assert_definition_allowed(NAMED_DEFINITION, agent_type=OTHER_AGENT)


if __name__ == "__main__":
    unittest.main()
