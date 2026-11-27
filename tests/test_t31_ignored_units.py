import functools
import unittest
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from fake_repo_home import Repo, place, repo_state

git_port = import_module("lib.git_port")
ignored_configs = import_module("lib.ignored_configs")
shell_snapshot = import_module("lib.shell_snapshot")

CONFIG = ".agent-discipline.json"
IGNORE = "cache/\n"
HIDDEN = "cache/" + CONFIG
DEEP = "cache/deep/" + CONFIG
VISIBLE = "new/" + CONFIG
GONE_LISTED = "cache/a/" + CONFIG
GONE_UNLISTED = "cache/b/" + CONFIG
BEFORE_PATHS = (HIDDEN,)
AFTER_PATHS = (HIDDEN, DEEP)
COSTLY = 0.05
CHEAP = 0.01
FAILED_COST = 0.5
IGNORED = "!!"


class Clock:
    def __init__(self):
        self.wall = 1_800_000_000.0
        self.mono = 100.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono


class ClockedView:
    def __init__(self, inner, clock, step, failing=False):
        self.inner, self.clock, self.step, self.failing = inner, clock, step, failing

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def ignored_files(self, root, *, config_only=False, directory=False, timeout=2):
        self.clock.mono += self.step
        if self.failing:
            return None
        return self.inner.ignored_files(root, config_only=config_only, directory=directory, timeout=timeout)


def fake_view(root, disk):
    return git_port.FakeGitView({"roots": {str(root): repo_state(Repo({".gitignore": IGNORE}, disk))}})


class IgnoredListing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        place(cls.root, {".gitignore": IGNORE, HIDDEN: "{}", DEEP: "{}"})
        (cls.root / ".git").mkdir()
        cls.before = fake_view(cls.root, {HIDDEN: "{}"})
        cls.after = fake_view(cls.root, {HIDDEN: "{}", DEEP: "{}"})

    def setUp(self):
        self.clock = Clock()

    def listed(self, record, view, *, advance=0.0, step=COSTLY, failing=False, **options):
        self.clock.wall += advance
        timed = ClockedView(view, self.clock, step, failing)
        with git_port.using_view(timed), mock.patch.object(ignored_configs, "time", self.clock):
            return ignored_configs.listing(self.root, True, record, self.clock.mono + 10, **options)

    def first_record(self, step=COSTLY):
        return self.listed(None, self.before, step=step).record

    def touched_cache_folder(self):
        marker = self.root / "cache" / "touched.txt"
        marker.write_text("x", encoding="utf-8")
        self.addCleanup(marker.unlink)

    def test_stored_listing_is_reused_while_fresh_or_when_searching_is_off(self):
        """Use a parameterized test for several inputs."""
        outcomes = {
            "fresh": self.listed(self.first_record(), self.after, advance=5).paths,
            "search_off": self.listed(self.first_record(), self.after, advance=3600, search=False).paths,
        }
        self.assertEqual(outcomes, dict.fromkeys(outcomes, BEFORE_PATHS))

    def test_listing_searches_again_when_stale_refreshed_cheap_or_a_watched_folder_changed(self):
        """Use a parameterized test for several inputs."""
        outcomes = {
            "stale": self.listed(self.first_record(), self.after, advance=31).paths,
            "refresh": self.listed(self.first_record(), self.after, advance=5, refresh=True).paths,
            "cheap": self.listed(self.first_record(step=CHEAP), self.after, advance=5).paths,
        }
        record = self.first_record()
        self.touched_cache_folder()
        outcomes["watched_folder"] = self.listed(record, self.after, advance=5).paths
        self.assertEqual(outcomes, dict.fromkeys(outcomes, AFTER_PATHS))

    def test_a_trusted_cheap_listing_is_reused_only_when_complete_with_unchanged_watched_folders(self):
        """Use a parameterized test for several inputs."""
        failed = self.listed(None, self.before, step=CHEAP, failing=True).record
        outcomes = {
            "complete": self.listed(self.first_record(step=CHEAP), self.after, advance=5, trust_cheap=True).paths,
            "incomplete": self.listed(failed, self.after, advance=5, trust_cheap=True).paths,
        }
        record = self.first_record(step=CHEAP)
        self.touched_cache_folder()
        outcomes["watched_folder"] = self.listed(record, self.after, advance=5, trust_cheap=True).paths
        self.assertEqual(outcomes, {"complete": BEFORE_PATHS, "incomplete": AFTER_PATHS, "watched_folder": AFTER_PATHS})

    def test_failed_search_keeps_the_old_paths_and_marks_the_listing_incomplete(self):
        """Feed input, check the return value."""
        failed = self.listed(self.first_record(), self.after, advance=31, step=FAILED_COST, failing=True)
        self.assertEqual((failed.paths, failed.complete), (BEFORE_PATHS, False))

    def test_failed_search_is_not_retried_within_thirty_seconds(self):
        """Feed input, check the return value."""
        failed = self.listed(self.first_record(), self.after, advance=31, step=FAILED_COST, failing=True)
        again = self.listed(failed.record, self.after, advance=5)
        self.assertEqual((again.paths, again.complete), (BEFORE_PATHS, False))


class ComparisonWithIgnoredConfigs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        place(cls.root, {".gitignore": IGNORE, HIDDEN: "{}", DEEP: "{}"})
        (cls.root / ".git").mkdir()

    def snapshot(self, view, extra=None, hidden=()):
        with git_port.using_view(view):
            marks = shell_snapshot.marks(self.root, True)
        shell_snapshot.hidden_marks(self.root, hidden, marks)
        shell_snapshot.config_marks(self.root, self.root, marks)
        return {"root": str(self.root), "cwd": str(self.root), "git": True, "unsearched": "", "at_ns": 0,
                "marks": {**marks, **(extra or {})}}

    def compared(self, snapshot, view, record):
        with git_port.using_view(view):
            return shell_snapshot.compare(snapshot, {}, {}, exempt=False, until=float("inf"), record=record)

    def test_changed_gitignore_in_git_status_forces_a_new_search_that_finds_a_new_ignored_config(self):
        """Use a parameterized test for several inputs."""
        clock = Clock()
        before = ClockedView(fake_view(self.root, {HIDDEN: "{}"}), clock, COSTLY)
        snapshot = self.snapshot(before, hidden=BEFORE_PATHS)
        with git_port.using_view(before), mock.patch.object(ignored_configs, "time", clock):
            record = ignored_configs.listing(self.root, True, None, clock.mono + 10).record
        views = {
            "gitignore_changed": fake_view(self.root, {HIDDEN: "{}", DEEP: "{}", ".gitignore": IGNORE + "# more\n"}),
            "gitignore_same": fake_view(self.root, {HIDDEN: "{}", DEEP: "{}"}),
        }
        with mock.patch.object(ignored_configs, "time", clock):
            seen = {label: set(self.compared(snapshot, ClockedView(view, clock, COSTLY), record).paths)
                    for label, view in views.items()}
        self.assertEqual(seen, {"gitignore_changed": {".gitignore", DEEP}, "gitignore_same": set()})

    def test_a_refreshing_comparison_uses_the_prefetched_listing_instead_of_searching_again(self):
        """Feed input, check the return value."""
        view = fake_view(self.root, {HIDDEN: "{}", DEEP: "{}"})
        snapshot = self.snapshot(view, hidden=BEFORE_PATHS)
        prefetched = ignored_configs.Listing(AFTER_PATHS, True)
        with git_port.using_view(ClockedView(view, Clock(), CHEAP, failing=True)):
            compared = shell_snapshot.compare(snapshot, {}, {}, exempt=False, until=float("inf"), refresh=True,
                                              refreshed=prefetched)
        self.assertEqual(compared.paths, (DEEP,))

    def test_incomplete_search_after_the_run_keeps_a_listed_missing_config_and_drops_an_unlisted_one(self):
        """Feed input, check the return value."""
        view = fake_view(self.root, {HIDDEN: "{}", DEEP: "{}"})
        mark = [IGNORED, [0o100644, 2, 1, 1, 1]]
        snapshot = self.snapshot(view, {GONE_LISTED: mark, GONE_UNLISTED: mark})
        stale = {"at": 0, "paths": [GONE_LISTED], "watched": {}, "complete": True, "cost": 1.0}
        compared = self.compared(snapshot, ClockedView(view, Clock(), CHEAP, failing=True), stale)
        self.assertEqual((compared.paths, compared.dropped), ((GONE_LISTED,), (GONE_UNLISTED,)))


class PreviousSources(unittest.TestCase):
    def test_new_ignored_and_untracked_paths_count_as_absent_before_the_run(self):
        """Feed input, check the return value."""
        stamp = [0o100644, 2, 1, 1, 1]
        compared = shell_snapshot.Comparison(Path("/project"), {"marks": {}, "git": True},
                                             {HIDDEN: [IGNORED, stamp], VISIBLE: ["??", stamp]}, (HIDDEN, VISIBLE), {})
        absent = (shell_snapshot.ABSENT, None)
        self.assertEqual(shell_snapshot.previous_sources(compared, [HIDDEN, VISIBLE]),
                         ({HIDDEN: absent, VISIBLE: absent}, set()))


class RememberedRoots(unittest.TestCase):
    def remembered(self, roots):
        return functools.reduce(lambda data, root: ignored_configs.remember(data, root, {"paths": [root]}), roots, {})

    def test_at_most_forty_roots_are_kept_with_the_newest_last(self):
        """Feed input, check the return value."""
        roots = [f"/p{index}" for index in range(41)]
        self.assertEqual(list(self.remembered(roots)["ignored"]), roots[1:])

    def test_remembering_a_known_root_again_moves_it_to_the_end(self):
        """Feed input, check the return value."""
        self.assertEqual(list(self.remembered(["/a", "/b", "/c", "/a"])["ignored"]), ["/b", "/c", "/a"])

    def test_missing_record_leaves_the_stored_roots_unchanged(self):
        """Feed input, check the return value."""
        data = self.remembered(["/a"])
        self.assertEqual(ignored_configs.remember(data, "/b", None), data)


class CommandWindows(unittest.TestCase):
    def test_only_an_ignored_mark_changed_inside_a_window_counts_as_made_during_a_command(self):
        """Use a parameterized test for several inputs."""
        windows = [[100, 200], [300, 400]]
        marks = {
            "inside": [IGNORED, [0, 0, 0, 350, 0]],
            "edge": [IGNORED, [0, 0, 0, 200, 0]],
            "between": [IGNORED, [0, 0, 0, 250, 0]],
            "status_mark": ["??", [0, 0, 0, 150, 0]],
            "short_stamp": [IGNORED, [0, 0, 0]],
        }
        made = {label: ignored_configs.made_during(mark, windows) for label, mark in marks.items()}
        self.assertEqual(made, {"inside": True, "edge": True, "between": False, "status_mark": False,
                                "short_stamp": False})


class FakeViewFolding(unittest.TestCase):
    def test_directory_listing_folds_ignored_paths_to_their_top_level_folders(self):
        """Feed input, check the return value."""
        root = Path("/project")
        disk = {"cache/a/b.json": "", "cache/c.txt": "", "logs/deep/x.log": "", "top.log": "", "src/keep.py": ""}
        files = {".gitignore": "cache/\nlogs/\n*.log\n", "src/keep.py": ""}
        view = git_port.FakeGitView({"roots": {str(root): repo_state(Repo(files, disk))}})
        self.assertEqual(view.ignored_files(root, directory=True), ["cache/", "logs/", "top.log"])


if __name__ == "__main__":
    unittest.main()
