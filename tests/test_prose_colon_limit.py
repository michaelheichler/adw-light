import json
import unittest

from commit_view_home import DENY, CommitViewHome
from in_memory_hook import PASSED, InMemoryHookCase


COLON = "prose_colon"
CONFIG_NAME = ".agent-discipline.json"
SENTENCES = (
    "The plan has one step: auth runs first.",
    "Lint runs second: it checks types too.",
    "The deploy waits for one thing: a green build.",
    "Rollback stays simple: revert the tag.",
    "The cache holds a single entry for every host: the last good build.",
    "Logs rotate daily: old ones move to the archive.",
)
REFUSED = (DENY, True)
NO_COLON_FINDING = (None, False)
NO_COMMIT_FINDING = (None, frozenset())


def colon_sentences(count):
    return " ".join(SENTENCES[:count])


def python_comment(count):
    return "size = 1\n# " + colon_sentences(count) + "\n"


def limit(value):
    return json.dumps({"prose_colon_limit": value})


class ColonLimitInWrites(InMemoryHookCase):
    files = {
        "tight/" + CONFIG_NAME: limit(1),
        "loose/" + CONFIG_NAME: limit(5),
        "strict/" + CONFIG_NAME: limit(0),
    }

    def colon_verdict(self, relative, content, folder=None):
        cwd = self.work / folder if folder else self.work
        decision, _ = self.write(self.work / relative, content, cwd=cwd)
        return decision, COLON in self.ledger_rules(relative)

    def test_markdown_document_with_six_sentence_colons_passes(self):
        self.assertEqual(self.write(self.work / "docs/plan.md", "# Plan\n\n" + colon_sentences(6) + "\n"), PASSED)

    def test_markdown_document_passes_under_a_project_limit_of_zero(self):
        content = "# Plan\n\n" + colon_sentences(2) + "\n"
        self.assertEqual(self.colon_verdict("strict/plan.md", content, "strict"), NO_COLON_FINDING)

    def test_text_file_with_three_sentence_colons_passes(self):
        self.assertEqual(self.colon_verdict("docs/notes.txt", colon_sentences(3) + "\n"), NO_COLON_FINDING)

    def test_text_file_with_four_sentence_colons_on_one_line_is_refused(self):
        self.assertEqual(self.colon_verdict("docs/notes.txt", colon_sentences(4) + "\n"), REFUSED)

    def test_text_file_with_four_sentence_colons_on_four_lines_is_refused(self):
        self.assertEqual(self.colon_verdict("docs/notes.txt", "\n".join(SENTENCES[:4]) + "\n"), REFUSED)

    def test_python_comment_with_three_sentence_colons_raises_no_colon_finding(self):
        _, colon_found = self.colon_verdict("src/size.py", python_comment(3))
        self.assertFalse(colon_found)

    def test_python_comment_with_four_sentence_colons_raises_a_colon_finding(self):
        _, colon_found = self.colon_verdict("src/size.py", python_comment(4))
        self.assertTrue(colon_found)

    def test_project_limit_of_one_refuses_two_sentence_colons(self):
        self.assertEqual(self.colon_verdict("tight/notes.txt", colon_sentences(2) + "\n", "tight"), REFUSED)

    def test_project_limit_of_five_passes_five_sentence_colons(self):
        self.assertEqual(self.colon_verdict("loose/notes.txt", colon_sentences(5) + "\n", "loose"), NO_COLON_FINDING)

    def test_project_limit_of_five_refuses_six_sentence_colons(self):
        self.assertEqual(self.colon_verdict("loose/notes.txt", colon_sentences(6) + "\n", "loose"), REFUSED)

    def test_project_limit_of_zero_refuses_a_single_sentence_colon(self):
        self.assertEqual(self.colon_verdict("strict/notes.txt", colon_sentences(1) + "\n", "strict"), REFUSED)


class ColonLimitInCommitMessages(CommitViewHome):
    projects = {"repo": {"README.md": "Notes.\n"}}

    def commit_verdict(self, count):
        return self.message_finding("repo", 'git commit -m "' + colon_sentences(count) + '"')

    def test_commit_message_with_three_sentence_colons_passes(self):
        self.assertEqual(self.commit_verdict(3), NO_COMMIT_FINDING)

    def test_commit_message_with_four_sentence_colons_is_refused(self):
        self.assertEqual(self.commit_verdict(4), (DENY, frozenset({COLON})))


if __name__ == "__main__":
    unittest.main()
