import hashlib
import json
import os
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

DENY = "deny"
WRITER = "adw-test-writer"
OLD_TEST = "tests/test_old.py"
CITATION = '    """A test must verify what the code does for its client, never how it does it."""\n'
PROSE = " ".join(("The", "file", "could", "not", "be", "opened", "today."))
DEBT_FILE = "def test_msg():\n" + CITATION + "    assert render() == " + json.dumps(PROSE) + "\n"
CLEAN_FILE = "def test_size():\n" + CITATION + "    assert len([1, 2]) == 2\n"
SHORT_WORDING = "def test_a():\n assert f()==" + json.dumps(" ".join(("The", "file", "is", "gone."))) + "\n"
PRINTF = "printf '" + SHORT_WORDING + "' >tests/test_z.py"
PLUS_TWIN = "(( x = 1 + 2 ))"
REFUSED_PRINTF = (DENY, ["test_principle_citation", "wording_assertion", "wording_debt_lock"])
PASSED = (None, [])
SHIFTS = (
    "(( x = 1 << 2 ))",
    "(( x <<= 2 ))",
    "((x=1<<2))",
    "(( x = 1 <<-2 ))",
    "if (( 1 << 2 )); then :; fi",
    "for (( i=0; i < 1<<2; i++ )); do :; done",
    "echo $(( 1 << 2 ))",
    "x=$((1<<2))",
)
MULTILINE_SHIFTS = (
    "(( x = 1\n<< 2 ))",
    "x=$(( 1\n<< 2 ))",
)
NESTED_SHIFTS = (
    "echo $(echo $(( 1 << 2 )))",
)
SUBSTITUTION_ASSIGNMENTS = (
    "y=$( (( x = 1 + 2 )); echo 1 )",
    "y=$( (( x = 1 << 2 )); echo 1 )",
    "y=$( # a + 2\necho 1 )",
    "y=$( # a << 2\necho 1 )",
)
HIDDEN_MARKERS = (
    "# a << 2",
    "x=$'it\\'s << 2'",
    "x=$'it\\'s + 2'",
    'x="a\n<< 2"',
    "x='a\n<< 2'",
)
HEREDOC_HIDERS = HIDDEN_MARKERS + ("y=$( # a << 2\necho 1 )",)
REAL_HEREDOCS = (
    "git commit -F - <<EOF\nAdd notes\nEOF",
    "cat <<EOF > notes.md\nStep one.\nEOF",
    "cat <<-EOF > notes.md\n\tStep one.\n\tEOF",
    "cat <<'EOF' > notes.md\nStep one.\nEOF",
    'cat <<"EOF" > notes.md\nStep one.\nEOF',
    "cat <<< 'hello' > notes.md",
    "cat <<A > a.md; cat <<B > b.md\nfirst\nA\nsecond\nB",
)
LONE_MARKERS = (
    "(( x = 1 << 2 ))",
    "echo $(echo $(( 1 << 2 )))",
    'x="a\n<< 2"',
    "echo $'it\\'s << 2'",
    "# a << 2\necho hi",
)


def project(root, name, old_test):
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / OLD_TEST).write_text(old_test, encoding="utf-8")
    return repo


class HeredocMarkerContexts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        root = Path(workspace.name).resolve()
        cls.state = root / "state"
        (root / "home").mkdir()
        cls.debt = project(root, "debt", DEBT_FILE)
        cls.clean = project(root, "clean", CLEAN_FILE)
        environment = mock.patch.dict(os.environ, {"HOME": str(root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)

    def reports(self):
        return {report: json.loads(report.read_text(encoding="utf-8")) for report in (self.state / "reports").glob("*.json")}

    def bash(self, repo, command, agent):
        session = "s" + hashlib.sha256(f"{self.id()}-{repo.name}-{command}".encode()).hexdigest()[:20]
        run_hook("SessionStart", {"session_id": session, "hook_event_name": "SessionStart", "cwd": str(repo), "source": "startup"}, state_dir=self.state, git_view=NO_REPOSITORY)
        before = self.reports()
        identity = {"agent_id": "a1", "agent_type": agent} if agent else {}
        body = {"session_id": session, "hook_event_name": "PreToolUse", "cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": command}, **identity}
        output = run_hook("PreToolUse", body, state_dir=self.state, git_view=NO_REPOSITORY)
        rows = [row for report, batch in self.reports().items() for row in batch[len(before.get(report, [])):]]
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, sorted({row["rule"] for row in rows if row.get("outcome") != "would_block"})

    def writer_verdicts(self, heads, tail):
        return {head: self.bash(self.debt, head + "\n" + tail, WRITER) for head in heads}

    def main_verdicts(self, commands):
        return {command: self.bash(self.clean, command, None) for command in commands}

    def test_plus_twin_before_a_wording_printf_is_refused(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.writer_verdicts((PLUS_TWIN,), PRINTF), {PLUS_TWIN: REFUSED_PRINTF})

    def test_shift_inside_arithmetic_before_a_wording_printf_is_refused_like_the_plus_twin(self):
        """Use a parameterized test for several inputs."""
        self.assertEqual(self.writer_verdicts(SHIFTS, PRINTF), dict.fromkeys(SHIFTS, REFUSED_PRINTF))

    def test_multiline_arithmetic_shift_before_a_wording_printf_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.writer_verdicts(MULTILINE_SHIFTS, PRINTF), dict.fromkeys(MULTILINE_SHIFTS, REFUSED_PRINTF))

    def test_shift_inside_command_substitution_before_a_wording_printf_is_refused(self):
        """Write a unit test for domain logic and algorithms."""
        self.assertEqual(self.writer_verdicts(NESTED_SHIFTS, PRINTF), dict.fromkeys(NESTED_SHIFTS, REFUSED_PRINTF))

    def test_command_substitution_assignment_before_a_wording_printf_is_refused_like_the_plus_twin(self):
        """Use a parameterized test for several inputs."""
        verdicts = self.writer_verdicts(SUBSTITUTION_ASSIGNMENTS, PRINTF)
        self.assertEqual(verdicts, dict.fromkeys(SUBSTITUTION_ASSIGNMENTS, REFUSED_PRINTF))

    def test_marker_inside_quotes_or_comments_does_not_hide_a_wording_printf(self):
        """Feed input, check the return value."""
        self.assertEqual(self.writer_verdicts(HIDDEN_MARKERS, PRINTF), dict.fromkeys(HIDDEN_MARKERS, REFUSED_PRINTF))

    def test_real_heredocs_of_the_main_agent_pass(self):
        """The test stays green when you restructure the code without changing its behavior."""
        self.assertEqual(self.main_verdicts(REAL_HEREDOCS), dict.fromkeys(REAL_HEREDOCS, PASSED))

    def test_lone_arithmetic_quote_or_comment_markers_of_the_main_agent_pass(self):
        """A test must verify what the code does for its client, never how it does it."""
        self.assertEqual(self.main_verdicts(LONE_MARKERS), dict.fromkeys(LONE_MARKERS, PASSED))


if __name__ == "__main__":
    unittest.main()
