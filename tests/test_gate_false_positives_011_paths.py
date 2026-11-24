from false_positive_home import DEBT_FILE, DENY, GATE, IDENTITY, LOCK, OLD_TEST, PASSED, REFUSED, SIZE_TEST, FalsePositiveHome
import subprocess
import unittest


TEST_WRITER = "adw-test-writer"
TEMP = "/" + "tmp"
PATH_FOUND = (DENY, ["hard_coded_path"])
LOCKED = (DENY, [GATE, LOCK])
CACHE_TEST = "tests/test_cache.py"
CACHE_SCRIPT = "scripts/cache.sh"
IGNORES = {".gitignore": "scratch/\ntests/test_tmp.py\n"}
SCRATCH_TEST = "scratch/test_probe.py"
CHANGED_TEST = SIZE_TEST + "\n\ndef test_sum():\n    assert 1 + 1 == 2\n"


def cache_script(name):
    return "#!/bin/sh\ncache=" + TEMP + "/" + name + "$$\nmkdir -p \"$cache\"\n"


def cache_test(name):
    literal = "f\"" + TEMP + "/" + name + "{os.getpid()}\""
    return "import os\n\n\ndef test_cache_folder():\n    assert os.path.basename(" + literal + ").startswith(\"" + name + "\")\n"


class MirroredTempLiterals(FalsePositiveHome):
    def writer_write(self, cwd, relative, content):
        tool_input = {"file_path": str(cwd / relative), "content": content}
        payload = {"cwd": str(cwd), "agent_type": TEST_WRITER, "tool_name": "Write", "tool_input": tool_input}
        return self.hook("PreToolUse", payload)

    def test_temp_literal_whose_prefix_a_tracked_script_uses_passes(self):
        repo = self.committed_repo({CACHE_SCRIPT: cache_script("adwprobe-")})
        self.assertEqual(self.writer_write(repo, CACHE_TEST, cache_test("adwprobe-")), PASSED)

    def test_temp_literal_whose_prefix_only_another_test_uses_is_refused(self):
        repo = self.committed_repo({"tests/test_other.py": cache_script("adwprobe-")})
        self.assertEqual(self.writer_write(repo, CACHE_TEST, cache_test("adwprobe-")), PATH_FOUND)

    def test_temp_literal_whose_prefix_only_an_uncommitted_script_uses_is_refused(self):
        repo = self.committed_repo({"README.md": "Short.\n"})
        self.place(repo, {CACHE_SCRIPT: cache_script("adwprobe-")})
        self.assertEqual(self.writer_write(repo, CACHE_TEST, cache_test("adwprobe-")), PATH_FOUND)

    def test_temp_literal_outside_a_git_repo_is_refused(self):
        loose = self.root / "loose"
        self.place(loose, {CACHE_SCRIPT: cache_script("adwprobe-")})
        self.assertEqual(self.writer_write(loose, CACHE_TEST, cache_test("adwprobe-")), PATH_FOUND)

    def test_temp_literal_with_a_three_character_prefix_is_refused_even_when_tracked(self):
        repo = self.committed_repo({CACHE_SCRIPT: cache_script("abc")})
        self.assertEqual(self.writer_write(repo, CACHE_TEST, cache_test("abc")), PATH_FOUND)

    def test_temp_literal_with_a_four_character_prefix_passes_when_tracked(self):
        repo = self.committed_repo({CACHE_SCRIPT: cache_script("abcd")})
        self.assertEqual(self.writer_write(repo, CACHE_TEST, cache_test("abcd")), PASSED)


class IgnoredScratchTests(FalsePositiveHome):
    def repo_with_debt(self):
        return self.committed_repo({**IGNORES, OLD_TEST: DEBT_FILE})

    def test_gitignored_scratch_test_passes_the_test_write_gate(self):
        repo = self.committed_repo(IGNORES)
        self.assertEqual(self.write(repo, repo / SCRATCH_TEST, SIZE_TEST), PASSED)

    def test_gitignored_scratch_test_passes_the_wording_debt_lock(self):
        repo = self.repo_with_debt()
        self.assertEqual(self.write(repo, repo / SCRATCH_TEST, SIZE_TEST), PASSED)

    def test_gitignored_test_folder_inside_a_scratch_folder_passes(self):
        repo = self.repo_with_debt()
        self.assertEqual(self.write(repo, repo / "scratch/tests/test_probe.py", SIZE_TEST), PASSED)

    def test_gitignored_file_under_the_top_level_tests_folder_is_refused(self):
        repo = self.committed_repo(IGNORES)
        self.assertEqual(self.write(repo, repo / "tests/test_tmp.py", SIZE_TEST), (DENY, [GATE]))

    def test_gitignored_file_under_the_top_level_tests_folder_meets_the_wording_debt_lock(self):
        repo = self.repo_with_debt()
        self.assertEqual(self.write(repo, repo / "tests/test_tmp.py", SIZE_TEST), LOCKED)

    def test_tracked_test_inside_an_ignored_folder_is_refused(self):
        repo = self.repo_with_debt()
        self.place(repo, {"scratch/test_kept.py": SIZE_TEST})
        subprocess.run(["git", "-C", str(repo), "add", "-f", "scratch/test_kept.py"], check=True)
        subprocess.run(["git", "-C", str(repo), *IDENTITY, "commit", "-qm", "kept"], check=True)
        self.assertEqual(self.write(repo, repo / "scratch/test_kept.py", CHANGED_TEST), LOCKED)

    def test_scratch_test_outside_a_git_repo_is_refused(self):
        loose = self.root / "loose"
        self.place(loose, IGNORES)
        self.assertEqual(self.write(loose, loose / SCRATCH_TEST, SIZE_TEST), REFUSED)


if __name__ == "__main__":
    unittest.main()
