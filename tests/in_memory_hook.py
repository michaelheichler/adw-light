import atexit
import json
from hashlib import sha256
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView
from t19_language_home import BUNDLE, GRAMMARS, LIBRARY_NEEDED, LIBRARY_READY, PRINCIPLE, VENV


NO_REPOSITORY = FakeGitView({"roots": {}})
DENY = "deny"
PASSED = (None, [])
TEST_WRITER = "adw-test-writer"


def _adw_home(home, language):
    adw = home / ".adw-lite"
    (adw / "install").mkdir(parents=True)
    (adw / "cache" / "grammars").mkdir(parents=True)
    if language:
        (adw / "install" / "venv").symlink_to(VENV, target_is_directory=True)
        (adw / "cache" / "grammars" / BUNDLE).symlink_to(GRAMMARS / BUNDLE, target_is_directory=True)
    return home


_language_homes = []


def language_home():
    if not _language_homes:
        workspace = TemporaryDirectory()
        atexit.register(workspace.cleanup)
        _language_homes.append(_adw_home(Path(workspace.name).resolve() / "home", language=True))
    return _language_homes[0]


def decision_of(response):
    return response.get("hookSpecificOutput", {}).get("permissionDecision")


class InMemoryHookCase(unittest.TestCase):
    files = {}
    language = False

    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.home = language_home() if cls.language else _adw_home(cls.root / "home", language=False)
        cls.state = cls.root / "state"
        cls.work = cls.root / "work"
        cls.work.mkdir()
        for relative, content in cls.files.items():
            path = cls.work / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def session(self):
        return "s" + sha256(self.id().encode()).hexdigest()[:16]

    def respond(self, event, payload):
        body = {"session_id": self.session(), "hook_event_name": event, **payload}
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, environment, clear=True):
            return run_hook(event, body, state_dir=self.state, git_view=self.git_view())

    def git_view(self):
        return NO_REPOSITORY

    def start_session(self):
        self.respond("SessionStart", {"cwd": str(self.work), "source": "startup"})

    def hook(self, event, payload):
        return decision_of(self.respond(event, payload)), self.reported_rules()

    def reported_rules(self):
        reports = sorted((self.state / "reports").glob(self.session() + "-*.json"))
        return sorted({row["rule"] for report in reports for row in json.loads(report.read_text(encoding="utf-8"))})

    def reported_rule_lines(self):
        reports = sorted((self.state / "reports").glob(self.session() + "-*.json"))
        rows = (row for report in reports for row in json.loads(report.read_text(encoding="utf-8")))
        return tuple(sorted({(row["rule"], row["line"]) for row in rows}))

    def ledger_rules(self, path):
        ledger = self.state / "ledger" / "ledger.jsonl"
        lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
        rows = (json.loads(line) for line in lines if line.strip())
        return {row["rule"] for row in rows
                if row.get("session_id") == self.session() and str(row.get("path", "")).endswith(str(path))}

    def bash(self, command):
        return self.hook("PreToolUse", {"cwd": str(self.work), "tool_name": "Bash", "tool_input": {"command": command}})

    def write(self, path, content, agent_type=None, cwd=None):
        tool_input = {"file_path": str(path), "content": content}
        payload = {"cwd": str(cwd or self.work), "tool_name": "Write", "tool_input": tool_input}
        if agent_type is not None:
            payload["agent_type"] = agent_type
        return self.hook("PreToolUse", payload)

    def exact_verdict(self, relative, content):
        decision, _ = self.write(self.work / relative, content, agent_type=TEST_WRITER)
        return decision, self.reported_rule_lines()


def blocked(*rows):
    return DENY, tuple(rows)


ALLOWED = (None, ())
