import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple

from t20_layer_home import BUNDLE, GRAMMARS, VENV, decision_of


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
WRITER = "adw-test-writer"
SESSION = "t13"
DENY = "deny"
NONE = "none"
CONTEXT = "context"
ASSERTION = "wording_assertion"
LOCATOR = "wording_ui_locator"
EXCEPTION = "wording_exception_match"
LOCATION = "literal_location"
LOCK = "wording_debt_lock"
WORDING_RULES = frozenset((ASSERTION, LOCATOR, EXCEPTION, LOCATION, "wording_log_assertion"))


def words(*parts):
    return " ".join(parts)


def quoted(text):
    return json.dumps(text)


def lines(*body):
    return "".join(line + "\n" for line in body)


class Outcome(NamedTuple):
    decision: str
    rows: tuple[tuple[str, int], ...]


def denial(rule, *at):
    return Outcome(DENY, tuple((rule, line) for line in at))


PASSED = Outcome(NONE, ())


class WordingHome:
    def __init__(self, case, exact):
        workspace = TemporaryDirectory()
        case.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.repo.mkdir()
        self.home.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        if exact:
            self.link_language_layer()

    def link_language_layer(self):
        adw = self.home / ".adw-lite"
        (adw / "install").mkdir(parents=True)
        (adw / "install" / "venv").symlink_to(VENV, target_is_directory=True)
        grammars = adw / "cache" / "grammars"
        grammars.mkdir(parents=True)
        (grammars / BUNDLE).symlink_to(GRAMMARS / BUNDLE, target_is_directory=True)

    def place(self, relative, content):
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def hook(self, event, payload):
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps({"session_id": SESSION, "cwd": str(self.repo), "hook_event_name": event, **payload}),
            text=True, capture_output=True, timeout=120,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )
        return call

    def start(self):
        return self.hook("SessionStart", {"source": "startup"})

    def write(self, relative, content):
        tool_input = {"file_path": str(self.repo / relative), "content": content}
        return self.hook("PreToolUse", {"agent_type": WRITER, "tool_name": "Write", "tool_input": tool_input})

    def report_rows(self):
        reports = sorted((self.state / "reports").glob("*.json")) if (self.state / "reports").is_dir() else ()
        return [row for report in reports for row in json.loads(report.read_text(encoding="utf-8"))]

    def outcome(self, relative, content, rules=WORDING_RULES):
        call = self.write(relative, content)
        rows = tuple(sorted({(row["rule"], row["line"]) for row in self.report_rows() if row["rule"] in rules}))
        return Outcome(decision_of(json.loads(call.stdout)), rows)

    def matches(self, rule):
        return tuple(sorted(str(row.get("match")) for row in self.report_rows() if row["rule"] == rule))
