from fake_repo_home import DENY, PASSED, README, FakeRepoCase, Repo
import unittest


PATH_FOUND = (DENY, ["hard_coded_path"])
SLOP_FOUND = (DENY, ["prose_semicolon", "spaced_hyphen", "utilize"])
SLOP_LINE = "We utilize the parser - fast; then it stops"
OUT = "out"
LOGS = "logs"


class OutputFolderHome(FakeRepoCase):
    projects = {folder: Repo({**README, ".gitignore": folder + "/\n"}) for folder in (OUT, LOGS)}

    def ignored_write(self, folder, relative, content):
        return self.write(folder, folder + "/" + relative, content)

    def plugin_cache(self):
        return str(self.home / ".adw-lite" / "cache" / "plugin" / "adw-light")


class CodeInsideIgnoredOutputFiles(OutputFolderHome):
    def test_log_holding_a_one_line_shell_function_keeps_its_path_finding(self):
        content = "f() { echo hi; }\ncp -R build " + self.plugin_cache() + "\n"
        self.assertEqual(self.ignored_write(LOGS, "helper.log", content), PATH_FOUND)

    def test_out_file_holding_top_level_python_keeps_its_path_finding(self):
        content = "TARGET = " + repr(self.plugin_cache()) + "\nprint(TARGET)\n"
        self.assertEqual(self.ignored_write(LOGS, "helper.out", content), PATH_FOUND)

    def test_log_holding_a_shell_function_with_a_subshell_body_keeps_its_path_finding(self):
        content = "f() (\n  cp -R build " + self.plugin_cache() + "\n)\n"
        self.assertEqual(self.ignored_write(LOGS, "helper.log", content), PATH_FOUND)

    def test_plain_deploy_log_holding_a_temp_folder_path_passes(self):
        release = str(self.root / "stage" / "release")
        content = "deploy started\ncopied release to " + release + "\ndeploy finished\n"
        self.assertEqual(self.ignored_write(LOGS, "deploy.log", content), PASSED)


class ProseInsideIgnoredTapStreams(OutputFolderHome):
    def test_test_description_holding_slop_prose_is_refused(self):
        content = "1..1\nok 1 - " + SLOP_LINE + "\n"
        self.assertEqual(self.ignored_write(OUT, "results.tap", content), SLOP_FOUND)

    def test_diagnostic_line_holding_slop_prose_is_refused(self):
        content = "1..1\nok 1 - parses input\n# " + SLOP_LINE + "\n"
        self.assertEqual(self.ignored_write(OUT, "results.tap", content), SLOP_FOUND)

    def test_plain_test_description_passes(self):
        self.assertEqual(self.ignored_write(OUT, "results.tap", "1..1\nok 1 - parses input\n"), PASSED)


if __name__ == "__main__":
    unittest.main()
