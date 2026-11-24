import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
CORPUS = Path(__file__).resolve().parent / "fixtures" / "corpus_what_comments.jsonl"
LISTED_RULE_RE = re.compile(r"^\d+\. .*\((\w+)\)$", re.M)
COMMENT_RULES = frozenset({"what_comment", "what_docstring", "weak_why_comment"})
WORKERS = 8


class Agreement(NamedTuple):
    what_agreed: int
    what_total: int
    why_agreed: int
    why_total: int
    rate: float


TODAY = Agreement(what_agreed=51, what_total=108, why_agreed=76, why_total=94, rate=0.6287)


def comment_file(row):
    return "app.js", "// " + row["text"] + "\nconst value = 1;\n"


def docstring_file(row):
    return "app.py", "def " + row["name"] + "():\n    \"\"\"" + row["text"] + "\"\"\"\n    return None\n"


def surface_file(row):
    return row["path"], row["source"]


FILE_FOR_KIND = {"comment": comment_file, "docstring": docstring_file, "surface": surface_file}


def corpus_rows():
    return [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


def listed_rules(response):
    output = response.get("hookSpecificOutput", {})
    text = output.get("permissionDecisionReason") or output.get("additionalContext") or ""
    return frozenset(LISTED_RULE_RE.findall(text))


def predicted_label(root, numbered_row):
    number, row = numbered_row
    workspace = root / ("row-%03d" % number)
    workspace.mkdir()
    path, content = FILE_FOR_KIND[row["kind"]](row)
    payload = {"cwd": str(root), "session_id": "corpus-%03d" % number, "hook_event_name": "PreToolUse",
               "tool_name": "Write", "tool_input": {"file_path": str(workspace / path), "content": content}}
    call = subprocess.run(
        [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(workspace / ".adw-state")],
        input=json.dumps(payload), text=True, capture_output=True, check=True,
    )
    return "what" if listed_rules(json.loads(call.stdout)) & COMMENT_RULES else "why"


def agreement(labels, predictions):
    pairs = list(zip(labels, predictions, strict=True))
    what = [predicted == "what" for label, predicted in pairs if label == "what"]
    why = [predicted == "why" for label, predicted in pairs if label == "why"]
    agreed = sum(what) + sum(why)
    return Agreement(sum(what), len(what), sum(why), len(why), round(agreed / len(pairs), 4))


class WhatWhyCorpusAgreement(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)

    def test_comment_rules_agree_with_the_labelled_corpus_at_todays_rate(self):
        rows = corpus_rows()
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            predictions = list(pool.map(lambda numbered: predicted_label(self.root, numbered), enumerate(rows)))

        self.assertEqual(TODAY, agreement([row["label"] for row in rows], predictions))


if __name__ == "__main__":
    unittest.main()
