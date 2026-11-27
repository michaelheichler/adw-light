import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_TOOL = ROOT / "tools" / "release_manifest.py"
SOURCE = "0123456789abcdef0123456789abcdef01234567"
STALE_PLUGIN = {"name": "adw-light", "version": "0.0.1", "description": "Discipline gate."}
CLAUDE_MANIFEST = '{\n  "name": "adw-light",\n  "version": "1.2.3",\n  "description": "Discipline gate."\n}\n'
CODEX_MANIFEST = ('{\n  "name": "adw-light",\n  "version": "1.2.3",\n  "description": "Discipline gate.",\n'
                  '  "hooks": "./.codex-plugin/empty-hooks.json"\n}\n')
EMPTY_HOOKS = '{"hooks": {}}\n'
RELEASE_RECORD = '{\n  "version": "1.2.3",\n  "source": "' + SOURCE + '"\n}\n'


def release_tree(root, version):
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "engine").mkdir()
    (root / "VERSION").write_text(version, encoding="utf-8")
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(STALE_PLUGIN), encoding="utf-8")
    return root


def stamp(tree):
    return subprocess.run([sys.executable, "-I", "-S", str(MANIFEST_TOOL), str(tree), SOURCE], capture_output=True,
                          text=True, check=False, timeout=60).returncode


class ReleaseManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        root = Path(folder.name)
        cls.good = release_tree(root / "good", "1.2.3\n")
        cls.bad = release_tree(root / "bad", "1.2\n")
        cls.good_code, cls.bad_code = stamp(cls.good), stamp(cls.bad)

    def text(self, name):
        return (self.good / name).read_text(encoding="utf-8")

    def test_the_claude_manifest_takes_the_version_file_value_and_keeps_its_other_keys(self):
        self.assertEqual((self.good_code, self.text(".claude-plugin/plugin.json")), (0, CLAUDE_MANIFEST))

    def test_the_codex_manifest_copies_the_claude_manifest_and_points_at_a_hooks_file_with_no_hooks(self):
        self.assertEqual((self.text(".codex-plugin/plugin.json"), self.text(".codex-plugin/empty-hooks.json")),
                         (CODEX_MANIFEST, EMPTY_HOOKS))

    def test_the_release_record_names_the_version_and_the_built_source(self):
        self.assertEqual(self.text("engine/RELEASE.json"), RELEASE_RECORD)

    def test_a_version_that_is_not_three_numbers_fails_and_stamps_nothing(self):
        written = sorted(str(path.relative_to(self.bad)) for path in self.bad.rglob("*") if path.is_file())
        self.assertEqual((self.bad_code != 0, written), (True, [".claude-plugin/plugin.json", "VERSION"]))


if __name__ == "__main__":
    unittest.main()
