import hashlib
import json
import unittest

from fake_repo_home import GATE, README, FakeRepoCase, Repo


DENY = "deny"
PROSE_RULE = "utilize"
NOTES = "notes.md"
SUB = "sub/readme.md"
CLEAN = "Short note.\n"
FLAGGED = "We utilize this tool for the build.\n"
HEAD = {**README, NOTES: "Old.\n", SUB: "Sub.\n"}
INSIDE = "inside"
INSIDE_FLAGGED = "inside_flagged"
OUTSIDE = "outside"
OUTSIDE_FLAGGED = "outside_flagged"
SAFE = ' -m "Add notes."'
FINDING = (DENY, [PROSE_RULE])
REFUSAL = (DENY, [])
PASSED = (None, [])
TEST_WRITE = (DENY, [GATE])


def staged(text):
    return Repo(HEAD, {NOTES: text}, fields={"index": {**HEAD, NOTES: text}})


def worktree(text):
    return Repo(HEAD, {NOTES: text})


def alias_run(options, body):
    return "git " + options + " -c alias.zap='!" + body + "' zap"


class CoverageHome(FakeRepoCase):
    projects = {
        INSIDE: worktree(CLEAN),
        INSIDE_FLAGGED: worktree(FLAGGED),
        OUTSIDE: staged(CLEAN),
        OUTSIDE_FLAGGED: staged(FLAGGED),
    }

    def session(self):
        label = self.__dict__.get("label", "")
        return "s" + hashlib.sha256((self.id() + label).encode()).hexdigest()[:16]

    def gate(self, project, command, call="c1"):
        self.label = project + command + call
        identity = {"tool_use_id": call} if call else {}
        decision, _ = self.hook(project, {"tool_name": "Bash", "tool_input": {"command": command}, **identity})
        return decision, self.blocked_rules()

    def blocked_rules(self):
        ledger = self.state / "ledger" / "ledger.jsonl"
        rows = (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())
        return sorted({row["rule"] for row in rows if row.get("session_id") == self.session() and row.get("outcome") == "block"})

    def outcomes(self, project, commands, call="c1"):
        return {command: self.gate(project, command, call) for command in commands}

    def outside(self, name):
        return "git -C " + str(self.root / name) + " commit"


class CommitsOutsideTheProject(CoverageHome):
    def test_signing_flags_with_flagged_staged_content_outside_the_project_deny_with_findings(self):
        """Use a parameterized test for several inputs."""
        commands = [self.outside(OUTSIDE_FLAGGED) + flag + SAFE for flag in (" --no-gpg-sign", " -S")]
        self.assertEqual(self.outcomes(INSIDE, commands), dict.fromkeys(commands, FINDING))

    def test_commits_the_gate_cannot_read_outside_the_project_are_refused(self):
        """Use a parameterized test for several inputs."""
        commands = [self.outside(OUTSIDE) + " -a --frobnicate" + SAFE, self.outside(OUTSIDE) + ' -m "$MSG"']
        self.assertEqual(self.outcomes(INSIDE, commands), dict.fromkeys(commands, REFUSAL))

    def test_commit_of_a_clean_index_outside_the_project_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(INSIDE, self.outside(OUTSIDE) + SAFE), PASSED)


class CommitsInsideTheProject(CoverageHome):
    def test_commits_the_after_run_check_covers_pass_before_the_run(self):
        """Use a parameterized test for several inputs."""
        commands = ['git commit -m "$MSG"', "git commit -a --fixup=HEAD"]
        self.assertEqual(self.outcomes(INSIDE, commands), dict.fromkeys(commands, PASSED))

    def test_signing_flags_with_all_on_flagged_files_deny_with_findings(self):
        """Use a parameterized test for several inputs."""
        commands = ["git commit -S -a" + SAFE, 'git commit --no-gpg-sign -am "Add notes."']
        self.assertEqual(self.outcomes(INSIDE_FLAGGED, commands), dict.fromkeys(commands, FINDING))

    def test_commits_without_a_tool_use_id_that_the_gate_cannot_read_are_refused(self):
        """Use a parameterized test for several inputs."""
        commands = ["GIT_INDEX_FILE=$X git commit" + SAFE, 'git commit -m "$MSG"']
        self.assertEqual(self.outcomes(INSIDE, commands, call=""), dict.fromkeys(commands, REFUSAL))

    def test_commits_that_move_the_repository_are_refused(self):
        """Use a parameterized test for several inputs."""
        commands = ["GIT_DIR=.git git commit" + SAFE, "GIT_WORK_TREE=. git commit" + SAFE, "env -C sub git commit" + SAFE,
                    "git --git-dir=.git commit" + SAFE, "git --work-tree=. commit" + SAFE]
        self.assertEqual(self.outcomes(INSIDE, commands), dict.fromkeys(commands, REFUSAL))


class ShellAliases(CoverageHome):
    def test_shell_alias_that_writes_a_test_file_is_denied(self):
        """Use a parameterized test for several inputs."""
        body = "touch tests/test_new.py"
        commands = ["git -c alias.zap='!" + body + "' zap", alias_run("-c user.name=x", body),
                    alias_run("-C " + str(self.root / INSIDE), body), alias_run("-C sub", body)]
        self.assertEqual(self.outcomes(INSIDE, commands), dict.fromkeys(commands, TEST_WRITE))

    def test_shell_alias_with_a_clean_body_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.gate(INSIDE, "git -c alias.zap='!echo hi' zap"), PASSED)


class QuietBranchCommands(CoverageHome):
    def test_quiet_branch_commands_pass_before_the_run(self):
        """Use a parameterized test for several inputs."""
        commands = ["git checkout -qb other", "git switch -q other"]
        self.assertEqual(self.outcomes(INSIDE, commands), dict.fromkeys(commands, PASSED))


if __name__ == "__main__":
    unittest.main()
