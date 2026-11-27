import unittest

from fake_repo_home import FakeRepoCase, Repo
from in_memory_hook import DENY, PASSED, PRINCIPLE, TEST_WRITER, InMemoryHookCase


TEMP = "/" + "tmp"
PASSIVE = (DENY, ["passive_voice"])
PATH_NOTED = (None, ["hard_coded_system_path"])
CACHE_SCRIPT = "scripts/cache.sh"
CACHE_TEST = "tests/test_cache.py"


def cache_script(name):
    return "#!/bin/sh\ncache=" + TEMP + "/" + name + "$$\nmkdir -p \"$cache\"\n"


def path_test(literal):
    return ("import os\n\n\ndef test_cache_folder():\n    \"\"\"" + PRINCIPLE + "\"\"\"\n"
            "    pid = os.getpid()\n    assert os.path.basename(" + literal + ")\n")


class PassiveBeforeAFlagAndAByPhrase(InMemoryHookCase):
    def verdict(self, sentence):
        return self.write(self.work / "docs/notes.md", "# Notes\n\n" + sentence + "\n")

    def test_passive_with_a_glued_flag_and_a_by_phrase_is_refused(self):
        self.assertEqual(self.verdict("The budget was cut -a lot by the board."), PASSIVE)

    def test_passive_with_a_short_flag_and_a_by_phrase_is_refused(self):
        self.assertEqual(self.verdict("The plan was set -x by the lead."), PASSIVE)


class TempLiteralsThatLeaveATrackedPrefix(FakeRepoCase):
    projects = {prefix.strip("/"): Repo({CACHE_SCRIPT: cache_script(prefix)}) for prefix in ("abcd/", "adwprobe-")}

    def writer_write(self, prefix, literal):
        return self.write(prefix.strip("/"), CACHE_TEST, path_test(literal), agent_type=TEST_WRITER)

    def test_glob_joined_to_a_second_absolute_path_by_a_colon_keeps_its_path_finding(self):
        """A test must verify what the code does for its client, never how it does it."""
        literal = '"' + TEMP + "/abcd/*:" + "/" + "opt/secret/x" + '"'
        self.assertEqual(self.writer_write("abcd/", literal), PATH_NOTED)

    def test_glob_followed_by_a_machine_root_keeps_its_path_finding(self):
        """A test must verify what the code does for its client, never how it does it."""
        literal = '"' + TEMP + "/abcd/*/" + "private/var/x" + '"'
        self.assertEqual(self.writer_write("abcd/", literal), PATH_NOTED)

    def test_placeholder_followed_by_dot_dot_keeps_its_path_finding(self):
        """A test must verify what the code does for its client, never how it does it."""
        literal = 'f"' + TEMP + '/adwprobe-{os.getpid()}/../etc"'
        self.assertEqual(self.writer_write("adwprobe-", literal), PATH_NOTED)

    def test_placeholder_followed_by_a_plain_file_name_passes(self):
        literal = 'f"' + TEMP + '/adwprobe-{pid}/run.txt"'
        self.assertEqual(self.writer_write("adwprobe-", literal), PASSED)


if __name__ == "__main__":
    unittest.main()
