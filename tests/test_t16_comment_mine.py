import csv
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
MINE = ROOT / "tools" / "comment_mine.py"
SAMPLE = ROOT / "measure" / "comment" / "sample"
COMMITTED = ROOT / "measure" / "comment" / "tables"
DEAD_PROXY = "http://127.0.0.1:9"
TABLE_FILES = ("why-words.tsv", "causal-links.tsv", "what-openers.tsv", "length.tsv", "marker-tags.tsv", "marker-verbs.tsv")


def words(*parts):
    return " ".join(parts)


WHY_COMMENT = words("Retry", "because", "the", "server", "drops", "sockets")
WHAT_COMMENT = words("Returns", "the", "value")
LONG_COMMENT = words(*(["word"] * 15)) + "\n" + words(*(["word"] * 15))
WHY_ROWS = 12
WHAT_ROWS = 20


def offline_env():
    env = dict(os.environ)
    env.update(HTTP_PROXY=DEAD_PROXY, HTTPS_PROXY=DEAD_PROXY, http_proxy=DEAD_PROXY, https_proxy=DEAD_PROXY, NO_PROXY="", no_proxy="")
    return env


def run_mine(root, rows, out):
    return subprocess.run(
        [sys.executable, "-I", str(MINE), "--rows", str(rows), "--out", str(out)],
        cwd=root, env=offline_env(), capture_output=True, text=True, timeout=300,
    )


def row(source, label, comment, language="java"):
    return {"source": source, "language": language, "label": label, "comment": comment, "snippet": "// " + comment + "\n"}


def labelled_rows():
    return (
        [row("dome", "why", WHY_COMMENT) for _ in range(WHY_ROWS)]
        + [row("dome", "what", WHAT_COMMENT) for _ in range(WHAT_ROWS)]
        + [row("avantes", "general", words("a", "b")), row("avantes", "general", words("a", "b", "c")), row("avantes", "general", LONG_COMMENT)]
        + [row("avantes", "notice", words("first", "line") + "\n" + words("second", "line"))]
    )


def marker_rows():
    return [
        row("avantes", "todo", "TODO: " + words("Add", "csv", "export")),
        row("avantes", "todo", "todo " + words("add", "paging:", "later")),
        row("avantes", "todo", "TODO " + words("Remove", "the", "shim")),
        row("avantes", "todo", "FIXME: " + words("Add", "a", "lock")),
        row("avantes", "todo", "HACK: " + words("Drop", "the", "retry")),
        row("avantes", "notice", "NOTE: " + words("Add", "nothing")),
    ]


def mined(rows):
    with TemporaryDirectory() as temp:
        root = Path(temp)
        source = root / "rows.jsonl"
        source.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
        result = run_mine(root, source, root / "tables")
        tables = {path.name: read_table(path) for path in (root / "tables").glob("*.tsv")}
    return result.returncode, tables


def read_table(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle, delimiter="\t"))


def records(table):
    header, *body = table
    return [dict(zip(header, line)) for line in body]


def ragged(tables):
    return sorted(name for name, table in tables.items() if len(table) < 1 or {len(line) for line in table} != {len(table[0])})


class MinedTablesAreReadableFiles(unittest.TestCase):
    def test_mining_the_committed_sample_writes_six_regular_tables(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            result = run_mine(root, SAMPLE, root / "tables")
            tables = {path.name: read_table(path) for path in (root / "tables").glob("*.tsv")}
        self.assertEqual((result.returncode, sorted(tables), ragged(tables)), (0, sorted(TABLE_FILES), []))

    def test_committed_tables_have_the_columns_the_miner_writes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            run_mine(root, SAMPLE, root / "tables")
            fresh = {name: read_table(root / "tables" / name)[0] for name in TABLE_FILES}
        committed = {name: read_table(COMMITTED / name) for name in TABLE_FILES}
        self.assertEqual(
            ({name: table[0] for name, table in committed.items()}, ragged(committed)),
            (fresh, []),
        )


class MinedTablesCountTheLabelledRows(unittest.TestCase):
    def test_why_words_keep_words_that_lean_towards_why_rows(self):
        code, tables = mined(labelled_rows())
        found = {item["word"]: item["why_rows"] for item in records(tables["why-words.tsv"])}
        self.assertEqual((code, found), (0, {name: str(WHY_ROWS) for name in ("because", "drops", "retry", "server", "sockets")}))

    def test_causal_links_count_why_and_what_rows_per_phrase(self):
        code, tables = mined(labelled_rows())
        found = {item["phrase"]: (item["why_rows"], item["what_rows"]) for item in records(tables["causal-links.tsv"])}
        self.assertEqual((code, found["because"], found["since"]), (0, (str(WHY_ROWS), "0"), ("0", "0")))

    def test_what_openers_list_first_words_seen_often_enough(self):
        code, tables = mined(labelled_rows())
        found = [(item["first_word"], item["what_rows"], item["why_rows"]) for item in records(tables["what-openers.tsv"])]
        self.assertEqual((code, found), (0, [("returns", str(WHAT_ROWS), "0")]))

    def test_length_table_gives_percentiles_and_shares_per_group(self):
        code, tables = mined(labelled_rows())
        found = {(item["role"], item["source"], item["language"]): item for item in records(tables["length.tsv"])}
        general = found[("human", "avantes", "java")]
        self.assertEqual(
            (code, general["rows"], general["p10_words"], general["p50_words"], general["p90_words"], general["share_at_most_25_words"], general["share_one_line"]),
            (0, "4", "2", "4", "30", "0.7500", "0.5000"),
        )

    def test_marker_tags_count_case_colon_and_capital_per_tag(self):
        code, tables = mined(marker_rows())
        found = {item["tag"]: (item["rows"], item["uppercase_share"], item["colon_share"], item["capitalized_next_share"]) for item in records(tables["marker-tags.tsv"])}
        self.assertEqual((code, found), (0, {
            "TODO": ("3", "0.6667", "0.3333", "0.6667"),
            "FIXME": ("1", "1.0000", "1.0000", "1.0000"),
            "HACK": ("1", "1.0000", "1.0000", "1.0000"),
            "NOTE": ("1", "1.0000", "1.0000", "1.0000"),
        }))

    def test_marker_verbs_count_first_words_after_work_tags_only(self):
        code, tables = mined(marker_rows())
        found = [tuple(line) for line in tables["marker-verbs.tsv"][1:]]
        self.assertEqual((code, found), (0, [("add", "3")]))


if __name__ == "__main__":
    unittest.main()
