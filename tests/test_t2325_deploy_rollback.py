import json
import shlex
import unittest
from pathlib import Path

from t2325_fixtures import built_version
from test_t2325_deploy import DeployCase

NEW_FILE = "skills/t2325-rollback-marker.txt"


def install_source(scratch):
    return scratch.home / ".adw-lite" / "install" / "source"


def codex_cache(scratch):
    return scratch.codex_home / "plugins" / "cache" / "adw-light"


def kept_out_folder(scratch):
    return scratch.root / "kept-out"


def deploy_keeping_out(cls):
    return cls.deploy(cls.scratch, cls.repo, ADW_KEEP_OUT=str(kept_out_folder(cls.scratch)))


def add_kept_out_hook(scratch):
    script = kept_out_folder(scratch) / "adw-light-notes.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    path = scratch.codex_home / "hooks.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"hooks": {}}
    command = "sh " + shlex.quote(str(script)) + " Stop"
    data["hooks"].setdefault("Stop", []).append({"hooks": [{"type": "command", "command": command}]})
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path.read_bytes()


def missing_runners(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    runners = [shlex.split(handler["command"])[1] for groups in data["hooks"].values() for group in groups
               for handler in group["hooks"] if handler["command"].startswith("ADW_CODEX_HOOK=1 ")]
    return [runner for runner in runners if not Path(runner).is_file()]


class FailedUpgradeRollbackTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        kept_out_folder(cls.scratch).mkdir()
        cls.first_code = deploy_keeping_out(cls)
        cls.first_version = built_version(install_source(cls.scratch))
        cls.hooks_before = add_kept_out_hook(cls.scratch)
        (cls.repo / NEW_FILE).write_text("new\n", encoding="utf-8")
        cls.scratch.commit_all(cls.repo, "add a file to the next release")
        cls.second_code = deploy_keeping_out(cls)

    def test_the_first_deploy_passes_and_the_failing_upgrade_exits_non_zero(self):
        self.assertEqual((self.first_code, self.second_code != 0), (0, True))

    def test_codex_hooks_json_is_restored_byte_for_byte(self):
        self.assertEqual((self.scratch.codex_home / "hooks.json").read_bytes(), self.hooks_before)

    def test_every_restored_codex_runner_exists(self):
        self.assertEqual(missing_runners(self.scratch.codex_home / "hooks.json"), [])

    def test_the_codex_cache_holds_only_the_earlier_version(self):
        self.assertEqual(sorted(path.name for path in (codex_cache(self.scratch) / "adw-light").iterdir()),
                         [self.first_version])

    def test_the_install_source_keeps_the_earlier_release_without_the_new_file(self):
        source = install_source(self.scratch)
        self.assertEqual((built_version(source), (source / NEW_FILE).exists()), (self.first_version, False))


class FailedFirstInstallRollbackTests(DeployCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, cls.repo = cls.fixture(cls.addClassCleanup)
        cls.hooks_before = add_kept_out_hook(cls.scratch)
        cls.code = deploy_keeping_out(cls)

    def test_the_deploy_exits_non_zero(self):
        self.assertNotEqual(self.code, 0)

    def test_no_install_source_and_no_codex_cache_remain(self):
        self.assertEqual((install_source(self.scratch).exists(), codex_cache(self.scratch).exists()), (False, False))

    def test_codex_hooks_json_is_restored_byte_for_byte(self):
        self.assertEqual((self.scratch.codex_home / "hooks.json").read_bytes(), self.hooks_before)


if __name__ == "__main__":
    unittest.main()
