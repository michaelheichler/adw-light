from quiet_git import disable_background_maintenance
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
PACK = "tree_sitter_language_pack"
BUNDLE = "tree-sitter-language-pack"
SITE = Path("lib") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
VENV = Path(os.environ.get("ADW_TEST_LANGUAGE_VENV") or Path.home() / ".adw-lite" / "install" / "venv")
GRAMMARS = Path(os.environ.get("ADW_TEST_GRAMMAR_CACHE") or Path.home() / ".adw-lite" / "cache" / "grammars")
LIBRARY_READY = (VENV / SITE / PACK).is_dir() and (GRAMMARS / BUNDLE).is_dir()
LIBRARY_NEEDED = "needs the pinned tree-sitter venv and a filled grammar cache (ADW_TEST_LANGUAGE_VENV, ADW_TEST_GRAMMAR_CACHE)"
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


class LayerHome:
    def __init__(self, case, exact):
        workspace = TemporaryDirectory()
        case.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.repo.mkdir()
        self.home.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        disable_background_maintenance(self.repo)
        if exact:
            self.link_language_layer()

    def link_language_layer(self):
        adw = self.home / ".adw-lite"
        (adw / "install").mkdir(parents=True)
        (adw / "install" / "venv").symlink_to(VENV, target_is_directory=True)
        grammars = adw / "cache" / "grammars"
        grammars.mkdir(parents=True)
        (grammars / BUNDLE).symlink_to(GRAMMARS / BUNDLE, target_is_directory=True)

    def write(self, relative, content):
        payload = {
            "session_id": "t20", "cwd": str(self.repo), "hook_event_name": "PreToolUse", "agent_type": TEST_WRITER,
            "tool_name": "Write", "tool_input": {"file_path": str(self.repo / relative), "content": content},
        }
        return subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True, timeout=120,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )

    def outcome(self, relative, content):
        call = self.write(relative, content)
        return Outcome(decision_of(json.loads(call.stdout)), self.ported_rows())

    def ledger(self):
        return [row for ledger in sorted((self.state / "ledger").glob("*.jsonl")) for row in json_lines(ledger)]

    def ported_rows(self):
        outcomes = {(row["rule"], row["outcome"]) for row in self.ledger() if row.get("rule") in PORTED_RULES}
        findings = {
            (finding["rule"], finding["line"]) for report in sorted((self.state / "reports").glob("*.json"))
            for finding in json.loads(report.read_text(encoding="utf-8")) if finding.get("rule") in PORTED_RULES
        }
        return tuple(sorted((rule, outcome, line) for rule, outcome in outcomes for found, line in findings if found == rule))

    def precision_reasons(self):
        return tuple(sorted(
            (row.get("language"), row.get("reason")) for row in self.ledger()
            if row.get("hook") == "language_layer" and row.get("event") == "reduced_precision"
        ))
