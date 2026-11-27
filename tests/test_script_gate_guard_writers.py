import unittest

from fake_repo_home import PASSED, SIZE_TEST, FakeRepoCase, Repo


COMPOSED = "r\u00e9.sh"
DECOMPOSED = "re\u0301.sh"
GUARDED_RUN = "[ -f run.sh ] && bash run.sh"
GUARDED_PROJ = "[ -f proj/run.sh ] && bash proj/run.sh"
MISSING = "missing"


def guarded(name):
    return f"[ -f {name} ] && bash {name}"


class WriterHome(FakeRepoCase):
    projects = {MISSING: Repo({"README.md": "Short.\n", "tests/test_a.py": SIZE_TEST}, {"work/keep.txt": "1\n"})}

    def missing(self, command):
        return self.bash(MISSING, command)


class WriteToAnotherFileKeepsTheGuard(WriterHome):
    def test_earlier_write_to_an_unrelated_file_passes(self):
        self.assertEqual(PASSED, self.missing("echo 1 > notes.txt; " + GUARDED_RUN))

    def test_later_write_to_an_unrelated_file_passes(self):
        self.assertEqual(PASSED, self.missing(GUARDED_RUN + "; echo 1 > notes.txt"))

    def test_write_beside_a_guarded_script_in_a_folder_passes(self):
        self.assertEqual(PASSED, self.missing("echo 1 > proj/notes.txt; " + GUARDED_PROJ))

    def test_guarded_composed_accent_name_passes(self):
        self.assertEqual(PASSED, self.missing(guarded(COMPOSED)))

    def test_guarded_decomposed_accent_name_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(PASSED, self.missing(guarded(DECOMPOSED)))


class QuotedTextKeepsTheGuard(WriterHome):
    def test_guard_after_closed_quotes_passes(self):
        self.assertEqual(PASSED, self.missing('echo "x" "y"; [ -f x ] && bash x'))

    def test_guard_after_a_closed_heredoc_passes(self):
        self.assertEqual(PASSED, self.missing("cat <<EOF\n1\nEOF\n" + GUARDED_RUN))

    def test_closed_quote_after_the_guard_passes(self):
        self.assertEqual(PASSED, self.missing(GUARDED_RUN + '; echo "abc"'))

    def test_closed_heredoc_after_the_guard_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(PASSED, self.missing(GUARDED_RUN + "; cat <<EOF\nabc\nEOF"))


if __name__ == "__main__":
    unittest.main()
