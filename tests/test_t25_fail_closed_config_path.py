import re
import sys
import unittest
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = str(PROJECT / "engine")
CONFIG_NAME = ".agent-discipline.json"
CAUSE = "gate-crash-7f3a"
DENY = "deny"
NAMED_CONFIG = re.compile(r"\S*" + re.escape(CONFIG_NAME))


class GateCrash(RuntimeError):
    pass


def crashing_gate(payload, config):
    raise GateCrash(CAUSE)


MISSING = object()


class FailClosedNamesGoverningConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        for folder in ("outer/inner/deep", "outer/side", "bare"):
            (cls.root / folder).mkdir(parents=True)
        for folder in ("outer", "outer/inner"):
            (cls.root / folder / CONFIG_NAME).write_text("{}", encoding="utf-8")
        sys.path.insert(0, ENGINE)
        cls.addClassCleanup(sys.path.remove, ENGINE)
        cls.pre_tool = import_module("pre_tool")

    def refusal(self, cwd):
        located = {"cwd": str(self.root / cwd)} if isinstance(cwd, str) and cwd else {}
        raw = {} if cwd is MISSING or located else {"cwd": cwd}
        payload = {**located, **raw, "tool_name": "Bash", "tool_input": {"command": "ls"}}
        gates = self.pre_tool.Gates(bash=crashing_gate, commit=crashing_gate)
        response = self.pre_tool.run(payload, gates=gates)
        reason = response["hookSpecificOutput"]["permissionDecisionReason"]
        decision = response["hookSpecificOutput"]["permissionDecision"]
        return decision, CAUSE in reason, frozenset(NAMED_CONFIG.findall(reason))

    def expected(self, governing):
        named = {str(self.root / governing / CONFIG_NAME)} if governing else set()
        return DENY, True, frozenset(named)

    def test_nearest_nested_config_governs_a_deep_folder(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.expected("outer/inner"), self.refusal("outer/inner/deep"))

    def test_outer_config_governs_a_sibling_folder(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.expected("outer"), self.refusal("outer/side"))

    def test_folder_without_config_names_none_although_the_checkout_has_one(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.expected(None), self.refusal("bare"))

    def test_absent_folder_without_config_names_none(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.expected(None), self.refusal("bare/absent"))

    def test_missing_cwd_names_none(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.expected(None), self.refusal(MISSING))

    def test_empty_cwd_names_none(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.expected(None), self.refusal(""))

    def test_non_string_cwd_names_none(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.expected(None), self.refusal(42))


if __name__ == "__main__":
    unittest.main()
