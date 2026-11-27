import os
import pwd
import shutil
import unittest
from typing import NamedTuple

from after_commit_home import CLEAN_MESSAGE, CLEAN_TEXT, FLAGGED_MESSAGE, FLAGGED_TEXT, NOTES, AfterCommitCase, commit_project
from after_run_home import CAUGHT, LET_THROUGH, Project
from fake_repo_home import Repo, repo_state
from in_memory_hook import ALLOWED, DENY, PASSED, PRINCIPLE, TEST_WRITER, InMemoryHookCase
from lib.git_port import FakeGitView


HOME_RULE = "hard_coded_path"
SYSTEM_RULE = "hard_coded_system_path"
CITATION = "test_principle_citation"
GATE = "test_write_gate"
LITERAL_LINE = 6
PATH_TEST = "tests/test_folder.py"
VENV_MODULE = ".venv/lib/python3.12/site-packages/pkg/paths.py"
OLD_TEST = "tests/test_old.py"
FIRST_BODY = "        items = [1, 2]\n        self.assertEqual(len(items), 2)\n"
SECOND_TEST = "\n    def test_second(self):\n        self.assertEqual(len([1]), 1)\n"
OLD_FILE = "import unittest\n\n\nclass Sample(unittest.TestCase):\n    def test_first(self):\n" + FIRST_BODY + SECOND_TEST
UNCITED_TEST = "\n    def test_third(self):\n        self.assertEqual(len([]), 0)\n"
REPO = "repo"
FRESH = "fresh"
NEW_FOLDER = "../" + FRESH
MADE = "mkdir " + NEW_FOLDER + " && git -C " + NEW_FOLDER + " commit "
CLEAN_COMMIT = MADE + "-m 'Add notes.'"
ACCOUNT = pwd.getpwuid(os.getuid()).pw_name
CAPITAL = ACCOUNT.capitalize()
SOURCE_MODULE = "src/paths.py"
VIEW_MODULE = "src/app.jsx"
REFUSED_HOME = (DENY, [HOME_RULE])
OLD_NESTED = REPO + "/node_modules/old15"
LATER = "later.md"


class NestedForm(NamedTuple):
    name: str
    made: str
    command: str


NESTED_FORMS = (
    NestedForm(REPO + "/node_modules/x", REPO + "/node_modules/x",
               "mkdir -p node_modules/x && cd node_modules/x && git init && git add . && git commit -m {message}"),
    NestedForm(REPO + "/.venv/v15", REPO + "/.venv",
               "mkdir -p .venv/v15 && cd .venv/v15 && git init && git add . && git commit -m {message}"),
    NestedForm(REPO + "/dist/d15", REPO + "/dist",
               "mkdir -p dist/d15 && cd dist/d15 && git init && git add . && git commit -m {message}"),
    NestedForm(REPO + "/node_modules/e15", REPO + "/node_modules/e15",
               "mkdir node_modules/e15 && git -C node_modules/e15 init && git -C node_modules/e15 add . "
               "&& git -C node_modules/e15 commit -m {message}"),
    NestedForm(REPO + "/node_modules/g15", REPO + "/node_modules/g15",
               "git init node_modules/g15 && git -C node_modules/g15 commit -m {message}"),
)
NESTED_NAMES = frozenset(form.name for form in NESTED_FORMS)


def rooted(*parts):
    return "/" + "/".join(parts)


def path_test(expression):
    return ("import os\n\n\ndef test_folder():\n    \"\"\"" + PRINCIPLE + "\"\"\"\n"
            "    assert os.path.basename(" + expression + ")\n")


def quoted(text):
    return '"' + text + '"'


def shell_quoted(text):
    return "'" + text.strip() + "'"


def escaped_second_letter(style):
    code = ord(ACCOUNT[1])
    escapes = {"unicode": "\\u" + format(code, "04x"), "hex": "\\x" + format(code, "02x"), "octal": "\\" + format(code, "03o")}
    return rooted("Users", ACCOUNT[0] + escapes[style] + ACCOUNT[2:], "x")


class PathLiteralsInTests(InMemoryHookCase):
    def verdict(self, literal):
        return self.exact_verdict(PATH_TEST, path_test(quoted(literal)))

    def real_home(self):
        return str(self.home / "data")

    def test_temp_root_literal_is_noted(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.verdict(rooted("tmp", "x")), (None, ((SYSTEM_RULE, LITERAL_LINE),)))

    def test_service_root_literal_is_noted(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.verdict(rooted("srv", "app") + "/"), (None, ((SYSTEM_RULE, LITERAL_LINE),)))

    def test_system_data_literal_is_noted(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.verdict(rooted("var", "lib", "app")), (None, ((SYSTEM_RULE, LITERAL_LINE),)))

    def test_package_manager_literal_is_noted(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.verdict(rooted("opt", "homebrew", "bin")), (None, ((SYSTEM_RULE, LITERAL_LINE),)))

    def test_sample_user_home_literal_leaves_no_row(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.verdict(rooted("Users", "sample", "data")), ALLOWED)

    def test_home_relative_literal_leaves_no_row(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.verdict("~" + "/x"), ALLOWED)

    def test_real_home_literal_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.verdict(self.real_home()), (DENY, ((HOME_RULE, LITERAL_LINE),)))

    def test_real_home_literal_after_a_temp_literal_on_the_same_line_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        expression = quoted(rooted("tmp", "x")) + " + " + quoted(self.real_home())
        verdict = self.exact_verdict(PATH_TEST, path_test(expression))
        self.assertEqual(verdict, (DENY, ((HOME_RULE, LITERAL_LINE), (SYSTEM_RULE, LITERAL_LINE))))

    def test_site_packages_file_holding_a_real_home_leaves_no_row(self):
        """Feed input, check the return value."""
        content = "DATA = " + quoted(self.real_home()) + "\n"
        self.assertEqual(self.write(self.work / VENV_MODULE, content), PASSED)


class CitationsOfChangedTestsOnly(InMemoryHookCase):
    files = {OLD_TEST: OLD_FILE}

    def git_view(self):
        return FakeGitView({"roots": {str(self.work): repo_state(Repo(self.files))}})

    def edit(self, old, new, agent_type=TEST_WRITER):
        tool_input = {"file_path": str(self.work / OLD_TEST), "old_string": old, "new_string": new}
        payload = {"cwd": str(self.work), "tool_name": "Edit", "tool_input": tool_input}
        return self.hook("PreToolUse", {**payload, "agent_type": agent_type} if agent_type else payload)

    def test_writer_deleting_one_test_leaves_its_uncited_neighbour_alone(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.edit(SECOND_TEST, ""), PASSED)

    def test_writer_shortening_an_uncited_test_must_cite_it(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.edit(FIRST_BODY, "        self.assertEqual(len([1, 2]), 2)\n"), (DENY, [CITATION]))

    def test_writer_adding_an_uncited_test_must_cite_it(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.edit(SECOND_TEST, SECOND_TEST + UNCITED_TEST), (DENY, [CITATION]))

    def test_other_agent_deleting_a_test_is_refused_by_the_test_write_gate(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.edit(SECOND_TEST, "", agent_type=None), (DENY, [GATE]))


class CommitIntoAFolderMadeInTheSameCommand(AfterCommitCase):
    projects = {REPO: commit_project(), FRESH: Project({}, listed=False)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        shutil.rmtree(cls.root / FRESH)

    def committed(self, files, message):
        def effect():
            self.addCleanup(shutil.rmtree, self.folder(FRESH), True)
            (self.folder(FRESH) / ".git").mkdir(parents=True)
            self.make_commit(FRESH, files, message)
            self.reveal(FRESH)
        return effect

    def repository(self, name, project):
        state = super().repository(name, project)
        if name != FRESH:
            return state
        first = self.tip(FRESH)
        return {**state, "revisions": {first.name: first.tree}, "parents": {first.name: []},
                "refs": {"HEAD": first.name, "main": first.name, "refs/heads/main": first.name},
                "messages": {first.name: first.message}, "index": dict(first.tree)}

    def before_the_run(self, command):
        self.start(REPO)
        return self.pre(REPO, command)

    def test_commit_into_a_folder_made_earlier_passes_before_the_run(self):
        """Feed input, check the return value."""
        self.assertEqual(self.before_the_run(CLEAN_COMMIT), LET_THROUGH)

    def test_flagged_first_commit_message_is_caught_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        effect = self.committed({NOTES: CLEAN_TEXT}, FLAGGED_MESSAGE)
        self.assertEqual(self.run_and_stop(REPO, CLEAN_COMMIT, effect), (LET_THROUGH, CAUGHT, CAUGHT))

    def test_flagged_file_in_the_first_commit_is_caught_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        effect = self.committed({NOTES: FLAGGED_TEXT}, CLEAN_MESSAGE)
        self.assertEqual(self.run_and_stop(REPO, CLEAN_COMMIT, effect), (LET_THROUGH, CAUGHT, CAUGHT))

    def test_clean_first_commit_releases_after_the_run(self):
        """Feed input, check the return value."""
        effect = self.committed({NOTES: CLEAN_TEXT}, CLEAN_MESSAGE)
        self.assertEqual(self.run_and_stop(REPO, CLEAN_COMMIT, effect), (LET_THROUGH, LET_THROUGH, None))

    def test_commit_into_a_folder_no_command_made_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.before_the_run("git -C " + NEW_FOLDER + " commit -m 'Add notes.'"), DENY)

    def test_flagged_literal_message_is_refused_before_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        command = MADE + "-m '" + FLAGGED_MESSAGE.strip() + "'"
        self.assertEqual(self.before_the_run(command), DENY)

    def test_flagged_literal_message_beside_a_message_file_is_refused_before_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        command = MADE + "-F msg.txt -m '" + FLAGGED_MESSAGE.strip() + "'"
        self.assertEqual(self.before_the_run(command), DENY)


class HomePathPrecision(InMemoryHookCase):
    def module_verdict(self, content):
        return self.write(self.work / SOURCE_MODULE, content)

    def assigned(self, literal):
        return self.module_verdict("P = " + quoted(literal) + "\n")

    def view_verdict(self, line):
        return self.write(self.work / VIEW_MODULE, line + "\n")

    def test_routes_without_the_local_account_pass(self):
        """Use a parameterized test for several inputs."""
        lines = ["fetch(" + quoted(rooted("home", "feed")) + ");", "<a href=" + quoted(rooted("home", "about")) + ">About</a>;",
                 "fetch(" + quoted(rooted("Users", "list")) + ");", "<Link to=" + quoted(rooted("home", "about")) + " />;"]
        verdicts = [self.view_verdict(line) for line in lines]
        self.assertEqual(verdicts, [PASSED] * 4)

    def test_route_naming_the_local_account_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.view_verdict("fetch(" + quoted(rooted("Users", ACCOUNT, "x")) + ");"), REFUSED_HOME)

    def test_capitalised_account_inside_a_plain_name_passes(self):
        """Feed input, check the return value."""
        self.assertEqual(self.module_verdict("AUTHOR = " + quoted(CAPITAL + " X") + "\n"), PASSED)

    def test_home_folder_joined_with_the_capitalised_account_is_refused(self):
        """Use a parameterized test for several inputs."""
        contents = ["from pathlib import Path\nP = Path(" + quoted(rooted("Users")) + ") / " + quoted(CAPITAL) + "\n",
                    "import os\nP = os.path.join(" + quoted(rooted("Users")) + ", " + quoted(CAPITAL) + ")\n"]
        verdicts = [self.module_verdict(content) for content in contents]
        self.assertEqual(verdicts, [REFUSED_HOME] * 2)

    def test_escaped_spelling_of_the_account_home_is_refused(self):
        """Use a parameterized test for several inputs."""
        verdicts = [self.assigned(escaped_second_letter(style)) for style in ("unicode", "hex", "octal")]
        self.assertEqual(verdicts, [REFUSED_HOME] * 3)

    def test_root_account_home_is_refused(self):
        """Use a parameterized test for several inputs."""
        verdicts = [self.assigned(path) for path in (rooted("var", "root", "x"), rooted("private", "var", "root", "x"))]
        self.assertEqual(verdicts, [REFUSED_HOME] * 2)

    def test_lower_case_and_doubled_slash_spellings_of_the_account_home_are_refused(self):
        """Use a parameterized test for several inputs."""
        doubled = "//" + "Users" + "//" + ACCOUNT + "//x"
        verdicts = [self.assigned(path) for path in (rooted("users", ACCOUNT, "x"), doubled)]
        self.assertEqual(verdicts, [REFUSED_HOME] * 2)

    def test_lower_case_users_route_without_an_account_passes(self):
        """Use a parameterized test for several inputs."""
        verdicts = [self.assigned(path) for path in (rooted("users", "sample", "x"), rooted("users", "123", "posts"))]
        self.assertEqual(verdicts, [PASSED] * 2)

    def test_bare_account_home_without_a_further_segment_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.assigned(rooted("Users", ACCOUNT)), REFUSED_HOME)


class CommitsIntoNewReposUnderSkippedFolders(AfterCommitCase):
    projects = {REPO: commit_project(), **{name: Project({}, listed=False) for name in NESTED_NAMES},
                OLD_NESTED: Project(commit_project({NOTES: FLAGGED_TEXT}).files)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for form in NESTED_FORMS:
            shutil.rmtree(cls.root / form.made, ignore_errors=True)

    def committed(self, form, files, message):
        def effect():
            self.addCleanup(shutil.rmtree, self.folder(form.made), True)
            (self.folder(form.name) / ".git").mkdir(parents=True)
            self.make_commit(form.name, files, message)
            self.reveal(form.name)
        return effect

    def repository(self, name, project):
        state = super().repository(name, project)
        if name not in NESTED_NAMES:
            return state
        first = self.tip(name)
        return {**state, "revisions": {first.name: first.tree}, "parents": {first.name: []},
                "refs": {"HEAD": first.name, "main": first.name, "refs/heads/main": first.name},
                "messages": {first.name: first.message}, "index": dict(first.tree)}

    def before_the_run(self, form, message):
        self.start(REPO, run=form.name)
        return self.pre(REPO, form.command.format(message=shell_quoted(message)), run=form.name)

    def outcome(self, form, files, message):
        self.start(REPO, run=form.name)
        command = form.command.format(message=shell_quoted(CLEAN_MESSAGE))
        decision, verdict, _ = self.pair(REPO, command, self.committed(form, files, message), run=form.name)
        return decision, verdict, self.stop(REPO, run=form.name)

    def test_clean_commit_message_in_a_new_nested_repo_passes_before_the_run(self):
        """Use a parameterized test for several inputs."""
        decisions = [self.before_the_run(form, CLEAN_MESSAGE) for form in NESTED_FORMS]
        self.assertEqual(decisions, [LET_THROUGH] * len(NESTED_FORMS))

    def test_flagged_message_of_a_new_nested_repo_is_caught_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        outcomes = [self.outcome(form, {NOTES: CLEAN_TEXT}, FLAGGED_MESSAGE) for form in NESTED_FORMS]
        self.assertEqual(outcomes, [(LET_THROUGH, CAUGHT, CAUGHT)] * len(NESTED_FORMS))

    def test_flagged_file_in_a_new_nested_repo_is_caught_after_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        outcomes = [self.outcome(form, {NOTES: FLAGGED_TEXT}, CLEAN_MESSAGE) for form in NESTED_FORMS]
        self.assertEqual(outcomes, [(LET_THROUGH, CAUGHT, CAUGHT)] * len(NESTED_FORMS))

    def test_clean_first_commit_of_a_new_nested_repo_releases_after_the_run(self):
        """Feed input, check the return value."""
        outcomes = [self.outcome(form, {NOTES: CLEAN_TEXT}, CLEAN_MESSAGE) for form in NESTED_FORMS]
        self.assertEqual(outcomes, [(LET_THROUGH, LET_THROUGH, None)] * len(NESTED_FORMS))

    def test_flagged_literal_message_in_a_new_nested_repo_is_refused_before_the_run(self):
        """The test fails when a real bug enters the code it exercises."""
        decisions = [self.before_the_run(form, FLAGGED_MESSAGE) for form in NESTED_FORMS]
        self.assertEqual(decisions, [DENY] * len(NESTED_FORMS))

    def test_clean_commit_over_flagged_history_of_an_existing_nested_repo_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.make_commit(OLD_NESTED, {NOTES: FLAGGED_TEXT + "More.\n"}, FLAGGED_MESSAGE)
        command = "git -C node_modules/old15 commit -m " + shell_quoted(CLEAN_MESSAGE)
        effect = self.committing(OLD_NESTED, {LATER: CLEAN_TEXT}, CLEAN_MESSAGE)
        self.assertEqual(self.run_and_stop(REPO, command, effect), (LET_THROUGH, LET_THROUGH, None))


if __name__ == "__main__":
    unittest.main()
