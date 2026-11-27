import hashlib
import json
import unittest

from fake_repo_home import DENY, GATE, PASSED, README, REFUSED, SIZE_TEST, FakeRepoCase, Repo, loose

CONFIG_NAME = ".agent-discipline.json"
SEALED = (DENY, ["config_seal"])
STATE_GUARD = "state_mutation"
INSTALL_GUARD = "watcher_install_surface"
DEFINITION_REFUSED = (DENY, ["test_writer_definition"])
WEAKENED = json.dumps({"rule_gates": {"comment_shape": "observe"}}, indent=2) + "\n"
MARKER = {"pyproject.toml": "[project]\nname = \"fixture\"\n"}
TESTS = {"tests/test_old.py": SIZE_TEST, "q1/.keep": ""}
DEFINITION = ".claude/agents/adw-test-writer.md"
PLAIN_PROSE = "Plain text.\n"
FOLDERS = ("scratch", "gitrepo", "marked")
QUOTES = ("'", '"')


class ScratchFoldersAndProjects(FakeRepoCase):
    projects = {
        "scratch": loose(TESTS),
        "gitrepo": Repo({**README, **TESTS}),
        "marked": loose({**MARKER, **TESTS}),
    }

    def test_config_write_passes_only_in_an_unmarked_temp_folder(self):
        """A test must verify what the code does for its client, never how it does it."""
        outcomes = {folder: self.write(folder, CONFIG_NAME, WEAKENED) for folder in FOLDERS}
        self.assertEqual(outcomes, {"scratch": PASSED, "gitrepo": SEALED, "marked": SEALED})

    def test_test_write_passes_only_in_an_unmarked_temp_folder(self):
        """A test must verify what the code does for its client, never how it does it."""
        outcomes = {folder: self.write(folder, "tests/test_new.py", SIZE_TEST) for folder in FOLDERS}
        self.assertEqual(outcomes, {"scratch": PASSED, "gitrepo": REFUSED, "marked": REFUSED})

    def test_removing_the_tests_folder_passes_only_in_an_unmarked_temp_folder(self):
        """A test must verify what the code does for its client, never how it does it."""
        outcomes = {folder: self.bash(folder, "rm -rf tests") for folder in FOLDERS}
        self.assertEqual(outcomes, {"scratch": PASSED, "gitrepo": REFUSED, "marked": REFUSED})

    def test_config_write_after_a_mixed_quoted_cd_is_judged_at_the_decoded_folder(self):
        """The test fails when a real bug enters the code it exercises."""
        outcomes = {
            (quote, "into_scratch"): self.bash("marked", self.quoted_cd_config(quote, "scratch"), cwd=self.root / "marked")
            for quote in QUOTES
        } | {
            (quote, "into_project"): self.bash("scratch", self.quoted_cd_config(quote, "gitrepo"), cwd=self.root / "scratch")
            for quote in QUOTES
        }
        expected = {(quote, "into_scratch"): PASSED for quote in QUOTES} | {(quote, "into_project"): SEALED for quote in QUOTES}
        self.assertEqual(outcomes, expected)

    def test_test_writer_definition_passes_only_under_a_fake_home_in_temp(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        targets = {
            "fake_home": ("scratch", self.root / "fakehome" / DEFINITION),
            "real_home": ("marked", self.home / DEFINITION),
            "project": ("marked", self.root / "marked" / DEFINITION),
        }
        outcomes = {label: self.write_path(*target) for label, target in targets.items()}
        self.assertEqual(outcomes, {"fake_home": PASSED, "real_home": DEFINITION_REFUSED, "project": DEFINITION_REFUSED})

    def quoted_cd_config(self, quote, folder):
        return "cd " + quote + str(self.root / folder) + quote + "/q1 && echo '{}' > " + CONFIG_NAME

    def write_path(self, project, path):
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": PLAIN_PROSE}}
        return self.hook(project, payload)


class WatcherFoldersUnderTheAdwHome(FakeRepoCase):
    projects = {"gitrepo": Repo(README)}

    def write_home(self, relative, guard):
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(self.home / relative), "content": "{}\n"}}
        decision, rules = self.hook("gitrepo", payload)
        return decision, guard in rules

    def test_only_the_dataset_cache_takes_writes_among_the_watcher_folders(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        targets = {
            "cache": (".adw-lite/cache/datasets/rows.json", STATE_GUARD),
            "state": (".adw-lite/state/ledger/ledger.jsonl", STATE_GUARD),
            "legacy": (".local/state/adw-lite/ledger/ledger.jsonl", STATE_GUARD),
            "install": (".adw-lite/install/adw_light.py", INSTALL_GUARD),
        }
        outcomes = {label: self.write_home(*target) for label, target in targets.items()}
        self.assertEqual(outcomes, {"cache": (None, False), "state": (DENY, True), "legacy": (DENY, True),
                                    "install": (DENY, True)})


class ConfigCliForms(FakeRepoCase):
    projects = {"gitrepo": Repo(README)}

    def test_status_and_help_pass_while_mutating_forms_are_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {
            "status": "adw-config tests status",
            "help": "adw-config rule --help",
            "bare_help": "adw-config --help",
            "switch": "adw-config tests on",
            "rule": "adw-config rule comment_shape off",
        }
        outcomes = {label: self.bash("gitrepo", command) for label, command in commands.items()}
        self.assertEqual(outcomes, {"status": PASSED, "help": PASSED, "bare_help": PASSED,
                                    "switch": SEALED, "rule": SEALED})


STATE_DELETED = (DENY, ["state_deletion"])
HOME_SIGN = "~"
WATCHER_FOLDERS = (".adw-lite/cache", ".adw-lite/state", ".adw-lite/install", ".local/state/adw-lite")
PROSE_LINE = "We utilize this."


class LabeledCalls(FakeRepoCase):
    def session(self):
        return "s" + hashlib.sha256((self.id() + self.__dict__.get("label", "")).encode()).hexdigest()[:16]

    def labeled(self, label, call, *arguments, **options):
        self.label = label
        return call(*arguments, **options)


class GlobDeletesUnderTheAdwHome(LabeledCalls):
    projects = {"gitrepo": Repo(README)}

    @classmethod
    def arrange(cls):
        for folder in WATCHER_FOLDERS:
            (cls.home / folder).mkdir(parents=True)

    def home_commands(self, *shapes):
        return [shape.format(HOME_SIGN) for shape in shapes]

    def state_deletion_outcome(self, command):
        decision, rules = self.labeled(command, self.bash, "gitrepo", command)
        return decision, "state_deletion" in rules

    def outcomes(self, commands):
        return {command: self.state_deletion_outcome(command) for command in commands}

    def test_glob_deletes_and_moves_are_refused_only_when_they_reach_the_state_root(self):
        """A test must verify what the code does for its client, never how it does it."""
        refused = self.home_commands("rm -rf {}/.adw-lite/*", "rm -rf {}/.adw-lite/s?ate", "rm -rf {}/.adw-lite/[s]tate",
                                     "rm -rf {}/.adw-l*", "rm -rf {}/.l*/state") + [f"mv {HOME_SIGN}/.adw-lite/st* {self.root}/x"]
        passing = self.home_commands("rm -rf {}/.adw-lite/cache/*", "rm -rf {}/.adw-lite/c*")
        expected = dict.fromkeys(refused, (DENY, True)) | dict.fromkeys(passing, (None, False))
        self.assertEqual(self.outcomes(refused + passing), expected)

    def test_deleting_finds_are_refused_only_when_a_branch_can_reach_the_state_root(self):
        """The test fails when a real bug enters the code it exercises."""
        refused = self.home_commands("find {} -name state -exec rm -rf {{}} +", "find {}/.local -name 'adw-*' -exec rm -rf {{}} \\;",
                                     "find {} ! -name x -exec rm -rf {{}} +")
        passing = self.home_commands("find {} -maxdepth 1 -name 'st*' -exec rm -rf {{}} +",
                                     "find {} -type d -name '*.pyc' -exec rm -rf {{}} +",
                                     "find {} -name 'st*' -o -name x -exec rm -rf {{}} +")
        expected = dict.fromkeys(refused, (DENY, True)) | dict.fromkeys(passing, (None, False))
        self.assertEqual(self.outcomes(refused + passing), expected)


class GlobDeletesAroundAStateOverride(LabeledCalls):
    projects = {"gitrepo": Repo(README)}

    @classmethod
    def arrange(cls):
        cls.state = cls.root / "ovr" / "st"
        cls.state.mkdir(parents=True)

    def test_a_glob_over_the_override_parent_is_refused_only_when_it_matches_the_override(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        commands = {"every_child": f"rm -rf {self.root}/ovr/*", "other_children": f"rm -rf {self.root}/ovr/x*"}
        outcomes = {label: self.labeled(label, self.bash, "gitrepo", command) for label, command in commands.items()}
        self.assertEqual(outcomes, {"every_child": STATE_DELETED, "other_children": PASSED})


class TargetsOutsideAScratchCwd(LabeledCalls):
    projects = {
        "scratch": loose(TESTS),
        "other": loose(TESTS),
        "kept": loose({CONFIG_NAME: "{}\n"}),
        "gitrepo": Repo({**README, **TESTS}),
        "marked": loose({**MARKER, **TESTS}),
    }

    def write_from(self, label, project, target, content):
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / target), "content": content}}
        return self.labeled(label, self.hook, project, payload)

    def bash_from_scratch(self, label, command):
        return self.labeled(label, self.bash, "scratch", command)

    def test_a_config_in_a_marked_temp_folder_is_sealed_from_any_cwd(self):
        """A test must verify what the code does for its client, never how it does it."""
        marked_config = f"marked/{CONFIG_NAME}"
        outcomes = {
            "scratch_cwd": self.write_from("scratch_cwd", "scratch", marked_config, WEAKENED),
            "git_cwd": self.write_from("git_cwd", "gitrepo", marked_config, WEAKENED),
            "cd_chain": self.bash_from_scratch("cd_chain", f"cd {self.root / 'marked'} && echo '{{}}' > {CONFIG_NAME}"),
            "unmarked_existing": self.write_from("unmarked_existing", "scratch", f"kept/{CONFIG_NAME}", WEAKENED),
        }
        self.assertEqual(outcomes, {"scratch_cwd": SEALED, "git_cwd": SEALED, "cd_chain": SEALED,
                                    "unmarked_existing": PASSED})

    def test_shell_prose_writes_from_a_scratch_cwd_match_the_write_tool_in_other_projects(self):
        """Resistance to refactoring is not negotiable, because a test that fails on every refactor trains the team to ignore failures."""
        printed = f"printf '%s\\n' '{PROSE_LINE}'"
        written = {folder: self.write_from("write_" + folder, "scratch", f"{folder}/notes.md", PROSE_LINE + "\n")
                   for folder in ("gitrepo", "marked")}
        outcomes = {
            "redirect": self.bash_from_scratch("redirect", f"{printed} > {self.root / 'gitrepo'}/notes.md"),
            "tee": self.bash_from_scratch("tee", f"{printed} | tee {self.root / 'marked'}/notes.md"),
            "scratch": self.bash_from_scratch("scratch", f"{printed} > {self.root / 'other'}/notes.md"),
            "write_tool_decision": written["gitrepo"][0],
        }
        self.assertEqual(outcomes, {"redirect": written["gitrepo"], "tee": written["marked"], "scratch": PASSED,
                                    "write_tool_decision": DENY})

if __name__ == "__main__":
    unittest.main()
