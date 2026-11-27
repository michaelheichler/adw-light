import re
import unittest

from after_run_home import CAUGHT, GATE, OLD_TEST, AfterRunCase, Project, project_files
from fake_repo_home import SIZE_TEST


REPO = "repo"
PROSE_RULE = "utilize"
FLAGGED_PROSE = "We utilize this tool for the build.\n"
CHANGED_TEST = SIZE_TEST.replace("test_size", "test_resized")
HIDDEN_WRITE = "python3 tools/bulk_write.py"
CAPPED_PATHS = tuple(f"docs/bulk/note_{number:03}.md" for number in range(200))
UNCAPPED_PATHS = tuple(f"docs/bulk/note_{number:03}.md" for number in range(201))
HEAVY_SOURCES = tuple(f"src/blob_{number}.py" for number in range(9))
HEAVY_TEXT = "value = 1\n" * 95_000


class OverflowCap(AfterRunCase):
    projects = {REPO: Project(project_files())}

    def write_all(self, paths, content="Short.\n"):
        for relative in paths:
            self.change(REPO, relative, content)

    def run_command(self, effect):
        self.start(REPO)
        _, verdict, posted = self.pair(REPO, HIDDEN_WRITE, effect)
        return verdict, posted

    def skipped_counts(self, posted):
        notice = posted.get("hookSpecificOutput", {}).get("additionalContext", "")
        return re.findall(r"\d+", notice)

    def overflow_rows(self):
        return [row["rule"] for row in self.timing_rows() if row["rule"] == "snapshot_overflow"]

    def modify_test_after_bulk(self):
        self.write_all(CAPPED_PATHS)
        self.change(REPO, OLD_TEST, CHANGED_TEST)

    def delete_test_after_bulk(self):
        self.write_all(CAPPED_PATHS)
        self.remove(REPO, OLD_TEST)

    def overflow_sources_and_modify_test(self):
        self.write_all(HEAVY_SOURCES, HEAVY_TEXT)
        self.change(REPO, OLD_TEST, CHANGED_TEST)

    def test_modified_test_sorted_past_the_cap_blocks_and_notes_one_skipped_path(self):
        """The test fails when a real bug enters the code it exercises."""
        verdict, posted = self.run_command(self.modify_test_after_bulk)
        observed = (verdict, self.blocked_rules(), self.finding_paths(GATE, REPO), self.overflow_rows(), self.skipped_counts(posted))
        self.assertEqual(observed, (CAUGHT, [GATE], [OLD_TEST], ["snapshot_overflow"], ["1"]))

    def test_deleted_test_sorted_past_the_cap_blocks(self):
        """The test fails when a real bug enters the code it exercises."""
        verdict, _ = self.run_command(self.delete_test_after_bulk)
        self.assertEqual((verdict, self.blocked_rules(), self.finding_paths(GATE, REPO)), (CAUGHT, [GATE], [OLD_TEST]))

    def test_source_overflow_still_blocks_a_test_change_from_the_path_list(self):
        """The test fails when a real bug enters the code it exercises."""
        verdict, _ = self.run_command(self.overflow_sources_and_modify_test)
        self.assertEqual((verdict, self.blocked_rules(), self.finding_paths(GATE, REPO)), (CAUGHT, [GATE], [OLD_TEST]))

    def test_diff_without_test_change_scans_the_first_paths_for_prose_and_notes_the_rest(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        _, posted = self.run_command(lambda: self.write_all(UNCAPPED_PATHS, FLAGGED_PROSE))
        observed = (self.finding_paths(PROSE_RULE, REPO), self.finding_rules(), self.skipped_counts(posted))
        self.assertEqual(observed, (list(CAPPED_PATHS), [PROSE_RULE], ["1"]))

    def test_overflow_row_counts_the_paths_past_the_cap_as_skipped(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.run_command(lambda: self.write_all(UNCAPPED_PATHS, FLAGGED_PROSE))
        skipped = [row.get("skipped") for row in self.timing_rows() if row["rule"] == "snapshot_overflow"]
        self.assertEqual(skipped, [len(UNCAPPED_PATHS) - len(CAPPED_PATHS)])


if __name__ == "__main__":
    unittest.main()
