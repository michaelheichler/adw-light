from fake_repo_home import (DEBT_FILE, DENY, GATE, LOCK, OBSERVED, OLD_TEST, PASSED, PRINCIPLE, README, REFUSED, SIZE_TEST,
                            TEST_WRITER, FakeRepoCase, Repo, loose)
import unittest


TEMP = "/" + "tmp"
PATH_NOTED = (None, ["hard_coded_system_path"])
REFUSED_WITH_DEBT_NOTED = ((DENY, [GATE, LOCK]), [(LOCK, OBSERVED)])
CACHE_TEST = "tests/test_cache.py"
CACHE_SCRIPT = "scripts/cache.sh"
IGNORES = {".gitignore": "scratch/\ntests/test_tmp.py\n"}
SCRATCH_TEST = "scratch/test_probe.py"
KEPT_TEST = "scratch/test_kept.py"
CHANGED_TEST = SIZE_TEST + "\n\ndef test_sum():\n    \"\"\"" + PRINCIPLE + "\"\"\"\n    assert 1 + 1 == 2\n"


def cache_script(name):
    return "#!/bin/sh\ncache=" + TEMP + "/" + name + "$$\nmkdir -p \"$cache\"\n"


def cache_test(name):
    literal = "f\"" + TEMP + "/" + name + "{os.getpid()}\""
    return ("import os\n\n\ndef test_cache_folder():\n    \"\"\"" + PRINCIPLE + "\"\"\"\n"
            "    assert os.path.basename(" + literal + ").startswith(\"" + name + "\")\n")


class MirroredTempLiterals(FakeRepoCase):
    projects = {
        "tracked": Repo({CACHE_SCRIPT: cache_script("adwprobe-")}),
        "other-test": Repo({"tests/test_other.py": cache_test("adwprobe-")}),
        "uncommitted": Repo(README, {CACHE_SCRIPT: cache_script("adwprobe-")}),
        "loose": loose({CACHE_SCRIPT: cache_script("adwprobe-")}),
        "abc": Repo({CACHE_SCRIPT: cache_script("abc")}),
        "abcd": Repo({CACHE_SCRIPT: cache_script("abcd")}),
    }

    def writer_write(self, project, prefix):
        return self.write(project, CACHE_TEST, cache_test(prefix), agent_type=TEST_WRITER)

    def test_temp_literal_whose_prefix_a_tracked_script_uses_passes(self):
        self.assertEqual(self.writer_write("tracked", "adwprobe-"), PASSED)

    def test_temp_literal_whose_prefix_only_another_test_uses_is_noted(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.writer_write("other-test", "adwprobe-"), PATH_NOTED)

    def test_temp_literal_whose_prefix_only_an_uncommitted_script_uses_is_noted(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.writer_write("uncommitted", "adwprobe-"), PATH_NOTED)

    def test_temp_literal_outside_a_git_repo_is_noted(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.writer_write("loose", "adwprobe-"), PATH_NOTED)

    def test_temp_literal_with_a_three_character_prefix_is_noted_even_when_tracked(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.writer_write("abc", "abc"), PATH_NOTED)

    def test_temp_literal_with_a_four_character_prefix_passes_when_tracked(self):
        self.assertEqual(self.writer_write("abcd", "abcd"), PASSED)


class IgnoredScratchTests(FakeRepoCase):
    projects = {
        "ignores": Repo(IGNORES),
        "debt": Repo({**IGNORES, OLD_TEST: DEBT_FILE}),
        "kept": Repo({**IGNORES, OLD_TEST: DEBT_FILE, KEPT_TEST: SIZE_TEST}),
        "loose": loose(IGNORES),
    }

    def test_gitignored_scratch_test_passes_the_test_write_gate(self):
        self.assertEqual(self.write("ignores", SCRATCH_TEST, SIZE_TEST), PASSED)

    def test_gitignored_scratch_test_passes_the_wording_debt_lock(self):
        self.assertEqual(self.write("debt", SCRATCH_TEST, SIZE_TEST), PASSED)

    def test_gitignored_test_folder_inside_a_scratch_folder_passes(self):
        self.assertEqual(self.write("debt", "scratch/tests/test_probe.py", SIZE_TEST), PASSED)

    def test_gitignored_file_under_the_top_level_tests_folder_is_refused(self):
        self.assertEqual(self.write("ignores", "tests/test_tmp.py", SIZE_TEST), (DENY, [GATE]))

    def test_gitignored_file_under_the_top_level_tests_folder_is_refused_and_its_debt_noted(self):
        verdict = self.write("debt", "tests/test_tmp.py", SIZE_TEST)
        self.assertEqual((verdict, self.ledger_outcomes(LOCK)), REFUSED_WITH_DEBT_NOTED)

    def test_tracked_test_inside_an_ignored_folder_is_refused_and_its_debt_noted(self):
        verdict = self.write("kept", KEPT_TEST, CHANGED_TEST)
        self.assertEqual((verdict, self.ledger_outcomes(LOCK)), REFUSED_WITH_DEBT_NOTED)

    def test_scratch_test_outside_a_git_repo_is_refused(self):
        self.assertEqual(self.write("loose", SCRATCH_TEST, SIZE_TEST), REFUSED)


if __name__ == "__main__":
    unittest.main()
