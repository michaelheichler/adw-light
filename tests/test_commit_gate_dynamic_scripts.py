import unittest

from commit_view_home import CommitViewHome, script


RUN = "bash ./s.sh"
REFERENCE = "reference"


def runs_dynamic(call):
    return {"s.sh": script(call + "\n")}


class DynamicScriptHome(CommitViewHome):
    projects = {
        REFERENCE: {"s.sh": script("bash ./missing-commit.sh\n")},
        "plain_word": runs_dynamic('bash "$DIR/x.sh" commit'),
        "upper_word": runs_dynamic('bash "$DIR/x.sh" COMMIT'),
        "flag": runs_dynamic('bash "$DIR/x.sh" --commit'),
        "flag_value": runs_dynamic('bash "$DIR/x.sh" --message=commit'),
        "hook_name": runs_dynamic('bash "$DIR/x.sh" commit-msg'),
        "spaced_git": runs_dynamic('bash "$DIR/x.sh" "git  commit"'),
        "no_argument": runs_dynamic('bash "$DIR/init.sh"'),
        "other_word": runs_dynamic('bash "$DIR/x.sh" x'),
        "longer_word": runs_dynamic('bash "$DIR/x.sh" committed'),
        "named_script": runs_dynamic('bash "$D/commit.sh" x'),
    }

    def named_refusal(self, name):
        return self.refusal(REFERENCE, RUN), self.refusal(name, RUN)


class CommitWordInDynamicScriptArguments(DynamicScriptHome):
    def test_commit_argument_is_refused_like_a_missing_script_named_commit(self):
        expected, actual = self.named_refusal("plain_word")

        self.assertEqual(expected, actual)

    def test_uppercase_commit_argument_is_refused(self):
        expected, actual = self.named_refusal("upper_word")

        self.assertEqual(expected, actual)

    def test_commit_flag_is_refused(self):
        expected, actual = self.named_refusal("flag")

        self.assertEqual(expected, actual)

    def test_commit_as_a_flag_value_is_refused(self):
        expected, actual = self.named_refusal("flag_value")

        self.assertEqual(expected, actual)

    def test_commit_msg_hook_name_is_refused(self):
        expected, actual = self.named_refusal("hook_name")

        self.assertEqual(expected, actual)

    def test_git_commit_with_two_spaces_is_refused(self):
        expected, actual = self.named_refusal("spaced_git")

        self.assertEqual(expected, actual)


class DynamicScriptWithoutACommitWord(DynamicScriptHome):
    def test_script_without_arguments_passes(self):
        self.assertEqual({}, self.gate("no_argument", RUN))

    def test_script_with_an_unrelated_argument_passes(self):
        self.assertEqual({}, self.gate("other_word", RUN))

    def test_argument_that_only_starts_with_commit_passes(self):
        self.assertEqual({}, self.gate("longer_word", RUN))

    def test_script_whose_own_file_name_holds_commit_passes(self):
        self.assertEqual({}, self.gate("named_script", RUN))


if __name__ == "__main__":
    unittest.main()
