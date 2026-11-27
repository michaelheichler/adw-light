import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView

AUDIT = ROOT / "tools" / "mutation_audit.py"
GIT_FOLDER_FILES = {".git/HEAD": "ref: refs/heads/main\n", ".git/config": "[core]\n"}
CHECKS_DIR = "checks"
CHECKS_PATTERN = "check_*.py"
MODULE = "pkg/messages.py"
CHECKS_MODULE = "check_messages"
CHECKS_CLASS = "MessageChecks"
KILLED = "killed"
SURVIVED = "survived"
WRAP = "wrap"
SPLIT = "split"
LISTED_ROW = re.compile(r"^(\S+:\d+) (wrap|split) ", re.MULTILINE)
HOOK_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolBatch",
    "PostToolUseFailure", "SubagentStart", "SubagentStop", "Stop", "SessionEnd",
)


def words(*parts):
    return " ".join(parts)


GREETING = words("the", "cache", "is", "empty")
WARNING = words("the", "disk", "is", "almost", "full")
WARNING_PART = words("disk", "is", "almost")
UNREAD = words("nobody", "reads", "this", "line")
MODULE_DOC = words("This", "module", "explains", "how", "the", "cache", "works", "for", "you.")
FUNCTION_DOC = words("this", "text", "is", "the", "docstring", "of", "the", "function")
LOOSE = words("a", "loose", "string", "sits", "on", "its", "own", "here")
FORMATTED = "f" + json.dumps(words("there", "are", "{count}", "files", "in", "the", "cache"))
BROKEN_SOURCE = json.dumps(words("the", "first", "line") + "\n" + words("the", "second", "line"))
TABBED_SOURCE = json.dumps(words("a", "tab") + "\t" + words("is", "in", "the", "text"))
QUOTED_SOURCE = json.dumps(words("she", "said", '"stop"', "to", "the", "man"))
NAMED_ESCAPE_SOURCE = '"' + words("the", "caf" + "\\N{LATIN SMALL LETTER E WITH ACUTE}", "is", "open") + '"'
WRONG = "wrong-value"


def function(name, *body):
    return ("", "", f"def {name}():", *("    " + line for line in body))


MODULE_LINES = (
    json.dumps(MODULE_DOC),
    *function("greeting", f"return {json.dumps(GREETING)}"),
    *function("warning", f"return {json.dumps(WARNING)}"),
    *function("unused", f"return {json.dumps(UNREAD)}"),
    *function("documented", json.dumps(FUNCTION_DOC), json.dumps(LOOSE), "return 1"),
    *function("formatted", "count = 3", f"return {FORMATTED}"),
    *function("broken", f"return {BROKEN_SOURCE}"),
    *function("tabbed", f"return {TABBED_SOURCE}"),
    *function("quoted", f"return {QUOTED_SOURCE}"),
    *function("named", f"return {NAMED_ESCAPE_SOURCE}"),
)

CHECK_BODIES = {
    "test_greeting": f"self.assertEqual(messages.greeting(), {json.dumps(GREETING)})",
    "test_warning_part": f"self.assertIn({json.dumps(WARNING_PART)}, messages.warning())",
    "test_already_failing": f"self.assertEqual(messages.greeting(), {json.dumps(WRONG)})",
    "test_broken_equals": f"self.assertEqual(messages.broken(), {BROKEN_SOURCE})",
    "test_broken_keeps_two_lines": "self.assertEqual(len(messages.broken().splitlines()), 2)",
    "test_tabbed_equals": f"self.assertEqual(messages.tabbed(), {TABBED_SOURCE})",
    "test_tabbed_keeps_its_tab": "self.assertEqual(messages.tabbed().count(chr(9)), 1)",
    "test_quoted_equals": f"self.assertEqual(messages.quoted(), {QUOTED_SOURCE})",
    "test_quoted_keeps_its_quotes": "self.assertEqual(messages.quoted().count(chr(34)), 2)",
}


def checks_module(bodies, top=()):
    lines = ["import unittest", *top, "from pkg import messages", "", "", f"class {CHECKS_CLASS}(unittest.TestCase):"]
    for name, body in bodies.items():
        lines.extend(("", f"    def {name}(self):", f"        {body}"))
    return "\n".join(lines) + "\n"


def line_of(source_line):
    return f"{MODULE}:{MODULE_LINES.index(source_line) + 1}"


def check_id(name):
    return f"{name} ({CHECKS_MODULE}.{CHECKS_CLASS}.{name})"


def build_project(root, checks):
    files = {
        "pkg/__init__.py": "",
        MODULE: "\n".join(MODULE_LINES) + "\n",
        f"{CHECKS_DIR}/{CHECKS_MODULE}.py": checks,
        **GIT_FOLDER_FILES,
    }
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def module_workspace(case_class):
    workspace = TemporaryDirectory()
    case_class.addClassCleanup(workspace.cleanup)
    return Path(workspace.name).resolve()


def run_audit(project, *extra):
    return subprocess.run(
        [sys.executable, str(AUDIT), "--project", str(project), "--tests-dir", CHECKS_DIR,
         "--pattern", CHECKS_PATTERN, "--jobs", "2", "--timeout", "120", *extra],
        text=True, capture_output=True, timeout=600,
    )


def tree_digest(project):
    digest = hashlib.sha256()
    for path in sorted(project.rglob("*")):
        digest.update(path.relative_to(project).as_posix().encode())
        digest.update(path.read_bytes() if path.is_file() else b"dir")
    return digest.hexdigest()


class MutationAuditReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project = build_project(module_workspace(cls) / "project", checks_module(CHECK_BODIES))
        cls.digest_before = tree_digest(project)
        cls.git_before = tree_digest(project / ".git")
        cls.call = run_audit(project, "--json")
        cls.digest_after = tree_digest(project)
        cls.git_after = tree_digest(project / ".git")
        cls.report = json.loads(cls.call.stdout) if cls.call.returncode == 0 else {}
        cls.statuses = {(row["path"] + ":" + str(row["line"]), row["kind"]): row for row in cls.report.get("results", ())}

    def row(self, source_line, kind):
        return self.statuses[(line_of(source_line), kind)]

    def test_the_audit_exits_0_with_a_report(self):
        self.assertEqual(self.call.returncode, 0, self.call.stderr)

    def test_an_equality_wording_check_is_named_as_dying_on_its_prose_line(self):
        greeting_line = line_of(f"    return {json.dumps(GREETING)}")
        self.assertIn(greeting_line, self.report["dying_tests"][check_id("test_greeting")])

    def test_the_wrapped_greeting_is_killed_by_its_equality_check_only(self):
        row = self.row(f"    return {json.dumps(GREETING)}", WRAP)
        self.assertEqual((row["status"], row["killers"]), (KILLED, [check_id("test_greeting")]))

    def test_a_substring_wording_check_dies_on_the_split_mutant(self):
        row = self.row(f"    return {json.dumps(WARNING)}", SPLIT)
        self.assertEqual((row["status"], row["killers"]), (KILLED, [check_id("test_warning_part")]))

    def test_a_substring_wording_check_survives_the_wrap_mutant(self):
        row = self.row(f"    return {json.dumps(WARNING)}", WRAP)
        self.assertEqual((row["status"], row["killers"]), (SURVIVED, []))

    def test_a_prose_string_no_check_reads_survives_both_kinds(self):
        rows = (self.row(f"    return {json.dumps(UNREAD)}", WRAP), self.row(f"    return {json.dumps(UNREAD)}", SPLIT))
        self.assertEqual([(row["status"], row["killers"]) for row in rows], [(SURVIVED, []), (SURVIVED, [])])

    def test_a_check_failing_before_mutation_is_a_baseline_failure_not_a_killer(self):
        self.assertEqual(self.report["baseline_failures"], [check_id("test_already_failing")])

    def test_a_check_failing_before_mutation_never_appears_as_dying(self):
        self.assertNotIn(check_id("test_already_failing"), self.report["dying_tests"])

    def test_mutated_escape_strings_are_killed_only_by_their_equality_checks(self):
        killers = {
            (kind, tuple(self.row(f"    return {source}", kind)["killers"]))
            for source in (BROKEN_SOURCE, TABBED_SOURCE, QUOTED_SOURCE) for kind in (WRAP, SPLIT)
        }
        expected = {
            (kind, (check_id(name),))
            for name in ("test_broken_equals", "test_tabbed_equals", "test_quoted_equals") for kind in (WRAP, SPLIT)
        }
        self.assertEqual(killers, expected)

    def test_checks_that_count_escaped_characters_never_die(self):
        escape_checks = {check_id(name) for name in ("test_broken_keeps_two_lines", "test_tabbed_keeps_its_tab", "test_quoted_keeps_its_quotes")}
        self.assertEqual(escape_checks & set(self.report["dying_tests"]), set())

    def test_the_audited_project_files_are_unchanged_after_the_run(self):
        self.assertEqual(self.digest_after, self.digest_before)

    def test_the_git_folder_of_the_audited_project_is_unchanged(self):
        self.assertEqual(self.git_after, self.git_before)


class MutationAuditListing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = build_project(module_workspace(cls) / "project", checks_module(CHECK_BODIES))

    def listed(self):
        call = run_audit(self.project, "--list")
        return call.returncode, set(LISTED_ROW.findall(call.stdout))

    def test_listing_skips_docs_loose_strings_fstrings_and_a_split_that_would_break_a_named_escape(self):
        returned = (GREETING, WARNING, UNREAD)
        escaped = (BROKEN_SOURCE, TABBED_SOURCE, QUOTED_SOURCE)
        sources = [f"    return {json.dumps(text)}" for text in returned] + [f"    return {text}" for text in escaped]
        expected = {(line_of(source), kind) for source in sources for kind in (WRAP, SPLIT)}
        expected.add((line_of(f"    return {NAMED_ESCAPE_SOURCE}"), WRAP))
        self.assertEqual(self.listed(), (0, expected))

    def test_kind_wrap_lists_only_wrap_mutants(self):
        call = run_audit(self.project, "--list", "--kind", WRAP)
        self.assertEqual({kind for _, kind in LISTED_ROW.findall(call.stdout)}, {WRAP})


class MutationAuditBaseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = build_project(module_workspace(cls) / "project", "")

    def test_a_project_whose_checks_folder_holds_no_tests_exits_2(self):
        call = run_audit(self.project, "--json")
        self.assertEqual(call.returncode, 2)


def sentinel_project(root, name):
    sentinel = root / (name + "-suite-ran")
    top = ("from pathlib import Path", f"Path({json.dumps(str(sentinel))}).write_text('ran')")
    project = build_project(root / name, checks_module({"test_greeting": CHECK_BODIES["test_greeting"]}, top))
    return project, sentinel


SENTINEL_PROJECTS = ("audited", "listed", "hooked")


class MutationAuditStaysOutOfHooks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = module_workspace(cls)
        cls.projects = {name: sentinel_project(cls.root, name) for name in SENTINEL_PROJECTS}
        (cls.root / "home").mkdir()
        cls.git = FakeGitView({"roots": {str(project): {"disk": {}} for project, _ in cls.projects.values()}})
        environment = mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", ""), "HOME": str(cls.root / "home")}, clear=True)
        environment.start()
        cls.addClassCleanup(environment.stop)

    def hook(self, event):
        project, _ = self.projects["hooked"]
        payload = {
            "cwd": str(project), "session_id": "mutation-audit", "hook_event_name": event,
            "tool_name": "Bash", "tool_input": {"command": "true"}, "source": "startup",
            "prompt": "run the suite", "last_assistant_message": "Done.",
        }
        return isinstance(run_hook(event, payload, state_dir=self.root / "state", git_view=self.git), dict)

    def test_the_sentinel_check_records_a_run_when_the_audit_runs(self):
        project, sentinel = self.projects["audited"]
        run_audit(project, "--json", "--jobs", "1")
        self.assertTrue(sentinel.exists())

    def test_listing_mutants_runs_no_tests(self):
        project, sentinel = self.projects["listed"]
        run_audit(project, "--list")
        self.assertFalse(sentinel.exists())

    def test_no_hook_event_runs_the_project_suite_through_the_audit(self):
        answers = {event: self.hook(event) for event in HOOK_EVENTS}
        self.assertEqual((answers, self.projects["hooked"][1].exists()), (dict.fromkeys(HOOK_EVENTS, True), False))


CAFE_RETURN = "    return " + json.dumps(words("the", "caf\u00e9", "is", "open", "now"), ensure_ascii=False)
PROSE_RETURN = f"    return {json.dumps(GREETING)}"
GHOST_ID = "ghost (m.T)"
GHOST_BODY = f"print({json.dumps('FAIL: ' + GHOST_ID)}) if messages.unused() != {json.dumps(UNREAD)} else None"


def add_module(root, relative, data):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def prose_module(header, encoding):
    return "\n".join((*header, "", "", "def text():", CAFE_RETURN, "")).encode(encoding)


def listed_paths(project, *extra):
    call = run_audit(project, "--list", *extra)
    return call.returncode, {place.rsplit(":", 1)[0] for place, _ in LISTED_ROW.findall(call.stdout)}


HELPER = "\n".join(function("helper", PROSE_RETURN.strip())).encode()
SCOPE_MODULES = {
    "encodings": {"pkg/legacy.py": prose_module(("# -*- coding: latin-1 -*-",), "latin-1"),
                  "pkg/modern.py": prose_module((), "utf-8")},
    "sibling_package": {"pkg2/notes.py": "\n".join(function("notes", PROSE_RETURN.strip())).encode()},
    "exact_file": {"pkg/other.py": "\n".join(function("other", PROSE_RETURN.strip())).encode()},
    "nested_tests": {"a/tests/helpers.py": HELPER, "a/testsX/helpers.py": HELPER},
}


class MutationAuditScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = module_workspace(cls)
        cls.projects = {name: build_project(root / name, "") for name in SCOPE_MODULES}
        for name, modules in SCOPE_MODULES.items():
            for relative, data in modules.items():
                add_module(cls.projects[name], relative, data)

    def test_a_latin_1_source_yields_no_mutants_while_its_utf_8_twin_is_listed(self):
        self.assertEqual(listed_paths(self.projects["encodings"]), (0, {MODULE, "pkg/modern.py"}))

    def test_source_pkg_lists_files_under_pkg_but_not_under_pkg2(self):
        self.assertEqual(listed_paths(self.projects["sibling_package"], "--source", "pkg"), (0, {MODULE}))

    def test_an_exact_file_given_as_source_is_the_only_file_listed(self):
        self.assertEqual(listed_paths(self.projects["exact_file"], "--source", MODULE), (0, {MODULE}))

    def test_a_nested_tests_dir_is_not_mutated_but_a_sibling_with_a_longer_name_is(self):
        listed = listed_paths(self.projects["nested_tests"], "--tests-dir", "a/tests")
        self.assertEqual(listed, (0, {MODULE, "a/testsX/helpers.py"}))


class MutationAuditReadsOnlyTheRunnerReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = build_project(module_workspace(cls) / "project", checks_module({"test_ghost": GHOST_BODY}))

    def test_a_passing_check_that_prints_a_fail_line_to_stdout_is_never_a_killer(self):
        call = run_audit(self.project, "--json", "--source", MODULE)
        report = json.loads(call.stdout) if call.returncode == 0 else {}
        unread_line = line_of(f"    return {json.dumps(UNREAD)}")
        killers = {test for row in report.get("results", ()) if f"{row['path']}:{row['line']}" == unread_line for test in row["killers"]}
        self.assertEqual((call.returncode, killers), (0, set()))


if __name__ == "__main__":
    unittest.main()
