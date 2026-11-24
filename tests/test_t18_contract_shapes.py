import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
CONTEXT_CAP = 4000
SHAPE_TOKENS = frozenset({"25", "because", "NOTE", "URL", "hence", "#123", "TODO", "FIXME", "HACK"})
REJECTED_TAGS = frozenset({"DONE", "XXX", "BUG", "OPTIMIZE"})


class Delivery(NamedTuple):
    exit_code: int
    context: str

    def tokens(self):
        return set(re.findall(r"#?[\w-]+", self.context))

    def sentence_words(self):
        return [set(re.findall(r"\w+", sentence)) for sentence in re.split(r"(?<=\.)\s+", self.context)]

    def sentences_deleting_markers(self):
        sentences = re.split(r"(?<=\.)\s+", self.context)
        return [sentence for sentence in sentences if {"Delete", "marker"} <= set(re.findall(r"\w+", sentence))]

    def non_python_comment_rules(self):
        return [
            ({"other", "languages"} <= words, "25" in words)
            for words in self.sentence_words()
            if {"other", "languages"} <= words or ("only" in words and words & {"comment", "comments"})
        ]

    def python_comment_bans(self):
        return [words for words in self.sentence_words() if {"Python", "comments", "docstrings"} <= words]


class ContractDelivery(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.project = self.root / "project"
        self.home = self.root / "home"
        self.project.mkdir()
        self.home.mkdir()

    def deliver(self, event, **fields):
        payload = {"session_id": "contract", "cwd": str(self.project), "hook_event_name": event, **fields}
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.root / "state")],
            input=json.dumps(payload), text=True, capture_output=True, check=False, timeout=60,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        response = json.loads(call.stdout) if call.stdout.strip() else {}
        return Delivery(call.returncode, response.get("hookSpecificOutput", {}).get("additionalContext", ""))

    def session_start(self):
        return self.deliver("SessionStart", source="startup")

    def test_a_new_session_learns_every_comment_shape_and_work_marker_tag(self):
        delivery = self.session_start()
        self.assertEqual((delivery.exit_code, (SHAPE_TOKENS | REJECTED_TAGS) - delivery.tokens()), (0, set()))

    def test_a_subagent_receives_the_same_contract_as_a_new_session(self):
        subagent = self.deliver("SubagentStart", agent_type="adw-test-writer", agent_id="probe")
        self.assertEqual((subagent.exit_code, subagent.context), (0, self.session_start().context))

    def test_no_contract_sentence_deletes_every_work_marker(self):
        delivery = self.session_start()
        self.assertEqual((delivery.exit_code, delivery.sentences_deleting_markers()), (0, []))

    def test_other_languages_get_the_shape_rule_as_their_only_comment_limit(self):
        delivery = self.session_start()
        self.assertEqual((delivery.exit_code, delivery.non_python_comment_rules()), (0, [(True, True)]))

    def test_python_source_keeps_its_ban_on_new_comments_and_docstrings(self):
        delivery = self.session_start()
        self.assertEqual((delivery.exit_code, len(delivery.python_comment_bans())), (0, 1))

    def test_the_contract_reaches_the_agent_without_clipping(self):
        context = self.session_start().context
        self.assertEqual((len(context) < CONTEXT_CAP, context.rstrip()[-1:]), (True, "."))


if __name__ == "__main__":
    unittest.main()
