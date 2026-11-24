import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
PROSE_FAMILIES = frozenset({"english", "punctuation"})
FLAGGED_PROSE = "We " + "util" + "ize this tool " + "—" + " for the build.\n"
NARRATED_CODE = "#" + " increment the counter\n" + "counter = 1\n"
PLAIN_PROSE = "Plain text.\n"
TEST_WRITER = "adw-test-writer"


def referring_test(reference):
    return f'def test_loads_the_fixture():\n    assert load("{reference}")\n'


class FixtureProseHarness(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name) / "repo"
        self.home = Path(workspace.name) / "home"
        self.state = Path(workspace.name) / "state"
        self.root.mkdir()
        self.home.mkdir()
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home)}
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True, env=self.env)
        self.hook("SessionStart", source="startup")

    def hook(self, event, cwd=None, **payload):
        payload_cwd = str(cwd or self.root)
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps({"cwd": payload_cwd, "session_id": "fixture-prose", "hook_event_name": event, **payload}),
            text=True, capture_output=True, check=True, cwd=self.root, env=self.env,
        )
        return json.loads(call.stdout)

    def place(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_gate(self, relative, content, agent_type=TEST_WRITER, cwd=None):
        return self.hook("PreToolUse", cwd=cwd, tool_name="Write", agent_id="agent-1", agent_type=agent_type,
                         tool_input={"file_path": str(self.root / relative), "content": content})

    def stage_all(self):
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, env=self.env)

    def commit_gate(self, agent_type=TEST_WRITER):
        return self.hook("PreToolUse", tool_name="Bash", agent_id="agent-1", agent_type=agent_type,
                         tool_input={"command": 'git commit -m "Add fixture"'})

    def decision(self, response):
        return response.get("hookSpecificOutput", {}).get("permissionDecision")

    def ledger_rows(self):
        ledger = self.state / "ledger" / "ledger.jsonl"
        return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]

    def rules_at(self, relative):
        return {row["rule"] for row in self.ledger_rows() if row["path"].endswith(relative)}

    def prose_families_at(self, relative):
        return {row["family"] for row in self.ledger_rows()
                if row["path"].endswith(relative) and row["family"] in PROSE_FAMILIES}


class UsedFixturesAtTheWriteGate(FixtureProseHarness):
    def assert_exempt_on_write(self, fixture, reference):
        self.place("tests/test_loader.py", referring_test(reference))

        response = self.write_gate(fixture, FLAGGED_PROSE)

        self.assertIsNone(self.decision(response))
        self.assertEqual(set(), self.prose_families_at(fixture))

    def test_fixture_under_tests_fixtures_named_by_file_name_draws_no_prose_findings(self):
        self.assert_exempt_on_write("tests/fixtures/sample.md", "sample.md")

    def test_fixture_under_tests_fixtures_named_by_relative_path_draws_no_prose_findings(self):
        self.assert_exempt_on_write("tests/fixtures/sample.md", "tests/fixtures/sample.md")

    def test_fixture_under_testdata_draws_no_prose_findings(self):
        self.assert_exempt_on_write("testdata/sample.md", "sample.md")

    def test_fixture_under_dunder_fixtures_draws_no_prose_findings(self):
        self.assert_exempt_on_write("src/__fixtures__/sample.md", "sample.md")

    def test_fixture_under_dunder_snapshots_draws_no_prose_findings(self):
        self.assert_exempt_on_write("src/__snapshots__/sample.md", "sample.md")

    def test_fixture_under_dunder_mocks_draws_no_prose_findings(self):
        self.assert_exempt_on_write("src/__mocks__/sample.md", "sample.md")

    def test_extensionless_fixture_named_with_its_parent_draws_no_prose_findings(self):
        self.assert_exempt_on_write("tests/fixtures/golden/OUTPUT", "golden/OUTPUT")


class UnusedFixturesAtTheWriteGate(FixtureProseHarness):
    def assert_flagged_on_write(self, fixture):
        response = self.write_gate(fixture, FLAGGED_PROSE)

        self.assertEqual("deny", self.decision(response))
        self.assertEqual(PROSE_FAMILIES, self.prose_families_at(fixture))

    def test_fixture_no_test_names_is_flagged(self):
        self.assert_flagged_on_write("tests/fixtures/sample.md")

    def test_prose_outside_fixture_folders_is_flagged_even_when_a_test_names_it(self):
        self.place("tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write("docs/sample.md")

    def test_fixtures_folder_outside_a_test_folder_is_flagged_even_when_a_test_names_it(self):
        self.place("tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write("src/fixtures/sample.md")

    def test_reference_inside_a_dot_folder_does_not_count(self):
        self.place(".cache/tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write("tests/fixtures/sample.md")

    def test_reference_inside_another_fixture_does_not_count(self):
        self.place("tests/fixtures/manifest.json", '{"files": ["sample.md"]}\n')

        self.assert_flagged_on_write("tests/fixtures/sample.md")

    def test_reference_inside_the_tests_readme_does_not_count(self):
        self.place("tests/README.md", "The loader reads sample.md.\n")

        self.assert_flagged_on_write("tests/fixtures/sample.md")

    def test_extensionless_fixture_named_without_its_parent_is_flagged(self):
        self.place("tests/test_loader.py", referring_test("OUTPUT"))

        self.assert_flagged_on_write("tests/fixtures/golden/OUTPUT")

    def test_longer_file_name_that_contains_the_fixture_name_does_not_count(self):
        self.place("tests/test_loader.py", referring_test("old_sample.md"))

        self.assert_flagged_on_write("tests/fixtures/sample.md")


class FixtureCodeAndWriterAtTheWriteGate(FixtureProseHarness):
    def test_used_code_fixture_still_draws_comment_findings(self):
        self.place("tests/test_loader.py", referring_test("counter.py"))

        response = self.write_gate("tests/fixtures/counter.py", NARRATED_CODE)

        self.assertEqual("deny", self.decision(response))
        self.assertIn("what_comment", self.rules_at("tests/fixtures/counter.py"))

    def test_fixture_write_by_another_agent_is_denied_by_the_test_gate(self):
        response = self.write_gate("tests/fixtures/x.md", PLAIN_PROSE, agent_type="general-purpose")

        self.assertEqual("deny", self.decision(response))
        self.assertIn("test_write_gate", self.rules_at("tests/fixtures/x.md"))

    def test_fixture_write_by_the_test_writer_is_allowed(self):
        response = self.write_gate("tests/fixtures/x.md", PLAIN_PROSE)

        self.assertIsNone(self.decision(response))
        self.assertNotIn("test_write_gate", self.rules_at("tests/fixtures/x.md"))


class FixturesAtTheCommitGate(FixtureProseHarness):
    def test_fixture_named_by_file_name_draws_no_prose_findings(self):
        self.place("tests/fixtures/sample.md", FLAGGED_PROSE)
        self.place("tests/test_loader.py", referring_test("sample.md"))
        self.stage_all()

        self.commit_gate()

        self.assertEqual(set(), self.prose_families_at("tests/fixtures/sample.md"))

    def test_snapshot_named_by_relative_path_draws_no_prose_findings(self):
        self.place("src/__snapshots__/sample.md", FLAGGED_PROSE)
        self.place("tests/test_loader.py", referring_test("src/__snapshots__/sample.md"))
        self.stage_all()

        self.commit_gate()

        self.assertEqual(set(), self.prose_families_at("src/__snapshots__/sample.md"))

    def test_fixture_no_test_names_is_blocked_with_prose_findings(self):
        self.place("tests/fixtures/sample.md", FLAGGED_PROSE)
        self.stage_all()

        response = self.commit_gate()

        self.assertEqual("deny", self.decision(response))
        self.assertEqual(PROSE_FAMILIES, self.prose_families_at("tests/fixtures/sample.md"))

    def test_prose_outside_fixture_folders_is_blocked_even_when_a_test_names_it(self):
        self.place("docs/sample.md", FLAGGED_PROSE)
        self.place("tests/test_loader.py", referring_test("sample.md"))
        self.stage_all()

        response = self.commit_gate()

        self.assertEqual("deny", self.decision(response))
        self.assertEqual(PROSE_FAMILIES, self.prose_families_at("docs/sample.md"))

    def test_fixtures_folder_outside_a_test_folder_is_blocked_even_when_a_test_names_it(self):
        self.place("src/fixtures/sample.md", FLAGGED_PROSE)
        self.place("tests/test_loader.py", referring_test("sample.md"))
        self.stage_all()

        response = self.commit_gate()

        self.assertEqual("deny", self.decision(response))
        self.assertEqual(PROSE_FAMILIES, self.prose_families_at("src/fixtures/sample.md"))

    def test_reference_inside_another_fixture_does_not_count(self):
        self.place("tests/fixtures/sample.md", FLAGGED_PROSE)
        self.place("tests/fixtures/manifest.json", '{"files": ["sample.md"]}\n')
        self.stage_all()

        response = self.commit_gate()

        self.assertEqual("deny", self.decision(response))
        self.assertEqual(PROSE_FAMILIES, self.prose_families_at("tests/fixtures/sample.md"))

    def test_used_code_fixture_still_draws_comment_findings(self):
        self.place("tests/fixtures/counter.py", NARRATED_CODE)
        self.place("tests/test_loader.py", referring_test("counter.py"))
        self.stage_all()

        response = self.commit_gate()

        self.assertEqual("deny", self.decision(response))
        self.assertIn("what_comment", self.rules_at("tests/fixtures/counter.py"))


if __name__ == "__main__":
    unittest.main()
