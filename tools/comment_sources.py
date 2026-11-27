from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from comment_rows import Row, code_rows, text_row

USER_AGENT = "adw-lite-comment-bench/1"
TIMEOUT = 180
RETRIES = 4
RETRY_CODES = frozenset({429, 500, 502, 503, 504})
PASCARELLA_FILE = "comments.tar.gz"
PASCARELLA_URLS = (
    "https://data.4tu.nl/file/dd32e1c8-2a38-458c-85c8-401ed5df673e/ea5f045c-c063-4c66-9acb-ce28f82aa99f",
    "https://zenodo.org/api/records/2628361/files/JavaCommentsClassification.tar.gz/content",
)
NLBSE_FILE = "java.csv"
NLBSE_URLS = (
    "https://raw.githubusercontent.com/nlbse2024/code-comment-classification/0a1d728e7058bd572c35e97a88efff1f5b92b1ae/java/input/java.csv",
)
DOME_FILE = "comment_data_final.json"
DOME_URLS = (
    "https://raw.githubusercontent.com/ICSE-DOME/DOME/cfde8e43feaae67354674cb6026f0409a7e6f8dd/src/comment_classifier/dataset/comment_data_final.json",
)
AVANTES_FILE = "corpus.csv"
AVANTES_URLS = (
    "https://raw.githubusercontent.com/ETF-NLP/AVANTES-Classification/87e08ce4cb8e872dc3deb754784189e10c57d33e/data/corpus.csv",
)
CODE4ML_FILE = "markup_data.csv"
CODE4ML_URLS = ("https://zenodo.org/api/records/7312803/files/markup_data.csv/content",)
CSN_ROWS_URL = "https://datasets-server.huggingface.co/rows"
CSN_DATASET = "code-search-net/code_search_net"
CSN_LANGUAGES = ("go", "java", "javascript", "php", "python", "ruby")
CSN_SPLIT = "test"
CSN_PAGE = 100
CSN_PAGES = 8
AVANTES_LANGUAGES = {
    "C": "c", "C++": "cpp", "C#": "csharp", "Java": "java", "JavaScript": "javascript",
    "TypeScript": "typescript", "PHP": "php", "Python": "python", "SQL": "sql",
}
PASCARELLA_MEMBER_RE = re.compile(r"/(?:listOfComments|List of comments)/cat/([^/]+)/\d+\.txt$")
AVANTES_WRAP_RE = re.compile(r"\s\*\s")


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    fetch: Callable[[Path], None]
    read: Callable[[Path], Iterator[Row]]
    why_what: bool


def download(urls: tuple[str, ...], target: Path) -> None:
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for url in urls:
        try:
            _stream(url, target)
            return
        except OSError as error:
            errors.append(f"{url}: {error}")
    raise OSError(f"could not download {target.name}: " + "; ".join(errors))


def _stream(url: str, target: Path) -> None:
    staging = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, staging.open("wb") as sink:
        shutil.copyfileobj(response, sink, 1 << 20)
    os.replace(staging, target)


def _fetch_file(name: str, urls: tuple[str, ...], folder: Path) -> None:
    download(urls, folder / name)


def _pascarella_rows(folder: Path) -> Iterator[Row]:
    with tarfile.open(folder / PASCARELLA_FILE, "r:gz") as archive:
        for member in archive:
            match = PASCARELLA_MEMBER_RE.search(member.name)
            handle = archive.extractfile(member) if match and member.isfile() else None
            row = text_row("pascarella", "java", match.group(1), handle.read().decode("utf-8", "replace")) if handle else None
            if row is not None:
                yield row


def _nlbse_rows(folder: Path) -> Iterator[Row]:
    texts: dict[str, str] = {}
    positive: dict[str, set[str]] = {}
    with (folder / NLBSE_FILE).open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            key = record["comment_sentence_id"]
            texts.setdefault(key, record["comment_sentence"])
            if record["instance_type"] == "1":
                positive.setdefault(key, set()).add(record["category"].lower())
    for key, labels in positive.items():
        row = text_row("nlbse2024", "java", "+".join(sorted(labels)), texts[key])
        if row is not None:
            yield row


def _dome_rows(folder: Path) -> Iterator[Row]:
    data = json.loads((folder / DOME_FILE).read_text(encoding="utf-8"))
    for comment, code, label in zip(data["comment"], data["code"], data["label"], strict=True):
        first = next((line for line in code.splitlines() if line.strip()), "")
        row = text_row("dome", "java", label.strip(), comment, first)
        if row is not None:
            yield row


def _avantes_rows(folder: Path) -> Iterator[Row]:
    with (folder / AVANTES_FILE).open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            language = AVANTES_LANGUAGES.get(record["ProgrammingLanguageID"])
            if record["NaturalLanguageID"] != "EN" or language is None:
                continue
            row = text_row("avantes", language, record["y8"], AVANTES_WRAP_RE.sub("\n", record["Comment"]))
            if row is not None:
                yield row


def _code4ml_rows(folder: Path) -> Iterator[Row]:
    csv.field_size_limit(sys.maxsize)
    with (folder / CODE4ML_FILE).open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            yield from code_rows("code4ml", "python", "unlabelled", record["code_block"])


def _csn_page(language: str, offset: int, length: int) -> dict:
    query = urllib.parse.urlencode({
        "dataset": CSN_DATASET, "config": language, "split": CSN_SPLIT, "offset": offset, "length": length,
    })
    request = urllib.request.Request(CSN_ROWS_URL + "?" + query, headers={"User-Agent": USER_AGENT})
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in RETRY_CODES or attempt == RETRIES - 1:
                raise
        except OSError:
            if attempt == RETRIES - 1:
                raise
        time.sleep(5 * (attempt + 1))
    raise OSError(f"no rows from {CSN_ROWS_URL} for {language}")


def _csn_fetch(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for language in CSN_LANGUAGES:
        target = folder / f"{language}.jsonl"
        if target.is_file():
            continue
        stride = _csn_page(language, 0, 1)["num_rows_total"] // CSN_PAGES
        codes = []
        for page in range(CSN_PAGES):
            codes.extend(item["row"]["func_code_string"] for item in _csn_page(language, page * stride, CSN_PAGE)["rows"])
        staging = target.with_name(target.name + ".part")
        staging.write_text("".join(json.dumps({"code": code}) + "\n" for code in codes), encoding="utf-8")
        os.replace(staging, target)


def _csn_rows(folder: Path) -> Iterator[Row]:
    for language in CSN_LANGUAGES:
        with (folder / f"{language}.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                yield from code_rows("codesearchnet", language, "unlabelled", json.loads(line)["code"])


SOURCES = {
    source.name: source for source in (
        Source("pascarella", partial(_fetch_file, PASCARELLA_FILE, PASCARELLA_URLS), _pascarella_rows, True),
        Source("nlbse2024", partial(_fetch_file, NLBSE_FILE, NLBSE_URLS), _nlbse_rows, True),
        Source("dome", partial(_fetch_file, DOME_FILE, DOME_URLS), _dome_rows, True),
        Source("avantes", partial(_fetch_file, AVANTES_FILE, AVANTES_URLS), _avantes_rows, False),
        Source("codesearchnet", _csn_fetch, _csn_rows, False),
        Source("code4ml", partial(_fetch_file, CODE4ML_FILE, CODE4ML_URLS), _code4ml_rows, False),
    )
}
WHY_WHAT_SOURCES = frozenset(name for name, source in SOURCES.items() if source.why_what)


def fetch(names: Iterable[str], cache: Path) -> None:
    for name in names:
        print(f"fetching {name} into {cache / name}", file=sys.stderr)
        SOURCES[name].fetch(cache / name)


def cached_rows(names: Iterable[str], cache: Path) -> Iterator[Row]:
    for name in names:
        yield from SOURCES[name].read(cache / name)


def dataset_rows(names: list[str], cache: Path, offline: bool) -> list[Row]:
    if not offline:
        fetch(names, cache)
    return list(cached_rows(names, cache))


def read_rows(path: Path) -> list[Row]:
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    rows = []
    for name in files:
        with name.open(encoding="utf-8") as handle:
            rows.extend(Row(**json.loads(line)) for line in handle if line.strip())
    return rows


def write_rows(path: Path, rows: Iterable[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row._asdict(), sort_keys=True) + "\n")
