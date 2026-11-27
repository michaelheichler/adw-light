import importlib.util
import json
import os
import tempfile
import time
import unittest
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from unittest import mock

from after_run_home import CAUGHT, CONFIG, LET_THROUGH, AfterRunCase, Project, project_files

ROOT = Path(__file__).resolve().parents[1]
after_run_config = import_module("lib.after_run_config")
git_port = import_module("lib.git_port")
protected = import_module("lib.protected")
session_state = import_module("lib.session_state")
shell_snapshot = import_module("lib.shell_snapshot")

REPO = "repo"
FRESH = "fresh"
PLAIN = "plain"
HIDDEN_WRITE = "python3 tools/settle.py"
OWNER_CHANGE = "rule filler enforce"
NESTED = "pkg/" + CONFIG
DENY = "deny"


def config_text(settings):
    return json.dumps(settings, indent=2) + "\n"


ORIGINAL = {"max_rows": 5, "rule_gates": {"comment_shape": "enforce"}}
PLANTED = {"max_rows": 7, "exempt_paths": ["src"], "rule_gates": {"comment_shape": "observe"}}
CREATED = {"max_rows": 3, "rule_gates": {"comment_shape": "off"}}
WITH_FILLER = {"max_rows": 5, "rule_gates": {"comment_shape": "enforce", "filler": "enforce"}}


def load_owner_hook():
    spec = importlib.util.spec_from_file_location("adw_config_hook_t31", ROOT / "hooks" / "adw_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OWNER = load_owner_hook()


class SealCase(AfterRunCase):
    projects = {
        REPO: Project(project_files({CONFIG: config_text(ORIGINAL)})),
        FRESH: Project(project_files()),
        PLAIN: Project(project_files({CONFIG: config_text(ORIGINAL)})),
    }

    def environment(self):
        return {"HOME": str(self.home), "PATH": os.environ.get("PATH", ""), "ADW_STATE_ROOT": str(self.state)}

    def agent_writes(self, name, relative, settings, run=""):
        return self.after_run(name, HIDDEN_WRITE, lambda: self.change(name, relative, config_text(settings)), run=run)

    def owner(self, name, arguments=OWNER_CHANGE, run=""):
        payload = {"session_id": self.session(run), "cwd": str(self.folder(name))}
        with mock.patch.dict(os.environ, self.environment(), clear=True), git_port.using_view(self.view()):
            path, discarded = OWNER.apply_owner(arguments, str(self.folder(name)), time.time(), payload)
        return json.loads(path.read_text(encoding="utf-8")), discarded

    def plant_seal(self, name, entry):
        key = str(self.folder(name) / CONFIG)

        def plant(state):
            return {**state, "after_run": {"pending": {"": {key: {**entry, "rows": [], "root": str(self.folder(name))}}}}}

        with mock.patch.dict(os.environ, self.environment(), clear=True):
            session_state.update_state_strict(self.session(), plant, None)


class OwnerCommandOverPendingSeal(SealCase):
    def test_owner_change_builds_on_the_sealed_content_and_names_the_discarded_keys(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.agent_writes(REPO, CONFIG, PLANTED)
        self.assertEqual(self.owner(REPO), (WITH_FILLER, ["exempt_paths", "max_rows", "rule_gates.comment_shape"]))

    def test_owner_change_releases_the_seal(self):
        """The test fails when a real bug enters the code it exercises."""
        self.agent_writes(REPO, CONFIG, PLANTED)
        self.owner(REPO)
        self.assertEqual((self.batch(REPO, HIDDEN_WRITE).get("decision"), self.stop(REPO)), (LET_THROUGH, LET_THROUGH))

    def test_second_owner_change_after_the_release_builds_on_the_file_with_nothing_discarded(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.agent_writes(REPO, CONFIG, PLANTED)
        self.owner(REPO)
        expected = {**WITH_FILLER, "rule_gates": {**WITH_FILLER["rule_gates"], "comment_shape": "observe"}}
        self.assertEqual(self.owner(REPO, "rule comment_shape observe"), (expected, []))

    def test_owner_change_over_an_agent_created_config_keeps_only_owner_values(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.agent_writes(FRESH, CONFIG, CREATED)
        self.assertEqual(self.owner(FRESH), ({"rule_gates": {"filler": "enforce"}}, ["max_rows", "rule_gates"]))

    def test_seal_without_a_readable_baseline_keeps_only_non_policy_keys(self):
        """Use a parameterized test for several inputs."""
        baselines = {
            "unknown": {"seal": shell_snapshot.UNKNOWN},
            "digest_mismatch": {"seal": sha256(b"other").hexdigest(), "seal_text": config_text(ORIGINAL)},
        }
        outcomes = {}
        for label, entry in baselines.items():
            self.change(PLAIN, CONFIG, config_text(PLANTED))
            self.plant_seal(PLAIN, entry)
            outcomes[label] = self.owner(PLAIN)[0]
        self.assertEqual(outcomes, dict.fromkeys(baselines, {"max_rows": 7, "rule_gates": {"filler": "enforce"}}))

    def test_owner_change_without_a_pending_seal_keeps_every_key(self):
        """The test stays green when you restructure the code without changing its behavior."""
        self.start(PLAIN)
        self.assertEqual(self.owner(PLAIN), (WITH_FILLER, []))

    def test_repeated_agent_edits_keep_the_content_from_before_the_first_seal(self):
        """The test fails when a real bug enters the code it exercises."""
        self.agent_writes(REPO, CONFIG, PLANTED)
        self.pair(REPO, HIDDEN_WRITE, lambda: self.change(REPO, CONFIG, config_text(CREATED)), call="c2")
        self.assertEqual(self.owner(REPO)[0], WITH_FILLER)


class SealEntry(unittest.TestCase):
    def test_restored_baseline_clears_both_seal_keys(self):
        """Feed input, check the return value."""
        path, root, digest = Path("/p") / CONFIG, Path("/p"), sha256(b"{}").hexdigest()
        seal_row, other_row = {"rule": "config_seal"}, {"rule": "utilize"}
        sealed = {"seal": digest, "seal_text": "{}", "digest": "x", "root": str(root)}
        outcomes = {
            "only_seal": after_run_config.seal_entry(path, root, {**sealed, "rows": [seal_row]}, digest, digest),
            "other_row": after_run_config.seal_entry(path, root, {**sealed, "rows": [seal_row, other_row]}, digest, digest),
        }
        self.assertEqual(outcomes, {"only_seal": ({}, []), "other_row": ({"digest": "x", "root": str(root), "rows": [other_row]}, [])})


class RestoreMatching(SealCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.object(tempfile, "tempdir", "/tmp"):
            super().setUpClass()

    def spellings(self, name, relative):
        folder = self.folder(name)
        logical = Path("/tmp") / folder.relative_to(folder.parent.parent) if Path("/tmp").resolve() != Path("/tmp") else folder
        return {"tmp": str(logical / relative), "private": str(folder / relative), "relative": relative}

    def matches(self, name, relative, commands, run):
        cfg = {"state_root": str(self.state / "state"), "ledger_root": str(self.state / "ledger")}
        path = self.folder(name) / relative
        found = {}
        with mock.patch.dict(os.environ, self.environment(), clear=True), git_port.using_view(self.view()):
            for label, command in commands.items():
                payload = {"session_id": self.session(run), "hook_event_name": "PreToolUse", "cwd": str(self.folder(name)),
                           "tool_name": "Bash", "tool_input": {"command": command}}
                with after_run_config.restore_scope(payload, cfg):
                    found[label] = protected.restore_check.get()(path, None)
        return found

    def test_removal_restores_a_new_config_under_every_spelling_and_nothing_else_matches(self):
        """Use a parameterized test for several inputs."""
        self.agent_writes(REPO, NESTED, CREATED, run="new")
        named = self.spellings(REPO, NESTED)
        commands = {**{"rm_" + label: "rm -- " + spelled for label, spelled in named.items()},
                    "semicolon": "rm -- " + NESTED + "; true", "glob": "rm -- pkg/*.json", "dollar": "rm -- $PWD/" + NESTED,
                    "tilde": "rm -- ~/" + NESTED, "newline": "rm -- " + NESTED + "\n", "extra_word": "rm -- " + NESTED + " x",
                    "wrong_verb": "git restore -- " + NESTED, "other_path": "rm -- README.md"}
        expected = {label: label.startswith("rm_") for label in commands}
        self.assertEqual(self.matches(REPO, NESTED, commands, "new"), expected)

    def test_git_version_restores_a_changed_tracked_config_and_removal_does_not(self):
        """Use a parameterized test for several inputs."""
        self.agent_writes(REPO, CONFIG, PLANTED, run="tracked")
        named = self.spellings(REPO, CONFIG)
        commands = {"restore_tmp": "git restore -- " + named["tmp"], "checkout_private": "git checkout -- " + named["private"],
                    "restore_relative": "git restore -- " + CONFIG, "head_relative": "git restore --source=HEAD -- " + CONFIG,
                    "wrong_verb": "rm -- " + CONFIG}
        expected = {label: label != "wrong_verb" for label in commands}
        self.assertEqual(self.matches(REPO, CONFIG, commands, "tracked"), expected)

    def test_gate_lets_the_matching_removal_of_a_sealed_new_config_through(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.agent_writes(REPO, NESTED, CREATED)
        decisions = {"restore": self.pre(REPO, "rm -- " + NESTED, call="c2"),
                     "chained": self.pre(REPO, "rm -- " + NESTED + "; true", call="c3")}
        self.assertEqual(decisions, {"restore": None, "chained": DENY})


if __name__ == "__main__":
    unittest.main()
