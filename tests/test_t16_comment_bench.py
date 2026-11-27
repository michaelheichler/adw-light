import csv
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "tools" / "comment_bench.py"
SAMPLE = ROOT / "measure" / "comment" / "sample"
DEAD_PROXY = "http://127.0.0.1:9"
WHY_WHAT_SOURCES = {"all", "dome", "nlbse2024", "pascarella"}
NLBSE_COLUMNS = ("comment_sentence_id", "class", "comment_sentence", "partition", "instance_type", "category")


def words(*parts):
    return " ".join(parts)


BLOCKED = words("Get", "the", "value")
VAGUE_MARKER = "TODO: " + words("fix", "this")
PASSING = words("Retry", "the", "request", "because", "the", "server", "drops", "idle", "sockets")
PASSING_TOO = words("Use", "a", "linked", "list", "because", "head", "inserts", "dominate")


def offline_env(state):
    env = dict(os.environ)
    env.update(
        ADW_STATE_ROOT=str(state),
        HTTP_PROXY=DEAD_PROXY, HTTPS_PROXY=DEAD_PROXY, http_proxy=DEAD_PROXY, https_proxy=DEAD_PROXY,
        NO_PROXY="", no_proxy="",
    )
    return env


def run_bench(root, *args):
    return subprocess.run(
        [sys.executable, "-I", str(BENCH), *args],
        cwd=root, env=offline_env(root / "state"), capture_output=True, text=True, timeout=300,
    )


def row(source, language, label, comment):
    marker = "# " if language in ("python", "ruby") else "// "
    return {"source": source, "language": language, "label": label, "comment": comment, "snippet": marker + comment + "\nint x = 1;\n"}


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
    return path


def labelled_rows():
    return {
        "dome": [
            row("dome", "java", "what", BLOCKED),
            row("dome", "java", "what", VAGUE_MARKER),
            row("dome", "java", "what", PASSING),
            row("dome", "java", "why", PASSING_TOO),
            row("dome", "python", "what", BLOCKED),
        ],
        "pascarella": [
            row("pascarella", "java", "summary", BLOCKED),
            row("pascarella", "java", "expand", PASSING),
            row("pascarella", "java", "rationale", VAGUE_MARKER),
            row("pascarella", "java", "directive", PASSING),
            row("pascarella", "java", "summary+rationale", BLOCKED),
        ],
        "avantes": [
            row("avantes", "java", "general", BLOCKED),
            row("avantes", "java", "notice", PASSING),
            row("avantes", "python", "general", BLOCKED),
            row("avantes", "java", "unknown-category", BLOCKED),
        ],
        "codesearchnet": [
            row("codesearchnet", "python", "unlabelled", PASSING),
            row("codesearchnet", "go", "unlabelled", PASSING_TOO),
        ],
    }


def write_folder(folder, groups):
    for name, rows in groups.items():
        write_rows(folder / f"{name}.jsonl", rows)
    return folder


def measured(result):
    return result.returncode, json.loads(result.stdout) if result.returncode == 0 else result.stderr


def counts(what_rows, why_rows, blocked_what, blocked_why, precision, recall):
    return {
        "what_rows": what_rows, "why_rows": why_rows, "blocked_what": blocked_what,
        "blocked_why": blocked_why, "precision": precision, "recall": recall,
    }


def pass_counts(pass_rows, blocked, rate):
    return {"pass_rows": pass_rows, "blocked": blocked, "rate": rate}


def nlbse_cache(cache):
    folder = cache / "nlbse2024"
    folder.mkdir(parents=True)
    records = [
        ("1", "summary", "1", BLOCKED), ("1", "rational", "0", BLOCKED),
        ("2", "summary", "1", BLOCKED), ("3", "summary", "1", PASSING),
        ("4", "rational", "1", PASSING_TOO), ("4", "summary", "0", PASSING_TOO),
        ("5", "rational", "1", VAGUE_MARKER), ("6", "summary", "1", BLOCKED), ("6", "rational", "1", BLOCKED),
    ]
    with (folder / "java.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(NLBSE_COLUMNS)
        for key, category, positive, text in records:
            writer.writerow((key, "Sample", text, "0", positive, category))
    return cache


def out_of_range(result):
    found = []
    for source, numbers in result["java_precision"].items():
        found += [(source, key) for key in ("precision", "recall") if not 0 <= numbers[key] <= 1]
        found += [(source, "blocked_what")] if numbers["blocked_what"] > numbers["what_rows"] else []
    for language, numbers in result["wrong_block"].items():
        found += [(language, "rate")] if not 0 <= numbers["rate"] <= 1 else []
    return found


def sample_line_count():
    return sum(1 for path in SAMPLE.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


class MeasureReportsPrecisionAndWrongBlocks(unittest.TestCase):
    def test_java_precision_counts_blocked_what_rows_against_blocked_why_rows_per_source(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            rows = write_folder(root / "rows", labelled_rows())
            code, result = measured(run_bench(root, "measure", "--rows", str(rows), "--json", "--workers", "1"))
        self.assertEqual((code, result["java_precision"]), (0, {
            "all": counts(5, 2, 3, 1, 0.75, 0.6),
            "dome": counts(3, 1, 2, 0, 1.0, 0.6667),
            "pascarella": counts(2, 1, 1, 1, 0.5, 0.5),
        }))

    def test_wrong_block_rate_counts_why_directive_and_human_rows_per_language(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            rows = write_folder(root / "rows", labelled_rows())
            code, result = measured(run_bench(root, "measure", "--rows", str(rows), "--json", "--workers", "1"))
        self.assertEqual((code, result["wrong_block"]), (0, {
            "go": pass_counts(1, 0, 0.0),
            "java": pass_counts(5, 2, 0.4),
            "python": pass_counts(2, 1, 0.5),
        }))

    def test_rows_total_counts_every_row_read_including_unmapped_labels(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            rows = write_folder(root / "rows", labelled_rows())
            code, result = measured(run_bench(root, "measure", "--rows", str(rows), "--json", "--workers", "1"))
        self.assertEqual((code, result["rows"]), (0, 16))

    def test_parallel_workers_give_the_same_numbers_as_one_worker(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            rows = write_folder(root / "rows", labelled_rows())
            single = measured(run_bench(root, "measure", "--rows", str(rows), "--json", "--workers", "1"))
            pooled = measured(run_bench(root, "measure", "--rows", str(rows), "--json", "--workers", "2"))
        self.assertEqual(pooled, single)

    def test_rows_option_accepts_a_single_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            single = write_rows(root / "dome.jsonl", labelled_rows()["dome"])
            code, result = measured(run_bench(root, "measure", "--rows", str(single), "--json", "--workers", "1"))
        self.assertEqual((code, result["rows"], result["java_precision"]["all"]), (0, 5, counts(3, 1, 2, 0, 1.0, 0.6667)))

    def test_source_option_keeps_only_rows_of_that_source(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            rows = write_folder(root / "rows", labelled_rows())
            code, result = measured(run_bench(root, "measure", "--rows", str(rows), "--source", "pascarella", "--json", "--workers", "1"))
        self.assertEqual((code, result), (0, {
            "rows": 5,
            "java_precision": {"all": counts(2, 1, 1, 1, 0.5, 0.5), "pascarella": counts(2, 1, 1, 1, 0.5, 0.5)},
            "wrong_block": {"java": pass_counts(2, 1, 0.5)},
        }))

    def test_rates_without_a_denominator_come_back_as_null(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            rows = write_rows(root / "rows.jsonl", [row("dome", "java", "what", PASSING)])
            code, result = measured(run_bench(root, "measure", "--rows", str(rows), "--json", "--workers", "1"))
        self.assertEqual((code, result["java_precision"]["dome"]), (0, counts(1, 0, 0, 0, None, 0.0)))

    def test_text_report_exits_zero_when_a_rate_has_no_denominator(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            rows = write_rows(root / "rows.jsonl", [row("dome", "java", "what", PASSING), row("avantes", "go", "general", PASSING)])
            result = run_bench(root, "measure", "--rows", str(rows), "--workers", "1")
        self.assertEqual((result.returncode, bool(result.stdout.strip())), (0, True))


class FixtureSampleRunsOffline(unittest.TestCase):
    def test_committed_sample_measures_offline_with_every_java_source(self):
        with TemporaryDirectory() as temp:
            code, result = measured(run_bench(Path(temp), "measure", "--rows", str(SAMPLE), "--json", "--workers", "1"))
        self.assertEqual(
            (code, result["rows"], set(result["java_precision"]), out_of_range(result)),
            (0, sample_line_count(), WHY_WHAT_SOURCES, []),
        )

    def test_committed_sample_reports_languages_beyond_java(self):
        with TemporaryDirectory() as temp:
            code, result = measured(run_bench(Path(temp), "measure", "--rows", str(SAMPLE), "--json", "--workers", "1"))
        self.assertEqual((code, {"java", "python", "go"} <= set(result["wrong_block"])), (0, True))

    def test_offline_measure_reads_a_prepared_cache(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            cache = nlbse_cache(root / "cache")
            code, result = measured(run_bench(root, "measure", "--offline", "--cache", str(cache), "--source", "nlbse2024", "--json", "--workers", "1"))
        self.assertEqual((code, result["rows"], result["java_precision"]["nlbse2024"]), (0, 6, counts(3, 2, 2, 1, 0.6667, 0.6667)))

    def test_offline_measure_with_an_empty_cache_fails_without_fetching(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "cache"
            cache.mkdir()
            result = run_bench(root, "measure", "--offline", "--cache", str(cache), "--source", "nlbse2024", "--json")
            left = sorted(path.name for path in cache.rglob("*"))
        self.assertEqual((result.returncode, left), (1, []))

    def test_unknown_source_is_rejected_with_usage_exit_code(self):
        with TemporaryDirectory() as temp:
            result = run_bench(Path(temp), "measure", "--rows", str(SAMPLE), "--source", "nosuch")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
