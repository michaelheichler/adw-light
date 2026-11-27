import json
import shutil
import unittest

from t2325_fixtures import (RUNTIME_TOP, ModuleFixture, Scratch, built_version, copy_source_repo, shipped_leaks,
                            source_version, tree_names)

STALE_MANIFEST = json.dumps({"name": "adw-light", "version": "0.0.1"}) + "\n"

AGENT_FILES = {
    "CLAUDE.md": "Project notes.\n",
    "AGENTS.md": "Agent notes.\n",
    "engine/CLAUDE.md": "Engine notes.\n",
    "skills/AGENTS.md": "Skill notes.\n",
    ".claude-plugin/CLAUDE.md": "Manifest notes.\n",
    ".claude/settings.json": "{}\n",
    ".agents/skills/probe.md": "Mirror.\n",
    ".gitnexus/meta.json": "{}\n",
    "hooks/.env": "TOKEN=1\n",
    "skills/t2325-marker.txt": "first\n",
    ".claude-plugin/plugin.json": STALE_MANIFEST,
}
MARKER = "skills/t2325-marker.txt"
UNTRACKED = "skills/t2325-untracked.txt"
MKTEMP_LOG_ENV = "ADW_TEST_MKTEMP_LOG"
RECORDING_MKTEMP = """#!/bin/sh
printf '%s\\n' "$*" >> "$ADW_TEST_MKTEMP_LOG"
exec {real} "$@"
"""


def decoy_tools(folder):
    (folder / "tools").mkdir(parents=True)
    for name in ("smoke_probe.py", "release_manifest.py"):
        (folder / "tools" / name).write_text("", encoding="utf-8")
    return folder


def recording_mktemp(folder):
    folder.mkdir(parents=True)
    (folder / "mktemp").write_text(RECORDING_MKTEMP.format(real=shutil.which("mktemp")), encoding="utf-8")
    (folder / "mktemp").chmod(0o755)
    return folder


def object_files(repo):
    objects = repo / ".git" / "objects"
    return sorted(str(path.relative_to(objects)) for path in objects.rglob("*"))


class ReleaseBuilds:
    def __init__(self, register_cleanup):
        self.scratch = Scratch(register_cleanup)
        self.repo = self.scratch.root / "repo"
        self.first = copy_source_repo(self.scratch, self.repo, AGENT_FILES)
        (self.repo / MARKER).write_text("second\n", encoding="utf-8")
        self.scratch.commit_all(self.repo, "second marker")
        (self.repo / MARKER).write_text("third\n", encoding="utf-8")
        (self.repo / UNTRACKED).write_text("loose\n", encoding="utf-8")
        self.status_before = self.scratch.git(self.repo, "status", "--porcelain")
        self.objects_before = object_files(self.repo)
        self.from_first = self.build("from-first", ADW_REF=self.first)
        self.from_main = self.build("from-main", ADW_REF="refs/heads/main")
        self.from_worktree = self.build("from-worktree")
        self.unknown = self.build("from-unknown", ADW_REF="refs/heads/no-such-branch")
        self.decoy_mktemp_log = self.scratch.root / "decoy-mktemp-calls"
        self.from_decoy = self.build("from-decoy", cwd=decoy_tools(self.scratch.root / "decoy"), ADW_REF="refs/heads/main",
                                     PATH=self.scratch.fakes.search_path(recording_mktemp(self.scratch.root / "recorder")),
                                     **{MKTEMP_LOG_ENV: str(self.decoy_mktemp_log)})
        self.build_leftovers = sorted(path.name for path in self.scratch.root.glob(".adw-build.*"))
        self.status_after = self.scratch.git(self.repo, "status", "--porcelain")
        self.objects_after = object_files(self.repo)

    def build(self, name, cwd=None, **env):
        target = self.scratch.root / name
        result = self.scratch.run(["bash", self.repo / "tools" / "build_release.sh"], cwd=cwd or self.repo,
                                  ADW_RELEASE=str(target), **env)
        return result.returncode, target


RELEASE_BUILDS = ModuleFixture(ReleaseBuilds)


class ReleaseBuildCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.built = RELEASE_BUILDS.get()

    def marker_state(self, build):
        _, release = build
        return (release / MARKER).read_text(encoding="utf-8"), (release / UNTRACKED).exists()


class ReleaseTreeShapeTests(ReleaseBuildCase):
    def test_ref_build_top_level_holds_exactly_the_runtime_entries(self):
        code, release = self.built.from_main
        self.assertEqual((code, {path.name for path in release.iterdir()}), (0, RUNTIME_TOP))

    def test_ref_build_ships_no_test_tool_doc_agent_or_dot_path(self):
        _, release = self.built.from_main
        self.assertEqual(shipped_leaks(tree_names(release)), [])

    def test_worktree_build_ships_no_test_tool_doc_agent_or_dot_path(self):
        _, release = self.built.from_worktree
        self.assertEqual(shipped_leaks(tree_names(release)), [])

    def test_ref_build_ships_the_engine_license_at_the_top(self):
        _, release = self.built.from_main
        self.assertEqual((release / "LICENSE").read_bytes(), (self.built.repo / "engine" / "LICENSE").read_bytes())

    def test_ref_build_stamps_the_version_file_value_over_a_stale_plugin_manifest(self):
        _, release = self.built.from_main
        self.assertEqual(built_version(release), source_version(self.built.repo))

    def test_ref_build_records_the_built_commit_and_version_for_deploy_to_verify(self):
        _, release = self.built.from_first
        record = json.loads((release / "engine" / "RELEASE.json").read_text(encoding="utf-8"))
        self.assertEqual(record, {"version": source_version(self.built.repo), "source": self.built.first})

    def test_ref_build_gives_the_codex_plugin_a_hooks_file_with_no_hooks(self):
        _, release = self.built.from_main
        manifest = json.loads((release / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((release / manifest["hooks"]).read_text(encoding="utf-8")), {"hooks": {}})

    def test_builds_create_nothing_under_home_development(self):
        self.assertFalse((self.built.scratch.home / "Development").exists())


class ReleaseRefSelectionTests(ReleaseBuildCase):
    def test_ref_build_ships_the_named_commit_and_ignores_newer_and_uncommitted_files(self):
        self.assertEqual(self.marker_state(self.built.from_first), ("first\n", False))

    def test_branch_ref_build_ships_the_branch_tip_and_ignores_uncommitted_files(self):
        self.assertEqual(self.marker_state(self.built.from_main), ("second\n", False))

    def test_worktree_build_ships_uncommitted_and_untracked_files(self):
        self.assertEqual(self.marker_state(self.built.from_worktree), ("third\n", True))

    def test_worktree_build_leaves_the_repository_index_and_status_unchanged(self):
        self.assertEqual(self.built.status_after, self.built.status_before)

    def test_worktree_build_of_a_dirty_tree_writes_no_object_into_the_source_repository(self):
        self.assertEqual(self.built.objects_after, self.built.objects_before)

    def test_a_build_started_from_a_folder_with_its_own_passing_tools_is_refused_and_leaves_no_release_or_work_folder(self):
        code, release = self.built.from_decoy
        self.assertEqual((code != 0, release.exists(), self.built.build_leftovers), (True, False, []))

    def test_a_build_started_outside_the_repository_root_never_creates_a_temp_work_folder(self):
        self.assertEqual((self.built.from_decoy[0] != 0, self.built.decoy_mktemp_log.exists()), (True, False))

    def test_unknown_ref_exits_non_zero_and_creates_no_release(self):
        code, release = self.built.unknown
        self.assertEqual((code != 0, release.exists()), (True, False))


if __name__ == "__main__":
    unittest.main()
