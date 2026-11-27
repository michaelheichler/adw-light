from fake_repo_home import PASSED, README, SIZE_TEST, FakeRepoCase, Repo
import shlex
import unittest


FEATURE_TEST = {"tests/test_feature.py": SIZE_TEST}
TRACKED_TEST = {"tests/test_a.py": SIZE_TEST}
PLAIN_CHANGE = {"notes.md": "More.\n"}
SETTINGS = "settings"
PROJECT = "project"
SCRATCH = "scratch"
TRACKED = "tracked"
FEATURE = "feature"
HEREDOC_PRINT = "python3 - <<'EOF'\nimport json\nwith open('settings.json') as f:\n    print(json.load(f))\nEOF"
HEREDOC_STDOUT = "python3 - <<'EOF'\nimport json, sys\njson.dump(json.load(open('settings.json')), sys.stdout, indent=2)\nEOF"


def topic_repo(main_files, topic_files):
    return Repo({**README, **main_files}, branches={"topic": [topic_files]})


class SettingsProject(FakeRepoCase):
    projects = {SETTINGS: Repo({**README, "settings.json": "{}"})}

    def python(self, command):
        return self.bash(SETTINGS, command)


class InlineReadsOfASettingsFile(SettingsProject):
    def test_one_liner_that_prints_the_file_passes(self):
        self.assertEqual(self.python("""python3 -c "print(open('settings.json').read())\""""), PASSED)

    def test_one_liner_that_dumps_the_parsed_file_to_stdout_passes(self):
        command = """python3 -c "import json, sys; json.dump(json.load(open('settings.json')), sys.stdout)\""""
        self.assertEqual(self.python(command), PASSED)

    def test_one_liner_that_copies_the_file_text_to_stdout_passes(self):
        command = """python3 -c "import sys; sys.stdout.write(open('settings.json').read())\""""
        self.assertEqual(self.python(command), PASSED)

    def test_one_liner_that_only_imports_json_and_pathlib_passes(self):
        self.assertEqual(self.python('python3 -c "import json, pathlib"'), PASSED)

    def test_pathlib_one_liner_that_prints_the_parsed_file_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        command = """python3 -c "import json, pathlib; print(json.loads(pathlib.Path('settings.json').read_text()))\""""
        self.assertEqual(self.python(command), PASSED)


class HeredocAccessToASettingsFile(SettingsProject):
    def test_heredoc_that_prints_the_parsed_file_passes(self):
        self.assertEqual(self.python(HEREDOC_PRINT), PASSED)

    def test_heredoc_that_dumps_the_parsed_file_to_stdout_passes(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.python(HEREDOC_STDOUT), PASSED)


class BranchHome(FakeRepoCase):
    projects = {
        PROJECT: Repo(README),
        SCRATCH: topic_repo({}, PLAIN_CHANGE),
        TRACKED: topic_repo(TRACKED_TEST, PLAIN_CHANGE),
        FEATURE: topic_repo({}, FEATURE_TEST),
        PROJECT + "/vendor/up": topic_repo({}, FEATURE_TEST),
    }

    def in_scratch_repo(self, command):
        return self.bash(PROJECT, f"cd {shlex.quote(str(self.root / SCRATCH))} && {command}")

    def in_nested_repo(self, command):
        return self.bash(PROJECT, f"cd vendor/up && {command}")


class BranchMovesInAScratchRepoWithoutTests(BranchHome):
    def test_switch_to_a_topic_branch_passes(self):
        self.assertEqual(self.in_scratch_repo("git switch topic"), PASSED)

    def test_switch_that_creates_a_branch_from_topic_passes(self):
        self.assertEqual(self.in_scratch_repo("git switch -c new topic"), PASSED)

    def test_squash_merge_of_a_topic_branch_passes(self):
        self.assertEqual(self.in_scratch_repo("git merge --squash topic"), PASSED)


class BranchMovesThatCanChangeTests(BranchHome):
    def test_squash_merge_of_a_branch_that_adds_a_test_passes(self):
        self.assertEqual(self.bash(FEATURE, "git merge --squash topic"), PASSED)

    def test_squash_merge_in_a_nested_repo_whose_branch_adds_a_test_passes(self):
        self.assertEqual(self.in_nested_repo("git merge --squash topic"), PASSED)


if __name__ == "__main__":
    unittest.main()
