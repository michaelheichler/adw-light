import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
from unittest import mock
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView


CORPUS = Path(__file__).resolve().parent / "fixtures" / "corpus_what_comments.jsonl"
LISTED_RULE_RE = re.compile(r"^\d+\. .*\((\w+)\)$", re.M)
COMMENT_RULES = frozenset({"what_comment", "what_docstring", "weak_why_comment"})


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


def predicted_label(root, git, number, row):
    path, content = FILE_FOR_KIND[row["kind"]](row)
    payload = {"cwd": str(root), "session_id": "corpus-%03d" % number, "hook_event_name": "PreToolUse",
               "tool_name": "Write", "tool_input": {"file_path": str(root / ("row-%03d" % number) / path), "content": content}}
    response = run_hook("PreToolUse", payload, state_dir=root / ".adw-state", git_view=git)
    return "what" if listed_rules(response) & COMMENT_RULES else "why"


def agreement(labels, predictions):
    pairs = list(zip(labels, predictions, strict=True))
    what = [predicted == "what" for label, predicted in pairs if label == "what"]
    why = [predicted == "why" for label, predicted in pairs if label == "why"]
    agreed = sum(what) + sum(why)
    return Agreement(sum(what), len(what), sum(why), len(why), round(agreed / len(pairs), 4))


class WhatWhyCorpusAgreement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name)
        cls.git = FakeGitView({"roots": {}})
        (cls.root / "home").mkdir()
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)

    def test_comment_rules_agree_with_the_labelled_corpus_at_todays_rate(self):
        """Compute expected values independently."""
        rows = corpus_rows()
        predictions = [predicted_label(self.root, self.git, number, row) for number, row in enumerate(rows)]

        self.assertEqual(TODAY, agreement([row["label"] for row in rows], predictions))


if __name__ == "__main__":
    unittest.main()
