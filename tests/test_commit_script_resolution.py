import time
import unittest

from commit_view_home import EMPTY, PROSE_COMMIT, SCANNED, CommitViewHome, script


ROOT_FINDER = 'dev=$(cd "$(dirname "$0")/.." && pwd -P)\nbash "$dev/tools/build.sh"\n'
FILLER_LINE = "echo filler\n"
UNDER_CAP_SECONDS = 5.0


def script_with_filler(total_bytes):
    count = (total_bytes - len(script(PROSE_COMMIT))) // len(FILLER_LINE)
    return script(FILLER_LINE * count + PROSE_COMMIT)


class ResolutionHome(CommitViewHome):
    projects = {
        "root_clean": {"tools/run.sh": script(ROOT_FINDER), "tools/build.sh": script("echo built\n")},
        "root_commit": {"tools/run.sh": script(ROOT_FINDER), "tools/build.sh": script(PROSE_COMMIT)},
        "big": {"big.sh": script_with_filler(500 * 1024)},
    }

    def timed_finding(self, name, command):
        started = time.monotonic()
        finding = self.message_finding(name, command)
        return finding, time.monotonic() - started


class ScriptsThatFindTheirRoot(ResolutionHome):
    def test_sibling_script_without_a_commit_passes(self):
        self.assertEqual({}, self.gate("root_clean", "bash tools/run.sh"))

    def test_sibling_script_with_a_commit_is_scanned_for_the_message(self):
        self.assertEqual(SCANNED, self.message_finding("root_commit", "bash tools/run.sh"))


class MissingScripts(ResolutionHome):
    def gate(self, command):
        return super().gate(EMPTY, command)

    def test_existence_check_before_running_a_missing_script_passes(self):
        self.assertEqual({}, self.gate("[ -f ./missing.sh ] && bash ./missing.sh"))

    def test_if_block_guarding_a_missing_script_passes(self):
        self.assertEqual({}, self.gate("if [ -f x ]; then bash x; fi"))

    def test_sourcing_an_absent_rc_file_passes(self):
        """Feed input, check the return value."""
        self.assertEqual({}, self.gate("source $HOME/.rc 2>/dev/null"))


class LargeScripts(ResolutionHome):
    def test_script_just_under_the_size_cap_is_scanned_in_time(self):
        """The test fails when a real bug enters the code it exercises."""
        finding, seconds = self.timed_finding("big", "bash ./big.sh")

        self.assertEqual((SCANNED, True), (finding, seconds < UNDER_CAP_SECONDS))


if __name__ == "__main__":
    unittest.main()
