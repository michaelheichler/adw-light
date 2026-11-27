import json
import time
import unittest
from importlib import import_module
from unittest import mock

from after_run_home import CAUGHT, CONFIG, LET_THROUGH, AfterRunCase, Project, project_files

after_run = import_module("lib.after_run")

REPO = "repo"
SEAL = "config_seal"
INCOMPLETE = "config_search_incomplete"
UNEXPLAINED = "unexplained_change"
STOP_BLOCK = "block"
QUIET = "git status --short"
HIDDEN_WRITE = "python3 tools/settle.py"
VISIBLE_NEW = "new/" + CONFIG
HIDDEN_NEW = "cache/" + CONFIG
HIDDEN_OLD = "cache/old/" + CONFIG
SETTINGS = '{"rule_gates": {"comment_shape": "observe"}}\n'
WHOLE_SEARCH = [(True, False), (False, True)]


class SearchView:
    def __init__(self, inner, case):
        self.inner = inner
        self.case = case

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def ignored_files(self, root, *, config_only=False, directory=False, timeout=2):
        self.case.searches.append((config_only, directory))
        if self.case.failing:
            return None
        return self.inner.ignored_files(root, config_only=config_only, directory=directory, timeout=timeout)


class WideClock:
    def __init__(self, stamp):
        self.stamp = stamp

    def __getattr__(self, name):
        return getattr(time, name)

    def time_ns(self):
        return self.stamp


class IgnoredSearchCase(AfterRunCase):
    projects = {REPO: Project(project_files({".gitignore": "cache/\n", HIDDEN_OLD: SETTINGS}))}

    def setUp(self):
        self.searches = []
        self.failing = False

    def view(self):
        return SearchView(super().view(), self)

    def fail_searches(self, *effects):
        for effect in effects:
            effect()
        self.failing = True

    def ledger_paths(self, rule, run=""):
        return self.finding_paths(rule, REPO, run)


class IncompleteSearchBeforeTheRun(IgnoredSearchCase):
    def create_both(self):
        self.change(REPO, VISIBLE_NEW, SETTINGS)
        self.change(REPO, HIDDEN_NEW, SETTINGS)
        self.failing = False

    def test_new_status_config_is_sealed_and_new_ignored_config_is_dropped_and_logged(self):
        """The test fails when a real bug enters the code it exercises."""
        self.failing = True
        verdict = self.after_run(REPO, HIDDEN_WRITE, self.create_both)
        observed = (verdict, self.ledger_paths(SEAL), self.ledger_paths(INCOMPLETE))
        self.assertEqual(observed, (CAUGHT, [VISIBLE_NEW], sorted([str(self.folder(REPO)), HIDDEN_NEW])))


class StoredListingUse(IgnoredSearchCase):
    def test_session_start_searches_for_ignored_configs(self):
        """Check the call count and the arguments of each mocked call."""
        self.start(REPO)
        self.assertEqual(self.searches, WHOLE_SEARCH)

    def test_tool_call_after_session_start_reuses_the_stored_listing(self):
        """Check the call count and the arguments of each mocked call."""
        self.start(REPO)
        self.searches.clear()
        self.pre(REPO, QUIET)
        self.assertEqual(self.searches, [])

    def test_tool_call_without_a_stored_listing_searches(self):
        """Check the call count and the arguments of each mocked call."""
        self.pre(REPO, QUIET)
        self.assertEqual(self.searches, WHOLE_SEARCH)

    def test_tool_call_after_a_searching_tool_call_reuses_its_stored_listing(self):
        """Check the call count and the arguments of each mocked call."""
        self.pre(REPO, QUIET, call="c1")
        self.searches.clear()
        self.pre(REPO, QUIET, call="c2")
        self.assertEqual(self.searches, [])


class StopWindows(IgnoredSearchCase):
    def stop_outcome(self, run):
        return self.stop(REPO, run=run), self.ledger_paths(SEAL, run), self.ledger_paths(UNEXPLAINED, run)

    def seal_actions(self):
        reports = sorted((self.state / "reports").glob(self.session() + "-*.json"))
        rows = [row for report in reports for row in json.loads(report.read_text(encoding="utf-8"))]
        return {row["action"] for row in rows if row["rule"] == SEAL}

    def test_new_ignored_config_missed_after_its_command_is_sealed_at_stop_with_a_removal_restore(self):
        """The test fails when a real bug enters the code it exercises."""
        self.start(REPO)
        self.pair(REPO, QUIET, lambda: self.fail_searches(lambda: self.change(REPO, HIDDEN_NEW, SETTINGS)))
        self.failing = False
        outcome = self.stop_outcome("")
        removal = "rm -- " + str(self.folder(REPO) / HIDDEN_NEW)
        self.assertEqual((outcome, {removal in action for action in self.seal_actions()}),
                         ((STOP_BLOCK, [HIDDEN_NEW], []), {True}))

    def test_new_ignored_config_made_between_commands_is_only_noted_at_stop(self):
        """The test stays green when you restructure the code without changing its behavior."""
        self.start(REPO)
        self.pair(REPO, QUIET)
        self.change(REPO, HIDDEN_NEW, SETTINGS)
        self.assertEqual(self.stop_outcome(""), (LET_THROUGH, [], [HIDDEN_NEW]))

    def wide_window_stop(self, run, effect):
        self.start(REPO, run=run)
        with mock.patch.object(after_run, "time", WideClock(0)):
            self.pre(REPO, QUIET, run=run)
        with mock.patch.object(after_run, "time", WideClock(2 ** 62)):
            self.post(REPO, QUIET, run=run)
            self.batch(REPO, QUIET, run=run)
        effect()
        return self.stop_outcome(run)

    def test_only_a_new_ignored_config_counts_as_made_inside_a_command_window(self):
        """Use a parameterized test for several inputs."""
        outcomes = {
            "new": self.wide_window_stop("new", lambda: self.change(REPO, HIDDEN_NEW, SETTINGS)),
            "changed": self.wide_window_stop("changed", lambda: self.change(REPO, HIDDEN_OLD, SETTINGS + " ")),
        }
        expected = {"new": (STOP_BLOCK, [HIDDEN_NEW], []), "changed": (LET_THROUGH, [], [HIDDEN_OLD])}
        self.assertEqual(outcomes, expected)


if __name__ == "__main__":
    unittest.main()
