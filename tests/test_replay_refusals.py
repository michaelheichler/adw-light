import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from lib import reporting

SPEC = importlib.util.spec_from_file_location("replay_refusals", ROOT / "tools" / "replay_refusals.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)

FAKE_ENTRY = """import pathlib
import sys
pathlib.Path(sys.argv[0]).with_name("stdin.json").write_text(sys.stdin.read(), encoding="utf-8")
print('{"hookSpecificOutput": {"permissionDecision": "deny"}}')
"""
SHARED = {}


def setUpModule():
    workspace = TemporaryDirectory()
    unittest.addModuleCleanup(workspace.cleanup)
    root = Path(workspace.name).resolve()
    engine = root / "tree" / "adw_light.py"
    engine.parent.mkdir()
    engine.write_text(FAKE_ENTRY, encoding="utf-8")
    (root / "project").mkdir()
    SHARED.update(root=root, engine=engine)


def stamp(offset):
    return (datetime.now(timezone.utc) + offset).isoformat()


def block_row(rule, **fields):
    return {"ts": stamp(timedelta(hours=-1)), "event": "PreToolUse", "outcome": "block", "rule": rule,
            "hook": "pre_bash", "session_id": "s1", "tool_use_id": "", **fields}


def write_lines(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def bash_entry(sid, tool_id, command, cwd, when):
    part = {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}
    return {"sessionId": sid, "cwd": cwd, "timestamp": when,
            "message": {"role": "assistant", "content": [part]}}


class ReplayRefusalsTests(unittest.TestCase):
    def sid(self):
        return "s" + sha256(self.id().encode()).hexdigest()[:16]

    def projects(self):
        return SHARED["root"] / ("projects-" + self.sid())

    def test_recent_block_window(self):
        rows = [
            block_row("one_day_old", ts=stamp(timedelta(days=-1))),
            block_row("commit_block", event="PreCommit"),
            block_row("five_days_old", ts=stamp(timedelta(days=-5))),
            block_row("future_row", ts=stamp(timedelta(days=1))),
            block_row("observed_only", outcome="would_block"),
            block_row("stop_event", event="Stop"),
        ]
        ledger = write_lines(SHARED["root"] / "window" / "ledger.jsonl", rows)
        found = [row["rule"] for row in replay.recent_blocks([ledger], 3)]
        self.assertEqual(found, ["one_day_old", "commit_block"])

    def test_transcript_matches_session_and_tool_id(self):
        sid, when = self.sid(), stamp(timedelta(hours=-1))
        write_lines(self.projects() / "-work" / f"{sid}.jsonl", [
            bash_entry(sid, "toolu_a", "git push", "/work/a", when),
            bash_entry("other", "toolu_a", "decoy in same file", "/work/decoy", when),
        ])
        write_lines(self.projects() / "-work" / "other.jsonl", [
            bash_entry("other", "toolu_a", "decoy in other session", "/work/other", when),
        ])
        row = block_row("matched", session_id=sid, tool_use_id="toolu_a", ts=when)
        calls = replay.transcript_calls(self.projects(), [row])
        self.assertEqual(calls, {(sid, "toolu_a", when): ("Bash", {"command": "git push"}, "/work/a", "id")})

    def test_time_match_is_labelled(self):
        sid, moment = self.sid(), datetime.now(timezone.utc) - timedelta(hours=1)
        call_time = (moment + timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
        write_lines(self.projects() / "-work" / f"{sid}.jsonl", [
            bash_entry(sid, "toolu_other", "make deploy", "/work/t", call_time),
        ])
        row = block_row("timed", session_id=sid, tool_use_id="toolu_missing", ts=moment.isoformat())
        calls = replay.transcript_calls(self.projects(), [row])
        self.assertEqual(list(calls.values()), [("Bash", {"command": "make deploy"}, "/work/t", "time")])

    def test_time_match_beyond_thirty_seconds_is_dropped(self):
        sid, moment = self.sid(), datetime.now(timezone.utc) - timedelta(hours=1)
        call_time = (moment + timedelta(seconds=31)).isoformat()
        write_lines(self.projects() / "-work" / f"{sid}.jsonl", [
            bash_entry(sid, "toolu_other", "make deploy", "/work/t", call_time),
        ])
        row = block_row("timed", session_id=sid, tool_use_id="toolu_missing", ts=moment.isoformat())
        self.assertEqual(replay.transcript_calls(self.projects(), [row]), {})

    def test_dedup_preserves_distinct_projects(self):
        shared = dict(tool_name="Bash", target="rm -rf build")
        rows = [
            block_row("shell_write", cwd="/missing/project-a", session_id="s1", **shared),
            block_row("shell_write", cwd="/missing/project-a", session_id="s2", **shared),
            block_row("shell_write", cwd="/missing/project-b", session_id="s3", **shared),
        ]
        results = list(replay.replay_rows(SHARED["engine"], rows, {}))
        self.assertEqual([row["cwd"] for row in results], ["/missing/project-a", "/missing/project-b"])

    def test_missing_cwd_is_unknown(self):
        row = block_row("shell_write", tool_name="Bash", target="rm -rf build")
        result = list(replay.replay_rows(SHARED["engine"], [row], {}))
        self.assertEqual([(item["still_refused"], item["matched_by"], item["cwd"]) for item in result],
                         [("unknown", "none", "")])

    def test_refused_command_only_reaches_hook_stdin(self):
        marker = SHARED["root"] / "refused-command-ran"
        command = f"touch {marker}"
        project = str(SHARED["root"] / "project")
        row = block_row("shell_write", tool_name="Bash", cwd=project, target=command, tool_use_id="toolu_r")
        result = list(replay.replay_rows(SHARED["engine"], [row], {}))
        received = json.loads((SHARED["engine"].with_name("stdin.json")).read_text(encoding="utf-8"))
        self.assertEqual(
            ([item["still_refused"] for item in result], marker.exists(), received["tool_input"], received["cwd"]),
            (["yes"], False, {"command": command}, project),
        )

    def test_recovered_secret_stays_out_of_reports_but_reaches_hook(self):
        """A test must verify what the code does for its client, never how it does it."""
        sid, root = self.sid(), SHARED["root"]
        command = 'curl -H "Authorization: Bearer abc123" https://example.test'
        row = block_row("shell_write", session_id=sid, tool_use_id="toolu_s")
        write_lines(self.projects() / "-work" / f"{sid}.jsonl", [
            bash_entry(sid, "toolu_s", command, str(root / "project"), row["ts"]),
        ])
        ledger = write_lines(root / f"ledger-{sid}" / "ledger.jsonl", [row])
        report = root / f"report-{sid}.json"
        argv = ["replay_refusals", str(ledger), "--tree", str(SHARED["engine"].parent),
                "--projects", str(self.projects()), "--json", str(report)]
        table = io.StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(table):
            replay.replay_cli()
        received = json.loads(SHARED["engine"].with_name("stdin.json").read_text(encoding="utf-8"))
        self.assertEqual(
            ("abc123" in table.getvalue(), "abc123" in report.read_text(encoding="utf-8"), received["tool_input"]),
            (False, False, {"command": command}),
        )

    def test_unreadable_transcript_times_match_nothing_and_valid_entry_still_matches(self):
        """Write a unit test for domain logic and algorithms."""
        sid, moment = self.sid(), datetime.now(timezone.utc) - timedelta(hours=1)
        naive = moment - timedelta(hours=2)
        write_lines(self.projects() / "-work" / f"{sid}.jsonl", [
            bash_entry(sid, "toolu_bad", "echo bad", "/work/b", "not a time"),
            bash_entry(sid, "toolu_naive", "echo naive", "/work/n", naive.replace(tzinfo=None).isoformat()),
            bash_entry(sid, "toolu_good", "echo good", "/work/g", moment.isoformat()),
        ])
        rows = [
            block_row("bad", session_id=sid, tool_use_id="toolu_bad", ts=(moment - timedelta(hours=4)).isoformat()),
            block_row("naive", session_id=sid, tool_use_id="toolu_naive", ts=naive.isoformat()),
            block_row("good", session_id=sid, tool_use_id="toolu_good", ts=moment.isoformat()),
        ]
        calls = replay.transcript_calls(self.projects(), rows)
        self.assertEqual(calls, {(sid, "toolu_good", moment.isoformat()): ("Bash", {"command": "echo good"}, "/work/g", "id")})


def ledger_rows_after_gate(ledger, command, outcomes):
    payload = {"tool_name": "Bash", "cwd": "/work/p", "tool_input": {"command": command}}

    def gate(turn_id):
        for outcome in outcomes:
            reporting.record_decision(
                session_id="s1", hook="pre_bash", event="PreToolUse", family="code", rule=outcome,
                path="", tool_use_id="toolu_l", outcome=outcome, duration_ms=0, turn_id=turn_id, root=ledger,
            )
        return {}

    invocation = reporting.LedgerInvocation("pre_bash", payload, ledger, SHARED["root"] / "state")
    reporting.run_with_ledger(invocation, gate)
    return [json.loads(line) for line in (ledger / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


class RefusalTargetLedgerTests(unittest.TestCase):
    def test_denied_row_records_target_and_allowed_row_records_none(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        command = "a" * 300 + "b" * 100
        rows = ledger_rows_after_gate(SHARED["root"] / "ledger-cut", command, ("block", "would_block"))
        keys = ("tool_name", "cwd", "target", "target_truncated")
        self.assertEqual(
            [{key: row[key] for key in keys if key in row} for row in rows],
            [{"tool_name": "Bash", "cwd": "/work/p", "target": "a" * 300, "target_truncated": True}, {}],
        )

    def test_denied_row_masks_bearer_and_token_values(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        command = 'FOO_TOKEN=xyz curl -H "Authorization: Bearer abc123" https://example.test'
        rows = ledger_rows_after_gate(SHARED["root"] / "ledger-mask", command, ("block",))
        self.assertEqual([row["target"] for row in rows],
                         ['FOO_TOKEN=*** curl -H "Authorization: Bearer ***" https://example.test'])


ORDINARY_COMMANDS = (
    "git push -u origin main",
    "sort -u names.txt",
    "git add -u src",
    "docker run -u 1000 img",
    'git commit -m "Add basic tests"',
    'git commit -m "Fix bearer parsing"',
    'echo "Parse key: value pairs"',
    "rg -n key: src",
    "llm --max-tokens 100",
    "train --tokenizer bert",
    "sort --sort-key name",
    "docker login -u bob --password-stdin",
    "gh auth login --with-token < tok.txt",
    "psql --no-password -h db",
    "setxkbmap --keyboard us",
    "vim api_key.txt",
    "cat token_utils.py",
)
CREDENTIAL_COMMANDS = (
    ('curl -H "Authorization: ghp_abc123" https://example.test', 'curl -H "Authorization: ***" https://example.test'),
    ("curl -uuser:abc123 https://example.test", "curl -u*** https://example.test"),
    ("mysql -pabc123 db", "mysql -p*** db"),
    ("sshpass -p abc123 ssh host", "sshpass -p *** ssh host"),
    ('curl -H "Cookie: session=abc123"', 'curl -H "Cookie: ***"'),
    ("http -a bob:abc123 https://x", "http -a *** https://x"),
    ("git push -u origin main && curl -u bob:abc123 https://x", "git push -u origin main && curl -u *** https://x"),
    ('{"headers": {"Authorization": "Bearer abc123"}}', '{"headers": {"Authorization": "Bearer ***"}}'),
    ('curl -H "Authorization: token abc123" https://example.test', 'curl -H "Authorization: token ***" https://example.test'),
    ('curl -H "x-api-key: abc123" https://example.test', 'curl -H "x-api-key: ***" https://example.test'),
    ('curl -H "X-Auth-Token: abc123" https://example.test', 'curl -H "X-Auth-Token: ***" https://example.test'),
    ("git clone https://user:abc123@example.test/r.git", "git clone https://***@example.test/r.git"),
    ("curl --oauth2-bearer abc123 https://example.test", "curl --oauth2-bearer *** https://example.test"),
    ('curl "https://example.test/api?token=abc123"', 'curl "https://example.test/api?token=***"'),
    ("GITHUB_TOKEN=abc123 gh pr list", "GITHUB_TOKEN=*** gh pr list"),
    ("AWS_SECRET_ACCESS_KEY=abc123 aws s3 ls", "AWS_SECRET_ACCESS_KEY=*** aws s3 ls"),
    ("PGPASSWORD=abc123 psql -h db", "PGPASSWORD=*** psql -h db"),
)

CLIENT_SECRET_COMMANDS = (
    ("docker login -p abc123 registry.test", "docker login -p *** registry.test"),
    ("docker login --password abc123 registry.test", "docker login --password *** registry.test"),
    ("docker login -u bob -p abc123 registry.test", "docker login -u bob -p *** registry.test"),
    ("redis-cli -a abc123 ping", "redis-cli -a *** ping"),
    ("redis-cli -h db -a abc123 ping", "redis-cli -h db -a *** ping"),
    ("curl -b session=abc123 https://example.test", "curl -b *** https://example.test"),
    ("curl --cookie session=abc123 https://example.test", "curl --cookie *** https://example.test"),
    ("curl -su user:abc123 https://example.test", "curl -su *** https://example.test"),
    ("curl -sSu user:abc123 https://example.test", "curl -sSu *** https://example.test"),
    ('redis-cli -a "abc 123" ping', "redis-cli -a *** ping"),
    ('docker login -p "abc 123" registry.test', "docker login -p *** registry.test"),
)
UNRELATED_FLAG_COMMANDS = (
    ("sort -b names.txt", "sort -b names.txt"),
    ("grep -a abc123 log.bin", "grep -a abc123 log.bin"),
    ("tar -p -xf abc123.tar", "tar -p -xf abc123.tar"),
    ("ls -su abc123", "ls -su abc123"),
    ("docker ps -a", "docker ps -a"),
    ("docker run -p 8080:80 img", "docker run -p 8080:80 img"),
    ("curl -s https://example.test | grep -a abc123", "curl -s https://example.test | grep -a abc123"),
    ("redis-cli ping && sort -b abc123", "redis-cli ping && sort -b abc123"),
    ("docker login -u bob && tar -p abc123", "docker login -u bob && tar -p abc123"),
    ("ls -su abc123 && redis-cli -a abc123 ping", "ls -su abc123 && redis-cli -a *** ping"),
    ("git push -u origin main && docker login -p abc123 r.test", "git push -u origin main && docker login -p *** r.test"),
    ("grep -a x log | curl -b session=abc123 https://x", "grep -a x log | curl -b *** https://x"),
)
SCHEME_WORD_TARGETS = (
    ('git commit -m "Basic word"', 'git commit -m "Basic word"'),
    ('echo "Bearer word"', 'echo "Bearer word"'),
    ("echo 'Basic word'", "echo 'Basic word'"),
    ('curl -H "Authorization: Basic word" https://x', 'curl -H "Authorization: Basic ***" https://x'),
    ('curl -H "Authorization: Bearer word" https://x', 'curl -H "Authorization: Bearer ***" https://x'),
    ('{"auth": "Bearer word"}', '{"auth": "Bearer ***"}'),
    ('{"Authorization": "Basic word"}', '{"Authorization": "Basic ***"}'),
)

WRAPPED_CLIENT_SECRET_COMMANDS = (
    ("tool --password s3cr3t", "tool --password ***"),
    ("npm login --password s3cr3t", "npm login --password ***"),
    ("az login -u bob --password s3cr3t", "az login -u bob --password ***"),
    ("foo --password 's3 cr3t'", "foo --password ***"),
    ("mysql --password abc123 db", "mysql --password *** db"),
    ("psql --password abc123", "psql --password ***"),
    ("curl -Iu bob:s3cr3t", "curl -Iu ***"),
    ("curl -Nu bob:s3cr3t", "curl -Nu ***"),
    ("curl -bsession=abc", "curl -b***"),
    ("curl --cookie=session=abc", "curl --cookie=***"),
    ("docker login -ps3cr3t", "docker login -p***"),
    ("sudo docker login -p s3cr3t", "sudo docker login -p ***"),
    ("sudo curl -u bob:s3cr3t", "sudo curl -u ***"),
    ("time curl -b x", "time curl -b ***"),
    ("{ curl -b x; }", "{ curl -b ***; }"),
    ("docker --config /c login -p x", "docker --config /c login -p ***"),
    ("curl -s \\\n -b x", "curl -s \\\n -b ***"),
    ("redis-cli --pass x", "redis-cli --pass ***"),
)
READABLE_FLAG_COMMANDS = (
    "sort -b",
    "grep -a",
    "ls -su",
    "tar -p",
    "docker run -p 80:80",
    "git push -u origin main",
    "docker ps -a",
    "docker login --password-stdin",
    'echo "Basic word"',
)
CURL_ANYWHERE_COMMANDS = (
    ("timeout 10 curl -u bob:s3cr3t https://x", "timeout 10 curl -u *** https://x"),
    ("sudo -u www curl -u bob:s3cr3t https://x", "sudo -u www curl -u *** https://x"),
    ("sudo -E curl -u bob:s3cr3t https://x", "sudo -E curl -u *** https://x"),
    ("nice curl -u bob:s3cr3t https://x", "nice curl -u *** https://x"),
    ("echo x | xargs curl -u bob:s3cr3t", "echo x | xargs curl -u ***"),
    ("bash -c \"curl -u bob:s3cr3t https://x\"", "bash -c \"curl -u *** https://x\""),
    ("ssh host 'curl -u bob:s3cr3t https://x'", "ssh host 'curl -u *** https://x'"),
    ("echo `curl -u bob:s3cr3t https://x`", "echo `curl -u *** https://x`"),
    ("timeout 5 docker login -p s3cr3t r.test", "timeout 5 docker login -p *** r.test"),
    ("curl -d \"$(curl -u bob:s3cr3t https://y)\" https://x", "curl -d \"$(curl -u *** https://y)\" https://x"),
    ("docker run curlimages/curl -u bob:s3cr3t https://x", "docker run curlimages/curl -u *** https://x"),
    ("docker run img redis-cli -a s3cr3t ping", "docker run img redis-cli -a *** ping"),
    ("nice -n 5 redis-cli -a s3cr3t ping", "nice -n 5 redis-cli -a *** ping"),
    ("curl -E cert.pem:certpw https://x", "curl -E *** https://x"),
)
GROUPED_CURL_FLAG_COMMANDS = (
    "curl -XPUT https://x",
    "curl -obackup.tar https://x",
    "curl -dbuild=1 https://x",
    "curl -Huser-agent:me https://x",
    "curl -Fupload=@f https://x",
    "curl -o -u https://x",
    "timeout 10 sort -b names",
    "sudo -u www ls -su abc",
    "curl -s https://x && sort -b abc",
    "bash -c \"sort -b abc\"",
    "curl --cookie-jar jar.txt https://x",
)


class SecretMaskTests(unittest.TestCase):
    def test_bearer_basic_and_scheme_credentials_are_masked(self):
        """Feed input, check the return value."""
        commands = [
            "Bearer abc123",
            "Basic dXNlcjpwYXNz",
            '{"headers": {"Authorization": "Bearer abc123"}}',
            '{"auth": "Basic dXNlcjpwYXNz"}',
            'curl -H "Authorization: token abc123" https://example.test',
        ]
        self.assertEqual([reporting.mask_target(command)[0] for command in commands], [
            "Bearer ***",
            "Basic ***",
            '{"headers": {"Authorization": "Bearer ***"}}',
            '{"auth": "Basic ***"}',
            'curl -H "Authorization: token ***" https://example.test',
        ])

    def test_key_and_token_names_are_masked_and_keyboard_is_kept(self):
        """Prefer output based tests."""
        commands = [
            'curl -H "x-api-key: abc123" https://example.test',
            'curl -H "X-Auth-Token: abc123" https://example.test',
            'curl -H "Private-Key: abc123" https://example.test',
            "tool --api_key abc123",
            "api_key=abc123 tool",
            "OPENAI_KEY=abc123 tool",
            "setxkbmap --keyboard us",
        ]
        self.assertEqual([reporting.mask_target(command)[0] for command in commands], [
            'curl -H "x-api-key: ***" https://example.test',
            'curl -H "X-Auth-Token: ***" https://example.test',
            'curl -H "Private-Key: ***" https://example.test',
            "tool --api_key ***",
            "api_key=*** tool",
            "OPENAI_KEY=*** tool",
            "setxkbmap --keyboard us",
        ])

    def test_url_userinfo_and_curl_user_credentials_are_masked(self):
        """Use output based if the code returns a value."""
        commands = [
            "git clone https://user:abc123@example.test/r.git",
            "curl -u user:abc123 https://example.test",
            "curl --user user:abc123 https://example.test",
            "curl --user=user:abc123 https://example.test",
            "curl --oauth2-bearer abc123 https://example.test",
        ]
        self.assertEqual([reporting.mask_target(command)[0] for command in commands], [
            "git clone https://***@example.test/r.git",
            "curl -u *** https://example.test",
            "curl --user *** https://example.test",
            "curl --user=*** https://example.test",
            "curl --oauth2-bearer *** https://example.test",
        ])

    def test_ordinary_commands_stay_readable(self):
        """Use a parameterized test for several inputs."""
        for command in ORDINARY_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(reporting.mask_target(command)[0], command)

    def test_credentials_in_a_credential_context_are_masked(self):
        """The test fails when a real bug enters the code it exercises."""
        for command, expected in CREDENTIAL_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(reporting.mask_target(command)[0], expected)

    def test_client_secret_arguments_are_masked(self):
        """Write a unit test for domain logic and algorithms."""
        for command, expected in CLIENT_SECRET_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(reporting.mask_target(command)[0], expected)

    def test_secret_flags_of_other_commands_stay_readable(self):
        """A test must verify what the code does for its client, never how it does it."""
        for command, expected in UNRELATED_FLAG_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(reporting.mask_target(command)[0], expected)

    def test_scheme_words_are_masked_only_in_an_auth_field(self):
        """Feed input, check the return value."""
        for target, expected in SCHEME_WORD_TARGETS:
            with self.subTest(target=target):
                self.assertEqual(reporting.mask_target(target)[0], expected)

    def test_client_secrets_after_wrappers_and_in_every_flag_form_are_masked(self):
        """The test fails when a real bug enters the code it exercises."""
        for command, expected in WRAPPED_CLIENT_SECRET_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(reporting.mask_target(command)[0], expected)

    def test_flags_that_carry_no_secret_stay_readable(self):
        """Use a parameterized test for several inputs."""
        for command in READABLE_FLAG_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(reporting.mask_target(command)[0], command)

    def test_client_secrets_inside_any_wrapper_or_nested_call_are_masked(self):
        """Make the test fail first."""
        for command, expected in CURL_ANYWHERE_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(reporting.mask_target(command)[0], expected)

    def test_grouped_curl_flags_without_a_secret_stay_readable(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        for command in GROUPED_CURL_FLAG_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(reporting.mask_target(command)[0], command)


if __name__ == "__main__":
    unittest.main()
