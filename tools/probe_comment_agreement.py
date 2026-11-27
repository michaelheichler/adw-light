from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_what_why_corpus import agreement, corpus_rows, predicted_label


def main() -> None:
    rows = corpus_rows()
    with TemporaryDirectory() as directory, ThreadPoolExecutor(8) as workers:
        root = Path(directory)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        predictions = list(workers.map(lambda pair: predicted_label(root, pair), enumerate(rows)))
    labels = [row["label"] for row in rows]
    print(agreement(labels, predictions))
    for label in ("what", "why"):
        print(label, [number for number, (want, got) in enumerate(zip(labels, predictions), 1) if want == label and want != got])


if __name__ == "__main__":
    main()
