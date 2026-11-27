from fake_repo_home import DENY, OLD_TEST, PASSED, REFUSED, SIZE_TEST, FakeRepoCase, Repo
import hashlib
import json
import unittest


SHEBANG = "#!/bin/bash\n"
COMMIT_ARM = 'git add x && git commit -m "We utilize this."'
DISPATCH = 'case "$1" in\n  sync) touch notes.txt ;;\n  commit) ' + COMMIT_ARM + ' ;;\nesac\n'
FALL_THROUGH = 'case "$1" in\n  sync) touch notes.txt ;&\n  commit) ' + COMMIT_ARM + ' ;;\nesac\n'
HEREDOC_ARM = (
    'case "$1" in\n  sync) touch notes.txt ;;\n  notes) cat > notes.txt <<EOF\nnote\nEOF\n  ;;\n'
    '  commit) ' + COMMIT_ARM + ' ;;\nesac\n'
)
SCRIPTS = {"s.sh": SHEBANG + DISPATCH, "fall.sh": SHEBANG + FALL_THROUGH, "heredoc.sh": SHEBANG + HEREDOC_ARM}
MESSAGE_FINDING = (DENY, [("commit_message.md", "utilize")])
NO_COMMITS = {"revisions": {}, "parents": {}, "refs": {}}
SCRATCH_IGNORE = {".gitignore": ".claude/tmp/\n", OLD_TEST: SIZE_TEST}
FLAGGED_MERGE = 'git merge --no-ff -m "sync" other'
SCRIPTED = "scripted"
SCRATCH = "scratch"


class CaseArmSelection(FakeRepoCase):
    projects = {SCRIPTED: Repo({}, SCRIPTS, fields=NO_COMMITS)}

    @classmethod
    def arrange(cls):
        for name in SCRIPTS:
            (cls.root / SCRIPTED / name).chmod(0o755)

    def session(self):
        return "s" + hashlib.sha256((self.id() + self.__dict__.get("label", "")).encode()).hexdigest()[:16]

    def outcome(self, command):
        self.label = command
        payload = {"cwd": str(self.root / SCRIPTED), "tool_name": "Bash", "tool_input": {"command": command}}
        decision = self.respond("PreToolUse", payload).get("hookSpecificOutput", {}).get("permissionDecision")
        ledger = self.state / "ledger" / "ledger.jsonl"
        rows = (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())
        blocked = {(row["path"], row["rule"]) for row in rows if row.get("session_id") == self.session() and row.get("outcome") == "block"}
        return decision, sorted(blocked)

    def test_script_runs_that_can_reach_a_committing_arm_deny_on_its_flagged_message(self):
        """Use a parameterized test for several inputs."""
        commands = ["bash ./s.sh sync", "bash ./s.sh commit", "source ./s.sh", "bash ./fall.sh sync", "bash ./heredoc.sh sync"]
        self.assertEqual({command: self.outcome(command) for command in commands}, dict.fromkeys(commands, MESSAGE_FINDING))


class NestedScratchClones(FakeRepoCase):
    projects = {
        SCRATCH: Repo(SCRATCH_IGNORE),
        SCRATCH + "/.claude/tmp/up": Repo({OLD_TEST: SIZE_TEST}),
        SCRATCH + "/vendor/up": Repo({OLD_TEST: SIZE_TEST}),
    }

    def test_pull_in_a_clone_under_an_ignored_scratch_folder_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.bash(SCRATCH, "git -C .claude/tmp/up pull"), PASSED)

class ArchiveExtraction(FakeRepoCase):
    projects = {SCRATCH: Repo(SCRATCH_IGNORE)}

    @classmethod
    def arrange(cls):
        (cls.root / SCRATCH / ".claude/tmp/x").mkdir(parents=True)

    def test_archive_extracted_into_an_ignored_scratch_folder_passes(self):
        self.assertEqual(self.bash(SCRATCH, "git archive HEAD | tar -x -C .claude/tmp/x"), PASSED)

    def test_archive_extracted_into_the_tests_folder_of_a_repo_with_ignored_scratch_is_refused(self):
        self.assertEqual(self.bash(SCRATCH, "git archive HEAD | tar -x -C tests/"), REFUSED)


class MergeOfChangedPaths(FakeRepoCase):
    projects = {
        "docs": Repo(SCRATCH_IGNORE, branches={"other": [{"docs/guide.md": "Guide.\n"}]}),
        "tests": Repo(SCRATCH_IGNORE, branches={"other": [{OLD_TEST: SIZE_TEST + "\n"}]}),
    }

    @classmethod
    def arrange(cls):
        (cls.root / "docs/.git/FETCH_HEAD").write_text("other-0\t\tbranch 'other' of .\n", encoding="utf-8")

    def test_flagged_merge_of_a_branch_that_changes_only_docs_passes(self):
        self.assertEqual(self.bash("docs", FLAGGED_MERGE), PASSED)

    def test_flagged_merge_of_a_branch_that_changes_a_test_file_passes(self):
        self.assertEqual(self.bash("tests", FLAGGED_MERGE), PASSED)

    def test_merge_of_fetch_head_passes(self):
        self.assertEqual(self.bash("docs", "git merge FETCH_HEAD"), PASSED)


if __name__ == "__main__":
    unittest.main()
