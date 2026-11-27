import hashlib
import json
import os
import sys
import unittest
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import NamedTuple
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView

PRINCIPLE = "A test must verify what the code does for its client, never how it does it."
DENY = "deny"
GATE = "test_write_gate"
LOCK = "wording_debt_lock"
OBSERVED = "would_block"
PASSED = (None, [])
REFUSED = (DENY, [GATE])
TEST_WRITER = "adw-test-writer"
OLD_TEST = "tests/test_old.py"
SIZE_TEST = 'def test_size():\n    """' + PRINCIPLE + '"""\n    assert len([1, 2]) == 2\n'
WORDING_TEST = "def test_msg():\n    assert render() == " + json.dumps("The file could not be opened today.") + "\n"
DEBT_FILE = SIZE_TEST + "\n\n" + WORDING_TEST
PLAIN_MODULE = "def helper():\n    return 1\n"
README = {"README.md": "Short.\n"}
BASE = "base"
INFO_EXCLUDE = ".git/info/exclude"


class Repo(NamedTuple):
    files: dict
    disk: dict = {}
    branches: dict = {}
    fields: dict = {}
    git: bool = True
    shares: str = ""


def loose(files):
    return Repo({}, files, git=False)


def place(folder, files):
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def ignore_rules(files):
    return [{"source": name, "pattern": line} for name, text in sorted(files.items())
            if PurePosixPath(name).name == ".gitignore" or name == INFO_EXCLUDE
            for line in text.splitlines() if line.strip()]


def branch_history(files, name, changes):
    trees, parents, tip, tree = {}, {}, BASE, dict(files)
    for number, change in enumerate(changes):
        commit, tree = f"{name}-{number}", {**tree, **change}
        trees[commit], parents[commit], tip = tree, [tip], commit
    return trees, parents, tip


def history(repo):
    trees, parents = {BASE: dict(repo.files)}, {BASE: []}
    tips = {"main": BASE, "other": BASE}
    for name, changes in repo.branches.items():
        branch_trees, branch_parents, tips[name] = branch_history(repo.files, name, changes)
        trees.update(branch_trees)
        parents.update(branch_parents)
    refs = {"HEAD": BASE, **tips, **{"refs/heads/" + name: tip for name, tip in tips.items()}}
    return {"revisions": trees, "parents": parents, "refs": refs}


def repo_state(repo):
    present = {**repo.files, **repo.disk}
    disk = {name: text for name, text in present.items() if not name.startswith(".git/")}
    state = {**history(repo), "index": dict(repo.files), "disk": disk, "ignore": ignore_rules(present)}
    return {**state, **repo.fields}


def decision_of(response):
    return response.get("hookSpecificOutput", {}).get("permissionDecision")


class FakeRepoCase(unittest.TestCase):
    projects = {}

    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.home = cls.root / "home"
        cls.state = cls.root / "state"
        cls.home.mkdir()
        roots = {}
        for name, repo in cls.projects.items():
            folder = cls.root / name
            folder.mkdir(parents=True, exist_ok=True)
            place(folder, {**repo.files, **repo.disk})
            if repo.git:
                (folder / ".git").mkdir(exist_ok=True)
                shared = {"common_dir": str(cls.root / repo.shares / ".git")} if repo.shares else {}
                roots[str(folder)] = {**repo_state(repo), **shared}
        cls.arrange()
        cls.git = FakeGitView({"roots": roots})

    @classmethod
    def arrange(cls):
        return None

    def session(self):
        return "s" + hashlib.sha256(self.id().encode()).hexdigest()[:16]

    def hook_environment(self):
        return {}

    def respond(self, event, payload):
        body = {"session_id": self.session(), "hook_event_name": event, **payload}
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", ""), **self.hook_environment()}
        with mock.patch.dict(os.environ, environment, clear=True):
            return run_hook(event, body, state_dir=self.state, git_view=self.git)

    def reported_rules(self):
        reports = sorted((self.state / "reports").glob(self.session() + "-*.json"))
        return sorted({row["rule"] for report in reports for row in json.loads(report.read_text(encoding="utf-8"))})

    def ledger_outcomes(self, rule):
        ledger = self.state / "ledger" / "ledger.jsonl"
        rows = map(json.loads, ledger.read_text(encoding="utf-8").splitlines())
        return sorted({(row["rule"], row["outcome"]) for row in rows if row.get("session_id") == self.session() and row.get("rule") == rule})

    def hook(self, project, payload, cwd=None):
        folder = self.root / project
        self.respond("SessionStart", {"cwd": str(folder), "source": "startup"})
        response = self.respond("PreToolUse", {"cwd": str(cwd or folder), **payload})
        return decision_of(response), self.reported_rules()

    def bash(self, project, command, cwd=None):
        return self.hook(project, {"tool_name": "Bash", "tool_input": {"command": command}}, cwd)

    def write(self, project, relative, content, agent_type=None, cwd=None):
        tool_input = {"file_path": str(self.root / project / relative), "content": content}
        payload = {"tool_name": "Write", "tool_input": tool_input}
        return self.hook(project, {**payload, "agent_type": agent_type} if agent_type else payload, cwd)

    def edit(self, project, relative, old, new):
        tool_input = {"file_path": str(self.root / project / relative), "old_string": old, "new_string": new}
        return self.hook(project, {"tool_name": "Edit", "tool_input": tool_input})
