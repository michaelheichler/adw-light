from false_positive_home import DENY, PASSED, FalsePositiveHome
import unittest


TEST_WRITER = "adw-test-writer"
TEMP = "/" + "tmp"
PASSIVE = (DENY, ["passive_voice"])
PATH_FOUND = (DENY, ["hard_coded_path"])
CACHE_SCRIPT = "scripts/cache.sh"
CACHE_TEST = "tests/test_cache.py"


def cache_script(name):
    return "#!/bin/sh\ncache=" + TEMP + "/" + name + "$$\nmkdir -p \"$cache\"\n"


def path_test(literal):
    return "import os\n\n\ndef test_cache_folder():\n    pid = os.getpid()\n    assert os.path.basename(" + literal + ")\n"


class PassiveBeforeAFlagAndAByPhrase(FalsePositiveHome):
    def verdict(self, sentence):
        repo = self.committed_repo({"README.md": "Short.\n"})
        return self.write(repo, repo / "docs/notes.md", "# Notes\n\n" + sentence + "\n")

    def test_passive_with_a_glued_flag_and_a_by_phrase_is_refused(self):
        self.assertEqual(self.verdict("The budget was cut -a lot by the board."), PASSIVE)

    def test_passive_with_a_short_flag_and_a_by_phrase_is_refused(self):
        self.assertEqual(self.verdict("The plan was set -x by the lead."), PASSIVE)


class TempLiteralsThatLeaveATrackedPrefix(FalsePositiveHome):
    def writer_write(self, prefix, literal):
        repo = self.committed_repo({CACHE_SCRIPT: cache_script(prefix)})
        tool_input = {"file_path": str(repo / CACHE_TEST), "content": path_test(literal)}
        payload = {"cwd": str(repo), "agent_type": TEST_WRITER, "tool_name": "Write", "tool_input": tool_input}
        return self.hook("PreToolUse", payload)

    def test_glob_joined_to_a_second_absolute_path_by_a_colon_keeps_its_path_finding(self):
        literal = '"' + TEMP + "/abcd/*:" + "/" + "opt/secret/x" + '"'
        self.assertEqual(self.writer_write("abcd/", literal), PATH_FOUND)

    def test_glob_followed_by_a_machine_root_keeps_its_path_finding(self):
        literal = '"' + TEMP + "/abcd/*/" + "private/var/x" + '"'
        self.assertEqual(self.writer_write("abcd/", literal), PATH_FOUND)

    def test_placeholder_followed_by_dot_dot_keeps_its_path_finding(self):
        literal = 'f"' + TEMP + '/adwprobe-{os.getpid()}/../etc"'
        self.assertEqual(self.writer_write("adwprobe-", literal), PATH_FOUND)

    def test_placeholder_followed_by_a_plain_file_name_passes(self):
        literal = 'f"' + TEMP + '/adwprobe-{pid}/run.txt"'
        self.assertEqual(self.writer_write("adwprobe-", literal), PASSED)


if __name__ == "__main__":
    unittest.main()
