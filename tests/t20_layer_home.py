import hashlib
import json
import os
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "engine"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(ENGINE))

from adw_light import run_hook
from lib.git_port import FakeGitView


PACK = "tree_sitter_language_pack"
BUNDLE = "tree-sitter-language-pack"
SITE = Path("lib") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
VENV = Path(os.environ.get("ADW_TEST_LANGUAGE_VENV") or Path.home() / ".adw-lite" / "install" / "venv")
GRAMMARS = Path(os.environ.get("ADW_TEST_GRAMMAR_CACHE") or Path.home() / ".adw-lite" / "cache" / "grammars")
LIBRARY_READY = (VENV / SITE / PACK).is_dir() and (GRAMMARS / BUNDLE).is_dir()
LIBRARY_NEEDED = "needs the pinned tree-sitter venv and a filled grammar cache (ADW_TEST_LANGUAGE_VENV, ADW_TEST_GRAMMAR_CACHE)"
PRINCIPLE = "A test must verify what the code does for its client, never how it does it."
CITATION = "// Principle: " + PRINCIPLE
HASH_CITATION = "# Principle: " + PRINCIPLE
TEST_WRITER = "adw-test-writer"
DENY = "deny"
CONTEXT = "context"
NONE = "none"
BLOCK = "block"
OBSERVED = "would_block"
LOOP = "assert_in_loop"
IF = "if_statements_in_tests"
ACT = "multiple_act_sections_in_unit_test"
PORTED_RULES = frozenset((
    LOOP, IF, ACT, "hardcoded_name_presence", "hardcoded_literal_in_source", "exposing_private_methods_for_testing",
    "exposing_private_state_for_testing", "mocking_concrete_classes", "incomplete_mock_call_verification",
    "test_fixture_reuse_via_constructor",
))
PLAIN = "plain"
NO_PROJECTS = {PLAIN: {}}


class Outcome(NamedTuple):
    decision: str
    rows: tuple[tuple[str, str, int], ...]


def decision_of(response):
    output = response.get("hookSpecificOutput", {})
    if output.get("permissionDecision") == DENY:
        return DENY
    return CONTEXT if output.get("additionalContext") else NONE


def json_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def session_of(case):
    return "s" + hashlib.sha256(case.id().encode("utf-8")).hexdigest()[:24]


def from_engine(module):
    places = [getattr(module, "__file__", None), *getattr(module, "__path__", ())]
    return any(isinstance(place, str) and Path(place).resolve().is_relative_to(ENGINE) for place in places)


def engine_modules():
    return {name: module for name, module in list(sys.modules.items()) if module is not None and from_engine(module)}


@contextmanager
def fresh_engine(hidden):
    saved = engine_modules()
    covered = {name: sys.modules.get(name) for name in hidden}
    for name in saved:
        del sys.modules[name]
    sys.modules.update(dict.fromkeys(hidden))
    try:
        yield
    finally:
        for name in engine_modules():
            del sys.modules[name]
        sys.modules.update(saved)
        sys.modules.update({name: module for name, module in covered.items() if module is not None})
        for name in (name for name, module in covered.items() if module is None):
            sys.modules.pop(name, None)


def link_language_layer(home):
    adw = home / ".adw-lite"
    (adw / "install").mkdir(parents=True)
    (adw / "install" / "venv").symlink_to(VENV, target_is_directory=True)
    grammars = adw / "cache" / "grammars"
    grammars.mkdir(parents=True)
    (grammars / BUNDLE).symlink_to(GRAMMARS / BUNDLE, target_is_directory=True)


def build_project(folder, files):
    (folder / ".git").mkdir(parents=True)
    for relative, content in files.items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return {"disk": dict(files)}


class LayerHome:
    def __init__(self, root, projects):
        self.root = root
        self.state = root / "state"
        self.git = FakeGitView({"roots": {
            str(root / name): build_project(root / name, files) for name, files in projects.items()
        }})

    @classmethod
    def open(cls, case_class, exact, projects=NO_PROJECTS):
        stack = ExitStack()
        case_class.addClassCleanup(stack.close)
        root = Path(stack.enter_context(TemporaryDirectory())).resolve()
        (root / "home").mkdir()
        if exact:
            link_language_layer(root / "home")
            stack.enter_context(mock.patch.object(sys, "path", [*sys.path, str(VENV / SITE)]))
        environment = {"HOME": str(root / "home"), "PATH": os.environ.get("PATH", "")}
        stack.enter_context(mock.patch.dict(os.environ, environment, clear=True))
        stack.enter_context(fresh_engine(() if exact else (PACK,)))
        return cls(root, projects)

    def project(self, name=PLAIN):
        return self.root / name

    def hook(self, case, event, fields, project=PLAIN):
        payload = {"session_id": session_of(case), "cwd": str(self.project(project)), "hook_event_name": event, **fields}
        return run_hook(event, payload, state_dir=self.state, git_view=self.git)

    def start(self, case, project=PLAIN):
        return self.hook(case, "SessionStart", {"source": "startup"}, project)

    def write(self, case, relative, content, project=PLAIN):
        tool_input = {"file_path": str(self.project(project) / relative), "content": content}
        return self.hook(case, "PreToolUse", {"agent_type": TEST_WRITER, "tool_name": "Write", "tool_input": tool_input}, project)

    def report_rows(self, case):
        reports = sorted((self.state / "reports").glob(session_of(case) + "-*.json"))
        return [row for report in reports for row in json.loads(report.read_text(encoding="utf-8"))]

    def ledger(self):
        return [row for ledger in sorted((self.state / "ledger").glob("*.jsonl")) for row in json_lines(ledger)]

    def ported_rows(self, case):
        session = session_of(case)
        outcomes = {(row["rule"], row["outcome"]) for row in self.ledger()
                    if row.get("session_id") == session and row.get("rule") in PORTED_RULES}
        findings = {(row["rule"], row["line"]) for row in self.report_rows(case) if row.get("rule") in PORTED_RULES}
        return tuple(sorted((rule, outcome, line) for rule, outcome in outcomes for found, line in findings if found == rule))

    def outcome(self, case, relative, content):
        response = self.write(case, relative, content)
        return Outcome(decision_of(response), self.ported_rows(case))

    def precision_reasons(self, relative, project=PLAIN):
        path = str(self.project(project) / relative)
        return tuple(sorted(
            (row.get("language"), row.get("reason")) for row in self.ledger()
            if row.get("hook") == "language_layer" and row.get("event") == "reduced_precision" and row.get("path") == path
        ))
