import json
import os
import subprocess
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

RULE = "hard_coded_path"
DENY = "deny"
AGENT = "adw-test-writer"
LOCAL_NAME = "zed42x"
OWNER = "maren"
SYSTEM_RULE = "hard_coded_system_path"
PRINCIPLE = "A test must verify what the code does for its client, never how it does it."
USER_VARIABLES = ("USER", "LOGNAME", "USERNAME")
KEPT_PATH_LINE = "SHOT = " + json.dumps("/" + "/".join(("tmp", "shot.png")))
COUNT_LINE = "COUNT = 1"


def rooted(*parts):
    return "/" + "/".join(parts)


def home_anchored(rest):
    return "~" + "/" + rest


def drive(letter, separator, *parts):
    return letter + ":" + separator + separator.join(parts)


def python_test(expression, prelude=""):
    return (
        "import unittest\nfrom pathlib import Path\n" + prelude + "\n\n"
        "class PathProbe(unittest.TestCase):\n"
        "    def test_reads_fixture(self):\n"
        "        \"\"\"" + PRINCIPLE + "\"\"\"\n"
        "        fixture = " + expression + "\n"
        "        self.assertEqual(fixture.suffix, \".json\")\n"
    )


class HardCodedPathGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.root = Path(workspace.name)
        cls.repo = cls.root / "repo"
        cls.state = cls.root / "state"
        (cls.root / "home").mkdir()
        (cls.repo / "src").mkdir(parents=True)
        (cls.repo / "src" / "kept.py").write_text(KEPT_PATH_LINE + "\n", encoding="utf-8")
        (cls.repo / "src" / "added.py").write_text(COUNT_LINE + "\n", encoding="utf-8")
        environment = mock.patch.dict(os.environ, {"HOME": str(cls.root / "home")})
        environment.start()
        cls.addClassCleanup(environment.stop)
        for name in USER_VARIABLES:
            os.environ.pop(name, None)

    def hook(self, tool_name, tool_input):
        payload = {
            "cwd": str(self.repo), "session_id": self._testMethodName, "hook_event_name": "PreToolUse",
            "agent_id": "a1", "agent_type": AGENT, "tool_name": tool_name, "tool_input": tool_input,
        }
        return run_hook("PreToolUse", payload, state_dir=self.state, git_view=NO_REPOSITORY)

    def path_lines(self, new_reports, rule=RULE):
        rows = [row for report in sorted(new_reports) for row in json.loads(report.read_text(encoding="utf-8"))]
        return tuple(row["line"] for row in rows if row["rule"] == rule)

    def reports(self):
        return set((self.state / "reports").glob("*.json"))

    def verdict_of(self, tool_name, tool_input, rule=RULE):
        earlier = self.reports()
        output = self.hook(tool_name, tool_input)
        return output.get("hookSpecificOutput", {}).get("permissionDecision"), self.path_lines(self.reports() - earlier, rule)

    def write(self, relative, content, rule=RULE):
        tool_input = {"file_path": str(self.repo / relative), "content": content}
        return self.verdict_of("Write", tool_input, rule)

    def write_as_user(self, relative, content, user):
        payload = {
            "cwd": str(self.repo), "session_id": self._testMethodName, "hook_event_name": "PreToolUse",
            "agent_id": "a1", "agent_type": AGENT, "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / relative), "content": content},
        }
        earlier = self.reports()
        call = subprocess.run(
            [sys.executable, "-ISB", str(PROJECT / "adw_light.py"), "PreToolUse", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True, check=True,
            env={"HOME": str(self.root / "home"), "PATH": os.environ.get("PATH", ""), "USER": user},
        )
        decision = json.loads(call.stdout).get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, self.path_lines(self.reports() - earlier)

    def edit(self, relative, old, new, rule=RULE):
        tool_input = {"file_path": str(self.repo / relative), "old_string": old, "new_string": new}
        return self.verdict_of("Edit", tool_input, rule)

    def test_literal_home_path_in_a_test_denies_on_its_line(self):
        """A test must verify what the code does for its client, never how it does it."""
        fixture = json.dumps(rooted("Users", OWNER, "project", "fixtures", "a.json"))
        verdict = self.write("tests/test_paths.py", python_test("Path(" + fixture + ")"))
        self.assertEqual(verdict, (DENY, (8,)))

    def test_portable_tilde_home_path_in_a_test_passes_with_a_note(self):
        """A test must verify what the code does for its client, never how it does it."""
        fixture = json.dumps(home_anchored("project/a.json"))
        verdict = self.write("tests/test_paths.py", python_test("Path(" + fixture + ")"), SYSTEM_RULE)
        self.assertEqual(verdict, (None, (8,)))

    def test_fixture_joined_to_a_computed_root_passes(self):
        content = python_test('ROOT / "fixtures" / "a.json"', prelude="\nROOT = Path(__file__).resolve().parent")
        self.assertEqual(self.write("tests/test_paths.py", content), (None, ()))

    def test_paths_in_a_markdown_file_pass(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = (
            "# Notes\n\nThe tool reads " + rooted("Users", OWNER, "project")
            + " and keeps its cache in " + home_anchored(".cache/tool") + ".\n"
        )
        self.assertEqual(self.write("docs/notes.md", content), (None, ()))

    def test_forward_slash_drive_letter_in_code_denies(self):
        content = "import os\nDATA = " + json.dumps(drive("D", "/", "data", "x")) + "\n"
        self.assertEqual(self.write("src/d.py", content), (DENY, (2,)))

    def test_backslash_drive_letter_in_go_denies(self):
        """A test must verify what the code does for its client, never how it does it."""
        literal = json.dumps(drive("C", "\\", "Users", OWNER, "data"))
        content = "package main\n\nvar dataDir = " + literal + "\n"
        self.assertEqual(self.write("src/data.go", content), (DENY, (3,)))

    def test_bare_absolute_path_in_shell_denies(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "#!/bin/sh\ncd " + rooted("home", OWNER, "project") + "\n"
        self.assertEqual(self.write("src/run.sh", content), (DENY, (2,)))

    def test_local_username_outside_a_home_path_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "OWNER = " + json.dumps(LOCAL_NAME) + "\n"
        self.assertEqual(self.write_as_user("src/who.py", content, LOCAL_NAME), (None, ()))

    def test_same_name_passes_when_it_is_not_the_local_user(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "OWNER = " + json.dumps(LOCAL_NAME) + "\n"
        self.assertEqual(self.write("src/who.py", content), (None, ()))

    def test_generic_account_name_passes_even_as_the_local_user(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "OWNER = " + json.dumps("runner") + "\n"
        self.assertEqual(self.write_as_user("src/who.py", content, "runner"), (None, ()))

    def test_two_letter_local_username_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "OWNER = " + json.dumps("zq") + "\n"
        self.assertEqual(self.write_as_user("src/who.py", content, "zq"), (None, ()))

    def test_deploy_script_with_a_remote_home_path_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "#!/bin/sh\nrsync -a . bob@host:" + rooted("home", OWNER, "project") + "\n"
        self.assertEqual(self.write("deploy/push.sh", content), (None, ()))

    def test_deploy_named_script_outside_a_deploy_folder_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "#!/bin/sh\nrsync -a . bob@host:" + rooted("home", OWNER, "project") + "\n"
        self.assertEqual(self.write("tools/deploy_site.sh", content), (None, ()))

    def test_same_remote_path_outside_deploy_scope_denies(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "#!/bin/sh\nrsync -a . bob@host:" + rooted("home", OWNER, "project") + "\n"
        self.assertEqual(self.write("src/push.sh", content), (DENY, (2,)))

    def test_path_inside_a_go_comment_passes(self):
        content = (
            "package main\n\n// read from " + rooted("Users", "x", "data")
            + " because the import job writes there\nvar ready = true\n"
        )
        self.assertEqual(self.write("src/data.go", content)[1], ())

    def test_url_with_a_home_segment_passes(self):
        content = "FEED = " + json.dumps("https://example.com/home/feed") + "\n"
        self.assertEqual(self.write("src/feed.py", content), (None, ()))

    def test_vue_template_path_passes_while_script_path_denies(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = (
            "<template>\n  <p>" + rooted("Users", OWNER, "notes") + "</p>\n</template>\n<script>\n"
            "export default { data() { return { dir: " + json.dumps(rooted("Users", OWNER, "data")) + " } } }\n"
            "</script>\n"
        )
        self.assertEqual(self.write("src/App.vue", content), (DENY, (5,)))

    def test_edit_that_keeps_an_existing_path_passes(self):
        verdict = self.edit("src/kept.py", KEPT_PATH_LINE, KEPT_PATH_LINE + "\n" + COUNT_LINE)
        self.assertEqual(verdict[0], None)

    def test_edit_that_adds_a_temp_path_notes_its_line(self):
        """A test must verify what the code does for its client, never how it does it."""
        verdict = self.edit("src/added.py", COUNT_LINE, COUNT_LINE + "\n" + KEPT_PATH_LINE, SYSTEM_RULE)
        self.assertEqual(verdict, (None, (2,)))

    def test_flask_route_with_a_parameter_passes(self):
        content = "@app.route(" + json.dumps(rooted("home", "<user>")) + ")\ndef profile(user):\n    return user\n"
        self.assertEqual(self.write("src/app.py", content), (None, ()))

    def test_fetch_of_an_api_route_passes(self):
        content = "fetch(" + json.dumps(rooted("private", "api", "items")) + ");\n"
        self.assertEqual(self.write("src/api.js", content), (None, ()))

    def test_express_route_parameter_passes(self):
        content = "app.get(" + json.dumps(rooted("home", ":id")) + ", h);\n"
        self.assertEqual(self.write("src/r.js", content), (None, ()))

    def test_route_pattern_assigned_to_a_plain_name_passes(self):
        content = "PATTERN = " + json.dumps(rooted("home", "<user>")) + "\n"
        self.assertEqual(self.write("src/patterns.py", content), (None, ()))

    def test_machine_path_after_a_skipped_route_on_one_line_denies(self):
        """A test must verify what the code does for its client, never how it does it."""
        route = json.dumps(rooted("home", "<u>"))
        content = "x = url_for(" + route + "); y = " + json.dumps(rooted("Users", OWNER, "d")) + "\n"
        self.assertEqual(self.write("src/links.py", content), (DENY, (1,)))

    def test_plain_open_call_with_a_machine_path_denies(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "open(" + json.dumps(rooted("Users", OWNER, "x")) + ")\n"
        self.assertEqual(self.write("src/open.py", content), (DENY, (1,)))

    def data_module(self):
        return "DATA = " + json.dumps(rooted("Users", OWNER, "data")) + "\n"

    def test_code_module_named_compose_denies(self):
        self.assertEqual(self.write("src/compose.py", self.data_module()), (DENY, (1,)))

    def test_code_module_named_deployer_denies(self):
        self.assertEqual(self.write("src/deployer.py", self.data_module()), (DENY, (1,)))

    def test_code_module_inside_a_deploy_package_denies(self):
        self.assertEqual(self.write("src/deploy/core.py", self.data_module()), (DENY, (1,)))

    def test_root_deploy_script_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "#!/bin/sh\ncp a " + rooted("Users", OWNER, "x") + "\n"
        self.assertEqual(self.write("deploy.sh", content), (None, ()))

    def test_dockerfile_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "FROM python:3.12\nWORKDIR " + rooted("Users", OWNER, "x") + "\n"
        self.assertEqual(self.write("Dockerfile", content), (None, ()))

    def test_compose_file_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "services:\n  app:\n    volumes:\n      - " + rooted("Users", OWNER, "x") + ":/data\n"
        self.assertEqual(self.write("docker-compose.yml", content), (None, ()))

    def test_ci_workflow_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "jobs:\n  build:\n    steps:\n      - run: cp a " + rooted("Users", OWNER, "x") + "\n"
        self.assertEqual(self.write(".github/workflows/ci.yml", content), (None, ()))

    def test_shell_end_of_options_marker_does_not_hide_a_path(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "cp -- \"$1\" " + rooted("Users", OWNER, "x") + "\n"
        self.assertEqual(self.write("src/copy.sh", content), (DENY, (1,)))

    def test_sql_dash_comment_with_a_path_passes(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "-- seeded from " + rooted("Users", OWNER, "x") + "\nSELECT 1;\n"
        self.assertEqual(self.write("src/seed.sql", content), (None, ()))

    def test_sql_statement_with_a_path_denies(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "SELECT 1;\nCOPY items FROM '" + rooted("Users", OWNER, "x") + "';\n"
        self.assertEqual(self.write("src/seed.sql", content), (DENY, (2,)))

    def computed_root_test(self, expression):
        return python_test(expression, prelude="\nROOT = Path(__file__).resolve().parent")

    def test_python_f_string_joined_to_a_computed_root_passes(self):
        content = self.computed_root_test('Path(f"{ROOT}' + rooted("tmp", "cache.json") + '")')
        self.assertEqual(self.write("tests/test_cache.py", content), (None, ()))

    def test_typescript_template_joined_to_a_computed_root_passes(self):
        content = (
            'import { root } from "./root";\n\n'
            "// Principle: " + PRINCIPLE + "\n"
            'test("cache dir sits under the root", () => {\n'
            "  const p = `${root}" + rooted("tmp", "cache") + "`;\n"
            '  expect(p.startsWith(root)).toBe(true);\n'
            "});\n"
        )
        self.assertEqual(self.write("tests/cache.test.ts", content), (None, ()))

    def test_shell_parameter_expansion_joined_to_a_root_passes(self):
        content = '#!/bin/sh\nOUT="${ROOT}' + rooted("tmp", "build") + '"\nmkdir -p "$OUT"\n'
        self.assertEqual(self.write("scripts/build.sh", content), (None, ()))

    def test_shell_command_substitution_joined_to_a_root_passes(self):
        content = '#!/bin/sh\nDATA="$(pwd)' + rooted("var", "data") + '"\nls "$DATA"\n'
        self.assertEqual(self.write("scripts/run.sh", content), (None, ()))

    def test_separate_system_literal_after_a_computed_root_join_is_still_noted(self):
        """A test must verify what the code does for its client, never how it does it."""
        expression = 'Path(f"{ROOT}/x" + ' + json.dumps(rooted("var", "data", "y.json")) + ")"
        verdict = self.write("tests/test_cache.py", self.computed_root_test(expression), SYSTEM_RULE)
        self.assertEqual(verdict, (None, (9,)))

    def test_shell_home_expansion_passes_but_absolute_path_still_fails(self):
        """A test must verify what the code does for its client, never how it does it."""
        command = "git config --file {}/.gitconfig user.name"
        expressions = ["subprocess.run(" + json.dumps(command.format(home)) + ", shell=True)" for home in ("~", rooted("Users", OWNER))]

        verdicts = [self.write("tests/test_config.py", python_test(expression, prelude="import subprocess")) for expression in expressions]

        self.assertEqual(verdicts, [(None, ()), (DENY, (8,))])

    def test_form_feed_line_keeps_the_reported_line_number(self):
        """A test must verify what the code does for its client, never how it does it."""
        content = "import os\n\x0c\nDATA = " + json.dumps(rooted("Users", OWNER, "data")) + "\n"
        self.assertEqual(self.write("src/form.py", content), (DENY, (3,)))


if __name__ == "__main__":
    unittest.main()
