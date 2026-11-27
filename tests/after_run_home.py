import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from fake_repo_home import README, SIZE_TEST, Repo, place, repo_state
from lib.git_port import FakeGitView

TEST_WRITER = "adw-test-writer"
GATE = "test_write_gate"
BLOCK = "block"
DENY = "deny"
CONFIG = ".agent-discipline.json"
OLD_TEST = "tests/test_old.py"
CLAUDE = "claude"
CODEX = "codex"
CAUGHT = BLOCK
LET_THROUGH = None


class Project(NamedTuple):
    files: dict
    git: bool = True
    listed: bool = True
    branches: dict = {}


def project_files(extra=None):
    return {**README, OLD_TEST: SIZE_TEST, **(extra or {})}


def nothing():
    return None


class AfterRunCase(unittest.TestCase):
    projects = {}

    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.home = cls.root / "home"
        cls.state = cls.root / "state"
        cls.home.mkdir()
        for name, project in cls.projects.items():
            folder = cls.root / name
            folder.mkdir(parents=True)
            place(folder, project.files)
            if project.git:
                (folder / ".git").mkdir()

    def folder(self, name):
        return self.root / name

    def disk(self, name):
        disks = self.__dict__.setdefault("_disks", {})
        return disks.setdefault(name, dict(self.projects[name].files)) if name in self.projects else {}

    def _restore_later(self, name, relative):
        original = self.projects.get(name, Project({})).files.get(relative)
        path = self.folder(name) / relative
        self.addCleanup(lambda: path.write_text(original, encoding="utf-8") if original is not None else path.unlink(missing_ok=True))

    def change(self, name, relative, content):
        path = self.folder(name) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        self._restore_later(name, relative)
        path.write_text(content, encoding="utf-8")
        self.disk(name)[relative] = content

    def remove(self, name, relative):
        self._restore_later(name, relative)
        (self.folder(name) / relative).unlink()
        self.disk(name).pop(relative, None)

    def rename(self, name, old, new):
        content = (self.folder(name) / old).read_text(encoding="utf-8")
        self.remove(name, old)
        self.change(name, new, content)

    def view(self):
        roots = {str(self.folder(name)): {**repo_state(Repo(project.files, branches=project.branches)), "disk": self.disk(name)}
                 for name, project in self.projects.items() if project.git and project.listed}
        return FakeGitView({"roots": roots})

    def session(self, run=""):
        return "s" + hashlib.sha256((self.id() + run).encode()).hexdigest()[:16]

    def respond(self, event, name, payload, host=CLAUDE, run=""):
        body = {"session_id": self.session(run), "hook_event_name": event, "cwd": str(self.folder(name)), **payload}
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, environment, clear=True):
            return run_hook(event, body, host=host, state_dir=self.state, git_view=self.view())

    def start(self, name, host=CLAUDE, run=""):
        self.respond("SessionStart", name, {"source": "startup"}, host, run)

    def bash_call(self, command, call):
        return {"tool_name": "Bash", "tool_use_id": call, "tool_input": {"command": command}}

    def pre(self, name, command, call="c1", host=CLAUDE, identity=None, run=""):
        payload = {**self.bash_call(command, call), **(identity or {})}
        response = self.respond("PreToolUse", name, payload, host, run)
        return response.get("hookSpecificOutput", {}).get("permissionDecision")

    def post(self, name, command, call="c1", host=CLAUDE, identity=None, run=""):
        payload = {**self.bash_call(command, call), "tool_response": {"stdout": "", "stderr": ""}, **(identity or {})}
        return self.respond("PostToolUse", name, payload, host, run)

    def batch(self, name, command, call="c1", identity=None, run=""):
        calls = [{**self.bash_call(command, call), "tool_response": {}}]
        return self.respond("PostToolBatch", name, {"tool_calls": calls, **(identity or {})}, CLAUDE, run)

    def stop(self, name, host=CLAUDE, run=""):
        return self.respond("Stop", name, {"last_assistant_message": "Done."}, host, run).get("decision")

    def pair(self, name, command, effect=nothing, *, call="c1", host=CLAUDE, before=None, after=None, run=""):
        decision = self.pre(name, command, call, host, before, run)
        effect()
        later = after if after is not None else before
        posted = self.post(name, command, call, host, later, run)
        if host == CODEX:
            return decision, posted.get("decision"), posted
        batched = self.batch(name, command, call, later, run)
        return decision, batched.get("decision"), posted

    def shell(self, name, command, effect=nothing, *, host=CLAUDE, before=None, after=None, run=""):
        self.start(name, host, run)
        decision, verdict, _ = self.pair(name, command, effect, host=host, before=before, after=after, run=run)
        return decision, verdict

    def after_run(self, name, command, effect=nothing, **options):
        return self.shell(name, command, effect, **options)[1]

    def ledger_rows(self, run=""):
        ledger = self.state / "ledger" / "ledger.jsonl"
        lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
        rows = (json.loads(line) for line in lines if line.strip())
        return [row for row in rows if row.get("session_id") == self.session(run) and row.get("hook") == "after_run"]

    def finding_rules(self, run=""):
        return sorted({row["rule"] for row in self.ledger_rows(run) if row.get("family") != "after_run"})

    def blocked_rules(self, run=""):
        return sorted({row["rule"] for row in self.ledger_rows(run) if row.get("outcome") == "block" and row.get("family") != "after_run"})

    def finding_paths(self, rule, name, run=""):
        folder = str(self.folder(name)) + "/"
        return sorted({row["path"].removeprefix(folder) for row in self.ledger_rows(run) if row.get("rule") == rule})

    def write_rules(self, name, relative, content, run=""):
        self.start(name, run=run)
        tool_input = {"file_path": str(self.folder(name) / relative), "content": content}
        self.respond("PreToolUse", name, {"tool_name": "Write", "tool_use_id": "w1", "tool_input": tool_input}, run=run)
        return self.report_rules(run)

    def report_rules(self, run=""):
        reports = sorted((self.state / "reports").glob(self.session(run) + "-*.json"))
        return sorted({row["rule"] for report in reports for row in json.loads(report.read_text(encoding="utf-8"))})

    def timing_rows(self, run=""):
        return [row for row in self.ledger_rows(run) if row.get("family") == "after_run"]


def identity(agent_type, agent_id="a1"):
    return {"agent_id": agent_id, "agent_type": agent_type}
