import base64
import hashlib
import json
import os
import sys
import unittest
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView


SHEBANG = "#!/bin/bash\n"
PROSE_COMMIT = 'git commit -m "We utilize this."\n'
CLEAN_LIB = "echo lib\n"
HIDDEN_REFERENCE = "xargs " + PROSE_COMMIT
DEEP_CHAIN = 40
DENY = "deny"
BLOCK = "block"
MESSAGE = "commit_message.md"
SCANNED = (DENY, frozenset({"utilize"}))
UNSCANNED = (DENY, frozenset())
EMPTY = "empty"


def script(body):
    return SHEBANG + body


def deep_chain():
    links = {f"deep{level}.sh": script(f"bash ./deep{level + 1}.sh\n") for level in range(1, DEEP_CHAIN)}
    return {**links, f"deep{DEEP_CHAIN}.sh": script(CLEAN_LIB)}


def blob(content):
    return {"base64": base64.b64encode(content).decode("ascii")} if isinstance(content, bytes) else content


def committed(files):
    blobs = {relative: blob(content) for relative, content in files.items()}
    return {"index": blobs, "disk": blobs, "revisions": {"base": blobs}, "refs": {"HEAD": "base"}}


def place(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


class CommitViewHome(unittest.TestCase):
    projects = {}
    links = {}
    locked = ()

    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        (cls.root / "home").mkdir()
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home"), "PATH": os.environ.get("PATH", "")}, clear=True)
        environment.start()
        cls.addClassCleanup(environment.stop)
        projects = {EMPTY: {}, **cls.projects}
        for name, files in projects.items():
            (cls.root / name).mkdir()
            for relative, content in files.items():
                place(cls.root / name / relative, content)
        for (name, relative), target in cls.links.items():
            (cls.root / name / relative).symlink_to(target)
        for name, relative in cls.locked:
            (cls.root / name / relative).chmod(0o000)
            cls.addClassCleanup((cls.root / name / relative).chmod, 0o755)
        cls.git = FakeGitView({"roots": {str(cls.root / name): committed(files) for name, files in projects.items()}})
        cls.calls = count()

    def project(self, name):
        return self.root / name

    def respond(self, name, command):
        session = "s" + hashlib.sha256(f"{self.id()}-{next(self.calls)}".encode()).hexdigest()[:20]
        payload = {"cwd": str(self.project(name)), "session_id": session, "hook_event_name": "PreToolUse",
                   "tool_name": "Bash", "tool_input": {"command": command}}
        return run_hook("PreToolUse", payload, state_dir=self.state, git_view=self.git), session

    def blocked_rows(self, session):
        ledger = self.state / "ledger" / "ledger.jsonl"
        rows = (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())
        return [row for row in rows if row.get("session_id") == session and row.get("outcome") == BLOCK]

    def gate(self, name, command):
        return self.respond(name, command)[0]

    def message_finding(self, name, command):
        response, session = self.respond(name, command)
        decision = response.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, frozenset(row["rule"] for row in self.blocked_rows(session) if row.get("path") == MESSAGE)

    def refusal(self, name, command):
        response, session = self.respond(name, command)
        output = response.get("hookSpecificOutput", {})
        rules = frozenset(row["rule"] for row in self.blocked_rows(session))
        return output.get("permissionDecision"), rules, "" if rules else output.get("permissionDecisionReason")
