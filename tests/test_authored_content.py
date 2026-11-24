import json
import subprocess
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
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
GRADLE_WRAPPER = """#!/bin/sh
i=0
while [ "$i" -lt 1100 ]; do
  echo "> Task :app:compileDebugKotlin$i UP-TO-DATE"
  i=$((i + 1))
done
"""


def line_of(text, fragment):
    return text[:text.index(fragment)].count("\n") + 1


class AuthoredContentRegressions(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.state = self.root / ".adw-state"

    def hook(self, event, **payload):
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), event, "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps({"cwd": str(self.root), "session_id": "authored-content",
                              "hook_event_name": event, **payload}),
            text=True, capture_output=True, check=True,
        )
        return json.loads(call.stdout)

    def start_session(self):
        self.hook("SessionStart", source="startup")

    def commit_baseline(self, *names):
        identity = ["-c", "user.name=ADW Test", "-c", "user.email=adw-test@example.invalid", "-c", "commit.gpgSign=false"]
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", *names], cwd=self.root, check=True)
        subprocess.run(["git", *identity, "commit", "-qm", "Record baseline"], cwd=self.root, check=True)

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

    def write_docx(self, name, document_xml):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", document_xml)
        return path

    def test_kdoc_edit_blocks_only_the_changed_line_and_not_the_inherited_block(self):
        source = self.root / "Repo.kt"
        source.write_text(KDOC_SOURCE, encoding="utf-8")
        self.commit_baseline("Repo.kt")
        self.start_session()
        old, new = " * The caller owns the handle.", " * This function returns the value."
        source.write_text(KDOC_SOURCE.replace(old, new), encoding="utf-8")
        self.post_tool("Edit", file_path=str(source), old_string=old, new_string=new)

        changed_line = line_of(source.read_text(encoding="utf-8"), new)
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
        source.write_text(PLAIN_KOTLIN_SOURCE, encoding="utf-8")
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
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        first, second = "A.kt", "B.kt"
        (self.root / "src" / first).write_text(PLAIN_KOTLIN_SOURCE, encoding="utf-8")
        (self.root / "src" / second).write_text(PLAIN_KOTLIN_SOURCE, encoding="utf-8")
        self.start_session()

        reason = self.deny_reason(self.pre_tool("Bash", command=f"for f in {first} {second}; do cp src/$f tests/$f; done"))

        self.assertIn(f"tests/{first}:{FILE_LEVEL_LINE} ", reason)
        self.assertIn(f"tests/{second}:{FILE_LEVEL_LINE} ", reason)
        self.assertEqual(1, reason.count(f"{first}:{FILE_LEVEL_LINE} "))
        self.assertEqual(1, reason.count(f"{second}:{FILE_LEVEL_LINE} "))
        self.assertNotIn(f"{first}/{first}", reason)

    def test_copying_a_python_test_into_a_fresh_directory_is_gated(self):
        source = self.root / "incoming" / "foo_test.py"
        source.parent.mkdir()
        source.write_text("def test_cache_size():\n    assert 20000 == 2 * 10000\n", encoding="utf-8")
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
        wrapper = self.root / "gradlew"
        wrapper.write_text(GRADLE_WRAPPER, encoding="utf-8")
        wrapper.chmod(0o755)
        self.commit_baseline("gradlew")
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
        xml = f'<w:document xmlns:w="{WORD_NAMESPACE}"><w:body><w:p><w:r><w:t>{DASHED_SENTENCE}</w:t></w:r></w:p></w:body></w:document>'
        path = self.write_docx("report.docx", xml)
        self.start_session()
        self.post_tool("Write", file_path=str(path), content="")

        stop = self.stop()

        self.assertEqual("block", stop["decision"])
        self.assertIn("report.docx:1 ", stop["reason"])
        self.assertIn("(banned_dash)", stop["reason"])

    def test_malformed_docx_never_blocks_stop(self):
        path = self.root / "broken.docx"
        path.write_bytes(b"PK\x03\x04" + b"not a zip archive" * 20)
        self.start_session()
        self.post_tool("Write", file_path=str(path), content="")

        stop = self.stop()

        self.assertNotIn("decision", stop)

    def test_extensionless_prose_file_is_scanned(self):
        self.start_session()

        response = self.pre_tool("Write", file_path=str(self.root / "NOTES"), content=FLAGGED_PROSE)

        self.assertIn("NOTES:1 ", self.deny_reason(response))
        self.assertIn("(utilize)", self.deny_reason(response))

    def test_post_tool_batch_scans_written_files_in_the_first_turn_before_any_stop(self):
        path = self.root / "first-turn.md"
        path.write_text(FLAGGED_PROSE, encoding="utf-8")

        batch = self.hook("PostToolBatch", tool_calls=[{
            "tool_name": "Write", "tool_use_id": "batch-write-1",
            "tool_input": {"file_path": str(path), "content": FLAGGED_PROSE}, "tool_response": {},
        }])

        self.assertNotIn("decision", batch)
        self.assertIn(f"{path.name}:{line_of(FLAGGED_PROSE, FLAGGED_WORD)} ", batch["hookSpecificOutput"]["additionalContext"])
        self.assertIn("(utilize)", batch["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
