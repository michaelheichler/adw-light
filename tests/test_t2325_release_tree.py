import unittest

from t2325_fixtures import RUNTIME_TOP, Scratch, copy_source_repo, shipped_leaks, tree_names

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
}
MARKER = "skills/t2325-marker.txt"
UNTRACKED = "skills/t2325-untracked.txt"


def object_files(repo):
    objects = repo / ".git" / "objects"
    return sorted(str(path.relative_to(objects)) for path in objects.rglob("*"))


class ReleaseBuildCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch(cls.addClassCleanup)
        cls.repo = cls.scratch.root / "repo"
        cls.first = copy_source_repo(cls.scratch, cls.repo, AGENT_FILES)
        (cls.repo / MARKER).write_text("second\n", encoding="utf-8")
        cls.scratch.commit_all(cls.repo, "second marker")
        (cls.repo / MARKER).write_text("third\n", encoding="utf-8")
        (cls.repo / UNTRACKED).write_text("loose\n", encoding="utf-8")
        cls.status_before = cls.scratch.git(cls.repo, "status", "--porcelain")
        cls.objects_before = object_files(cls.repo)
        cls.from_first = cls.build("from-first", ADW_REF=cls.first)
        cls.from_main = cls.build("from-main", ADW_REF="refs/heads/main")
        cls.from_worktree = cls.build("from-worktree")
        cls.unknown = cls.build("from-unknown", ADW_REF="refs/heads/no-such-branch")
        cls.status_after = cls.scratch.git(cls.repo, "status", "--porcelain")
        cls.objects_after = object_files(cls.repo)

    @classmethod
    def build(cls, name, **env):
        target = cls.scratch.root / name
        result = cls.scratch.run(["bash", cls.repo / "tools" / "build_release.sh"], ADW_RELEASE=str(target), **env)
        return result.returncode, target

    def marker_state(self, build):
        _, release = build
        return (release / MARKER).read_text(encoding="utf-8"), (release / UNTRACKED).exists()


class ReleaseTreeShapeTests(ReleaseBuildCase):
    def test_ref_build_top_level_holds_exactly_the_six_runtime_entries(self):
        code, release = self.from_main
        self.assertEqual((code, {path.name for path in release.iterdir()}), (0, RUNTIME_TOP))

    def test_ref_build_ships_no_test_tool_doc_agent_or_dot_path(self):
        _, release = self.from_main
        self.assertEqual(shipped_leaks(tree_names(release)), [])

    def test_worktree_build_ships_no_test_tool_doc_agent_or_dot_path(self):
        _, release = self.from_worktree
        self.assertEqual(shipped_leaks(tree_names(release)), [])

    def test_builds_create_nothing_under_home_development(self):
        self.assertFalse((self.scratch.home / "Development").exists())


class ReleaseRefSelectionTests(ReleaseBuildCase):
    def test_ref_build_ships_the_named_commit_and_ignores_newer_and_uncommitted_files(self):
        self.assertEqual(self.marker_state(self.from_first), ("first\n", False))

    def test_branch_ref_build_ships_the_branch_tip_and_ignores_uncommitted_files(self):
        self.assertEqual(self.marker_state(self.from_main), ("second\n", False))

    def test_worktree_build_ships_uncommitted_and_untracked_files(self):
        self.assertEqual(self.marker_state(self.from_worktree), ("third\n", True))

    def test_worktree_build_leaves_the_repository_index_and_status_unchanged(self):
        self.assertEqual(self.status_after, self.status_before)

    def test_worktree_build_of_a_dirty_tree_writes_no_object_into_the_source_repository(self):
        self.assertEqual(self.objects_after, self.objects_before)

    def test_unknown_ref_exits_non_zero_and_creates_no_release(self):
        code, release = self.unknown
        self.assertEqual((code != 0, release.exists()), (True, False))


if __name__ == "__main__":
    unittest.main()
