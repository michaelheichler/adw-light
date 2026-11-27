import json
import re
import shutil
import unittest
from datetime import UTC, datetime

from t2325_fixtures import ROOT, SUITE_PASSES, ModuleFixture, Scratch

FAIL_ENV = "FAKE_SUITE_FAIL"
DURING_ENV = "FAKE_SUITE_DURING"
FAKE_MODULES = ("test_alpha.py", "test_beta.py")
FAKE_COUNT = 3
EXPORT_IGNORED = {
    ".gitattributes": "tests/test_gamma.py export-ignore\n",
    "tests/test_gamma.py": """import unittest


class GammaTests(unittest.TestCase):
    def test_product(self):
        self.assertEqual(2 * 3, 6)
""",
}
FAKE_SUITE = {
    "tests/test_alpha.py": """import json
import os
import subprocess
import unittest


class AlphaTests(unittest.TestCase):
    def test_the_fail_switch_is_off(self):
        self.assertNotIn("FAKE_SUITE_FAIL", os.environ)

    def test_the_during_command_succeeds(self):
        command = json.loads(os.environ.get("FAKE_SUITE_DURING", '["true"]'))
        self.assertEqual(subprocess.run(command, check=False).returncode, 0)
""",
    "tests/test_beta.py": """import unittest


class BetaTests(unittest.TestCase):
    def test_sum(self):
        self.assertEqual(sum([1, 2]), 3)
""",
}


class SuiteCheckout:
    def __init__(self, register_cleanup):
        self.scratch = Scratch(register_cleanup)
        self.repo = self.scratch.root / "repo"
        for name in ("tools/run_tests.py", "tools/slow_tests.py"):
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        for name, text in FAKE_SUITE.items():
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.scratch.git(self.scratch.root, "init", "-q", "-b", "main", self.repo.name)
        self.baseline = self.scratch.commit_all(self.repo, "fake suite")
        (self.repo / "moved.txt").write_text("moved\n", encoding="utf-8")
        self.moved = self.scratch.commit_all(self.repo, "moved head")
        for name, text in EXPORT_IGNORED.items():
            (self.repo / name).write_text(text, encoding="utf-8")
        self.exported = self.scratch.commit_all(self.repo, "export ignored test")

    def restore(self):
        self.scratch.git(self.repo, "checkout", "-q", "--detach", self.baseline)
        self.scratch.git(self.repo, "clean", "-q", "-f", "-d", "-x")
        shutil.rmtree(self.scratch.home / ".adw-lite", ignore_errors=True)

    def run(self, *args, **env):
        self.restore()
        return self.scratch.run(["python3", self.repo / "tools" / "run_tests.py", *args], cwd=self.repo,
                                timeout=300, **env)

    def run_at(self, commit, **env):
        self.restore()
        self.scratch.git(self.repo, "checkout", "-q", "--detach", commit)
        return self.scratch.run(["python3", self.repo / "tools" / "run_tests.py"], cwd=self.repo, timeout=300, **env)

    def stamps(self):
        folder = self.scratch.home / SUITE_PASSES
        return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []

    def stamp(self, commit):
        return json.loads((self.scratch.home / SUITE_PASSES / f"{commit}.json").read_text(encoding="utf-8"))


CHECKOUT = ModuleFixture(SuiteCheckout)


def interpreter_version(scratch):
    reported = scratch.run(["python3", "--version"], timeout=60).stdout
    return re.fullmatch(r"Python (\S+)\n", reported)[1]


def listed_modules(stdout):
    lines = stdout.splitlines()
    rows = (re.fullmatch(r"\s*\d+\.\d{3}s\s+(\S+)", line) for line in lines[lines.index("Slowest test modules:") + 1:])
    return sorted(row[1] for row in rows if row)


class PassingCleanRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = CHECKOUT.get()
        cls.started = datetime.now(UTC)
        cls.result = cls.checkout.run()
        cls.finished = datetime.now(UTC)
        cls.names = cls.checkout.stamps()
        cls.record = cls.checkout.stamp(cls.checkout.baseline) if cls.names else {}

    def test_a_passing_clean_run_exits_zero_and_writes_one_stamp_named_for_head(self):
        self.assertEqual((self.result.returncode, self.names), (0, [f"{self.checkout.baseline}.json"]))

    def test_the_stamp_records_the_head_commit_and_the_test_count(self):
        self.assertEqual((self.record["commit"], self.record["ran"]), (self.checkout.baseline, FAKE_COUNT))

    def test_the_stamp_wall_time_is_positive_and_within_the_run(self):
        span = (self.finished - self.started).total_seconds()
        self.assertTrue(0 < self.record["wall_seconds"] <= span)

    def test_the_stamp_date_is_a_utc_time_inside_the_run(self):
        date = datetime.fromisoformat(self.record["date"])
        self.assertEqual((date.utcoffset().total_seconds(), self.started <= date <= self.finished), (0, True))

    def test_the_stamp_records_the_version_of_the_python_that_ran_the_suite(self):
        self.assertEqual(self.record["python_version"], interpreter_version(self.checkout.scratch))


class FailingRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = CHECKOUT.get()
        cls.result = cls.checkout.run(**{FAIL_ENV: "1"})

    def test_a_failing_run_exits_non_zero_and_writes_no_stamp(self):
        self.assertEqual((self.result.returncode != 0, self.checkout.stamps()), (True, []))


class DirtyTreeRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = CHECKOUT.get()
        cls.checkout.restore()
        (cls.checkout.repo / "notes.txt").write_text("draft\n", encoding="utf-8")
        cls.result = cls.checkout.scratch.run(["python3", cls.checkout.repo / "tools" / "run_tests.py"],
                                              cwd=cls.checkout.repo, timeout=300)

    def test_a_passing_run_on_a_dirty_tree_exits_zero_and_writes_no_stamp(self):
        self.assertEqual((self.result.returncode, self.checkout.stamps()), (0, []))


class HeadMovesDuringRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = CHECKOUT.get()
        move = json.dumps(["git", "-C", str(cls.checkout.repo), "checkout", "-q", "--detach", cls.checkout.moved])
        cls.result = cls.checkout.run(**{DURING_ENV: move})
        cls.head_after = cls.checkout.scratch.git(cls.checkout.repo, "rev-parse", "HEAD")

    def test_a_head_change_during_a_passing_run_writes_no_stamp_for_either_commit(self):
        self.assertEqual((self.result.returncode, self.head_after, self.checkout.stamps()),
                         (0, self.checkout.moved, []))


HIDDEN_FAILING_BETA = """import unittest


class BetaTests(unittest.TestCase):
    def test_sum(self):
        self.assertEqual(sum([1, 2]), 4)
"""


class HiddenEditRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = CHECKOUT.get()
        cls.checkout.restore()
        beta = cls.checkout.repo / "tests" / "test_beta.py"
        cls.checkout.scratch.git(cls.checkout.repo, "update-index", "--assume-unchanged", "tests/test_beta.py")
        cls.addClassCleanup(cls.checkout.scratch.git, cls.checkout.repo, "checkout", "--", "tests/test_beta.py")
        cls.addClassCleanup(cls.checkout.scratch.git, cls.checkout.repo, "update-index", "--no-assume-unchanged",
                            "tests/test_beta.py")
        beta.write_text(HIDDEN_FAILING_BETA, encoding="utf-8")
        cls.result = cls.checkout.scratch.run(["python3", cls.checkout.repo / "tools" / "run_tests.py"],
                                              cwd=cls.checkout.repo, timeout=300)
        cls.names = cls.checkout.stamps()

    def test_an_edit_hidden_from_git_status_does_not_change_what_the_suite_runs(self):
        self.assertEqual((self.result.returncode, self.names, self.checkout.stamp(self.checkout.baseline)["ran"]),
                         (0, [f"{self.checkout.baseline}.json"], FAKE_COUNT))


class DirtyFailingEditRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = CHECKOUT.get()
        cls.checkout.restore()
        cls.addClassCleanup(cls.checkout.scratch.git, cls.checkout.repo, "checkout", "--", "tests/test_beta.py")
        (cls.checkout.repo / "tests" / "test_beta.py").write_text(HIDDEN_FAILING_BETA, encoding="utf-8")
        cls.result = cls.checkout.scratch.run(["python3", cls.checkout.repo / "tools" / "run_tests.py"],
                                              cwd=cls.checkout.repo, timeout=300)

    def test_a_dirty_run_tests_head_so_a_failing_uncommitted_edit_neither_fails_it_nor_stamps(self):
        self.assertEqual((self.result.returncode, self.checkout.stamps()), (0, []))


class StateRootOverrideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = CHECKOUT.get()
        cls.state = cls.checkout.scratch.root / "override-state"
        cls.addClassCleanup(shutil.rmtree, cls.state, ignore_errors=True)
        cls.result = cls.checkout.run(ADW_STATE_ROOT=str(cls.state))
        cls.written = sorted(path.name for path in (cls.state / "suite-passes").glob("*.json"))

    def test_adw_state_root_moves_the_stamp_out_of_the_home_state_folder(self):
        self.assertEqual((self.result.returncode, self.written, self.checkout.stamps()),
                         (0, [f"{self.checkout.baseline}.json"], []))


class ExportIgnoredTestRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = CHECKOUT.get()
        cls.result = cls.checkout.run_at(cls.checkout.exported)
        cls.names = cls.checkout.stamps()

    def test_a_committed_test_marked_export_ignore_still_runs_and_counts(self):
        record = self.checkout.stamp(self.checkout.exported) if self.names else {}
        self.assertEqual((self.result.returncode, self.names, record.get("ran")),
                         (0, [f"{self.checkout.exported}.json"], FAKE_COUNT + 1))


class ProfileRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = CHECKOUT.get()
        cls.result = cls.checkout.run("--profile")

    def test_profile_lists_every_fake_suite_module_under_the_slowest_modules_heading(self):
        self.assertEqual((self.result.returncode, listed_modules(self.result.stdout)), (0, sorted(FAKE_MODULES)))


if __name__ == "__main__":
    unittest.main()
