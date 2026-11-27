import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView
from lib import protected, wording_debt
from lib.config import CONFIG_NAME, CONTRACT_STRINGS_KEY, contract_strings, effective_config
from test_adw_config import CODEX_PREFIX, HOOK, PROMPT, SLASH, codex, slash


NO_REPOSITORY = FakeGitView({"roots": {}})
DENY = "deny"
WORDING = "wording_assertion"
MATCH = "wording_exception_match"
SEAL = "config_seal"
WRITER = "adw-test-writer"
PROBE = "tests/test_probe.py"
CONTRACT = " ".join(("delete", "firewall", "zone", "LAN", "from", "WAN"))
SHOUTED_CONTRACT = " ".join(("Delete", "firewall", "zone", "LAN", "from", "WAN"))
SPACED_CONTRACT = " ".join(("delete", "firewall", "zone", "", "LAN", "from", "WAN"))
PROSE = " ".join(("the", "cache", "is", "empty", "today"))
OTHER = " ".join(("set", "service", "ntp", "server", "pool"))
TEMPLATE = " ".join(("delete", "firewall", "zone", "{}", "from", "{}"))
SPEC_TEMPLATE = " ".join(("delete", "firewall", "zone", "{!r}", "from", "{:>10}"))
TEMPLATED = 'assert computed == f"delete firewall zone {lan} from {wan}"'
RESPACED = 'assert computed == f"delete firewall zone {lan}  from {wan}"'
MOVED = 'assert computed == f"delete firewall {lan} zone from {wan}"'
CITATION = '        """A test must verify what the code does for its client, never how it does it."""'
HEADER = ("import unittest", "", "", "class Probe(unittest.TestCase):", "", "    def test_probe(self):", CITATION)
SEEDED = {"tests": "deny", CONTRACT_STRINGS_KEY: [CONTRACT]}
DEBT_TEST = "\n".join((*HEADER, "        computed = render()", f"        assert computed == {json.dumps(CONTRACT)}")) + "\n"


def probe(assertion):
    return "\n".join((*HEADER, "        computed = render()", "        " + assertion)) + "\n"


def suite(*assertions):
    methods = [line for index, assertion in enumerate(assertions)
               for line in ("", f"    def test_probe_{index}(self):", CITATION, "        computed = render()", "        " + assertion)]
    return "\n".join(("import unittest", "", "", "class Probe(unittest.TestCase):", *methods)) + "\n"


def seeded(root, name, settings, files=()):
    folder = root / name
    (folder / ".git").mkdir(parents=True)
    (folder / "tests").mkdir()
    (folder / CONFIG_NAME).write_text(json.dumps(settings), encoding="utf-8")
    for relative, text in files:
        (folder / relative).write_text(text, encoding="utf-8")
    return folder


def stored(folder):
    return json.loads((folder / CONFIG_NAME).read_text(encoding="utf-8"))


class ContractStringGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / "state"
        (cls.root / "home").mkdir()
        cls.gated = seeded(cls.root, "gated", SEEDED)
        cls.debt = seeded(cls.root, "debt", {"tests": "deny"}, [(PROBE, DEBT_TEST)])
        cls.owner_add = seeded(cls.root, "owner_add", {"tests": "deny", CONTRACT_STRINGS_KEY: [OTHER]})
        cls.owner_codex = seeded(cls.root, "owner_codex", {"tests": "deny"})
        cls.owner_remove = seeded(cls.root, "owner_remove", {"tests": "deny", CONTRACT_STRINGS_KEY: [OTHER, CONTRACT]})
        cls.owner_agent = seeded(cls.root, "owner_agent", SEEDED)
        cls.template_gate = seeded(cls.root, "template_gate", {"tests": "deny"})
        cls.spec_template_gate = seeded(cls.root, "spec_template_gate", {"tests": "deny"})
        cls.template_debt = seeded(cls.root, "template_debt", {"tests": "deny"}, [(PROBE, suite(TEMPLATED, RESPACED, MOVED))])
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)
        HOOK.apply(f"contract add {TEMPLATE}", str(cls.template_gate), time.time())
        HOOK.apply(f"contract add {TEMPLATE}", str(cls.template_debt), time.time())
        HOOK.apply(f"contract add {SPEC_TEMPLATE}", str(cls.spec_template_gate), time.time())

    def respond(self, tool_name, tool_input, name, folder=None):
        folder = folder or self.gated
        payload = {"cwd": str(folder), "session_id": self._testMethodName + "-" + name,
                   "hook_event_name": "PreToolUse", "agent_type": WRITER, "tool_name": tool_name, "tool_input": tool_input}
        earlier = set((self.state / "reports").glob("*.json"))
        response = run_hook("PreToolUse", payload, state_dir=self.state, git_view=NO_REPOSITORY)
        added = sorted(set((self.state / "reports").glob("*.json")) - earlier)
        rows = [row for report in added for row in json.loads(report.read_text(encoding="utf-8"))]
        return response.get("hookSpecificOutput", {}).get("permissionDecision"), rows

    def wording_verdict(self, name, assertion, folder=None):
        folder = folder or self.gated
        decision, rows = self.respond("Write", {"file_path": str(folder / PROBE), "content": probe(assertion)}, name, folder)
        return decision, sorted({row["rule"] for row in rows if row["rule"] in (WORDING, MATCH)})

    def seal_verdict(self, name, tool_name, tool_input):
        decision, rows = self.respond(tool_name, tool_input, name)
        return decision, sorted({(row["rule"], row["action"]) for row in rows})

    def debt_total(self, session, folder=None):
        folder = folder or self.debt
        contracts = contract_strings(effective_config(cwd=str(folder)))
        return wording_debt.project_debt(session, str(folder), str(self.state), time.monotonic() + 30, contracts).total

    def owner(self, folder, event, payload):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            response = HOOK.main(event)
        return response.get("decision"), stored(folder)[CONTRACT_STRINGS_KEY]

    def test_f_string_assertions_block_only_on_unapproved_literal_text(self):
        """A machine token passes, for example a rule id, a JSON key, an enum value, or an exit code."""
        cases = {
            "prose_f_string": 'assert computed == f"the cache for {name} is empty today"',
            "placeholder_only_f_string": 'assert computed == f"{first} {second}"',
            "exact_contract": f"assert computed == {json.dumps(CONTRACT)}",
            "exact_contract_f_string": f"assert computed == f{json.dumps(CONTRACT)}",
            "case_near_match": f"assert computed == {json.dumps(SHOUTED_CONTRACT)}",
            "spacing_near_match": f"assert computed == {json.dumps(SPACED_CONTRACT)}",
            "contract_beside_prose": f"assert computed == ({json.dumps(CONTRACT)}, {json.dumps(PROSE)})",
        }
        verdicts = {name: self.wording_verdict(name, assertion) for name, assertion in cases.items()}
        blocked = (DENY, [WORDING])
        expected = {
            "prose_f_string": blocked, "placeholder_only_f_string": (None, []), "exact_contract": (None, []),
            "exact_contract_f_string": (None, []), "case_near_match": blocked, "spacing_near_match": blocked,
            "contract_beside_prose": blocked,
        }
        self.assertEqual(verdicts, expected)

    def test_f_strings_with_neutral_literal_text_pass(self):
        """A machine token passes, for example a rule id, a JSON key, an enum value, or an exit code."""
        cases = {
            "command_with_zone": 'assert computed == f"delete {wan_zone}"',
            "bearer_header": 'assert computed == f"Bearer {token}"',
            "version_tag": 'assert computed == f"v{major}.{minor}"',
            "user_key": 'assert computed == f"user:{uid}"',
            "error_code": 'assert computed == f"error: {code}"',
            "item_count": 'assert computed == f"{n} items"',
            "unit_plural": 'assert computed == f"{count} {unit}s"',
            "vyos_address": 'assert computed == f"set interfaces ethernet {iface} address {addr}"',
        }
        verdicts = {name: self.wording_verdict(name, assertion) for name, assertion in cases.items()}
        self.assertEqual(verdicts, {name: (None, []) for name in cases})

    def test_f_strings_with_sentence_text_block(self):
        """An equality assertion whose expected value is a sentence."""
        cases = {
            "sentence_around_name": 'assert computed == f"The cache for {name} is empty now"',
            "sentence_after_count": 'assert computed == f"Removed {n} files from the cache"',
            "sentence_beside_f_string": 'assert computed == [f"{a}", "The cache is empty for every user"]',
        }
        verdicts = {name: self.wording_verdict(name, assertion) for name, assertion in cases.items()}
        self.assertEqual(verdicts, {name: (DENY, [WORDING]) for name in cases})

    def test_owner_template_passes_only_its_exact_f_string_shape(self):
        """A machine token passes, for example a rule id, a JSON key, an enum value, or an exit code."""
        cases = {"templated": TEMPLATED, "respaced": RESPACED, "moved": MOVED}
        verdicts = {name: self.wording_verdict(name, assertion, self.template_gate) for name, assertion in cases.items()}
        blocked = (DENY, [WORDING])
        self.assertEqual(verdicts, {"templated": (None, []), "respaced": blocked, "moved": blocked})

    def test_sentence_text_anywhere_inside_an_f_string_blocks(self):
        """An equality assertion whose expected value is a sentence."""
        cases = {
            "format_spec_sentence": '''assert computed == f"{now:The cache is empty today}"''',
            "second_field_spec_sentence": '''assert computed == f"{now:%A} {x:the cache is empty today}"''',
            "date_spec_with_sentence": '''assert computed == f"{now:%A the cache is empty today}"''',
            "nested_f_string": '''assert computed == f"{f'The cache for {name} is empty now'}"''',
            "string_in_field": '''assert computed == f"{'The cache is empty for every user'}"''',
            "string_in_nested_spec": '''assert computed == f"{x:{'The cache is empty for every user'}}"''',
            "startswith_spec": '''assert computed.startswith(f"{x:The cache is empty today}")''',
            "regex_field": '''self.assertRegex(computed, f"{'The cache is empty for every user'}")''',
        }
        verdicts = {name: self.wording_verdict(name, assertion) for name, assertion in cases.items()}
        self.assertEqual(verdicts, {name: (DENY, [WORDING]) for name in cases})

    def test_sentence_text_joined_by_plus_or_bound_to_a_name_blocks(self):
        """An equality assertion whose expected value is a sentence."""
        cases = {
            "f_string_plus_sentence": '''assert computed == f"{a}" + "The cache is empty for every user"''',
            "sentence_plus_f_string": '''assert computed == "The cache for " + f"{name}" + " is empty now"''',
            "bound_expected": '''msg = f"The cache for {name} is empty now"
        assert computed == msg''',
            "bound_actual": '''actual = f"Result: {render()}"
        assert actual == "Result: the cache is empty now"''',
        }
        verdicts = {name: self.wording_verdict(name, assertion) for name, assertion in cases.items()}
        self.assertEqual(verdicts, {name: (DENY, [WORDING]) for name in cases})

    def test_sentence_in_an_f_string_match_pattern_blocks(self):
        """A prose pattern in `pytest.raises(match=...)`, `assertRaisesRegex`, or `excinfo.match` blocks as `wording_exception_match`."""
        assertion = '''with pytest.raises(ValueError, match=f"{x:The cache is empty today}"):
            render()'''
        self.assertEqual(self.wording_verdict(self._testMethodName, assertion), (DENY, [MATCH]))

    def test_format_specs_and_machine_text_pass(self):
        """A machine token passes, for example a rule id, a JSON key, an enum value, or an exit code."""
        cases = {
            "weekday_spec": '''assert computed == f"{now:%A}"''',
            "date_spec": '''assert computed == f"{now:%Y-%m-%d}"''',
            "float_spec": '''assert computed == f"{value:.2f}"''',
            "align_spec": '''assert computed == f"{value:>10}"''',
            "command_with_spec": '''assert computed == f"set interfaces ethernet {iface:>4} address {addr}"''',
            "command_plus_chain": '''assert computed == "set interfaces ethernet " + f"{iface}" + " address"''',
            "text_plus_call": '''assert computed == "the cache " + str(3)''',
            "bound_command": '''cmd = f"delete {zone}"
        assert computed == cmd''',
            "literal_against_itself": '''self.assertEqual("the cache is empty", "the cache is empty")''',
            "escaped_braces_only": '''assert computed == f"{{lan}} the cache for {name} is empty now"''',
        }
        verdicts = {name: self.wording_verdict(name, assertion) for name, assertion in cases.items()}
        self.assertEqual(verdicts, {name: (None, []) for name in cases})

    def test_bare_owner_template_rejects_text_conversions_and_specs_in_its_fields(self):
        """A machine token passes, for example a rule id, a JSON key, an enum value, or an exit code."""
        cases = {
            "exact_shape": '''assert computed == f"delete firewall zone {lan} from {wan}"''',
            "plus_chain": '''assert computed == "delete firewall zone " + f"{lan}" + " from " + f"{wan}"''',
            "bound_command": '''cmd = f"delete firewall zone {lan} from {wan}"
        assert computed == cmd''',
            "sentence_spec": '''assert computed == f"delete firewall zone {lan:the cache is empty} from {wan}"''',
            "conversion": '''assert computed == f"delete firewall zone {lan!r} from {wan}"''',
            "align_spec": '''assert computed == f"delete firewall zone {lan} from {wan:>10}"''',
        }
        verdicts = {name: self.wording_verdict(name, assertion, self.template_gate) for name, assertion in cases.items()}
        blocked = (DENY, [WORDING])
        expected = {"exact_shape": (None, []), "plus_chain": (None, []), "bound_command": (None, []),
                    "sentence_spec": blocked, "conversion": blocked, "align_spec": blocked}
        self.assertEqual(verdicts, expected)

    def test_owner_template_with_conversion_and_spec_passes_only_that_shape(self):
        """A machine token passes, for example a rule id, a JSON key, an enum value, or an exit code."""
        cases = {
            "matching_shape": '''assert computed == f"delete firewall zone {lan!r} from {wan:>10}"''',
            "bare_shape": '''assert computed == f"delete firewall zone {lan} from {wan}"''',
        }
        verdicts = {name: self.wording_verdict(name, assertion, self.spec_template_gate) for name, assertion in cases.items()}
        self.assertEqual(verdicts, {"matching_shape": (None, []), "bare_shape": (DENY, [WORDING])})

    def test_wording_debt_skips_only_f_strings_matching_an_owner_template(self):
        """ADW also counts the wording tests in each project folder at session start."""
        self.assertEqual(self.debt_total(self._testMethodName, self.template_debt), 2)

    def test_wording_debt_follows_owner_contract_edits(self):
        """ADW also counts the wording tests in each project folder at session start."""
        session = self._testMethodName
        totals = [self.debt_total(session)]
        HOOK.apply(f"contract add {CONTRACT}", str(self.debt), time.time())
        totals.append(self.debt_total(session))
        HOOK.apply(f"contract remove {CONTRACT}", str(self.debt), time.time())
        totals.append(self.debt_total(session))
        self.assertEqual(totals, [1, 0, 1])

    def test_agent_changes_of_the_contract_key_get_the_owner_policy_seal(self):
        """Feed input, check the return value."""
        config = str(self.gated / CONFIG_NAME)
        current = (self.gated / CONFIG_NAME).read_text(encoding="utf-8")
        added = json.dumps({**SEEDED, CONTRACT_STRINGS_KEY: [CONTRACT, PROSE]})
        dropped = json.dumps({"tests": "deny"})
        shell = "printf '%s' " + json.dumps(added) + " > " + CONFIG_NAME
        cases = {
            "write_adds_entry": ("Write", {"file_path": config, "content": added}),
            "write_drops_key": ("Write", {"file_path": config, "content": dropped}),
            "edit_changes_entry": ("Edit", {"file_path": config, "old_string": json.dumps(CONTRACT),
                                            "new_string": json.dumps(PROSE)}),
            "shell_rewrites_config": ("Bash", {"command": shell}),
        }
        verdicts = {name: self.seal_verdict(name, tool, tool_input) for name, (tool, tool_input) in cases.items()}
        sealed = (DENY, [(SEAL, protected.POLICY_ACTION)])
        self.assertEqual((verdicts, json.dumps(CONTRACT) in current), ({name: sealed for name in cases}, True))

    def test_owner_slash_command_adds_an_entry_once(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        command = f"contract add {CONTRACT}"
        self.owner(self.owner_add, SLASH, slash(command, str(self.owner_add)))
        outcome = self.owner(self.owner_add, SLASH, slash(command, str(self.owner_add)))
        self.assertEqual(outcome, ("block", [OTHER, CONTRACT]))

    def test_owner_codex_command_adds_an_entry(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        payload = codex(f"{CODEX_PREFIX} contract add {CONTRACT}", str(self.owner_codex))
        self.assertEqual(self.owner(self.owner_codex, PROMPT, payload), ("block", [CONTRACT]))

    def test_owner_command_removes_only_the_named_entry(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        outcome = self.owner(self.owner_remove, SLASH, slash(f"contract remove {CONTRACT}", str(self.owner_remove)))
        self.assertEqual(outcome, ("block", [OTHER]))

    def test_owner_command_ignores_payloads_that_carry_an_agent_id(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        cwd = str(self.owner_agent)
        cases = {
            "slash_add": (SLASH, slash(f"contract add {PROSE}", cwd, agent_id="a1")),
            "slash_remove": (SLASH, slash(f"contract remove {CONTRACT}", cwd, agent_id="a1")),
            "codex_add": (PROMPT, codex(f"{CODEX_PREFIX} contract add {PROSE}", cwd, agent_id="a1")),
        }
        outcomes = {name: self.owner(self.owner_agent, event, payload) for name, (event, payload) in cases.items()}
        self.assertEqual(outcomes, {name: (None, [CONTRACT]) for name in cases})


if __name__ == "__main__":
    unittest.main()
