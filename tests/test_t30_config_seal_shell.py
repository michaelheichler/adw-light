import io
import gzip
import json
import os
import tarfile
import unittest
import zipfile
from hashlib import sha256
from importlib import import_module
from unittest import mock

from in_memory_hook import DENY, InMemoryHookCase, decision_of, run_hook
from test_t30_config_seal import ADDED_CONFIG_PATCH, CONFIG_NAME, OPEN_TESTS, WEAKENED, tar_fixture

git_port = import_module("lib.git_port")
NO_REPOSITORY = git_port.FakeGitView({"roots": {}})
SEALED = (DENY, ["config_seal"])
PASSED = (None, [])
THRESHOLDS = json.dumps({"max_rows": 5, "list_item_cap": 4, "tests": "allow"}, indent=2) + "\n"


def odc_member(name, body):
    encoded = name.encode() + b"\0"
    fields = b"070707" + b"0" * 42 + b"%011o" % 0 + b"%06o" % len(encoded) + b"%011o" % len(body)
    return fields + encoded + body


def cpio_bytes(names):
    return b"".join(odc_member(name, b"{}") for name in names) + odc_member("TRAILER!!!", b"")


def write_zip(path, names):
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "{}")


class ShellImportedConfigStaysSealed(InMemoryHookCase):
    files = {
        "p/" + CONFIG_NAME: OPEN_TESTS,
        "p/cfgdir/" + CONFIG_NAME: WEAKENED,
        "p/clean/notes.txt": "",
        "p/tree/keep.txt": "",
        "p/cleantree/keep.txt": "",
        "p/sub/keep.txt": "",
        "p/mid/sess/keep.txt": "",
        "p/add.patch": ADDED_CONFIG_PATCH,
        "p/bad.patch": "",
        "away/keep.txt": "",
        "gov/" + CONFIG_NAME: OPEN_TESTS,
        "gov/sess/keep.txt": "",
        "thr/" + CONFIG_NAME: THRESHOLDS,
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        project = cls.work / "p"
        planted = str(project / "sub" / CONFIG_NAME)
        tar_fixture(project / "trav.tar", [("../" + CONFIG_NAME, None)])
        tar_fixture(project / "nested.tar", [("pkg/../../" + CONFIG_NAME, None)])
        tar_fixture(project / "abs.tar", [(planted, None)])
        tar_fixture(project / "ancestor.tar", [("../p/mid/" + CONFIG_NAME, None)])
        tar_fixture(project / "clean.tar", [("pkg/notes.txt", None)])
        write_zip(project / "trav.zip", ["../" + CONFIG_NAME])
        write_zip(project / "cfg.zip", ["pkg/" + CONFIG_NAME])
        write_zip(project / "clean.zip", ["pkg/notes.txt"])
        (project / "cfg.cpio").write_bytes(cpio_bytes(["pkg/" + CONFIG_NAME]))
        (project / "cfg.cpio.gz").write_bytes(gzip.compress(cpio_bytes(["pkg/" + CONFIG_NAME])))
        (project / "clean.cpio").write_bytes(cpio_bytes(["pkg/notes.txt"]))
        (project / "tree" / "link").symlink_to(project / "cfgdir", target_is_directory=True)
        (project / "cleantree" / "link").symlink_to(project / "clean", target_is_directory=True)
        (project / "cfglink").symlink_to(project / "cfgdir", target_is_directory=True)

    def shell(self, label, command, cwd="p"):
        return self.tool(label, "Bash", {"command": command}, cwd)

    def tool(self, label, name, tool_input, cwd):
        session = "s" + sha256((self.id() + label).encode()).hexdigest()[:16]
        body = {"session_id": session, "hook_event_name": "PreToolUse", "cwd": str(self.work / cwd),
                "tool_name": name, "tool_input": tool_input}
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, environment, clear=True), git_port.using_view(git_port.FakeGitView({"roots": {}})):
            response = run_hook("PreToolUse", body, state_dir=self.state, git_view=NO_REPOSITORY)
        reports = sorted((self.state / "reports").glob(session + "-*.json"))
        rules = sorted({row["rule"] for report in reports for row in json.loads(report.read_text(encoding="utf-8"))})
        return decision_of(response), rules

    def outcomes(self, commands, cwd="p"):
        return {label: self.shell(label, command, cwd) for label, command in commands.items()}

    def test_archive_members_that_land_on_a_config_are_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"traversal": "tar -xf trav.tar", "nested": "tar -xf nested.tar", "absolute": "tar -xf abs.tar",
                    "absolute_cluster": "tar -xPf abs.tar", "absolute_long": "tar -x --absolute-names -f abs.tar",
                    "absolute_trailing": "tar -xf abs.tar -P", "unzip_traversal": "unzip trav.zip"}
        self.assertEqual(self.outcomes(commands), dict.fromkeys(commands, SEALED))

    def test_ditto_extraction_of_a_config_member_is_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"zip": "ditto -x -k cfg.zip out", "zip_traversal": "ditto -x -k trav.zip out",
                    "cpio": "ditto -x cfg.cpio out", "gzip_cpio": "ditto -x cfg.cpio.gz out"}
        self.assertEqual(self.outcomes(commands), dict.fromkeys(commands, SEALED))

    def test_folder_copy_that_follows_a_link_to_a_config_folder_is_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"cp_follow": "cp -RL tree d1", "cp": "cp -R tree d2", "rsync_follow": "rsync -aL tree/ d3/",
                    "rsync": "rsync -a tree/ d4/", "cp_link_operand": "cp -R cfglink d5",
                    "scp": "scp -r cfgdir d6", "scp_upper": "scp -R cfgdir d7"}
        self.assertEqual(self.outcomes(commands), dict.fromkeys(commands, SEALED))

    def test_symbolic_link_to_a_config_folder_is_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        config_folder = str(self.work / "p" / "cfgdir")
        commands = {"plain": "ln -s cfgdir l1", "force": "ln -sf cfgdir l2", "no_dereference": "ln -sn cfgdir l3",
                    "relative": "ln -s ../cfgdir sub/l4", "into_folder": "ln -s " + config_folder + " sub/"}
        self.assertEqual(self.outcomes(commands), dict.fromkeys(commands, SEALED))

    def test_patch_that_adds_a_config_is_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"patch": "patch -p1 < add.patch", "git_apply": "git apply add.patch"}
        self.assertEqual(self.outcomes(commands), dict.fromkeys(commands, SEALED))

    def test_extraction_outside_the_project_that_lands_in_a_session_ancestor_is_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        command = "tar -xf ../../ancestor.tar -C ../../../away"
        self.assertEqual(self.shell("ancestor", command, "p/mid/sess"), SEALED)

    def test_moving_the_folder_with_the_governing_config_out_of_the_project_is_sealed(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.shell("move", "mv ../../gov ../../away/gov", "gov/sess"), SEALED)

    def test_archive_and_folder_work_that_brings_in_no_config_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        outside = "../away"
        commands = {"clean_tar": "tar -xf clean.tar", "ditto_zip": "ditto -x -k clean.zip out",
                    "ditto_cpio": "ditto -x clean.cpio out", "ditto_create": "ditto -c -k tree tree.zip",
                    "ditto_outside": "ditto -x -k cfg.zip " + outside, "cp": "cp -R clean c1",
                    "cp_follow": "cp -RL cleantree c2", "cp_lower": "cp -r cleantree c3",
                    "cp_outside": "cp -R tree " + outside + "/c4"}
        self.assertEqual(self.outcomes(commands), dict.fromkeys(commands, PASSED))

    def test_links_and_remote_copies_that_bring_in_no_config_pass(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"clean_link": "ln -s clean l5", "scp_clean": "scp -r clean c5", "scp_from_host": "scp -r host:/srv c6",
                    "scp_to_host": "scp -r cfgdir host:/srv", "rsync_to_host": "rsync -a tree/ host:/srv/",
                    "scp_outside": "scp -r cfgdir ../away/c7"}
        self.assertEqual(self.outcomes(commands), dict.fromkeys(commands, PASSED))

    def test_patch_checks_that_write_nothing_pass(self):
        """A test must verify what the code does for its client, never how it does it."""
        commands = {"dry_run": "patch --dry-run -p1 < add.patch", "dry_run_input": "patch --dry-run -p1 -i add.patch",
                    "check": "git apply --check add.patch", "stat": "git apply --stat add.patch"}
        self.assertEqual(self.outcomes(commands), dict.fromkeys(commands, PASSED))

    def test_agent_may_change_thresholds_in_an_existing_config(self):
        """A test must verify what the code does for its client, never how it does it."""
        path = str(self.work / "thr" / CONFIG_NAME)
        edit = {"file_path": path, "old_string": '"max_rows": 5', "new_string": '"max_rows": 9'}
        self.assertEqual(self.tool("thresholds", "Edit", edit, "thr"), PASSED)


if __name__ == "__main__":
    unittest.main()
