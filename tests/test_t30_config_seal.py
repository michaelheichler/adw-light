import importlib.util
import io
import json
import os
import shlex
import tarfile
import unittest
import zipfile
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from unittest import mock

from in_memory_hook import DENY, InMemoryHookCase, decision_of, run_hook

ROOT = Path(__file__).resolve().parents[1]
CONFIG_NAME = ".agent-discipline.json"
SEAL = "config_seal"
SEALED = (DENY, [SEAL])
PASSED = (None, [])
GOVERNING = json.dumps({"max_rows": 5, "rule_gates": {"comment_shape": "enforce"}}, indent=2) + "\n"
WEAKENED = json.dumps({"rule_gates": {"comment_shape": "observe"}}, indent=2) + "\n"
SESSION = "proj/svc/session"


def load_owner_hook():
    spec = importlib.util.spec_from_file_location("adw_config_hook_t30", ROOT / "hooks" / "adw_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OWNER = load_owner_hook()
git_port = import_module("lib.git_port")
NO_REPOSITORY = git_port.FakeGitView({"roots": {}})
AUTH_ENV = import_module("lib.protected").AUTH_ENV
OWNER_COMMAND = "/adw-light:config"


def owner_slash(arguments, cwd):
    return {"hook_event_name": "UserPromptExpansion", "expansion_type": "slash_command", "command_source": "plugin",
            "command_name": "adw-light:config", "command_args": arguments, "cwd": str(cwd)}


class GoverningConfigStaysSealed(InMemoryHookCase):
    files = {
        "proj/" + CONFIG_NAME: GOVERNING,
        SESSION + "/sub/notes.txt": "",
        "fresh/notes.txt": "",
        "fresh/pyproject.toml": "[project]\nname = \"fixture\"\n",
        "scratch/" + CONFIG_NAME: WEAKENED,
        "scratch/copy.json": WEAKENED,
        "owner_new/notes.txt": "",
        "owner_old/" + CONFIG_NAME: GOVERNING,
    }

    def outcome(self, label, tool, tool_input, cwd=SESSION):
        session = "s" + sha256((self.id() + label).encode()).hexdigest()[:16]
        body = {"session_id": session, "hook_event_name": "PreToolUse", "cwd": str(self.work / cwd),
                "tool_name": tool, "tool_input": tool_input}
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, environment, clear=True):
            response = run_hook("PreToolUse", body, state_dir=self.state, git_view=NO_REPOSITORY)
        reports = sorted((self.state / "reports").glob(session + "-*.json"))
        rules = sorted({row["rule"] for report in reports for row in json.loads(report.read_text(encoding="utf-8"))})
        return decision_of(response), rules

    def config(self, folder):
        return str(self.work / folder / CONFIG_NAME)

    def shell(self, label, command, cwd=SESSION):
        return self.outcome(label, "Bash", {"command": command}, cwd)

    def test_agent_cannot_create_a_config_anywhere_it_would_govern_the_project(self):
        """A test must verify what the code does for its client, never how it does it."""
        targets = {"session": (SESSION, SESSION), "ancestor": ("proj/svc", SESSION),
                   "root": ("fresh", "fresh"), "descendant": (SESSION + "/sub", SESSION)}
        outcomes = {label: self.outcome(label, "Write", {"file_path": self.config(folder), "content": WEAKENED}, cwd)
                    for label, (folder, cwd) in targets.items()}
        self.assertEqual(outcomes, dict.fromkeys(targets, SEALED))

    def test_policy_change_through_every_direct_edit_tool_is_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        path = self.config("proj")
        patch = ("*** Begin Patch\n*** Update File: " + path + "\n@@\n"
                 '-    "comment_shape": "enforce"\n+    "comment_shape": "observe"\n*** End Patch\n')
        edit = {"old_string": '"enforce"', "new_string": '"observe"'}
        outcomes = {
            "edit": self.outcome("edit", "Edit", {"file_path": path, **edit}),
            "multiedit": self.outcome("multiedit", "MultiEdit", {"file_path": path, "edits": [edit]}),
            "patch": self.outcome("patch", "apply_patch", {"input": patch}),
        }
        self.assertEqual(outcomes, dict.fromkeys(outcomes, SEALED))

    def test_shell_writes_into_a_governing_config_are_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        target = shlex.quote(self.config("proj"))
        source = shlex.quote(str(self.work / "scratch" / "copy.json"))
        weakened = shlex.quote(json.dumps({"rule_gates": {"comment_shape": "observe"}}))
        commands = {
            "overwrite": f"printf '%s' {weakened} > {target}",
            "append": f"printf '%s' {weakened} >> {target}",
            "tee": f"printf '%s' {weakened} | tee {target}",
            "cp": f"cp {source} {target}",
            "mv": f"mv {source} {target}",
            "output_flag": f"sort -o {target} {source}",
            "cd_descendant": f"cd sub && printf '%s' {weakened} > {CONFIG_NAME}",
        }
        outcomes = {label: self.shell(label, command) for label, command in commands.items()}
        self.assertEqual(outcomes, dict.fromkeys(commands, SEALED))

    def test_config_content_the_gate_cannot_read_is_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        path = self.config("proj")
        outcomes = {
            "malformed": self.outcome("malformed", "Write", {"file_path": path, "content": "{\"rule_gates\": "}),
            "oversized": self.outcome("oversized", "Write", {"file_path": path, "content": " " * 300_000 + "{}"}),
            "unprojectable": self.outcome("unprojectable", "Edit", {"file_path": path, "old_string": "absent-anchor",
                                                                    "new_string": "{}"}),
        }
        self.assertEqual(outcomes, dict.fromkeys(outcomes, SEALED))

    def test_scratch_config_outside_any_project_stays_free_to_write_and_delete(self):
        """A test must verify what the code does for its client, never how it does it."""
        outcomes = {
            "write": self.outcome("write", "Write", {"file_path": self.config("loose"), "content": WEAKENED}),
            "remove": self.shell("remove", "rm -rf " + shlex.quote(str(self.work / "scratch"))),
        }
        self.assertEqual(outcomes, dict.fromkeys(outcomes, PASSED))

    def owner_run(self, folder):
        payload = owner_slash("rule filler enforce", self.work / folder)
        with git_port.using_view(git_port.FakeGitView({"roots": {}})), mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            OWNER.main("UserPromptExpansion")
        return json.loads((self.work / folder / CONFIG_NAME).read_text(encoding="utf-8"))

    def test_owner_command_creates_the_project_config(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.assertEqual(self.owner_run("owner_new"), {"rule_gates": {"filler": "enforce"}})

    def test_owner_command_updates_the_project_config_and_keeps_other_keys(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        expected = {"max_rows": 5, "rule_gates": {"comment_shape": "enforce", "filler": "enforce"}}
        self.assertEqual(self.owner_run("owner_old"), expected)


OPEN_TESTS = json.dumps({"tests": "allow"}) + "\n"
WALK_CAP_FILES = 20_001
COPY_FILES = {
    "copy/" + CONFIG_NAME: GOVERNING,
    "copy/withcfg/" + CONFIG_NAME: WEAKENED,
    "copy/withcfg/notes.txt": "",
    "copy/clean/notes.txt": "",
    "copy/inner/keep.txt": "",
    "copy/other/keep.txt": "",
    "away/keep.txt": "",
}
ADDED_CONFIG_PATCH = (
    "diff --git a/pkg/" + CONFIG_NAME + " b/pkg/" + CONFIG_NAME + "\nnew file mode 100644\n"
    "--- /dev/null\n+++ b/pkg/" + CONFIG_NAME + "\n@@ -0,0 +1 @@\n+{}\n"
)
RENAMED_CONFIG_PATCH = (
    "diff --git a/pkg/settings.json b/pkg/" + CONFIG_NAME + "\nsimilarity index 100%\n"
    "rename from pkg/settings.json\nrename to pkg/" + CONFIG_NAME + "\n"
)


def tar_fixture(path, members):
    with tarfile.open(path, "w") as archive:
        for name, target in members:
            info = tarfile.TarInfo(name)
            if target is not None:
                info.type, info.linkname = tarfile.SYMTYPE, target
                archive.addfile(info)
                continue
            info.size = 2
            archive.addfile(info, io.BytesIO(b"{}"))


class ImportedConfigStaysSealed(InMemoryHookCase):
    files = {**COPY_FILES, "arc/" + CONFIG_NAME: OPEN_TESTS, "arc/bad.tar": "not an archive",
             "arc/bad.zip": "not an archive", "arc/add.patch": ADDED_CONFIG_PATCH,
             "arc/rename.patch": RENAMED_CONFIG_PATCH}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        archives = cls.work / "arc"
        tar_fixture(archives / "config.tar", [("pkg/" + CONFIG_NAME, None)])
        tar_fixture(archives / "linked.tar", [("pkg/link", "notes.txt"), ("pkg/" + CONFIG_NAME, None)])
        with zipfile.ZipFile(archives / "config.zip", "w") as archive:
            archive.writestr("pkg/" + CONFIG_NAME, "{}")
        wide = cls.work / "copy" / "wide"
        wide.mkdir()
        for index in range(WALK_CAP_FILES):
            (wide / f"f{index}.txt").touch()

    def shell(self, label, command, cwd):
        session = "s" + sha256((self.id() + label).encode()).hexdigest()[:16]
        body = {"session_id": session, "hook_event_name": "PreToolUse", "cwd": str(self.work / cwd),
                "tool_name": "Bash", "tool_input": {"command": command}}
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, environment, clear=True), git_port.using_view(git_port.FakeGitView({"roots": {}})):
            response = run_hook("PreToolUse", body, state_dir=self.state, git_view=NO_REPOSITORY)
        reports = sorted((self.state / "reports").glob(session + "-*.json"))
        rows = [row for report in reports for row in json.loads(report.read_text(encoding="utf-8"))]
        return decision_of(response), sorted({row["rule"] for row in rows}), rows

    def outcomes(self, commands, cwd):
        return {label: self.shell(label, command, cwd)[:2] for label, command in commands.items()}

    def test_folder_copy_that_carries_a_config_into_the_project_is_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"rsync": "rsync -a withcfg/ inner/", "cp": "cp -r withcfg newdir", "cp_dot": "cp -r withcfg/. inner/",
                    "cp_upper": "cp -R withcfg other/sub2", "mv": "mv withcfg newdir", "ditto": "ditto withcfg newdir4"}
        self.assertEqual(self.outcomes(commands, "copy"), dict.fromkeys(commands, SEALED))

    def test_folder_copy_of_a_clean_folder_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"rsync": "rsync -a clean/ inner/", "cp": "cp -r clean newdir", "cp_dot": "cp -r clean/. inner/",
                    "cp_upper": "cp -R clean other/sub2", "mv": "mv clean newdir", "ditto": "ditto clean newdir4"}
        self.assertEqual(self.outcomes(commands, "copy"), dict.fromkeys(commands, PASSED))

    def test_folder_copy_to_a_destination_outside_the_project_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"rsync": "rsync -a withcfg/ ../away/", "cp": "cp -r withcfg ../away/newdir",
                    "cp_dot": "cp -r withcfg/. ../away/", "cp_upper": "cp -R withcfg ../away/sub2",
                    "ditto": "ditto withcfg ../away/newdir4"}
        self.assertEqual(self.outcomes(commands, "copy"), dict.fromkeys(commands, PASSED))

    def test_folder_copy_whose_walk_cannot_finish_within_the_cap_is_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.shell("wide", "cp -r wide newdir", "copy")[:2], SEALED)

    def test_archive_and_patch_with_a_config_member_are_sealed_with_the_tests_policy_off(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"tar": "tar -xf config.tar", "after_link": "tar -xf linked.tar", "unzip": "unzip config.zip",
                    "patch": "git apply add.patch", "rename_only": "git apply rename.patch"}
        self.assertEqual(self.outcomes(commands, "arc"), dict.fromkeys(commands, SEALED))

    def test_commands_that_bring_in_no_readable_config_keep_their_result(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"bad_tar": "tar -xf bad.tar", "bad_zip": "unzip bad.zip", "list_tar": "tar -tf config.tar",
                    "list_zip": "unzip -l config.zip", "stash_pop": "git stash pop"}
        self.assertEqual(self.outcomes(commands, "arc"), dict.fromkeys(commands, PASSED))

    def test_seal_on_a_grant_key_points_to_the_owner_command_not_the_escape_variable(self):
        """A test must verify what the code does for its client, never how it does it."""
        writes = {key: "printf '%s' " + shlex.quote(json.dumps({key: ["x"]})) + " > " + CONFIG_NAME
                  for key in ("protected_paths_authorized", "exempt_paths")}
        actions = {key: {(OWNER_COMMAND in row["action"], AUTH_ENV in row["action"])
                         for row in self.shell(key, command, "copy")[2] if row["rule"] == SEAL}
                   for key, command in writes.items()}
        self.assertEqual(actions, dict.fromkeys(writes, {(True, False)}))


if __name__ == "__main__":
    unittest.main()
