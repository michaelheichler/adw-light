# Comment measurement bench

`tools/comment_bench.py` measures the comment-shape rule in `engine/lib/comment_shapes.py` on comments that people wrote and that the dataset authors labelled. Nobody on our side labels a row. The tool downloads each dataset on first use into `~/.adw-lite/cache/datasets`, and `--cache DIR` picks another folder.

## Commands

```bash
python3 tools/comment_bench.py fetch
python3 tools/comment_bench.py measure
python3 tools/comment_bench.py measure --json
python3 tools/comment_bench.py measure --rows measure/comment/sample
python3 tools/comment_bench.py sample --size 2880 --seed 16
python3 tools/comment_mine.py
```

The option `--source NAME` limits a command to one dataset, and it repeats for more. The option `--offline` reads only the cache. The command `measure --rows PATH` reads one row file or every `.jsonl` file in a folder, with no network access.

## Sources

| Name | Content | Download |
| --- | --- | --- |
| pascarella | Java comments from six open source projects, sorted into 16 categories by Pascarella and Bacchelli | 4TU.ResearchData record 12682880, with Zenodo record 2628361 as the mirror |
| nlbse2024 | Java class comment sentences from the NLBSE 2024 tool competition | GitHub nlbse2024/code-comment-classification, java/input/java.csv |
| dome | Java method comments with developer intent labels from the DOME paper (ICSE 2023) | GitHub ICSE-DOME/DOME, comment_data_final.json |
| avantes | Comments in eight languages from the AVANTES project, English half only | GitHub ETF-NLP/AVANTES-Classification, data/corpus.csv |
| codesearchnet | Line comments inside functions from the CodeSearchNet test split, 800 functions per language | Hugging Face dataset code-search-net/code_search_net through the datasets-server rows API |
| code4ml | Comments inside the hand-marked Kaggle code cells of Code4ML | Zenodo record 7312803, markup_data.csv |

NLBSE 2024 reuses projects and text from the Pascarella data, so the two sources do not count as independent evidence.

## How a row becomes a verdict

1. The tool writes each comment as line comments in its language and adds the next code line when the dataset has one.
2. `scan_all` from `engine/lib/scanner.py` scans that text. A `comment_shape` or `deferred_work_comment` finding counts as a block, whatever the gate state.
3. `measure/comment/labels.tsv` maps each author label to a role (what, why, directive, human). The tool ignores a label that the table does not list.
4. Java blocking precision counts blocked what rows over all blocked what and why rows in pascarella, nlbse2024, and dome.
5. The wrong-block rate per language counts blocked rows over all rows with the role why, directive, or human.

The role table is the only judgment on our side, and it works at category level. Edit it to change what counts as a wrong block.

## Mined tables

`tools/comment_mine.py` counts words and lengths in the labelled rows and writes tab-separated tables to `measure/comment/tables`.

- `why-words.tsv` lists words that lean toward why comments, with smoothed log odds.
- `causal-links.tsv` scores each causal link from `comment_shape_words.json` the same way.
- `what-openers.tsv` lists the first words of comments and the share of what labels.
- `length.tsv` gives word-count percentiles and the share at or under the word limit per role, source, and language.
- `marker-tags.tsv` and `marker-verbs.tsv` count work-marker tags, their case and colon use, and the first word after the tag.

## Fixture

`measure/comment/sample` holds 2880 rows in one file per source, 480 rows each, drawn with seed 16. The per-source files stay under the 500-line warning of the file length rule. Each row keeps the source, language, author label, comment, and scanned text. `measure --rows measure/comment/sample` runs on it offline.

## Results on 2026-11-23

The full run scanned 55299 rows. Java blocking precision came out at 0.9033, from 21541 blocked what rows and 2307 blocked why rows. Per source it reached 0.8822 for dome, 0.9204 for nlbse2024, and 0.9472 for pascarella. The rule blocked 67 percent of the Java why rows.

| Language | Rows that should pass | Blocked | Rate |
| --- | --- | --- | --- |
| c | 577 | 551 | 0.9549 |
| cpp | 511 | 489 | 0.9569 |
| csharp | 618 | 599 | 0.9693 |
| go | 631 | 610 | 0.9667 |
| java | 4997 | 4824 | 0.9654 |
| javascript | 1679 | 1586 | 0.9446 |
| php | 1090 | 1065 | 0.9771 |
| python | 5233 | 3590 | 0.6860 |
| ruby | 514 | 378 | 0.7354 |
| sql | 592 | 6 | 0.0101 |
| typescript | 316 | 284 | 0.8987 |

The engine reads no SQL line comments, so the SQL rate says nothing about the rule. The rule blocked 973 of the 974 Eclipse `$NON-NLS-1$` markers in the Pascarella directive rows, which a directive check should let through.
