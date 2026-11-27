import json
import unittest

from after_run_home import (
    CONFIG, LET_THROUGH, AfterRunCase, Project, project_files,
)

REPO = "repo"
SCRATCH = "scratch"
HIDDEN_WRITE = "python3 tools/settle.py"


def config_text(settings):
    return json.dumps(settings, indent=2) + "\n"


GOVERNING = config_text({"max_rows": 5, "rule_gates": {"comment_shape": "enforce"}})
WEAKENED = config_text({"max_rows": 5, "rule_gates": {"comment_shape": "observe"}})
THRESHOLD = config_text({"max_rows": 9, "rule_gates": {"comment_shape": "enforce"}})


class AgentConfigChangesAfterTheRun(AfterRunCase):
    projects = {
        REPO: Project(project_files({CONFIG: GOVERNING, "pkg/notes.md": "Notes.\n"})),
        SCRATCH: Project({CONFIG: WEAKENED, "notes.txt": ""}, git=False),
    }

    def edit_verdict(self, label, content, relative=CONFIG):
        verdict = self.after_run(REPO, HIDDEN_WRITE, lambda: self.change(REPO, relative, content), run=label)
        self.doCleanups()
        return verdict

    def test_threshold_only_edit_of_an_existing_config_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        verdict = self.edit_verdict("threshold", THRESHOLD)
        self.assertEqual((verdict, self.blocked_rules("threshold")), (LET_THROUGH, []))

    def test_scratch_config_outside_the_project_and_a_temp_folder_deletion_pass(self):
        """A test must verify what the code does for its client, never how it does it."""
        scratch = self.folder(SCRATCH)
        outcomes = {
            "scratch_write": self.shell(REPO, HIDDEN_WRITE, lambda: self.change(SCRATCH, CONFIG, GOVERNING), run="w"),
            "temp_deletion": self.shell(REPO, "rm -rf " + str(scratch), lambda: self.remove(SCRATCH, CONFIG), run="d"),
        }
        self.assertEqual(outcomes, dict.fromkeys(outcomes, (None, LET_THROUGH)))


if __name__ == "__main__":
    unittest.main()
