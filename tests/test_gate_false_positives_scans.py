from false_positive_home import PASSED, PLAIN_MODULE, FalsePositiveHome
import unittest


SENTENCE_RULE = "ste_sentence_length"
LIST_RULE = "oversized_list"
SHORT_THEN_CODE_SENTENCE = (
    "# Notes\n\nThe gate reads each command once before it runs. git_calls now walks every nested script,"
    " alias, and function body that the command can reach in one pass.\n"
)
LONG_LIST = "# Index\n\n" + "".join(f"- Entry {number} names one saved fact.\n" for number in range(1, 13))
ONE_LONG_SENTENCE = SHORT_THEN_CODE_SENTENCE.replace("runs. git_calls", "runs, and git_calls")
SHORT_NOTE = "# Notes\n\nThe gate reads each command once before it runs. ok.\n"
CODE_SPAN_TAIL = (
    "ok. `git_calls` now walks every nested script, alias, and function body that the command can reach"
    " in one pass through the whole tree, so a script near the size cap still reads in time before the hook stops.\n"
)
CODE_SPAN_SENTENCE = SHORT_NOTE.replace("ok.\n", CODE_SPAN_TAIL)
MEMORY_INDEX = ".claude/projects/x/memory/MEMORY.md"


class InlinePython(FalsePositiveHome):
    def test_python_c_that_imports_inert_modules_and_prints_passes(self):
        repo = self.committed_repo({"src/app.py": PLAIN_MODULE})
        self.assertEqual(self.bash(repo, "python3 -c 'import sys, traceback; print(1)'"), PASSED)

    def test_python_c_that_opens_a_file_for_writing_is_refused(self):
        repo = self.committed_repo({"src/app.py": PLAIN_MODULE})
        self.assertEqual(self.bash(repo, "python3 -c \"open('x','w')\""), ("deny", ["inline_interpreter_write"]))


class ProseScans(FalsePositiveHome):
    def test_sentence_that_starts_with_a_code_identifier_is_measured_on_its_own(self):
        repo = self.committed_repo({"README.md": "Short.\n"})
        self.assertEqual(self.write(repo, repo / "docs/notes.md", SHORT_THEN_CODE_SENTENCE), PASSED)

    def test_same_words_joined_into_one_sentence_are_reported_as_too_long(self):
        repo = self.committed_repo({"README.md": "Short.\n"})
        self.assertEqual(self.write(repo, repo / "docs/notes.md", ONE_LONG_SENTENCE), (None, [SENTENCE_RULE]))

    def test_new_long_sentence_that_opens_with_a_code_span_is_the_agents_own_finding(self):
        repo = self.committed_repo({"docs/notes.md": SHORT_NOTE})
        self.assertEqual(self.write(repo, repo / "docs/notes.md", CODE_SPAN_SENTENCE), (None, [SENTENCE_RULE]))

    def test_long_list_in_a_memory_index_under_home_is_not_reported_as_oversized(self):
        self.assertEqual(self.write(self.home, self.home / MEMORY_INDEX, LONG_LIST), PASSED)

    def test_long_list_in_a_memory_index_nested_below_docs_is_reported_as_oversized(self):
        nested_index = self.home / "docs" / ".claude/projects/p/memory/MEMORY.md"
        self.assertEqual(self.write(self.home, nested_index, LONG_LIST), (None, [LIST_RULE]))

    def test_same_long_list_in_a_docs_file_is_reported_as_oversized(self):
        self.assertEqual(self.write(self.home, self.home / "docs/list.md", LONG_LIST), (None, [LIST_RULE]))


if __name__ == "__main__":
    unittest.main()
