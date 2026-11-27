import hashlib
import json
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


PROSE_FAMILIES = frozenset({"english", "punctuation"})
FLAGGED_PROSE = "We " + "util" + "ize this tool " + "\u2014" + " for the build.\n"
NARRATED_CODE = "#" + " increment the counter\n" + "counter = 1\n"
PLAIN_PROSE = "Plain text.\n"
TEST_WRITER = "adw-test-writer"
DENY = "deny"


def referring_test(reference):
    return f'def test_loads_the_fixture():\n    assert load("{reference}")\n'


def untracked(files):
    return {"disk": files}


def staged(files):
    return {"disk": files, "index": files}


def place(folder, files):
    for relative, content in files.items():
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_text(content, encoding="utf-8")


def outside(files, nested):
    return {relative: content for relative, content in files.items()
            if not any(relative.startswith(folder + "/") for folder in nested)}


def inside(files, folder):
    return {relative[len(folder) + 1:]: content for relative, content in files.items() if relative.startswith(folder + "/")}


class FixtureProseHome(unittest.TestCase):
    projects = {}
    plain = ()
    nested = {}
    links = {}
    fifos = ()

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
        roots = {}
        for name, spec in cls.projects.items():
            folder = cls.root / name
            folder.mkdir()
            place(folder, {relative: text for relative, text in spec.get("disk", {}).items() if (name, relative) not in cls.links})
            hidden = cls.nested.get(name, ())
            for sub in hidden:
                (folder / sub / ".git").mkdir(parents=True)
                roots[str(folder / sub)] = {"disk": inside(spec.get("disk", {}), sub)}
            if name not in cls.plain:
                roots[str(folder)] = {"disk": outside(spec.get("disk", {}), hidden), "index": spec.get("index", {}),
                                      "ignore": spec.get("ignore", [])}
        for (name, relative), target in cls.links.items():
            (cls.root / name / relative).parent.mkdir(parents=True, exist_ok=True)
            (cls.root / name / relative).symlink_to(cls.root / name / target)
        for name, relative in cls.fifos:
            (cls.root / name / relative).parent.mkdir(parents=True, exist_ok=True)
            os.mkfifo(cls.root / name / relative)
        cls.git = FakeGitView({"roots": roots})

    def session(self):
        return "s" + hashlib.sha256(self.id().encode()).hexdigest()[:20]

    def hook(self, event, cwd, **payload):
        body = {"cwd": str(cwd), "session_id": self.session(), "hook_event_name": event, **payload}
        return run_hook(event, body, state_dir=self.state, git_view=self.git)

    def start(self, name):
        self.hook("SessionStart", self.root / name, source="startup")

    def write_gate(self, name, relative, content, agent_type=TEST_WRITER, cwd=None):
        self.start(name)
        return self.hook("PreToolUse", cwd or self.root / name, tool_name="Write", agent_id="agent-1", agent_type=agent_type,
                         tool_input={"file_path": str(self.root / name / relative), "content": content})

    def edit_gate(self, name, relative, old, new):
        self.start(name)
        return self.hook("PreToolUse", self.root / name, tool_name="Edit", agent_id="agent-1", agent_type=TEST_WRITER,
                         tool_input={"file_path": str(self.root / name / relative), "old_string": old, "new_string": new})

    def commit_gate(self, name, agent_type=TEST_WRITER):
        self.start(name)
        return self.hook("PreToolUse", self.root / name, tool_name="Bash", agent_id="agent-1", agent_type=agent_type,
                         tool_input={"command": 'git commit -m "Add fixture"'})

    def decision(self, response):
        return response.get("hookSpecificOutput", {}).get("permissionDecision")

    def ledger_rows(self):
        ledger = self.state / "ledger" / "ledger.jsonl"
        rows = (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())
        return [row for row in rows if row.get("session_id") == self.session()]

    def rules_at(self, relative):
        return {row["rule"] for row in self.ledger_rows() if row["path"].endswith(relative)}

    def prose_families_at(self, relative):
        return {row["family"] for row in self.ledger_rows()
                if row["path"].endswith(relative) and row["family"] in PROSE_FAMILIES}

    def write_verdict(self, name, fixture, **options):
        response = self.write_gate(name, fixture, FLAGGED_PROSE, **options)
        return self.decision(response), self.prose_families_at(fixture)

    def commit_verdict(self, name, fixture, **options):
        response = self.commit_gate(name, **options)
        return self.decision(response), self.prose_families_at(fixture)


EXEMPT = (None, set())
FLAGGED = (DENY, set(PROSE_FAMILIES))
