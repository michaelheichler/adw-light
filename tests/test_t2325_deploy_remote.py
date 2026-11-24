import json
import os
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path

from t2325_fixtures import all_paths
from test_t2325_deploy import DeployCase

REMOTE_HOST = "deploy@deploy-target.example"
ENV_HOST = "builder@env-target.example"
KEEP_OUT_SAMPLE = "/srv/sample/kept out/adw-light:/srv/sample/cost$5/adw-light-plugin"
REMOTE_OLD_COPIES = ["/srv/sample/adw-light", "/srv/sample/old copies/adw-light-plugin"]
SSH_STUB = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with open(os.environ["REMOTE_STUB_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(["ssh", *sys.argv[1:]]) + "\\n")
if "mktemp" in sys.argv[-1]:
    print(os.environ["REMOTE_STUB_STAGE"])
elif "REMOTE_STUB_SHELL" in os.environ:
    installer = Path(os.environ["REMOTE_STUB_STAGE"]) / "tools" / "install_layers.sh"
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_text(os.environ["REMOTE_STUB_INSTALLER"], encoding="utf-8")
    shell = os.environ["REMOTE_STUB_SHELL"]
    os.execvp(shell, [shell, "-c", sys.argv[-1]])
"""
RSYNC_STUB = """#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path
words = iter(sys.argv[1:])
paths = [word for word in words if not (word == "-e" and next(words)) and not word.startswith("-")]
*sources, remote = paths
if ":" not in remote:
    os.execv(os.environ["REMOTE_REAL_RSYNC"], ["rsync", *sys.argv[1:]])
with open(os.environ["REMOTE_STUB_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(["rsync", *sys.argv[1:]]) + "\\n")
capture = Path(os.environ["REMOTE_STUB_CAPTURE"]) / Path(remote.split(":", 1)[1]).name
capture.mkdir(parents=True, exist_ok=True)
for source in map(Path, sources):
    if source.is_dir():
        shutil.copytree(source, capture, dirs_exist_ok=True)
    else:
        shutil.copy2(source, capture / source.name)
"""
REPORTING_INSTALLER = """#!/usr/bin/env bash
exec python3 -c 'import json, os; open(os.environ["REMOTE_REPORT"], "w").write(json.dumps(os.environ.get("ADW_KEEP_OUT")))'
"""
FAILING_INSTALLER = """#!/usr/bin/env bash
python3 -c 'import json, os, sys; open(os.environ["REMOTE_REPORT"], "w").write(json.dumps(sys.argv[1:]))' "$@"
exit 7
"""
PASSING_INSTALLER = """#!/usr/bin/env bash
exit 0
"""
INSTALLER_FAILURE_CODE = 7
ZSH = shutil.which("zsh")


def tree_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts}


def retire_options(folders):
    return [word for folder in folders for word in ("--retire", folder)]


class RemoteDeployCase(DeployCase):
    @classmethod
    def remote_stage(cls):
        holder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(holder.cleanup)
        return str(Path(holder.name).resolve() / "remote-home" / ".adw-lite" / "install" / ".stage.Probe42")

    @classmethod
    def keep_out_seen_by_remote_installer(cls, stage):
        installer = Path(stage) / "tools" / "install_layers.sh"
        installer.parent.mkdir(parents=True)
        installer.write_text(REPORTING_INSTALLER, encoding="utf-8")
        report = cls.scratch.root / "remote-report.json"
        code = cls.scratch.run(["bash", "-c", cls.calls[-1][-1]], REMOTE_REPORT=str(report)).returncode
        return code, json.loads(report.read_text(encoding="utf-8"))

    @classmethod
    def deploy_to_stub_remote(cls, stage, args, **env):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        stubs = cls.scratch.root / "stub-bin"
        stubs.mkdir()
        for name, text in (("ssh", SSH_STUB), ("rsync", RSYNC_STUB)):
            (stubs / name).write_text(text, encoding="utf-8")
            (stubs / name).chmod(0o755)
        cls.log = cls.scratch.root / "remote-calls.jsonl"
        cls.log.write_text("", encoding="utf-8")
        cls.capture = cls.scratch.root / "remote-received"
        result = cls.scratch.run(["bash", cls.repo / "tools" / "deploy.sh", *args], cwd=cls.repo, timeout=900,
                                 PATH=str(stubs) + os.pathsep + os.environ["PATH"], REMOTE_STUB_LOG=str(cls.log),
                                 REMOTE_STUB_STAGE=stage, REMOTE_STUB_CAPTURE=str(cls.capture),
                                 REMOTE_REAL_RSYNC=shutil.which("rsync"), **env)
        cls.code = result.returncode
        cls.calls = [json.loads(line) for line in cls.log.read_text(encoding="utf-8").splitlines()]


class RemoteDeployTests(RemoteDeployCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = cls.remote_stage()
        cls.deploy_to_stub_remote(cls.stage, ["--host", REMOTE_HOST, *retire_options(REMOTE_OLD_COPIES)])

    def test_deploy_exits_zero(self):
        self.assertEqual(self.code, 0)

    def test_the_remote_gets_one_staging_call_two_copies_and_one_install_call_in_that_order(self):
        self.assertEqual([(call[0], call[-1].startswith(REMOTE_HOST + ":")) for call in self.calls],
                         [("ssh", False), ("rsync", True), ("rsync", True), ("ssh", False)])

    def test_every_ssh_call_goes_to_the_named_host_in_batch_mode(self):
        ssh_calls = [call[1:4] for call in self.calls if call[0] == "ssh"]
        self.assertEqual(ssh_calls, [["-o", "BatchMode=yes", REMOTE_HOST]] * 2)

    def test_the_remote_installs_through_both_plugin_layers_and_retires_each_named_folder_intact(self):
        install = shlex.split(self.calls[-1][-1].split(";")[0])
        expected = ["bash", self.stage + "/tools/install_layers.sh", "--claude", "--codex",
                    *retire_options(REMOTE_OLD_COPIES), self.stage + "/release"]
        self.assertEqual(install, expected)

    def test_the_remote_installer_runs_without_adw_keep_out_when_none_is_set_here(self):
        self.assertEqual(self.keep_out_seen_by_remote_installer(self.stage), (0, None))

    def test_the_remote_receives_the_same_release_that_was_installed_here(self):
        source = self.scratch.home / ".adw-lite" / "install" / "source"
        self.assertEqual(tree_bytes(self.capture / "release"), tree_bytes(source))

    def test_the_remote_receives_only_the_installer_and_the_smoke_probe_as_tools(self):
        self.assertEqual(sorted(tree_bytes(self.capture / "tools")), ["install_layers.sh", "smoke_probe.py"])


class KeepOutForwardingTests(RemoteDeployCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = cls.remote_stage()
        cls.deploy_to_stub_remote(cls.stage, ["--host", REMOTE_HOST], ADW_KEEP_OUT=KEEP_OUT_SAMPLE)

    def test_the_remote_installer_receives_the_local_adw_keep_out_value_unchanged(self):
        self.assertEqual((self.code, self.keep_out_seen_by_remote_installer(self.stage)), (0, (0, KEEP_OUT_SAMPLE)))


class UnexpectedRemoteStageTests(RemoteDeployCase):
    @classmethod
    def setUpClass(cls):
        cls.deploy_to_stub_remote("/srv/sample/stage", [], ADW_DEPLOY_HOST=ENV_HOST)

    def test_a_staging_folder_outside_the_remote_adw_lite_install_stops_the_deploy_before_any_copy(self):
        self.assertEqual((self.code != 0, [call[0] for call in self.calls], self.capture.exists()),
                         (True, ["ssh"], False))

    def test_adw_deploy_host_names_the_remote_when_no_host_flag_is_given(self):
        self.assertEqual([call[3] for call in self.calls], [ENV_HOST])


class RemoteCommandRunTests(RemoteDeployCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = Path(cls.remote_stage())
        cls.deploy_to_stub_remote(str(cls.stage), ["--host", REMOTE_HOST])

    def run_logged_remote_command(self, shell, installer_text):
        installer = self.stage / "tools" / "install_layers.sh"
        installer.parent.mkdir(parents=True, exist_ok=True)
        installer.write_text(installer_text, encoding="utf-8")
        report = self.scratch.root / "remote-argv.json"
        code = self.scratch.run([shell, "-c", self.calls[-1][-1]], REMOTE_REPORT=str(report)).returncode
        return code, self.stage.exists(), self.stage.parent.is_dir()

    def test_the_remote_installer_gets_the_staged_release_as_one_argument(self):
        self.run_logged_remote_command("bash", FAILING_INSTALLER)
        report = self.scratch.root / "remote-argv.json"
        self.assertEqual(json.loads(report.read_text(encoding="utf-8")),
                         ["--claude", "--codex", str(self.stage / "release")])

    def test_the_remote_command_returns_the_installer_status_and_removes_only_the_staging_folder(self):
        self.assertEqual((self.code, self.run_logged_remote_command("bash", FAILING_INSTALLER)),
                         (0, (INSTALLER_FAILURE_CODE, False, True)))

    @unittest.skipUnless(ZSH, "zsh is not installed")
    def test_a_zsh_login_shell_returns_the_failing_installer_status_and_removes_only_the_staging_folder(self):
        self.assertEqual(self.run_logged_remote_command(ZSH, FAILING_INSTALLER), (INSTALLER_FAILURE_CODE, False, True))

    @unittest.skipUnless(ZSH, "zsh is not installed")
    def test_a_zsh_login_shell_returns_zero_for_a_passing_installer_and_removes_only_the_staging_folder(self):
        self.assertEqual(self.run_logged_remote_command(ZSH, PASSING_INSTALLER), (0, False, True))


@unittest.skipUnless(ZSH, "zsh is not installed")
class ZshRemoteDeployTests(RemoteDeployCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = Path(cls.remote_stage())
        cls.deploy_to_stub_remote(str(cls.stage), ["--host", REMOTE_HOST], REMOTE_STUB_SHELL=ZSH,
                                  REMOTE_STUB_INSTALLER=FAILING_INSTALLER,
                                  REMOTE_REPORT=str(Path(cls.stage).parent / "remote-argv.json"))

    def test_a_failing_remote_install_under_zsh_fails_the_deploy_with_the_installer_status(self):
        self.assertEqual(self.code, INSTALLER_FAILURE_CODE)

    def test_a_failing_remote_install_under_zsh_still_removes_only_the_staging_folder(self):
        self.assertEqual((self.stage.exists(), self.stage.parent.is_dir()), (False, True))


class UnsafeRemoteStageCase(RemoteDeployCase):
    unsafe_stage = ""

    @classmethod
    def setUpClass(cls):
        cls.deploy_to_stub_remote(cls.unsafe_stage, ["--host", REMOTE_HOST])

    def refused_after_the_staging_call(self):
        return self.code != 0, [call[0] for call in self.calls], self.capture.exists()


class StageWithSpaceTests(UnsafeRemoteStageCase):
    unsafe_stage = "/srv/sample/a b/.adw-lite/install/.stage.X1"

    def test_a_staging_folder_with_a_space_stops_the_deploy_before_any_copy_or_install(self):
        self.assertEqual(self.refused_after_the_staging_call(), (True, ["ssh"], False))


class StageWithSemicolonTests(UnsafeRemoteStageCase):
    unsafe_stage = "/srv/sample/a;b/.adw-lite/install/.stage.X1"

    def test_a_staging_folder_with_a_semicolon_stops_the_deploy_before_any_copy_or_install(self):
        self.assertEqual(self.refused_after_the_staging_call(), (True, ["ssh"], False))


class RemoteArgumentRefusalTests(DeployCase):
    def refused_before_anything_runs(self, *args):
        scratch, repo = self.fixture(self.addCleanup)
        code = self.deploy(scratch, repo, *args)
        return code != 0, all_paths(scratch.config_dir, scratch.codex_home, scratch.tmp, scratch.home / ".adw-lite")

    def test_retire_without_a_host_exits_non_zero_and_installs_nothing(self):
        self.assertEqual(self.refused_before_anything_runs("--retire", REMOTE_OLD_COPIES[0]), (True, []))

    def test_host_without_a_target_exits_non_zero_and_installs_nothing(self):
        self.assertEqual(self.refused_before_anything_runs("--host"), (True, []))

    def test_retire_without_a_folder_exits_non_zero_and_installs_nothing(self):
        self.assertEqual(self.refused_before_anything_runs("--host", REMOTE_HOST, "--retire"), (True, []))


if __name__ == "__main__":
    unittest.main()
