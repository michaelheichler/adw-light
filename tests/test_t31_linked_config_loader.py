import json
import os
import unittest
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from fake_repo_home import place
from in_memory_hook import run_hook

config = import_module("lib.config")
git_port = import_module("lib.git_port")

CONFIG = ".agent-discipline.json"
LINKED_RULE = "linked_config_ignored"
FILES = {
    "outside/withcfg/" + CONFIG: "{}",
    "outside/plain/notes.txt": "",
    "proj/" + CONFIG: "{}",
    "proj/sub/notes.txt": "",
    "proj/real/" + CONFIG: "{}",
    "bare/notes.txt": "",
    "top/" + CONFIG: "{}",
    "top/proj3/src/notes.txt": "",
    "home/notes.txt": "",
    "loose/notes.txt": "",
}
LINKS = {"proj/sub/link": "outside/withcfg", "bare/link": "outside/withcfg", "home/link": "outside/withcfg",
         "loose/link": "outside/withcfg", "proj/alias": "proj/real"}


class LinkedConfigLoader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        place(cls.root, FILES)
        for project in ("proj", "bare", "top/proj3"):
            (cls.root / project / ".git").mkdir()
        for link, target in LINKS.items():
            (cls.root / link).symlink_to(cls.root / target, target_is_directory=True)
        scratch = TemporaryDirectory(dir="/tmp")
        cls.addClassCleanup(scratch.cleanup)
        cls.tmp_spelled = Path(scratch.name) / "cfgproj"
        place(cls.tmp_spelled, {CONFIG: "{}", "deep/notes.txt": ""})

    def governing(self, cwd):
        with mock.patch.dict(os.environ, {"HOME": str(self.root / "home")}):
            return config.project_config_path(cwd)

    def config_in(self, folder):
        return self.root / folder / CONFIG

    def test_config_behind_a_link_that_leaves_the_project_is_ignored_for_the_next_one_up(self):
        """A test must verify what the code does for its client, never how it does it."""
        found = {"project_config": self.governing(self.root / "proj" / "sub" / "link"),
                 "fallback": self.governing(self.root / "bare" / "link")}
        self.assertEqual(found, {"project_config": self.config_in("proj"), "fallback": self.config_in("bare")})

    def test_loader_keeps_its_result_where_no_link_leaves_a_project(self):
        """Use a parameterized test for several inputs."""
        cases = {
            "parent_above_project": (self.root / "top" / "proj3" / "src", self.config_in("top")),
            "link_in_home": (self.root / "home" / "link", self.config_in("outside/withcfg")),
            "link_outside_projects": (self.root / "loose" / "link", self.config_in("outside/withcfg")),
            "link_inside_project": (self.root / "proj" / "alias", self.config_in("proj/real")),
            "tmp_spelling": (self.tmp_spelled / "deep", self.tmp_spelled.resolve() / CONFIG),
            "resolved_cwd": ((self.root / "proj" / "sub" / "link").resolve(), self.config_in("outside/withcfg")),
        }
        found = {label: self.governing(cwd) for label, (cwd, _) in cases.items()}
        self.assertEqual(found, {label: expected for label, (_, expected) in cases.items()})

    def ledger_rows(self, session):
        ledger = self.root / "state" / "ledger" / "ledger.jsonl"
        rows = map(json.loads, ledger.read_text(encoding="utf-8").splitlines())
        return [(row["rule"], row["path"]) for row in rows if row.get("session_id") == session and row.get("rule") == LINKED_RULE]

    def test_ignored_linked_config_is_logged_once_per_session(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        session = "linked-session"
        body = {"session_id": session, "hook_event_name": "PreToolUse", "cwd": str(self.root / "proj" / "sub" / "link"),
                "tool_name": "Bash", "tool_input": {"command": "ls"}}
        environment = {"HOME": str(self.root / "home"), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, environment, clear=True):
            for _ in range(2):
                run_hook("PreToolUse", body, state_dir=self.root / "state", git_view=git_port.FakeGitView({"roots": {}}))
        self.assertEqual(self.ledger_rows(session), [(LINKED_RULE, str(self.config_in("outside/withcfg")))])


if __name__ == "__main__":
    unittest.main()
