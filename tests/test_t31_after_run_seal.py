import json
import time
import unittest

from after_run_home import (
    CAUGHT, CODEX, CONFIG, LET_THROUGH, TEST_WRITER, AfterRunCase, Project, identity, project_files,
)

REPO = "repo"
WINDOWED = "windowed"
SCRATCH = "scratch"
LINKED = "linked"
CLEAN = "clean"
SEAL = "config_seal"
STOP_BLOCK = "block"
HIDDEN_WRITE = "python3 tools/settle.py"
NESTED = "pkg/" + CONFIG


def config_text(settings):
    return json.dumps(settings, indent=2) + "\n"


GOVERNING = config_text({"max_rows": 5, "rule_gates": {"comment_shape": "enforce"}})
OPEN_WINDOW = config_text({"max_rows": 5, "tests_allow_until": int(time.time()) + 3600,
                           "rule_gates": {"comment_shape": "enforce"}})
WEAKENED = config_text({"max_rows": 5, "rule_gates": {"comment_shape": "observe"}})
THRESHOLD = config_text({"max_rows": 9, "rule_gates": {"comment_shape": "enforce"}})
EDITS = {
    "rule_gates": WEAKENED,
    "gates": config_text({"max_rows": 5, "gates": {"prose": "observe"}, "rule_gates": {"comment_shape": "enforce"}}),
    "mixed": config_text({"max_rows": 9, "rule_gates": {"comment_shape": "observe"}}),
    "malformed": '{"max_rows": ',
}


class AgentConfigChangesAfterTheRun(AfterRunCase):
    projects = {
        REPO: Project(project_files({CONFIG: GOVERNING, "pkg/notes.md": "Notes.\n"})),
        WINDOWED: Project(project_files({CONFIG: OPEN_WINDOW})),
        SCRATCH: Project({CONFIG: WEAKENED, "notes.txt": ""}, git=False),
        LINKED: Project({CONFIG: WEAKENED}, git=False),
        CLEAN: Project({"notes.txt": ""}, git=False),
    }

    def effects(self):
        return {
            "created": lambda: self.change(REPO, NESTED, WEAKENED),
            "changed": lambda: self.change(REPO, CONFIG, WEAKENED),
            "deleted": lambda: self.remove(REPO, CONFIG),
        }

    def sealed_outcome(self, kind, host):
        run = kind + host
        verdict = self.after_run(REPO, HIDDEN_WRITE, self.effects()[kind], host=host, run=run)
        outcome = verdict, self.blocked_rules(run), self.stop(REPO, host=host, run=run)
        self.doCleanups()
        return outcome

    def link(self, name, folder):
        path = self.folder(REPO) / name
        path.symlink_to(self.folder(folder), target_is_directory=True)
        self.addCleanup(path.unlink)
        self.disk(REPO)[name] = ""

    def edit_verdict(self, label, content, relative=CONFIG):
        verdict = self.after_run(REPO, HIDDEN_WRITE, lambda: self.change(REPO, relative, content), run=label)
        self.doCleanups()
        return verdict

    def test_agent_shell_change_to_a_governing_config_is_sealed_after_the_run_on_both_hosts_and_at_stop(self):
        """Use a parameterized test for several inputs."""
        cases = [(kind, host) for kind in self.effects() for host in ("claude", CODEX)]
        outcomes = {case: self.sealed_outcome(*case) for case in cases}
        self.assertEqual(outcomes, dict.fromkeys(cases, (CAUGHT, [SEAL], STOP_BLOCK)))

    def test_threshold_only_edit_of_an_existing_config_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        verdict = self.edit_verdict("threshold", THRESHOLD)
        self.assertEqual((verdict, self.blocked_rules("threshold")), (LET_THROUGH, []))

    def test_policy_malformed_and_threshold_only_creation_edits_are_sealed(self):
        """Use a parameterized test for several inputs."""
        verdicts = {label: self.edit_verdict(label, content) for label, content in EDITS.items()}
        verdicts["threshold_creation"] = self.edit_verdict("threshold_creation", THRESHOLD, NESTED)
        self.assertEqual(verdicts, dict.fromkeys([*EDITS, "threshold_creation"], CAUGHT))

    def test_seal_holds_inside_an_open_tests_window_and_for_the_test_writer(self):
        """The test fails when a real bug enters the code it exercises."""
        verdicts = {
            "window": self.after_run(WINDOWED, HIDDEN_WRITE, lambda: self.change(WINDOWED, CONFIG, WEAKENED), run="window"),
            "test_writer": self.after_run(REPO, HIDDEN_WRITE, lambda: self.change(REPO, CONFIG, WEAKENED),
                                          before=identity(TEST_WRITER), run="test_writer"),
        }
        self.assertEqual(verdicts, dict.fromkeys(verdicts, CAUGHT))

    def test_scratch_config_outside_the_project_and_a_temp_folder_deletion_pass(self):
        """A test must verify what the code does for its client, never how it does it."""
        scratch = self.folder(SCRATCH)
        outcomes = {
            "scratch_write": self.shell(REPO, HIDDEN_WRITE, lambda: self.change(SCRATCH, CONFIG, GOVERNING), run="w"),
            "temp_deletion": self.shell(REPO, "rm -rf " + str(scratch), lambda: self.remove(SCRATCH, CONFIG), run="d"),
        }
        self.assertEqual(outcomes, dict.fromkeys(outcomes, (None, LET_THROUGH)))

    def test_new_link_to_a_folder_holding_a_config_is_sealed_and_a_link_to_a_clean_folder_passes(self):
        """Use a parameterized test for several inputs."""
        verdicts = {
            "config_folder": self.after_run(REPO, HIDDEN_WRITE, lambda: self.link("cfglink", LINKED), run="cfg"),
            "clean_folder": self.after_run(REPO, HIDDEN_WRITE, lambda: self.link("cleanlink", CLEAN), run="clean"),
        }
        self.assertEqual(verdicts, {"config_folder": CAUGHT, "clean_folder": LET_THROUGH})


if __name__ == "__main__":
    unittest.main()
