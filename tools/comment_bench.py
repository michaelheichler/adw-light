from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
HOME_TREE = TOOLS.parent
for folder in (TOOLS, HOME_TREE / "engine"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from comment_measure import expectations, report, summary, verdicts
from comment_rows import Row
from comment_sources import SOURCES, dataset_rows, fetch, read_rows, write_rows
from lib.config_roots import dataset_cache_root

SAMPLE_DIR = HOME_TREE / "measure" / "comment" / "sample"
SAMPLE_SIZE = 2880
SAMPLE_SEED = 16


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comment_bench.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "measure", "sample"):
        command = commands.add_parser(name)
        command.add_argument("--cache", type=Path, default=None)
        command.add_argument("--source", action="append", choices=sorted(SOURCES), default=None)
    measure = commands.choices["measure"]
    measure.add_argument("--rows", type=Path, default=None)
    measure.add_argument("--offline", action="store_true")
    measure.add_argument("--json", action="store_true")
    measure.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    sample = commands.choices["sample"]
    sample.add_argument("--out", type=Path, default=SAMPLE_DIR)
    sample.add_argument("--size", type=int, default=SAMPLE_SIZE)
    sample.add_argument("--seed", type=int, default=SAMPLE_SEED)
    sample.add_argument("--offline", action="store_true")
    return parser


def _names(args: argparse.Namespace) -> list[str]:
    return list(dict.fromkeys(args.source or SOURCES))


def _dataset_rows(args: argparse.Namespace) -> list[Row]:
    return dataset_rows(_names(args), args.cache or dataset_cache_root(), args.offline)


def _measure(args: argparse.Namespace) -> int:
    rows = read_rows(args.rows) if args.rows else _dataset_rows(args)
    if args.rows and args.source:
        rows = [row for row in rows if row.source in args.source]
    result = summary(rows, verdicts(rows, args.workers), expectations())
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else report(result))
    return 0


def sampled(rows: list[Row], size: int, seed: int) -> list[Row]:
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        groups[row.source].append(row)
    quota = size // max(len(groups), 1)
    generator = random.Random(seed)
    picked = []
    for name in sorted(groups):
        picked.extend(generator.sample(groups[name], min(quota, len(groups[name]))))
    return picked


def _sample(args: argparse.Namespace) -> int:
    rows = sampled(_dataset_rows(args), args.size, args.seed)
    for name in sorted({row.source for row in rows}):
        write_rows(args.out / f"{name}.jsonl", [row for row in rows if row.source == name])
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


def _fetch(args: argparse.Namespace) -> int:
    fetch(_names(args), args.cache or dataset_cache_root())
    return 0


COMMANDS = {"fetch": _fetch, "measure": _measure, "sample": _sample}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (OSError, ValueError, KeyError) as error:
        print(f"comment_bench: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
