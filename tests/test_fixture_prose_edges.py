import unittest

from fixture_prose_home import (
    DENY, EXEMPT, FLAGGED, FLAGGED_PROSE, PLAIN_PROSE, TEST_WRITER, FixtureProseHome, referring_test, staged, untracked,
)


FIXTURE = "tests/fixtures/sample.md"
LOADER = "tests/test_loader.py"
GOLDEN = "tests/fixtures/golden/OUTPUT"
OTHER_AGENT = "general-purpose"
UNTESTED_GO = 'package pkg\n\nvar fixture = "../tests/fixtures/sample.md"\n'
TESTED_GO = (
    'package pkg\n\nimport "testing"\n\n'
    'func TestX(t *testing.T) {\n\t_ = "../tests/fixtures/sample.md"\n}\n'
)
PATH_ONLY_PYTHON = 'FIXTURE = "tests/fixtures/sample.md"\n'
DEFINITION = "tests/fixtures/.claude/agents/" + TEST_WRITER + ".md"
SHARED_DOC = {"docs/sample.md": PLAIN_PROSE}


class EdgeCases(FixtureProseHome):
    projects = {
        "plumbing_untested": {"index": {FIXTURE: FLAGGED_PROSE, "pkg/loader_test.go": UNTESTED_GO}},
        "plumbing_tested": {"index": {FIXTURE: FLAGGED_PROSE, "pkg/loader_test.go": TESTED_GO}},
        "helper_outside_tests": untracked({"src/test_helpers.py": PATH_ONLY_PYTHON}),
        "helper_inside_tests": untracked({"tests/helpers.py": PATH_ONLY_PYTHON}),
        "unstaged_referrer": {"disk": {FIXTURE: FLAGGED_PROSE, LOADER: referring_test("sample.md")},
                              "index": {FIXTURE: FLAGGED_PROSE}},
        "deleted_referrer": {"disk": {FIXTURE: FLAGGED_PROSE},
                             "index": {FIXTURE: FLAGGED_PROSE, LOADER: referring_test("sample.md")}},
        "other_folder": untracked({LOADER: referring_test("docs/sample.md")}),
        "nested_test": untracked({"tests/unit/test_x.py": referring_test("../fixtures/sample.md")}),
        "shared_bare_name": untracked({"tests/fixtures/b/sample.md": PLAIN_PROSE, LOADER: referring_test("sample.md")}),
        "golden_with_parent": staged({GOLDEN: FLAGGED_PROSE, LOADER: referring_test("golden/OUTPUT")}),
        "golden_bare": staged({GOLDEN: FLAGGED_PROSE, LOADER: referring_test("OUTPUT")}),
        "ignored": {"disk": {".gitignore": "build/\n", "build/" + LOADER: referring_test("sample.md")},
                    "ignore": [{"source": ".gitignore", "pattern": "build/"}]},
        "node_modules": untracked({"node_modules/pkg/" + LOADER: referring_test("sample.md")}),
        "vendor": untracked({"vendor/pkg/" + LOADER: referring_test("sample.md")}),
        "dot_folder": untracked({".tools/" + LOADER: referring_test("sample.md")}),
        "nested_repository": untracked({"sub/" + LOADER: referring_test("sample.md")}),
        "dangling_beside_real": untracked({"tests/test_a_link.py": "", LOADER: referring_test("sample.md")}),
        "plain_fifo": untracked({"pkg/loader_test.go": TESTED_GO}),
        "plain_dangling": untracked({"pkg/loader_test.go": TESTED_GO}),
        "plain_nested": untracked({"sub/" + LOADER: referring_test("sample.md")}),
        "plain_node_modules": untracked({"node_modules/pkg/" + LOADER: referring_test("sample.md")}),
        "plain_referrer": untracked({LOADER: referring_test("sample.md")}),
        "root_referrer": untracked({LOADER: referring_test("sample.md")}),
        "shared_doc_bare": untracked({**SHARED_DOC, LOADER: referring_test("sample.md")}),
        "shared_doc_folder": untracked({**SHARED_DOC, LOADER: referring_test("fixtures/sample.md")}),
        "shared_doc_full": untracked({**SHARED_DOC, LOADER: referring_test(FIXTURE)}),
        "definition": untracked({LOADER: referring_test(".claude/agents/" + TEST_WRITER + ".md")}),
        "edit_used": untracked({FIXTURE: PLAIN_PROSE, LOADER: referring_test("sample.md")}),
        "edit_unused": untracked({FIXTURE: PLAIN_PROSE}),
    }
    plain = ("plain_fifo", "plain_dangling", "plain_nested", "plain_node_modules", "plain_referrer")
    nested = {"nested_repository": ("sub",), "plain_nested": ("sub",)}
    links = {("dangling_beside_real", "tests/test_a_link.py"): "missing.py",
             ("plain_dangling", "tests/test_link.py"): "missing.py"}
    fifos = (("plain_fifo", "tests/test_pipe.py"),)

    def edit_verdict(self, name):
        response = self.edit_gate(name, FIXTURE, PLAIN_PROSE, FLAGGED_PROSE)
        return self.decision(response), self.prose_families_at(FIXTURE)


class PlumbingStagedChainAtTheCommitGate(EdgeCases):
    def test_go_file_without_a_test_function_does_not_unlock_the_fixture(self):
        self.assertEqual(FLAGGED, self.commit_verdict("plumbing_untested", FIXTURE, agent_type=OTHER_AGENT))

    def test_real_go_test_naming_the_fixture_unlocks_it(self):
        self.commit_gate("plumbing_tested", agent_type=OTHER_AGENT)

        self.assertEqual(set(), self.prose_families_at(FIXTURE))


class ReferrerKindAtTheWriteGate(EdgeCases):
    def test_test_named_helper_outside_test_folders_holding_only_a_path_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("helper_outside_tests", FIXTURE))

    def test_plain_helper_inside_tests_naming_the_fixture_counts(self):
        self.assertEqual(EXEMPT, self.write_verdict("helper_inside_tests", FIXTURE))


class ReferrerSourceAtTheCommitGate(EdgeCases):
    def test_referrer_on_disk_but_not_staged_does_not_count(self):
        self.assertEqual(FLAGGED, self.commit_verdict("unstaged_referrer", FIXTURE))

    def test_staged_referrer_deleted_from_disk_still_counts(self):
        self.commit_gate("deleted_referrer")

        self.assertEqual(set(), self.prose_families_at(FIXTURE))


class ReferenceMatching(EdgeCases):
    def test_same_name_under_another_folder_does_not_unlock_the_fixture(self):
        self.assertEqual(FLAGGED, self.write_verdict("other_folder", FIXTURE))

    def test_parent_relative_path_from_a_nested_test_unlocks_the_fixture(self):
        self.assertEqual(EXEMPT, self.write_verdict("nested_test", FIXTURE))

    def test_bare_name_shared_by_two_fixtures_unlocks_neither(self):
        self.assertEqual(FLAGGED, self.write_verdict("shared_bare_name", "tests/fixtures/a/sample.md"))

    def test_extensionless_fixture_named_with_its_parent_passes_the_commit_gate(self):
        self.commit_gate("golden_with_parent")

        self.assertEqual(set(), self.prose_families_at(GOLDEN))

    def test_extensionless_fixture_named_bare_is_blocked_at_the_commit_gate(self):
        self.assertEqual(FLAGGED, self.commit_verdict("golden_bare", GOLDEN))


class HiddenReferrersAtTheWriteGate(EdgeCases):
    def test_referrer_in_a_git_ignored_folder_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("ignored", FIXTURE))

    def test_referrer_under_node_modules_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("node_modules", FIXTURE))

    def test_referrer_under_vendor_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("vendor", FIXTURE))

    def test_referrer_under_a_dot_folder_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("dot_folder", FIXTURE))

    def test_referrer_inside_a_nested_repository_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("nested_repository", FIXTURE))


class UnreadableReferrersAtTheWriteGate(EdgeCases):
    def test_dangling_symlink_named_like_a_test_beside_a_real_referrer_keeps_the_exemption(self):
        self.assertEqual(EXEMPT, self.write_verdict("dangling_beside_real", FIXTURE))


class UnreadableReferrersInAPlainFolder(EdgeCases):
    def test_fifo_in_tests_read_before_a_real_go_test_keeps_the_exemption(self):
        self.assertEqual(EXEMPT, self.write_verdict("plain_fifo", FIXTURE))

    def test_dangling_symlink_in_tests_read_before_a_real_go_test_keeps_the_exemption(self):
        self.assertEqual(EXEMPT, self.write_verdict("plain_dangling", FIXTURE))


class HiddenReferrersInAPlainFolder(EdgeCases):
    def test_referrer_inside_a_nested_repository_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("plain_nested", FIXTURE))

    def test_referrer_under_node_modules_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("plain_node_modules", FIXTURE))

    def test_plain_referrer_in_tests_counts(self):
        self.assertEqual(EXEMPT, self.write_verdict("plain_referrer", FIXTURE))


class WriteGateRoot(EdgeCases):
    def test_payload_cwd_above_the_fixture_repository_gives_no_exemption(self):
        self.assertEqual(FLAGGED, self.write_verdict("root_referrer", FIXTURE, cwd=self.root))


class SharedNameAtTheWriteGate(EdgeCases):
    def test_bare_name_shared_with_a_doc_outside_fixtures_does_not_unlock_the_fixture(self):
        self.assertEqual(FLAGGED, self.write_verdict("shared_doc_bare", FIXTURE))

    def test_name_with_its_fixtures_folder_unlocks_the_fixture(self):
        self.assertEqual(EXEMPT, self.write_verdict("shared_doc_folder", FIXTURE))

    def test_full_relative_path_unlocks_the_fixture(self):
        self.assertEqual(EXEMPT, self.write_verdict("shared_doc_full", FIXTURE))


class TestWriterDefinitionFixture(EdgeCases):
    def test_used_fixture_shaped_like_an_installed_test_writer_definition_is_still_denied(self):
        response = self.write_gate("definition", DEFINITION, PLAIN_PROSE)

        self.assertEqual((DENY, True), (self.decision(response), "test_writer_definition" in self.rules_at(DEFINITION)))


class EditToolAtTheWriteGate(EdgeCases):
    def test_edit_to_a_used_fixture_draws_no_prose_findings(self):
        self.assertEqual(EXEMPT, self.edit_verdict("edit_used"))

    def test_edit_to_an_unused_fixture_draws_prose_findings(self):
        self.assertEqual(FLAGGED, self.edit_verdict("edit_unused"))


if __name__ == "__main__":
    unittest.main()
