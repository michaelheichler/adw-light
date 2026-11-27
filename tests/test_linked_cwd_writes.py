import json
import unittest

from after_run_home import CODEX, DENY, GATE, TEST_WRITER, AfterRunCase, Project, identity, project_files
from fake_repo_home import SIZE_TEST


PROJ = "proj"
OUTSIDE = "outside"
LINK = "proj/out"
BARE = "bare"
BARE_LINK = "proj/bare"
CONFIG = ".agent-discipline.json"
FLAGGED = "We utilize this tool for the build.\n"
CLEAN = "Short.\n"
LENIENT = json.dumps({"tests": "allow", "rule_gates": {"utilize": "off"}}, indent=2) + "\n"
NEW_TEST = "tests/test_new.py"
OLD_TEST = "tests/test_old.py"
NOTES = "docs/new.md"
OPAQUE = "printf 'More.\\n' >> \"$(cat target.txt)\""
NESTED = "nested"
NESTED_HELPER = "tests/e2e/helpers.py"
EDGE = "edge"
EDGE_PROJ = "edge/tests/proj"
APP = "src/app.py"
PASSED = (None, [])
REFUSED = (DENY, [GATE])
FLAGGED_WRITE = (DENY, ["utilize"])


class LinkedCwdWrites(AfterRunCase):
    projects = {
        PROJ: Project(project_files({"docs/old.md": CLEAN})),
        OUTSIDE: Project({CONFIG: LENIENT}, git=False),
        BARE: Project({"target.txt": "../proj/docs/made.md\n"}, git=False),
        NESTED: Project({"pyproject.toml": "[project]\nname = 'nested'\n", "tests/e2e/package.json": "{}\n"}, git=False),
        EDGE: Project({"Makefile": "all:\n", "tests/proj/pyproject.toml": "[project]\nname = 'proj'\n", "tests/proj/" + APP: "x = [2]\n"}, git=False),
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.root / LINK).symlink_to(cls.root / OUTSIDE, target_is_directory=True)
        (cls.root / BARE_LINK).symlink_to(cls.root / BARE, target_is_directory=True)

    def tool(self, tool_name, tool_input, host="claude", cwd=LINK):
        self.start(cwd, host)
        response = self.respond("PreToolUse", cwd, {"tool_name": tool_name, "tool_use_id": "w1", "tool_input": tool_input}, host)
        return response.get("hookSpecificOutput", {}).get("permissionDecision"), self.report_rules()

    def write_into(self, path, content, cwd=LINK):
        return self.tool("Write", {"file_path": str(path), "content": content}, cwd=cwd)

    def edit_into(self, path):
        return self.tool("Edit", {"file_path": str(path), "old_string": "2]", "new_string": "3]"})

    def named_shell(self, command, cwd=LINK):
        self.start(cwd)
        return self.pre(cwd, command), self.report_rules()

    def patch(self, files):
        body = "".join(f"*** Add File: {name}\n+{content}" for name, content in files.items())
        return self.tool("apply_patch", {"input": "*** Begin Patch\n" + body + "*** End Patch\n"}, CODEX)

    def reported_paths(self):
        reports = sorted((self.state / "reports").glob(self.session() + "-*.json"))
        rows = (row for report in reports for row in json.loads(report.read_text(encoding="utf-8")))
        return sorted({(row["rule"], str(self.root / LINK / row["path"]).removeprefix(str(self.root) + "/")) for row in rows})

    def project(self, relative):
        return self.root / PROJ / relative

    def physical(self, relative):
        return "../" + PROJ + "/" + relative

    def test_write_of_a_new_test_by_absolute_path_from_a_linked_cwd_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.write_into(self.project(NEW_TEST), SIZE_TEST), REFUSED)

    def test_write_of_a_new_test_by_physical_relative_path_from_a_linked_cwd_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.write_into(self.physical(NEW_TEST), SIZE_TEST), REFUSED)

    def test_edit_of_a_test_by_absolute_path_from_a_linked_cwd_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.edit_into(self.project(OLD_TEST)), REFUSED)

    def test_edit_of_a_test_by_physical_relative_path_from_a_linked_cwd_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.edit_into(self.physical(OLD_TEST)), REFUSED)

    def test_codex_patch_judges_each_destination_by_its_own_policy(self):
        """A test must verify what the code does for its client, never how it does it."""
        files = {self.project(NEW_TEST): SIZE_TEST, self.project(NOTES): FLAGGED, "scratch.md": CLEAN}
        decision, _ = self.patch(files)
        self.assertEqual((decision, self.reported_paths()), (DENY, [(GATE, "proj/" + NEW_TEST), ("utilize", "proj/" + NOTES)]))

    def test_project_prose_rules_govern_a_write_despite_a_lenient_outside_config(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.write_into(self.project(NOTES), FLAGGED), FLAGGED_WRITE)

    def test_outside_config_that_allows_tests_does_not_open_the_project_test_gate(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.write_into(self.physical(NEW_TEST), SIZE_TEST), REFUSED)

    def test_write_into_the_outside_folder_follows_the_outside_config(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.write_into(self.root / OUTSIDE / "notes.md", FLAGGED), PASSED)

    def test_shell_write_of_a_named_test_by_absolute_path_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.named_shell(f"printf 'x\\n' > {self.project(NEW_TEST)}"), REFUSED)

    def test_shell_write_of_a_named_test_by_physical_relative_path_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.named_shell(f"printf 'x\\n' > {self.physical(NEW_TEST)}"), REFUSED)

    def test_literal_shell_prose_write_into_the_project_gets_its_finding(self):
        """Feed input, check the return value."""
        self.assertEqual(self.named_shell(f"printf 'We utilize this tool for the build.\\n' > {self.project(NOTES)}"), FLAGGED_WRITE)

    def test_opaque_shell_finding_is_released_at_stop_once_repaired(self):
        """Act, then check the state of the system under test or its collaborators through the public API."""
        self.after_run(BARE_LINK, OPAQUE, lambda: self.change(PROJ, "docs/made.md", FLAGGED), before=identity(TEST_WRITER))
        self.change(PROJ, "docs/made.md", CLEAN)
        self.assertIsNone(self.stop(BARE_LINK))

    def test_write_of_a_helper_under_a_nested_package_marker_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.write_into(self.root / NESTED / NESTED_HELPER, CLEAN, cwd=NESTED), REFUSED)

    def test_shell_redirect_to_a_helper_under_a_nested_package_marker_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.named_shell(f"printf 'x\\n' > {self.root / NESTED / NESTED_HELPER}", cwd=NESTED), REFUSED)

    def edge_project(self, relative):
        return self.root / EDGE_PROJ / relative

    def test_source_write_in_a_project_nested_under_an_outer_tests_folder_passes(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.write_into(self.edge_project("src/app2.py"), CLEAN, cwd=EDGE_PROJ), PASSED)

    def test_source_edit_in_a_project_nested_under_an_outer_tests_folder_passes(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        edit = {"file_path": str(self.edge_project(APP)), "old_string": "2]", "new_string": "3]"}
        self.assertEqual(self.tool("Edit", edit, cwd=EDGE_PROJ), PASSED)

    def test_source_shell_redirect_in_a_project_nested_under_an_outer_tests_folder_passes(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.named_shell("printf 'x\\n' > src/app3.py", cwd=EDGE_PROJ), PASSED)

    def test_test_write_in_a_project_nested_under_an_outer_tests_folder_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.write_into(self.edge_project("tests/test_x.py"), SIZE_TEST, cwd=EDGE_PROJ), REFUSED)

    def test_write_of_a_nested_helper_from_the_outer_tests_folder_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.write_into(self.root / NESTED / NESTED_HELPER, CLEAN, cwd=NESTED + "/tests"), REFUSED)

    def test_shell_redirect_to_a_nested_helper_from_the_outer_tests_folder_is_refused(self):
        """The test fails when a real bug enters the code it exercises."""
        self.assertEqual(self.named_shell(f"printf 'x\\n' > {self.root / NESTED / NESTED_HELPER}", cwd=NESTED + "/tests"), REFUSED)

    def test_helper_write_from_inside_its_own_marked_package_passes_by_lead_decision_as_on_main(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.write_into(self.root / NESTED / NESTED_HELPER, CLEAN, cwd=NESTED + "/tests/e2e"), PASSED)

    def test_outside_config_that_allows_tests_opens_a_test_write_into_the_outside_folder(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.write_into(self.root / OUTSIDE / NEW_TEST, SIZE_TEST), PASSED)

    def test_outside_config_with_a_rule_off_is_ignored_for_a_prose_write_by_physical_relative_path(self):
        """Observable behavior is an operation or a state that helps a client reach one of its goals."""
        self.assertEqual(self.write_into(self.physical(NOTES), FLAGGED), FLAGGED_WRITE)

    def test_unrelated_scratch_write_from_a_linked_cwd_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        self.assertEqual(self.write_into(self.root / OUTSIDE / "scratch.md", CLEAN), PASSED)

    def test_unrelated_scratch_shell_write_from_a_linked_cwd_passes(self):
        """The test stays green when you restructure the code without changing its behavior."""
        self.assertEqual(self.named_shell("printf 'Short.\\n' > scratch.txt"), PASSED)


if __name__ == "__main__":
    unittest.main()
