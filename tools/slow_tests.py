from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", action="store_true", required=True)
    parser.add_argument("modules", nargs="*", type=Path)
    args = parser.parse_args()
    tests = Path("tests").resolve()
    paths = args.modules or sorted(tests.glob("test*.py"))
    search = os.pathsep.join(filter(None, (str(tests), os.environ.get("PYTHONPATH"))))
    env = dict(os.environ, PYTHONPATH=search)
    timings: list[tuple[float, Path]] = []
    failed = False
    for path in paths:
        start = monotonic()
        result = subprocess.run([sys.executable, "-m", "unittest", path.stem], env=env)
        timings.append((monotonic() - start, path))
        failed |= result.returncode != 0
    print("Slowest test modules:")
    for seconds, path in sorted(timings, reverse=True)[:10]:
        print(f"{seconds:8.3f}s  {path.name}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
