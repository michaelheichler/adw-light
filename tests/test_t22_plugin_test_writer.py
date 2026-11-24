import os
import unittest
from pathlib import Path

from test_fixture_prose import PLAIN_PROSE, TEST_WRITER, FixtureProseHarness


PLUGIN_TEST_WRITER = "adw-light:" + TEST_WRITER
OTHER_AGENT = "general-purpose"
DEFINITION_RULE = "test_writer_definition"
TEST_GATE_RULE = "test_write_gate"
BEHAVIOR_TEST = "def test_sum_adds_two_numbers():\n    assert sum([2, 3]) == 5\n"
DEFINITION_FILE = TEST_WRITER + ".md"
NAMED_DEFINITION = "---\nname: " + TEST_WRITER + "\n---\n" + PLAIN_PROSE


class TestWriterPluginHarness(FixtureProseHarness):
    def write_to(self, path, content, agent_type=PLUGIN_TEST_WRITER):
        return self.hook("PreToolUse", tool_name="Write", agent_id="agent-1", agent_type=agent_type,
                         tool_input={"file_path": str(path), "content": content})

    def rules_for(self, path):
        ledger = self.state / "ledger" / "ledger.jsonl"
        if not ledger.exists():
            return set()
        return {row["rule"] for row in self.ledger_rows() if row["path"].endswith(str(path))}


class PluginNamedTestWriterAtTheTestGate(TestWriterPluginHarness):
    def test_plugin_named_test_writer_may_write_a_test_file(self):
        response = self.write_to(self.root / "tests" / "test_x.py", BEHAVIOR_TEST)

        self.assertIsNone(self.decision(response))
        self.assertNotIn(TEST_GATE_RULE, self.rules_for("tests/test_x.py"))

    def test_other_agent_is_denied_the_same_test_write(self):
        response = self.write_to(self.root / "tests" / "test_x.py", BEHAVIOR_TEST, agent_type=OTHER_AGENT)

        self.assertEqual("deny", self.decision(response))
        self.assertIn(TEST_GATE_RULE, self.rules_for("tests/test_x.py"))

    def test_lookalike_plugin_prefix_is_denied(self):
        response = self.write_to(self.root / "tests" / "test_x.py", BEHAVIOR_TEST, agent_type="other-plugin:" + TEST_WRITER)

        self.assertEqual("deny", self.decision(response))
        self.assertIn(TEST_GATE_RULE, self.rules_for("tests/test_x.py"))


class InstalledDefinitionStaysProtected(TestWriterPluginHarness):
    def assert_definition_denied(self, path, content=PLAIN_PROSE):
        response = self.write_to(path, content)

        self.assertEqual("deny", self.decision(response))
        self.assertIn(DEFINITION_RULE, self.rules_for(path))

    def test_claude_home_definition_is_denied(self):
        self.assert_definition_denied(self.home / ".claude" / "agents" / DEFINITION_FILE)

    def test_codex_home_definition_is_denied(self):
        self.assert_definition_denied(self.home / ".codex" / "agents" / DEFINITION_FILE)

    def test_plugin_cache_definition_is_denied(self):
        cache = self.home / ".claude" / "plugins" / "cache" / "adw-light" / "adw-light" / "x"
        self.assert_definition_denied(cache / "agents" / DEFINITION_FILE)

    def test_adw_lite_home_definition_is_denied(self):
        self.assert_definition_denied(self.home / ".adw-lite" / "install" / "agents" / DEFINITION_FILE)

    def test_project_claude_folder_definition_is_denied(self):
        self.assert_definition_denied(self.root / ".claude" / "agents" / DEFINITION_FILE)

    def test_renamed_file_that_declares_the_test_writer_is_denied(self):
        self.assert_definition_denied(self.home / ".claude" / "agents" / "helper.md", NAMED_DEFINITION)

    def test_source_agents_folder_linked_into_the_install_is_denied(self):
        installed = self.home / ".claude" / "agents"
        installed.mkdir(parents=True)
        os.symlink(installed, self.root / "agents")

        self.assert_definition_denied(self.root / "agents" / DEFINITION_FILE)


class SourceRepoDefinitionIsWritable(TestWriterPluginHarness):
    def assert_definition_allowed(self, content, agent_type=PLUGIN_TEST_WRITER):
        path = self.root / "agents" / DEFINITION_FILE

        response = self.write_to(path, content, agent_type=agent_type)

        self.assertIsNone(self.decision(response))
        self.assertNotIn(DEFINITION_RULE, self.rules_for(path))

    def test_source_repo_agent_file_passes(self):
        self.assert_definition_allowed(PLAIN_PROSE)

    def test_source_repo_agent_file_naming_the_test_writer_passes(self):
        self.assert_definition_allowed(NAMED_DEFINITION)

    def test_source_repo_agent_file_passes_for_any_agent(self):
        self.assert_definition_allowed(NAMED_DEFINITION, agent_type=OTHER_AGENT)


if __name__ == "__main__":
    unittest.main()
