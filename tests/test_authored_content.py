import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "engine"))

from adw_light import run_hook
from lib.git_port import FakeGitView


FLAGGED_PROSE = "We utilize this tool for the build.\n"
FLAGGED_WORD = "utilize"
DASHED_SENTENCE = "The cache warms up \u2014 then the build starts."
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
KDOC_SOURCE = """package app

/**
 * Loads the repository from disk.
 * We utilize the cache for the build.
 * The caller owns the handle.
 */
class Repo {
    fun load(): Int = 1
}
"""
KDOC_TEST_MARKER_SOURCE = """package app

/**
 * Repository for the app.
 *
 *   @Test
 */
class AppRepository {
    fun load(): Int = 1
}
"""
PLAIN_KOTLIN_SOURCE = """package app

class AppRepository {
    fun load(): Int = 1
}
"""
KOTLIN_TEST_SOURCE = """package app

import org.junit.Assert.assertEquals
import org.junit.Test

class AppRepositoryChecks {
    @Test
    fun loadReturnsOne() {
        assertEquals(1, AppRepository().load())
    }
}
"""
FILE_LEVEL_LINE = 1
PROJECT_MARKER = {"pyproject.toml": "[project]\nname = \"fixture\"\n"}
GRADLE_WRAPPER = """#!/bin/sh
i=0
while [ "$i" -lt 1100 ]; do
  echo "> Task :app:compileDebugKotlin$i UP-TO-DATE"
  i=$((i + 1))
done
"""


def line_of(text, fragment):
    return text[:text.index(fragment)].count("\n") + 1


def docx(document_xml):
    return {"[Content_Types].xml": "<Types/>", "word/document.xml": document_xml}


DASHED_DOCX = docx(f'<w:document xmlns:w="{WORD_NAMESPACE}"><w:body><w:p><w:r><w:t>{DASHED_SENTENCE}</w:t></w:r></w:p></w:body></w:document>')
KDOC_OLD = " * The caller owns the handle."
KDOC_NEW = " * This function returns the value."
FOLDERS = {
    "kdoc": {"Repo.kt": KDOC_SOURCE},
    "kotlin_write": {},
    "kotlin_edit": {"AppRepository.kt": PLAIN_KOTLIN_SOURCE},
    "junit": PROJECT_MARKER,
    "copy_loop": {"src/A.kt": PLAIN_KOTLIN_SOURCE, "src/B.kt": PLAIN_KOTLIN_SOURCE, "tests/.keep": "", **PROJECT_MARKER},
    "copy_test": {"incoming/foo_test.py": "def test_cache_size():\n    assert 20000 == 2 * 10000\n", **PROJECT_MARKER},
    "awk": {},
    "gradle": {"gradlew": GRADLE_WRAPPER},
    "timeline": {},
    "docx": {},
    "broken_docx": {},
    "notes": {},
    "batch": {"first-turn.md": FLAGGED_PROSE},
}
COMMITTED = {"kdoc": {"Repo.kt": KDOC_SOURCE}, "gradle": {"gradlew": GRADLE_WRAPPER}}


def build(folder, files):
    folder.mkdir()
    for relative, content in files.items():
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_text(content, encoding="utf-8")
    if "gradlew" in files:
        (folder / "gradlew").chmod(0o755)


def tracked(files):
    return {"index": files, "disk": files, "revisions": {"base": files}, "refs": {"HEAD": "base"}}


class AuthoredContentRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.base = Path(workspace.name).resolve()
        cls.state = cls.base / ".adw-state"
        (cls.base / "home").mkdir()
        for name, files in FOLDERS.items():
            build(cls.base / name, files)
        with zipfile.ZipFile(cls.base / "docx" / "report.docx", "w") as archive:
            for member, text in DASHED_DOCX.items():
                archive.writestr(member, text)
        (cls.base / "broken_docx" / "broken.docx").write_bytes(b"PK\x03\x04" + b"not a zip archive" * 20)
        cls.git = FakeGitView({"roots": {str(cls.base / name): tracked(files) for name, files in COMMITTED.items()}})
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.base / "home"), "PATH": os.environ.get("PATH", "")}, clear=True)
        environment.start()
        cls.addClassCleanup(environment.stop)

    def setUp(self):
        self.root = self.base / FOLDERS_BY_TEST[self._testMethodName]

    def hook(self, event, **payload):
        body = {"cwd": str(self.root), "session_id": "s" + hashlib.sha256(self.id().encode()).hexdigest()[:20],
                "hook_event_name": event, **payload}
        return run_hook(event, body, state_dir=self.state, git_view=self.git)

    def start_session(self):
        self.hook("SessionStart", source="startup")

    def pre_tool(self, tool_name, **tool_input):
        return self.hook("PreToolUse", tool_name=tool_name, tool_input=tool_input)

    def deny_reason(self, response):
        return response.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

    def post_tool(self, tool_name, **tool_input):
        return self.hook("PostToolUse", tool_name=tool_name, tool_use_id=f"{tool_name}-1",
                         tool_input=tool_input, tool_response={"stdout": "", "stderr": ""})

    def stop(self):
        return self.hook("Stop", last_assistant_message="Done.")

    def run_in_root(self, command):
        subprocess.run(command, shell=True, cwd=self.root, check=True)

    def test_kdoc_edit_blocks_only_the_changed_line_and_not_the_inherited_block(self):
        source = self.root / "Repo.kt"
        self.start_session()
        source.write_text(KDOC_SOURCE.replace(KDOC_OLD, KDOC_NEW), encoding="utf-8")
        self.post_tool("Edit", file_path=str(source), old_string=KDOC_OLD, new_string=KDOC_NEW)

        changed_line = line_of(source.read_text(encoding="utf-8"), KDOC_NEW)
        block_start = line_of(KDOC_SOURCE, "/**")

        reason = self.stop()["reason"]

        self.assertIn(f"{source.name}:{changed_line} ", reason)
        self.assertIn("(what_comment)", reason)
        self.assertNotIn(f"{source.name}:{block_start} ", reason)
        self.assertNotIn("prose_comment_block", reason)

    def test_kotlin_source_with_test_tag_in_kdoc_is_not_gated_as_a_test_on_write(self):
        self.start_session()

        response = self.pre_tool("Write", file_path=str(self.root / "AppRepository.kt"), content=KDOC_TEST_MARKER_SOURCE)

        self.assertNotIn("test_write_gate", self.deny_reason(response))

    def test_kotlin_source_with_test_tag_in_kdoc_is_not_gated_as_a_test_on_edit(self):
        source = self.root / "AppRepository.kt"
        self.start_session()

        response = self.pre_tool("Edit", file_path=str(source), old_string="class AppRepository {",
                                 new_string="/**\n *   @Test\n */\nclass AppRepository {")

        self.assertNotIn("test_write_gate", self.deny_reason(response))

    def test_real_kotlin_junit_test_is_gated_by_its_content(self):
        self.start_session()

        target = self.root / "AppRepositoryChecks.kt"

        response = self.pre_tool("Write", file_path=str(target), content=KOTLIN_TEST_SOURCE)

        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])
        self.assertIn(f"{target.name}:{FILE_LEVEL_LINE} ", self.deny_reason(response))
        self.assertIn("(test_write_gate)", self.deny_reason(response))

    def test_literal_cp_loop_into_an_existing_directory_yields_one_target_per_file(self):
        first, second = "A.kt", "B.kt"
        self.start_session()

        reason = self.deny_reason(self.pre_tool("Bash", command=f"for f in {first} {second}; do cp src/$f tests/$f; done"))

        self.assertIn(f"tests/{first}:{FILE_LEVEL_LINE} ", reason)
        self.assertIn(f"tests/{second}:{FILE_LEVEL_LINE} ", reason)
        self.assertEqual(1, reason.count(f"{first}:{FILE_LEVEL_LINE} "))
        self.assertEqual(1, reason.count(f"{second}:{FILE_LEVEL_LINE} "))
        self.assertNotIn(f"{first}/{first}", reason)

    def test_copying_a_python_test_into_a_fresh_directory_is_gated(self):
        source = self.root / "incoming" / "foo_test.py"
        self.start_session()

        response = self.pre_tool("Bash", command=f"mkdir -p pkg && cp {source} pkg/")

        self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])
        self.assertIn(f"pkg/{source.name}:{FILE_LEVEL_LINE} ", self.deny_reason(response))
        self.assertIn("(test_write_gate)", self.deny_reason(response))

    def test_prose_redirected_by_awk_is_flagged_and_blocks_stop(self):
        output = "notes.md"
        command = "awk 'BEGIN { print \"%s\" }' > %s" % (DASHED_SENTENCE, output)
        self.start_session()
        self.run_in_root(command)
        self.post_tool("Bash", command=command)
        dash_line = line_of((self.root / output).read_text(encoding="utf-8"), "\u2014")

        stop = self.stop()

        self.assertEqual("block", stop["decision"])
        self.assertIn(f"{output}:{dash_line} ", stop["reason"])
        self.assertIn("(banned_dash)", stop["reason"])

    def test_gradle_log_from_a_pristine_wrapper_draws_no_length_finding_and_no_stop_block(self):
        self.start_session()
        command = "./gradlew build > gradle-log"
        self.run_in_root(command)

        post = self.post_tool("Bash", command=command)
        stop = self.stop()

        self.assertEqual(1100, len((self.root / "gradle-log").read_text(encoding="utf-8").splitlines()))
        self.assertNotIn("file_too_long", json.dumps(post))
        self.assertNotIn("decision", stop)

    def test_timestamp_prefixed_prose_written_by_the_agent_gets_prose_findings(self):
        self.start_session()
        content = "2026-11-19 10:00:01 " + FLAGGED_PROSE
        target = self.root / "timeline.txt"

        response = self.pre_tool("Write", file_path=str(target), content=content)

        self.assertIn(f"{target.name}:{line_of(content, FLAGGED_WORD)} ", self.deny_reason(response))
        self.assertIn("(utilize)", self.deny_reason(response))

    def test_docx_with_an_em_dash_in_its_document_body_blocks_stop(self):
        self.start_session()
        self.post_tool("Write", file_path=str(self.root / "report.docx"), content="")

        stop = self.stop()

        self.assertEqual("block", stop["decision"])
        self.assertIn("report.docx:1 ", stop["reason"])
        self.assertIn("(banned_dash)", stop["reason"])

    def test_malformed_docx_never_blocks_stop(self):
        self.start_session()
        self.post_tool("Write", file_path=str(self.root / "broken.docx"), content="")

        stop = self.stop()

        self.assertNotIn("decision", stop)

    def test_content_guessed_prose_file_is_allowed_with_its_findings_noted(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.start_session()

        response = self.pre_tool("Write", file_path=str(self.root / "NOTES"), content=FLAGGED_PROSE)

        self.assertNotEqual("deny", response["hookSpecificOutput"].get("permissionDecision"))
        self.assertIn(f"NOTES:{line_of(FLAGGED_PROSE, FLAGGED_WORD)} ", response["hookSpecificOutput"]["additionalContext"])
        self.assertIn("(utilize)", response["hookSpecificOutput"]["additionalContext"])

    def test_post_tool_batch_scans_written_files_in_the_first_turn_before_any_stop(self):
        path = self.root / "first-turn.md"

        batch = self.hook("PostToolBatch", tool_calls=[{
            "tool_name": "Write", "tool_use_id": "batch-write-1",
            "tool_input": {"file_path": str(path), "content": FLAGGED_PROSE}, "tool_response": {},
        }])

        self.assertNotIn("decision", batch)
        self.assertIn(f"{path.name}:{line_of(FLAGGED_PROSE, FLAGGED_WORD)} ", batch["hookSpecificOutput"]["additionalContext"])
        self.assertIn("(utilize)", batch["hookSpecificOutput"]["additionalContext"])


FOLDERS_BY_TEST = {
    "test_kdoc_edit_blocks_only_the_changed_line_and_not_the_inherited_block": "kdoc",
    "test_kotlin_source_with_test_tag_in_kdoc_is_not_gated_as_a_test_on_write": "kotlin_write",
    "test_kotlin_source_with_test_tag_in_kdoc_is_not_gated_as_a_test_on_edit": "kotlin_edit",
    "test_real_kotlin_junit_test_is_gated_by_its_content": "junit",
    "test_literal_cp_loop_into_an_existing_directory_yields_one_target_per_file": "copy_loop",
    "test_copying_a_python_test_into_a_fresh_directory_is_gated": "copy_test",
    "test_prose_redirected_by_awk_is_flagged_and_blocks_stop": "awk",
    "test_gradle_log_from_a_pristine_wrapper_draws_no_length_finding_and_no_stop_block": "gradle",
    "test_timestamp_prefixed_prose_written_by_the_agent_gets_prose_findings": "timeline",
    "test_docx_with_an_em_dash_in_its_document_body_blocks_stop": "docx",
    "test_malformed_docx_never_blocks_stop": "broken_docx",
    "test_content_guessed_prose_file_is_allowed_with_its_findings_noted": "notes",
    "test_post_tool_batch_scans_written_files_in_the_first_turn_before_any_stop": "batch",
}


if __name__ == "__main__":
    unittest.main()
