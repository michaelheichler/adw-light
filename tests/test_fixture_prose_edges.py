from quiet_git import disable_background_maintenance
import os
import shutil
import subprocess
import unittest

from test_fixture_prose import (
    FLAGGED_PROSE, PLAIN_PROSE, PROSE_FAMILIES, TEST_WRITER, FixtureProseHarness, referring_test,
)


FIXTURE = "tests/fixtures/sample.md"
OTHER_AGENT = "general-purpose"
UNTESTED_GO = 'package pkg\n\nvar fixture = "../tests/fixtures/sample.md"\n'
TESTED_GO = (
    'package pkg\n\nimport "testing"\n\n'
    'func TestX(t *testing.T) {\n\t_ = "../tests/fixtures/sample.md"\n}\n'
)
PATH_ONLY_PYTHON = 'FIXTURE = "tests/fixtures/sample.md"\n'


class EdgeHarness(FixtureProseHarness):
    def git(self, *args, content=None, cwd=None):
        call = subprocess.run(["git", *args], cwd=cwd or self.root, input=content, text=True,
                              capture_output=True, check=True, env=self.env)
        return call.stdout.strip()

    def stage_blob(self, relative, content):
        blob = self.git("hash-object", "-w", "--stdin", content=content)
        self.git("update-index", "--add", "--cacheinfo", "100644," + blob + "," + relative)

    def assert_flagged_on_write(self, fixture=FIXTURE, cwd=None):
        response = self.write_gate(fixture, FLAGGED_PROSE, cwd=cwd)

        self.assertEqual("deny", self.decision(response))
        self.assertEqual(PROSE_FAMILIES, self.prose_families_at(fixture))

    def assert_exempt_on_write(self, fixture=FIXTURE):
        response = self.write_gate(fixture, FLAGGED_PROSE)

        self.assertIsNone(self.decision(response))
        self.assertEqual(set(), self.prose_families_at(fixture))

    def assert_flagged_on_commit(self, fixture=FIXTURE):
        response = self.commit_gate()

        self.assertEqual("deny", self.decision(response))
        self.assertEqual(PROSE_FAMILIES, self.prose_families_at(fixture))

    def assert_exempt_on_commit(self, fixture=FIXTURE):
        self.commit_gate()

        self.assertEqual(set(), self.prose_families_at(fixture))


class PlumbingStagedChainAtTheCommitGate(EdgeHarness):
    def test_go_file_without_a_test_function_does_not_unlock_the_fixture(self):
        self.stage_blob(FIXTURE, FLAGGED_PROSE)
        self.stage_blob("pkg/loader_test.go", UNTESTED_GO)

        response = self.commit_gate(agent_type=OTHER_AGENT)

        self.assertEqual("deny", self.decision(response))
        self.assertEqual(PROSE_FAMILIES, self.prose_families_at(FIXTURE))

    def test_real_go_test_naming_the_fixture_unlocks_it(self):
        self.stage_blob(FIXTURE, FLAGGED_PROSE)
        self.stage_blob("pkg/loader_test.go", TESTED_GO)

        self.commit_gate(agent_type=OTHER_AGENT)

        self.assertEqual(set(), self.prose_families_at(FIXTURE))


class ReferrerKindAtTheWriteGate(EdgeHarness):
    def test_test_named_helper_outside_test_folders_holding_only_a_path_does_not_count(self):
        self.place("src/test_helpers.py", PATH_ONLY_PYTHON)

        self.assert_flagged_on_write()

    def test_plain_helper_inside_tests_naming_the_fixture_counts(self):
        self.place("tests/helpers.py", PATH_ONLY_PYTHON)

        self.assert_exempt_on_write()


class ReferrerSourceAtTheCommitGate(EdgeHarness):
    def test_referrer_on_disk_but_not_staged_does_not_count(self):
        self.place(FIXTURE, FLAGGED_PROSE)
        self.git("add", FIXTURE)
        self.place("tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_commit()

    def test_staged_referrer_deleted_from_disk_still_counts(self):
        self.place(FIXTURE, FLAGGED_PROSE)
        self.place("tests/test_loader.py", referring_test("sample.md"))
        self.stage_all()
        (self.root / "tests/test_loader.py").unlink()

        self.assert_exempt_on_commit()


class ReferenceMatching(EdgeHarness):
    def test_same_name_under_another_folder_does_not_unlock_the_fixture(self):
        self.place("tests/test_loader.py", referring_test("docs/sample.md"))

        self.assert_flagged_on_write()

    def test_parent_relative_path_from_a_nested_test_unlocks_the_fixture(self):
        self.place("tests/unit/test_x.py", referring_test("../fixtures/sample.md"))

        self.assert_exempt_on_write()

    def test_bare_name_shared_by_two_fixtures_unlocks_neither(self):
        self.place("tests/fixtures/b/sample.md", "Plain text.\n")
        self.place("tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write("tests/fixtures/a/sample.md")

    def test_extensionless_fixture_named_with_its_parent_passes_the_commit_gate(self):
        self.place("tests/fixtures/golden/OUTPUT", FLAGGED_PROSE)
        self.place("tests/test_loader.py", referring_test("golden/OUTPUT"))
        self.stage_all()

        self.assert_exempt_on_commit("tests/fixtures/golden/OUTPUT")

    def test_extensionless_fixture_named_bare_is_blocked_at_the_commit_gate(self):
        self.place("tests/fixtures/golden/OUTPUT", FLAGGED_PROSE)
        self.place("tests/test_loader.py", referring_test("OUTPUT"))
        self.stage_all()

        self.assert_flagged_on_commit("tests/fixtures/golden/OUTPUT")


class HiddenReferrersAtTheWriteGate(EdgeHarness):
    def test_referrer_in_a_git_ignored_folder_does_not_count(self):
        self.place(".gitignore", "build/\n")
        self.place("build/tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write()

    def test_referrer_under_node_modules_does_not_count(self):
        self.place("node_modules/pkg/tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write()

    def test_referrer_under_vendor_does_not_count(self):
        self.place("vendor/pkg/tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write()

    def test_referrer_under_a_dot_folder_does_not_count(self):
        self.place(".tools/tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write()

    def test_referrer_inside_a_nested_repository_does_not_count(self):
        self.place("sub/tests/test_loader.py", referring_test("sample.md"))
        self.git("init", "-q", cwd=self.root / "sub")
        disable_background_maintenance(self.root / "sub", self.env)

        self.assert_flagged_on_write()


class UnreadableReferrersAtTheWriteGate(EdgeHarness):
    def test_dangling_symlink_named_like_a_test_beside_a_real_referrer_keeps_the_exemption(self):
        (self.root / "tests").mkdir()
        (self.root / "tests/test_a_link.py").symlink_to(self.root / "missing.py")
        self.place("tests/test_loader.py", referring_test("sample.md"))

        self.assert_exempt_on_write()


class PlainFolderHarness(EdgeHarness):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.root / ".git")


class UnreadableReferrersInAPlainFolder(PlainFolderHarness):
    def test_fifo_in_tests_read_before_a_real_go_test_keeps_the_exemption(self):
        (self.root / "tests").mkdir()
        os.mkfifo(self.root / "tests/test_pipe.py")
        self.place("pkg/loader_test.go", TESTED_GO)

        self.assert_exempt_on_write()

    def test_dangling_symlink_in_tests_read_before_a_real_go_test_keeps_the_exemption(self):
        (self.root / "tests").mkdir()
        (self.root / "tests/test_link.py").symlink_to(self.root / "missing.py")
        self.place("pkg/loader_test.go", TESTED_GO)

        self.assert_exempt_on_write()


class HiddenReferrersInAPlainFolder(PlainFolderHarness):
    def test_referrer_inside_a_nested_repository_does_not_count(self):
        self.place("sub/tests/test_loader.py", referring_test("sample.md"))
        self.git("init", "-q", cwd=self.root / "sub")
        disable_background_maintenance(self.root / "sub", self.env)

        self.assert_flagged_on_write()

    def test_referrer_under_node_modules_does_not_count(self):
        self.place("node_modules/pkg/tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write()

    def test_plain_referrer_in_tests_counts(self):
        self.place("tests/test_loader.py", referring_test("sample.md"))

        self.assert_exempt_on_write()


class WriteGateRoot(EdgeHarness):
    def test_payload_cwd_above_the_fixture_repository_gives_no_exemption(self):
        self.place("tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write(cwd=self.root.parent)


class SharedNameAtTheWriteGate(EdgeHarness):
    def setUp(self):
        super().setUp()
        self.place("docs/sample.md", PLAIN_PROSE)

    def test_bare_name_shared_with_a_doc_outside_fixtures_does_not_unlock_the_fixture(self):
        self.place("tests/test_loader.py", referring_test("sample.md"))

        self.assert_flagged_on_write()

    def test_name_with_its_fixtures_folder_unlocks_the_fixture(self):
        self.place("tests/test_loader.py", referring_test("fixtures/sample.md"))

        self.assert_exempt_on_write()

    def test_full_relative_path_unlocks_the_fixture(self):
        self.place("tests/test_loader.py", referring_test(FIXTURE))

        self.assert_exempt_on_write()


class TestWriterDefinitionFixture(EdgeHarness):
    def test_used_fixture_shaped_like_an_installed_test_writer_definition_is_still_denied(self):
        definition = "tests/fixtures/.claude/agents/" + TEST_WRITER + ".md"
        self.place("tests/test_loader.py", referring_test(".claude/agents/" + TEST_WRITER + ".md"))

        response = self.write_gate(definition, PLAIN_PROSE)

        self.assertEqual("deny", self.decision(response))
        self.assertIn("test_writer_definition", self.rules_at(definition))


class EditToolAtTheWriteGate(EdgeHarness):
    def setUp(self):
        super().setUp()
        self.place(FIXTURE, PLAIN_PROSE)

    def edit_gate(self):
        return self.hook("PreToolUse", tool_name="Edit", agent_id="agent-1", agent_type=TEST_WRITER,
                         tool_input={"file_path": str(self.root / FIXTURE), "old_string": PLAIN_PROSE,
                                     "new_string": FLAGGED_PROSE})

    def test_edit_to_a_used_fixture_draws_no_prose_findings(self):
        self.place("tests/test_loader.py", referring_test("sample.md"))

        response = self.edit_gate()

        self.assertIsNone(self.decision(response))
        self.assertEqual(set(), self.prose_families_at(FIXTURE))

    def test_edit_to_an_unused_fixture_draws_prose_findings(self):
        response = self.edit_gate()

        self.assertEqual("deny", self.decision(response))
        self.assertEqual(PROSE_FAMILIES, self.prose_families_at(FIXTURE))


if __name__ == "__main__":
    unittest.main()
