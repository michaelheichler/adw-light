import unittest

from test_t16_comment_mine import mined, records, row, words


def signed_marker_rows():
    return [
        row("avantes", "todo", "TODO(alice): " + words("Add", "csv", "export")),
        row("avantes", "todo", "TODO(bob): " + words("Add", "paging", "support")),
        row("avantes", "todo", "TODO: " + words("Add", "a", "cache")),
    ]


class MinedMarkersReadPastTheAuthor(unittest.TestCase):
    def test_marker_verbs_count_the_word_after_the_author_and_never_the_author(self):
        code, tables = mined(signed_marker_rows())
        found = [tuple(line) for line in tables["marker-verbs.tsv"][1:]]
        self.assertEqual((code, found), (0, [("add", "3")]))

    def test_marker_tags_count_the_colon_and_capital_after_an_author(self):
        code, tables = mined(signed_marker_rows())
        found = {item["tag"]: (item["rows"], item["colon_share"], item["capitalized_next_share"]) for item in records(tables["marker-tags.tsv"])}
        self.assertEqual((code, found), (0, {"TODO": ("3", "1.0000", "1.0000")}))


if __name__ == "__main__":
    unittest.main()
