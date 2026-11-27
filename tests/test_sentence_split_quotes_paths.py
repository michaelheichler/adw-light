import unittest

from in_memory_hook import InMemoryHookCase


SENTENCE_RULE = "ste_sentence_length"
NOTES = "docs/notes.md"
LONG_ON_LINE_THREE = ((SENTENCE_RULE, 3),)
NO_LONG_SENTENCE = ()
FIVE_QUOTED_SENTENCES = "The first check on the file prints \"ready.\" The second check on the file prints \"done.\" The third check on the file prints \"safe.\" The fourth check on the file prints \"clean.\" The fifth check on the file prints \"closed.\""
DEPLOY_PATH_OPENS_SENTENCE = "Ship the release from a clean main checkout. tools/deploy.sh runs the tests and builds a release copy in a temp folder, then it updates the install copy."
FILE_NAMES_IN_SHORT_SENTENCE = "The tool packs archive.tar.gz and runs app.test.js before each release so the review team sees one clean copy."
VERSION_AS_ONE_WORD = "Release v1.2.3 ships today. Release v1.2.3 holds the new gate and the old reader and the fixed parser so every team can update in one short step now."
PERIOD_INSIDE_SHORT_CODE_SPAN = "Run `make a.b` first. Then the gate reads each line once and stops early when the first finding blocks the write in the shared work folder for the team."
GERMAN_QUOTE_CLOSES_SENTENCE = "He wrote „Stop here.“ Then the gate reads each line once and stops early when the first finding blocks the write in the shared work folder for the team."
PATH_AFTER_USW = "We check files, folders, scripts usw. tools/deploy.sh then builds one release copy."
PATH_AFTER_BZW = "We read the header bzw. docs/notes.md before the gate runs."
DOTTED_WORDS_IN_LONG_SENTENCE = "The gate reads is.ok.so.on and no.one.saw.it once and then it stops early when the first finding blocks the write in one short pass today."
SINGLE_LETTER_DOTS_IN_LONG_SENTENCE = "The gate reads a.b.c d.e.f g.h.i j.k.l once and then it stops early when the first finding blocks the write in one pass."
TWO_LONG_SENTENCES_IN_ONE_CODE_SPAN = "`The gate reads each line once and then it stops early when the first finding blocks the write in the shared work folder for the whole team and goes home. The gate reads each line once and then it stops early when the first finding blocks the write in the shared work folder for the whole team and goes home.`"
INTERNAL_QUOTE_IN_LONG_SENTENCE = "The gate reads each line once and prints \"stop now\" and then it stops early when the first finding blocks the write in the shared work folder for us."
PATH_IN_LONG_SENTENCE = "The gate reads docs/notes.md once and then it stops early when the first finding blocks the write in the shared work folder for the whole team every night."
SHORT_SENTENCES_AFTER_QUOTED_LABELS = "The gate marks the first row \"held\". Nothing else changes. The next run marks the same row \"clear\". The write then goes through to the shared folder."
THIRTY_WORD_SENTENCE = "The gate reads each line of the notes file once and then it stops early when the first finding blocks the write in the shared work folder that the whole team uses."
PLAIN_LONG_SENTENCE = "The gate reads each line once and then it stops early when the first finding blocks the write in the shared work folder for the team."


class SentenceSplitting(InMemoryHookCase):
    def long_sentence_rows(self, paragraph):
        self.write(self.work / NOTES, "# Notes\n\n" + paragraph + "\n")
        return tuple(row for row in self.reported_rule_lines() if row[0] == SENTENCE_RULE)

    def test_five_short_quoted_sentences_on_one_line_are_measured_one_by_one(self):
        self.assertEqual(self.long_sentence_rows(FIVE_QUOTED_SENTENCES), NO_LONG_SENTENCE)

    def test_sentence_that_opens_with_the_deploy_script_path_is_measured_on_its_own(self):
        self.assertEqual(self.long_sentence_rows(DEPLOY_PATH_OPENS_SENTENCE), NO_LONG_SENTENCE)

    def test_short_sentence_holding_archive_and_test_file_names_passes(self):
        self.assertEqual(self.long_sentence_rows(FILE_NAMES_IN_SHORT_SENTENCE), NO_LONG_SENTENCE)

    def test_version_number_counts_as_one_word(self):
        self.assertEqual(self.long_sentence_rows(VERSION_AS_ONE_WORD), NO_LONG_SENTENCE)

    def test_period_inside_a_short_code_span_does_not_end_the_sentence(self):
        self.assertEqual(self.long_sentence_rows(PERIOD_INSIDE_SHORT_CODE_SPAN), NO_LONG_SENTENCE)

    def test_german_closing_quote_ends_the_sentence(self):
        self.assertEqual(self.long_sentence_rows(GERMAN_QUOTE_CLOSES_SENTENCE), NO_LONG_SENTENCE)

    def test_period_after_usw_before_a_path_keeps_the_sentence_short(self):
        self.assertEqual(self.long_sentence_rows(PATH_AFTER_USW), NO_LONG_SENTENCE)

    def test_period_after_bzw_before_a_path_keeps_the_sentence_short(self):
        self.assertEqual(self.long_sentence_rows(PATH_AFTER_BZW), NO_LONG_SENTENCE)

    def test_dotted_words_count_as_words_in_a_long_sentence(self):
        self.assertEqual(self.long_sentence_rows(DOTTED_WORDS_IN_LONG_SENTENCE), LONG_ON_LINE_THREE)

    def test_single_letter_dotted_groups_count_as_words_in_a_long_sentence(self):
        self.assertEqual(self.long_sentence_rows(SINGLE_LETTER_DOTS_IN_LONG_SENTENCE), LONG_ON_LINE_THREE)

    def test_two_long_sentences_inside_one_backtick_span_are_reported(self):
        self.assertEqual(self.long_sentence_rows(TWO_LONG_SENTENCES_IN_ONE_CODE_SPAN), LONG_ON_LINE_THREE)

    def test_long_sentence_holding_an_internal_quote_is_reported(self):
        self.assertEqual(self.long_sentence_rows(INTERNAL_QUOTE_IN_LONG_SENTENCE), LONG_ON_LINE_THREE)

    def test_long_sentence_holding_a_path_is_reported(self):
        self.assertEqual(self.long_sentence_rows(PATH_IN_LONG_SENTENCE), LONG_ON_LINE_THREE)

    def test_short_sentences_after_quoted_labels_are_measured_one_by_one(self):
        self.assertEqual(self.long_sentence_rows(SHORT_SENTENCES_AFTER_QUOTED_LABELS), NO_LONG_SENTENCE)

    def test_thirty_word_sentence_is_reported(self):
        self.assertEqual(self.long_sentence_rows(THIRTY_WORD_SENTENCE), LONG_ON_LINE_THREE)

    def test_plain_long_sentence_is_reported(self):
        self.assertEqual(self.long_sentence_rows(PLAIN_LONG_SENTENCE), LONG_ON_LINE_THREE)


if __name__ == "__main__":
    unittest.main()
