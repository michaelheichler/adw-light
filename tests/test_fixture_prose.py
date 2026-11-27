import unittest

from fixture_prose_home import (
    EXEMPT, FLAGGED, FLAGGED_PROSE, NARRATED_CODE, PLAIN_PROSE, PROSE_FAMILIES, FixtureProseHome, referring_test, staged,
    untracked,
)


LOADER = "tests/test_loader.py"
SAMPLE = "tests/fixtures/sample.md"
GOLDEN = "tests/fixtures/golden/OUTPUT"
COUNTER = "tests/fixtures/counter.py"


class FixtureProseCases(FixtureProseHome):
    projects = {
        "none": untracked({}),
        "by_name": untracked({LOADER: referring_test("sample.md")}),
        "by_path": untracked({LOADER: referring_test(SAMPLE)}),
        "by_parent": untracked({LOADER: referring_test("golden/OUTPUT")}),
        "by_bare_output": untracked({LOADER: referring_test("OUTPUT")}),
        "by_longer_name": untracked({LOADER: referring_test("old_sample.md")}),
        "in_dot_folder": untracked({".cache/" + LOADER: referring_test("sample.md")}),
        "in_manifest": untracked({"tests/fixtures/manifest.json": '{"files": ["sample.md"]}\n'}),
        "in_readme": untracked({"tests/README.md": "The loader reads sample.md.\n"}),
        "by_counter": untracked({LOADER: referring_test("counter.py")}),
        "commit_by_name": staged({SAMPLE: FLAGGED_PROSE, LOADER: referring_test("sample.md")}),
        "commit_snapshot": staged({"src/__snapshots__/sample.md": FLAGGED_PROSE,
                                   LOADER: referring_test("src/__snapshots__/sample.md")}),
        "commit_unnamed": staged({SAMPLE: FLAGGED_PROSE}),
        "commit_docs": staged({"docs/sample.md": FLAGGED_PROSE, LOADER: referring_test("sample.md")}),
        "commit_src_fixtures": staged({"src/fixtures/sample.md": FLAGGED_PROSE, LOADER: referring_test("sample.md")}),
        "commit_manifest": staged({SAMPLE: FLAGGED_PROSE, "tests/fixtures/manifest.json": '{"files": ["sample.md"]}\n'}),
        "commit_counter": staged({COUNTER: NARRATED_CODE, LOADER: referring_test("counter.py")}),
    }


class UsedFixturesAtTheWriteGate(FixtureProseCases):
    def test_fixture_under_tests_fixtures_named_by_file_name_draws_no_prose_findings(self):
        self.assertEqual(EXEMPT, self.write_verdict("by_name", SAMPLE))

    def test_fixture_under_tests_fixtures_named_by_relative_path_draws_no_prose_findings(self):
        self.assertEqual(EXEMPT, self.write_verdict("by_path", SAMPLE))

    def test_fixture_under_testdata_draws_no_prose_findings(self):
        self.assertEqual(EXEMPT, self.write_verdict("by_name", "testdata/sample.md"))

    def test_fixture_under_dunder_fixtures_draws_no_prose_findings(self):
        self.assertEqual(EXEMPT, self.write_verdict("by_name", "src/__fixtures__/sample.md"))

    def test_fixture_under_dunder_snapshots_draws_no_prose_findings(self):
        self.assertEqual(EXEMPT, self.write_verdict("by_name", "src/__snapshots__/sample.md"))

    def test_fixture_under_dunder_mocks_draws_no_prose_findings(self):
        self.assertEqual(EXEMPT, self.write_verdict("by_name", "src/__mocks__/sample.md"))

    def test_extensionless_fixture_named_with_its_parent_draws_no_prose_findings(self):
        self.assertEqual(EXEMPT, self.write_verdict("by_parent", GOLDEN))


class UnusedFixturesAtTheWriteGate(FixtureProseCases):
    def test_fixture_no_test_names_is_flagged(self):
        self.assertEqual(FLAGGED, self.write_verdict("none", SAMPLE))

    def test_prose_outside_fixture_folders_is_flagged_even_when_a_test_names_it(self):
        self.assertEqual(FLAGGED, self.write_verdict("by_name", "docs/sample.md"))

    def test_fixtures_folder_outside_a_test_folder_is_flagged_even_when_a_test_names_it(self):
        self.assertEqual(FLAGGED, self.write_verdict("by_name", "src/fixtures/sample.md"))

    def test_reference_inside_a_dot_folder_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("in_dot_folder", SAMPLE))

    def test_reference_inside_another_fixture_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("in_manifest", SAMPLE))

    def test_reference_inside_the_tests_readme_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("in_readme", SAMPLE))

    def test_extensionless_fixture_named_without_its_parent_is_reported_but_allowed(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual((None, set(PROSE_FAMILIES)), self.write_verdict("by_bare_output", GOLDEN))

    def test_longer_file_name_that_contains_the_fixture_name_does_not_count(self):
        self.assertEqual(FLAGGED, self.write_verdict("by_longer_name", SAMPLE))


class FixtureCodeAndWriterAtTheWriteGate(FixtureProseCases):
    def test_used_code_fixture_still_draws_comment_findings(self):
        response = self.write_gate("by_counter", COUNTER, NARRATED_CODE)

        self.assertEqual(("deny", True), (self.decision(response), "what_comment" in self.rules_at(COUNTER)))

    def test_fixture_write_by_another_agent_is_denied_by_the_test_gate(self):
        response = self.write_gate("none", "tests/fixtures/x.md", PLAIN_PROSE, agent_type="general-purpose")

        self.assertEqual(("deny", True), (self.decision(response), "test_write_gate" in self.rules_at("tests/fixtures/x.md")))

    def test_fixture_write_by_the_test_writer_is_allowed(self):
        response = self.write_gate("none", "tests/fixtures/x.md", PLAIN_PROSE)

        self.assertEqual((None, False), (self.decision(response), "test_write_gate" in self.rules_at("tests/fixtures/x.md")))


class FixturesAtTheCommitGate(FixtureProseCases):
    def test_fixture_named_by_file_name_draws_no_prose_findings(self):
        self.commit_gate("commit_by_name")

        self.assertEqual(set(), self.prose_families_at(SAMPLE))

    def test_snapshot_named_by_relative_path_draws_no_prose_findings(self):
        self.commit_gate("commit_snapshot")

        self.assertEqual(set(), self.prose_families_at("src/__snapshots__/sample.md"))

    def test_fixture_no_test_names_is_blocked_with_prose_findings(self):
        self.assertEqual(FLAGGED, self.commit_verdict("commit_unnamed", SAMPLE))

    def test_prose_outside_fixture_folders_is_blocked_even_when_a_test_names_it(self):
        self.assertEqual(FLAGGED, self.commit_verdict("commit_docs", "docs/sample.md"))

    def test_fixtures_folder_outside_a_test_folder_is_blocked_even_when_a_test_names_it(self):
        self.assertEqual(FLAGGED, self.commit_verdict("commit_src_fixtures", "src/fixtures/sample.md"))

    def test_reference_inside_another_fixture_does_not_count(self):
        self.assertEqual(FLAGGED, self.commit_verdict("commit_manifest", SAMPLE))

    def test_used_code_fixture_still_draws_comment_findings(self):
        response = self.commit_gate("commit_counter")

        self.assertEqual(("deny", True), (self.decision(response), "what_comment" in self.rules_at(COUNTER)))


if __name__ == "__main__":
    unittest.main()
