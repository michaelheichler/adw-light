import time
import unittest
from pathlib import Path

from after_commit_home import FLAGGED_TEXT
from after_run_home import CAUGHT, CODEX, LET_THROUGH, OLD_TEST, AfterRunCase, Project, project_files
from fake_repo_home import PLAIN_MODULE, SIZE_TEST
from lib import git_port
from lib.after_run import Hook
from lib.after_run_snapshot import project_locations
from lib.named_directories import named_directories


REPO = "repo"
OTHER = "O"
LOOSE = "loose"
MARKED = "marked"
NOT_NAMING = {
    "plain_word": "echo hello world",
    "sed_expression": "sed -i -e 's/a/b/' notes.txt",
}


def naming_commands(other):
    return {
        "word_prefix": "cp src/a.py " + other + "/tests/test_$(cat n).py",
        "loop_word": "for d in " + other + '; do cp README.md "$d/tests/$(cat n)"; done',
        "xargs_braces": "xargs -I{} cp src/a.py " + other + "/tests/{}.py",
        "directory_flag": "tar -xf x.tar --directory=" + other,
        "parent_relative": "cp src/a.py ../" + OTHER + "/tests/test_$(cat n).py",
    }


def blocking_forms(other):
    return {
        "word_prefix": "cp src/a.py " + other + "/tests/test_$(cat n).py",
        "cd_then_copy": "cd " + other + ' && cp README.md "$(cat n)"',
        "tar_directory": 'tar -xf "$(cat n)" -C ' + other,
        "subshell_cd": "(cd " + other + '/tests && cp ../README.md "$(cat n)")',
        "loop_word": "for d in " + other + '; do cp README.md "$d/tests/$(cat n)"; done',
        "prose_copy": "cp README.md " + other + "/docs/$(cat n).md",
        "parent_relative": "cp src/a.py ../" + OTHER + "/tests/test_$(cat n).py",
        "pushd_eval": "pushd " + other + ' && eval "$CMD"',
        "stash_pop": "git -C " + other + " stash pop",
        "find_exec_sed": "find " + other + " -name '*.py' -exec sed -i s/a/b/ {} \\;",
        "xargs_braces": "xargs -I{} cp src/a.py " + other + "/tests/{}.py",
    }


EDITS_OLD_TEST = {"stash_pop", "find_exec_sed"}


class CountingView:
    def __init__(self, inner, folders):
        self.inner, self.folders = inner, folders

    def __getattr__(self, name):
        method = getattr(self.inner, name)

        def counted(*args, **options):
            self.folders.extend(arg for arg in args if isinstance(arg, Path))
            return method(*args, **options)

        return counted


class NamedProjectCase(AfterRunCase):
    projects = {
        REPO: Project(project_files({"src/a.py": PLAIN_MODULE})),
        OTHER: Project(project_files({"docs/guide.md": "Guide.\n"})),
        LOOSE: Project({"notes.txt": "Notes.\n"}, git=False),
        MARKED: Project({"pyproject.toml": "", "notes.txt": "Notes.\n"}, git=False),
    }

    def other(self):
        return str(self.folder(OTHER))

    def names_other(self, command):
        return any(path.is_relative_to(self.folder(OTHER)) for path in named_directories(command, self.folder(REPO)))

    def names_outside_cwd(self, command):
        return any(not path.is_relative_to(self.folder(REPO)) for path in named_directories(command, self.folder(REPO)))

    def effect(self, kind):
        if kind in EDITS_OLD_TEST:
            return lambda: self.change(OTHER, OLD_TEST, SIZE_TEST + "X = " + repr(kind) + "\n")
        if kind == "prose_copy":
            return lambda: self.change(OTHER, "docs/" + kind + ".md", FLAGGED_TEXT)
        return lambda: self.change(OTHER, "tests/test_" + kind + ".py", SIZE_TEST.replace("test_size", "test_" + kind))

    def claude_steps(self, kind, command):
        self.start(REPO, run=kind)
        _, batched, _ = self.pair(REPO, command, self.effect(kind), run=kind)
        return batched, self.stop(REPO, run=kind)

    def codex_steps(self, kind, command):
        self.start(REPO, CODEX, run=kind)
        _, posted, _ = self.pair(REPO, command, self.effect(kind), host=CODEX, run=kind)
        return posted, self.stop(REPO, CODEX, run=kind)


class CommandWordsNameOtherProjects(NamedProjectCase):
    def test_prefixes_loop_words_xargs_braces_directory_flags_and_parent_paths_name_the_other_project(self):
        """Use a parameterized test for several inputs."""
        commands = naming_commands(self.other())
        named = {kind: self.names_other(command) for kind, command in commands.items()}
        self.assertEqual(named, dict.fromkeys(commands, True))

    def test_a_plain_word_and_a_sed_expression_name_no_directory_outside_the_cwd(self):
        """Use a parameterized test for several inputs."""
        named = {kind: self.names_outside_cwd(command) for kind, command in NOT_NAMING.items()}
        self.assertEqual(named, dict.fromkeys(NOT_NAMING, False))


class UnmarkedDirectoriesCostNoGitCall(NamedProjectCase):
    def asked_about(self, name):
        asked = []
        command = "cp src/a.py " + str(self.folder(name)) + "/copy.py"
        hook = Hook({"tool_input": {"command": command}}, {}, time.monotonic(), "PreToolUse")
        with git_port.using_view(CountingView(self.view(), asked)):
            project_locations(hook, self.folder(REPO))
        return any(folder.is_relative_to(self.folder(name)) for folder in asked)

    def test_only_a_named_directory_under_a_project_marker_is_asked_about_in_git(self):
        """Feed input, check the return value."""
        asked = {name: self.asked_about(name) for name in (LOOSE, MARKED)}
        self.assertEqual(asked, {LOOSE: False, MARKED: True})


class WritesIntoNamedProjectsBlockAfterTheRun(NamedProjectCase):
    def test_claude_batch_and_stop_block_writes_into_a_project_the_command_names(self):
        """The test fails when a real bug enters the code it exercises."""
        forms = blocking_forms(self.other())
        verdicts = {kind: self.claude_steps(kind, command) for kind, command in forms.items()}
        self.assertEqual(verdicts, dict.fromkeys(forms, (CAUGHT, CAUGHT)))

    def test_codex_post_tool_use_and_stop_block_writes_into_a_project_the_command_names(self):
        """The test fails when a real bug enters the code it exercises."""
        forms = blocking_forms(self.other())
        verdicts = {kind: self.codex_steps(kind, command) for kind, command in forms.items()}
        self.assertEqual(verdicts, dict.fromkeys(forms, (CAUGHT, CAUGHT)))


if __name__ == "__main__":
    unittest.main()
