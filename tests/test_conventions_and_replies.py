import hashlib
import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView


NO_REPOSITORY = FakeGitView({"roots": {}})

ANDROIDAPS_RULES = Path(__file__).resolve().parent / "fixtures" / "androidaps_rules.md"
PLAIN_KDOC = "    /**\n     * Reads the stored value from disk and returns it to the caller.\n     */"
NARRATING_LINE = "    // Return the value here.\n"
UNRESOLVED_KDOC = "    /**\n     * Reads the value that [StoreCache] keeps for the caller.\n     */"
BACKTICKED_KDOC = "    /**\n     * Reads the value that `LoopPlugin.applySMBRequest` keeps for the caller.\n     */"
DIGIT_RANGE_REPLY = "The build ran.\n\nIt took 3\u20135 minutes."
COLON_NOTE = "The cache grows here: it never shrinks. The disk fills: the job stops. The log says: retry. The fix is plain: cap it."
PLAIN_KOTLIN = "package app\n\nclass X {\n    fun load(): Int = 1\n}\n"
ADW_LITE_RULES = (
    "- No new comments or docstrings in Python source. Intent lives in names and tests.\n"
    "- The engine's own comment family enforces this.\n"
)
WHAT_DOCSTRING = 'def cache_size():\n    """Return the size of the cache."""\n    return 20000\n'
BRACKETED_BACKTICK_KDOC = "    /**\n     * Reads the value that `[StoreCache]` keeps for the caller.\n     */"
DOC_TEXT = "     * "
DOCSTRING_QUOTES = '"""'
REPO_KT = "Repo.kt"
CALC_KT = "Calc.kt"
CALC_PY = "calc.py"
CACHE_PY = "cache.py"
TODO_MD = "todo.md"
LIST_MD = "list.md"


def sentences(count):
    return ["The cache holds item %d for the build." % number for number in range(1, count + 1)]


def kotlin_with_doc(doc):
    return "package app\n\nclass Repo {\n" + doc + "\n    fun load(): Int = 1\n}\n"


def line_of(text, fragment):
    return text[:text.index(fragment)].count("\n") + 1


class ConventionHarness(unittest.TestCase):
    rules = ""

    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name).resolve()
        cls.state = cls.root / ".adw-state"
        cls.project = cls.root / "project"
        cls.project.mkdir()
        (cls.root / "home").mkdir()
        (cls.project / "CLAUDE.md").write_text(cls.rules, encoding="utf-8")
        cls.git = FakeGitView({"roots": {str(cls.project): {"disk": {"CLAUDE.md": cls.rules}}}})
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home"), "PATH": os.environ.get("PATH", "")}, clear=True)
        environment.start()
        cls.addClassCleanup(environment.stop)

    def hook(self, event, **payload):
        body = {"cwd": str(self.project), "session_id": "s" + hashlib.sha256(self.id().encode()).hexdigest()[:20],
                "hook_event_name": event, **payload}
        return run_hook(event, body, state_dir=self.state, git_view=self.git)

    def write_attempt(self, name, content):
        self.hook("SessionStart", source="startup")
        return self.hook("PreToolUse", tool_name="Write",
                         tool_input={"file_path": str(self.project / name), "content": content})

    def deny_reason(self, response):
        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])
        return response["hookSpecificOutput"]["permissionDecisionReason"]


class InProcessHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name)
        cls.state = cls.root / ".adw-state"
        (cls.root / "home").mkdir()
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)

    def hook(self, event, **payload):
        base = {"cwd": str(self.root), "session_id": self._testMethodName, "hook_event_name": event}
        return run_hook(event, {**base, **payload}, state_dir=self.state, git_view=NO_REPOSITORY)

    def reply_scan_rows(self):
        ledger = self.state / "ledger" / "ledger.jsonl"
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]
        mine = (row for row in rows if row["session_id"] == self._testMethodName)
        return [(row["event"], row["rule"], row["outcome"]) for row in mine if row["hook"] == "reply_scan"]

    def write_attempt(self, name, content):
        self.hook("SessionStart", source="startup")
        return self.hook("PreToolUse", tool_name="Write",
                         tool_input={"file_path": str(self.root / name), "content": content})

    def deny_reason(self, response):
        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])
        return response["hookSpecificOutput"]["permissionDecisionReason"]

    def reply(self, message, **extra):
        return self.hook("Stop", last_assistant_message=message, **extra)


class AndroidApsConventionMode(ConventionHarness):
    rules = ANDROIDAPS_RULES.read_text(encoding="utf-8")

    def test_plain_english_kdoc_on_a_kotlin_function_passes(self):
        self.assertEqual({}, self.write_attempt("Repo.kt", kotlin_with_doc(PLAIN_KDOC)))

    def test_narrating_line_comment_still_blocks_as_a_what_comment(self):
        content = kotlin_with_doc(NARRATING_LINE + PLAIN_KDOC)

        reason = self.deny_reason(self.write_attempt(REPO_KT, content))

        self.assertRegex(reason, rf"{re.escape(REPO_KT)}:{line_of(content, NARRATING_LINE)} [^\n]*\(what_comment\)")

    def test_kdoc_link_to_a_name_missing_from_the_file_blocks(self):
        content = kotlin_with_doc(UNRESOLVED_KDOC)

        reason = self.deny_reason(self.write_attempt(REPO_KT, content))

        self.assertRegex(reason, rf"{re.escape(REPO_KT)}:{line_of(content, DOC_TEXT)} [^\n]*\(unresolved_doc_reference\)")

    def test_kdoc_naming_an_external_symbol_in_backticks_passes(self):
        self.assertEqual({}, self.write_attempt("Repo.kt", kotlin_with_doc(BACKTICKED_KDOC)))

    def test_bracket_link_inside_backticks_is_not_checked_as_a_kdoc_link(self):
        self.assertEqual({}, self.write_attempt("Repo.kt", kotlin_with_doc(BRACKETED_BACKTICK_KDOC)))

    def test_python_what_docstring_still_blocks_because_the_rules_cover_kdoc_only(self):
        reason = self.deny_reason(self.write_attempt(CALC_PY, WHAT_DOCSTRING))

        self.assertRegex(reason, rf"{re.escape(CALC_PY)}:{line_of(WHAT_DOCSTRING, DOCSTRING_QUOTES)} [^\n]*\(what_docstring\)")


class WhyOnlyDocCells:
    def test_python_what_docstring_blocks(self):
        reason = self.deny_reason(self.write_attempt(CALC_PY, WHAT_DOCSTRING))

        self.assertRegex(reason, rf"{re.escape(CALC_PY)}:{line_of(WHAT_DOCSTRING, DOCSTRING_QUOTES)} [^\n]*\(what_docstring\)")

    def test_kotlin_what_kdoc_blocks(self):
        content = kotlin_with_doc(PLAIN_KDOC)

        reason = self.deny_reason(self.write_attempt(CALC_KT, content))

        self.assertRegex(reason, rf"{re.escape(CALC_KT)}:{line_of(content, DOC_TEXT)} [^\n]*\(what_comment\)")


class WhyOnlyUnderAdwLightRules(WhyOnlyDocCells, ConventionHarness):
    rules = ADW_LITE_RULES


class WhyOnlyWhenRulesBanKdoc(WhyOnlyDocCells, ConventionHarness):
    rules = "No new comments or KDoc in Kotlin source.\n"


class WhyOnlyWhenRulesAskForFewerComments(WhyOnlyDocCells, ConventionHarness):
    rules = "Avoid docstrings. Keep comments to a minimum.\n"


class WhyOnlyWhenRulesMentionReviewComments(WhyOnlyDocCells, ConventionHarness):
    rules = "Reply to review comments in the PR thread.\n"


class WhyOnlyWithoutConventionFiles(InProcessHarness):
    def test_the_same_plain_english_kdoc_blocks_under_why_only(self):
        content = kotlin_with_doc(PLAIN_KDOC)

        reason = self.deny_reason(self.write_attempt(REPO_KT, content))

        self.assertRegex(reason, rf"{re.escape(REPO_KT)}:{line_of(content, DOC_TEXT)} [^\n]*\(what_comment\)")

    def test_python_what_docstring_blocks_under_why_only(self):
        reason = self.deny_reason(self.write_attempt(CALC_PY, WHAT_DOCSTRING))

        self.assertRegex(reason, rf"{re.escape(CALC_PY)}:{line_of(WHAT_DOCSTRING, DOCSTRING_QUOTES)} [^\n]*\(what_docstring\)")


class PythonDocstringBanInConventionFile(ConventionHarness):
    rules = "No new comments or docstrings in Python source.\n"

    def test_new_python_docstring_blocks_when_claude_md_bans_docstrings(self):
        content = 'def cache_size():\n    """Return the size of the cache."""\n    return 20000\n'

        reason = self.deny_reason(self.write_attempt(CACHE_PY, content))

        self.assertRegex(reason, rf"{re.escape(CACHE_PY)}:{line_of(content, DOCSTRING_QUOTES)} [^\n]*\(added_docstring\)")


class ReplyScanMasking(InProcessHarness):
    def test_german_quoted_phrase_with_a_colon_passes(self):
        message = "The phone stays in a sleeping CPU state. Michael wrote \u201ea sleeping CPU: with the screen off\u201c in the log."

        self.assertEqual({}, self.reply(message))

    def test_blockquote_line_with_a_mid_sentence_colon_passes(self):
        message = "The log shows one line.\n\n> The build stopped here: the disk was full.\n\nThe build failed."

        self.assertEqual({}, self.reply(message))

    def test_short_quote_with_banned_words_between_prose_lines_passes(self):
        message = "The log shows one line.\n\n> We utilize the new cache: it is robust and seamless.\n\nThe build failed."

        self.assertEqual({}, self.reply(message))

    def test_reply_made_only_of_blockquote_lines_is_logged_as_would_block(self):
        message = "> We utilize the new cache: it is robust. The disk fills: the job stops.\n> The log says: retry. The fix is plain: cap the cache."

        stop = self.reply(message)

        self.assertEqual({}, stop)
        self.assertIn(("Stop", "prose_colon", "would_block"), self.reply_scan_rows())

    def test_quoted_line_with_a_pipe_right_after_a_table_stays_a_quote(self):
        message = ("Two files changed.\n\n| File | Line |\n|---|---|\n| `a.py` | `12` |\n"
                   "> We stopped here | We utilize the cache: it is robust.\n\nThe build passed.")

        self.assertEqual({}, self.reply(message))

    def test_en_dash_between_digits_is_logged_once_without_blocking_the_reply(self):
        stop = self.reply(DIGIT_RANGE_REPLY)

        self.assertEqual({}, stop)
        self.assertEqual([("Stop", "banned_dash", "would_block")], self.reply_scan_rows())

    def test_same_reply_passes_when_stop_hook_is_already_active(self):
        self.assertEqual({}, self.reply(DIGIT_RANGE_REPLY, stop_hook_active=True))


class ReplyTableCells(InProcessHarness):
    def test_row_of_backticked_file_line_references_passes(self):
        message = "The change touches two files.\n\n| File | Line |\n|---|---|\n| `a.py:12` | `b.py:40` |\n"

        self.assertEqual({}, self.reply(message))

    def test_prose_cell_with_more_sentence_colons_than_the_limit_is_logged_as_would_block(self):
        message = "The change touches two files.\n\n| File | Note |\n|---|---|\n| `a.py:12` | " + COLON_NOTE + " |\n"

        stop = self.reply(message)

        self.assertEqual({}, stop)
        self.assertIn(("Stop", "prose_colon", "would_block"), self.reply_scan_rows())

    def test_bare_path_cell_next_to_a_colon_sentence_is_logged_as_would_block(self):
        message = ("The change touches two files.\n\n| File | Note |\n|---|---|\n"
                   "| engine/lib/markup.py | " + COLON_NOTE + " |\n")

        stop = self.reply(message)

        self.assertEqual({}, stop)
        self.assertIn(("Stop", "prose_colon", "would_block"), self.reply_scan_rows())

    def test_bare_path_cell_holding_banned_words_passes(self):
        message = "The change touches two files.\n\n| File | Line |\n|---|---|\n| src/utilize/robust.py | `markup.py:443` |\n"

        self.assertEqual({}, self.reply(message))


class TaskCheckboxesAndListCap(InProcessHarness):
    def observed_list_context(self, content):
        output = self.write_attempt(TODO_MD, content)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", output)
        return output["additionalContext"]

    def test_twelve_task_checkboxes_in_one_run_raise_an_observed_oversized_list_row(self):
        content = "# Tasks\n\n" + "".join("- [ ] %s\n" % sentence for sentence in sentences(12))
        first_item = line_of(content, sentences(1)[0])

        self.assertRegex(self.observed_list_context(content), rf"{re.escape(TODO_MD)}:{first_item} [^\n]*\(oversized_list\)")

    def test_thirty_checked_task_checkboxes_raise_an_observed_oversized_list_row(self):
        content = "# Tasks\n\n" + "".join("- [x] %s\n" % sentence for sentence in sentences(30))
        first_item = line_of(content, sentences(1)[0])

        self.assertRegex(self.observed_list_context(content), rf"{re.escape(TODO_MD)}:{first_item} [^\n]*\(oversized_list\)")

    def test_five_checkboxes_then_five_plain_items_count_as_two_short_runs(self):
        items = sentences(10)
        content = "# Tasks\n\n" + "".join("- [ ] %s\n" % s for s in items[:5]) + "".join("- %s\n" % s for s in items[5:])

        self.assertEqual({}, self.write_attempt("todo.md", content))

    def test_five_numbered_checkboxes_then_five_numbered_items_count_as_two_short_runs(self):
        items = sentences(10)
        content = ("# Tasks\n\n" + "".join("%d. [ ] %s\n" % (n, s) for n, s in enumerate(items[:5], 1))
                   + "".join("%d. %s\n" % (n, s) for n, s in enumerate(items[5:], 6)))

        self.assertEqual({}, self.write_attempt("todo.md", content))

    def test_twelve_plain_list_items_raise_an_observed_oversized_list_row(self):
        content = "# Items\n\n" + "".join("- %s\n" % sentence for sentence in sentences(12))

        output = self.write_attempt(LIST_MD, content)["hookSpecificOutput"]

        self.assertNotIn("permissionDecision", output)
        self.assertRegex(output["additionalContext"], rf"{re.escape(LIST_MD)}:{line_of(content, sentences(1)[0])} [^\n]*\(oversized_list\)")


class CopyLoopTargets(InProcessHarness):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.root / "src").mkdir()
        (cls.root / "tests").mkdir()
        (cls.root / "pyproject.toml").write_text("[project]\nname = \"fixture\"\n", encoding="utf-8")
        (cls.root / "src" / "A.kt").write_text(PLAIN_KOTLIN, encoding="utf-8")
        (cls.root / "src" / "B.kt").write_text(PLAIN_KOTLIN, encoding="utf-8")

    def test_literal_cp_loop_into_tests_directory_lists_the_directory_once(self):
        self.hook("SessionStart", source="startup")

        response = self.hook("PreToolUse", tool_name="Bash",
                             tool_input={"command": "for f in A.kt B.kt; do cp src/$f tests/; done"})
        targets = re.findall(r"^\d+\. (\S+):1 ", self.deny_reason(response), re.M)

        self.assertEqual(["tests", "tests/A.kt", "tests/B.kt"], sorted(targets))


class SteRulesInReplies(InProcessHarness):
    def test_reply_with_only_ste_observe_hits_does_not_block(self):
        message = ("This parser is the fastest.\n\nWe spin up the job before the run.\n\n"
                   "The job has received the report.\n\nCheck the cache.\n\nVerify the result.")

        self.assertEqual({}, self.reply(message))

    def test_reply_with_a_nominalization_is_logged_as_would_block(self):
        stop = self.reply("This parser is the fastest.\n\nThe job performs an analysis of the log.")

        self.assertEqual({}, stop)
        self.assertEqual([("Stop", "ste_nominalization", "would_block")], self.reply_scan_rows())


if __name__ == "__main__":
    unittest.main()
