from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
HOME_TREE = TOOLS.parent
for folder in (TOOLS, HOME_TREE / "engine"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from comment_measure import WHAT, WHY, expectations, role
from comment_rows import Row
from comment_sources import SOURCES, WHY_WHAT_SOURCES, dataset_rows, read_rows
from lib.config_roots import dataset_cache_root

TABLES = HOME_TREE / "measure" / "comment" / "tables"
WORDS_FILE = HOME_TREE / "engine" / "lib" / "comment_shape_words.json"
WORD_RE = re.compile(r"[a-z][a-z'-]*[a-z]|[a-z]")
TAG_RE = re.compile(r"^(?P<tag>todo|fixme|xxx|hack|note|bug|optimize|done)\b(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
NEXT_WORD_RE = re.compile(r"^[\s:\-]*(?:\([^)]*\))?[\s:\-]*(?P<word>[A-Za-z][\w'-]*)")
COLON_RE = re.compile(r"^\s*(?:\([^)]*\))?\s*:")
WORK_TAGS = frozenset({"TODO", "FIXME", "HACK"})
MIN_WHY_ROWS = 10
MIN_OPENER_ROWS = 20
MIN_VERB_ROWS = 2
TOP_WORDS = 80
TOP_OPENERS = 60
PERCENTILES = (10, 25, 50, 75, 90)


def words(text: str) -> set[str]:
    return set(WORD_RE.findall(text.lower()))


def log_odds(hits: int, total: int, other_hits: int, other_total: int) -> float:
    mine = (hits + 0.5) / (total - hits + 0.5)
    theirs = (other_hits + 0.5) / (other_total - other_hits + 0.5)
    return round(math.log(mine / theirs), 3)


def share(part: int, whole: int) -> str:
    return f"{part / whole:.4f}" if whole else "n/a"


def labelled(rows: list[Row], table: dict[tuple[str, str], str]) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = {WHAT: [], WHY: []}
    for row in rows:
        kind = role(row, table)
        if row.source in WHY_WHAT_SOURCES and kind in groups:
            groups[kind].append(row)
    return groups


def word_rows(groups: dict[str, list[Row]]) -> list[list[object]]:
    why = Counter(word for row in groups[WHY] for word in words(row.comment))
    what = Counter(word for row in groups[WHAT] for word in words(row.comment))
    why_total, what_total = len(groups[WHY]), len(groups[WHAT])
    scored = [
        [word, count, what[word], share(count, why_total), share(what[word], what_total), log_odds(count, why_total, what[word], what_total)]
        for word, count in why.items() if count >= MIN_WHY_ROWS
    ]
    scored = [row for row in scored if row[5] > 0]
    return sorted(scored, key=lambda row: (-row[5], row[0]))[:TOP_WORDS]


def causal_rows(groups: dict[str, list[Row]], phrases: Iterable[str]) -> list[list[object]]:
    why_total, what_total = len(groups[WHY]), len(groups[WHAT])
    rows = []
    for phrase in phrases:
        pattern = re.compile(r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b", re.IGNORECASE)
        why = sum(1 for row in groups[WHY] if pattern.search(row.comment))
        what = sum(1 for row in groups[WHAT] if pattern.search(row.comment))
        rows.append([phrase, why, what, share(why, why_total), share(what, what_total), log_odds(why, why_total, what, what_total)])
    return sorted(rows, key=lambda row: (-row[5], row[0]))


def opener_rows(groups: dict[str, list[Row]]) -> list[list[object]]:
    openers = {kind: Counter(_opener(row.comment) for row in group) for kind, group in groups.items()}
    totals = openers[WHAT] + openers[WHY]
    ranked = [word for word, count in totals.most_common() if word and count >= MIN_OPENER_ROWS][:TOP_OPENERS]
    return [[word, openers[WHAT][word], openers[WHY][word], share(openers[WHAT][word], totals[word])] for word in ranked]


def _opener(comment: str) -> str:
    found = WORD_RE.findall(comment.lower())
    return found[0] if found else ""


def length_rows(rows: list[Row], table: dict[tuple[str, str], str], max_words: int) -> list[list[object]]:
    groups: dict[tuple[str, str, str], list[Row]] = {}
    for row in rows:
        groups.setdefault((role(row, table), row.source, row.language), []).append(row)
    output = []
    for (kind, source, language), group in sorted(groups.items()):
        counts = sorted(len(row.comment.split()) for row in group)
        cuts = [counts[min(len(counts) - 1, len(counts) * point // 100)] for point in PERCENTILES]
        short = sum(1 for count in counts if count <= max_words)
        single = sum(1 for row in group if "\n" not in row.comment)
        output.append([kind, source, language, len(group), *cuts, share(short, len(group)), share(single, len(group))])
    return output


def marker_rows(rows: list[Row]) -> tuple[list[list[object]], list[list[object]]]:
    tags: dict[str, Counter] = {}
    verbs: Counter = Counter()
    for row in rows:
        match = TAG_RE.match(row.comment.strip())
        if match is None:
            continue
        tag, rest = match.group("tag"), match.group("rest")
        follow = NEXT_WORD_RE.match(rest)
        counts = tags.setdefault(tag.upper(), Counter())
        counts.update(rows=1, upper=tag.isupper(), colon=bool(COLON_RE.match(rest)), capital=bool(follow and follow.group("word")[0].isupper()))
        if tag.upper() in WORK_TAGS and follow:
            verbs[follow.group("word").lower()] += 1
    tag_table = [
        [tag, counts["rows"], share(counts["upper"], counts["rows"]), share(counts["colon"], counts["rows"]), share(counts["capital"], counts["rows"])]
        for tag, counts in sorted(tags.items(), key=lambda item: (-item[1]["rows"], item[0]))
    ]
    verb_table = [[verb, count] for verb, count in sorted(verbs.items(), key=lambda item: (-item[1], item[0])) if count >= MIN_VERB_ROWS]
    return tag_table, verb_table


def write_table(path: Path, header: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(header)] + ["\t".join(str(value) for value in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mine(rows: list[Row], out: Path) -> list[Path]:
    table = expectations()
    settings = json.loads(WORDS_FILE.read_text(encoding="utf-8"))
    groups = labelled(rows, table)
    shares = ["why_rows", "what_rows", "why_share", "what_share", "log_odds"]
    tag_table, verb_table = marker_rows(rows)
    tables = {
        "why-words.tsv": (["word", *shares], word_rows(groups)),
        "causal-links.tsv": (["phrase", *shares], causal_rows(groups, settings["causal_links"])),
        "what-openers.tsv": (["first_word", "what_rows", "why_rows", "what_share"], opener_rows(groups)),
        "length.tsv": (
            ["role", "source", "language", "rows", *(f"p{point}_words" for point in PERCENTILES), f"share_at_most_{settings['max_words']}_words", "share_one_line"],
            length_rows(rows, table, int(settings["max_words"])),
        ),
        "marker-tags.tsv": (["tag", "rows", "uppercase_share", "colon_share", "capitalized_next_share"], tag_table),
        "marker-verbs.tsv": (["first_word_after_tag", "rows"], verb_table),
    }
    written = []
    for name, (header, body) in tables.items():
        write_table(out / name, header, body)
        written.append(out / name)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="comment_mine.py")
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--rows", type=Path, default=None)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--out", type=Path, default=TABLES)
    args = parser.parse_args(argv)
    try:
        rows = read_rows(args.rows) if args.rows else dataset_rows(list(SOURCES), args.cache or dataset_cache_root(), args.offline)
        for path in mine(rows, args.out):
            print(f"wrote {path}")
    except (OSError, ValueError, KeyError) as error:
        print(f"comment_mine: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
