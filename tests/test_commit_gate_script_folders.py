import unittest

from commit_gate_home import CLEAN_LIB, PROSE_COMMIT, SCANNED, SHEBANG, CommitGateHome


CD_SOURCE_PREFIX = '. "$(CDPATH=\'\' cd -- "${BASH_SOURCE[0]%/*}" 2>/dev/null && pwd)/lib/x.sh"\n'
CD_DIRNAME_SOURCE = '. "$(CDPATH=\'\' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/x.sh"\n'
CD_DIRNAME_ZERO_PWD_FALLBACK = '. "$(CDPATH=\'\' cd -- "$(dirname "$0")" && pwd -P 2>/dev/null || pwd)/lib/x.sh"\n'
ASSIGNED_DIRNAME_FALLBACK = (
    'SCRIPT_DIR=$(CDPATH=\'\' cd -- "$(dirname "$0")" && pwd -P 2>/dev/null || dirname "$0")\n'
    '. "$SCRIPT_DIR/lib/x.sh"\n'
)
GUARDED_ASSIGNMENT = (
    'if ! SCRIPT_DIR="$(CDPATH=\'\' cd -- "$(dirname "$0")" && pwd -P 2>/dev/null)"; then\n'
    "  SCRIPT_DIR=$(pwd -P)\n"
    "fi\n"
    '. "$SCRIPT_DIR/lib/x.sh"\n'
)
PARENT_OF_SOURCED_LIB = (
    'D=$(CDPATH=\'\' cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd) || return 0\n'
    '. "$D/lib/y.sh"\n'
)
SOURCE_FROM_DIRNAME = '. "$(dirname "$0")/lib/x.sh"\n'


class ScriptFolderIdioms(CommitGateHome):
    def tool_sourcing(self, body, folder_lib, working_lib):
        self.place("tools/s.sh", SHEBANG + body)
        self.place("tools/lib/x.sh", folder_lib)
        self.place("lib/x.sh", working_lib)
        return self.gate("bash tools/s.sh")

    def test_cd_into_the_source_prefix_passes_a_clean_lib(self):
        self.assertEqual({}, self.tool_sourcing(CD_SOURCE_PREFIX, CLEAN_LIB, PROSE_COMMIT))

    def test_cd_into_the_source_prefix_scans_the_lib(self):
        response = self.tool_sourcing(CD_SOURCE_PREFIX, PROSE_COMMIT, CLEAN_LIB)

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_cd_into_dirname_of_the_source_passes_a_clean_lib(self):
        self.assertEqual({}, self.tool_sourcing(CD_DIRNAME_SOURCE, CLEAN_LIB, PROSE_COMMIT))

    def test_cd_into_dirname_of_the_source_scans_the_lib(self):
        response = self.tool_sourcing(CD_DIRNAME_SOURCE, PROSE_COMMIT, CLEAN_LIB)

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_cd_with_a_pwd_fallback_passes_a_clean_lib(self):
        self.assertEqual({}, self.tool_sourcing(CD_DIRNAME_ZERO_PWD_FALLBACK, CLEAN_LIB, PROSE_COMMIT))

    def test_cd_with_a_pwd_fallback_scans_the_lib(self):
        response = self.tool_sourcing(CD_DIRNAME_ZERO_PWD_FALLBACK, PROSE_COMMIT, CLEAN_LIB)

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_assigned_folder_with_a_dirname_fallback_passes_a_clean_lib(self):
        self.assertEqual({}, self.tool_sourcing(ASSIGNED_DIRNAME_FALLBACK, CLEAN_LIB, PROSE_COMMIT))

    def test_assigned_folder_with_a_dirname_fallback_scans_the_lib(self):
        response = self.tool_sourcing(ASSIGNED_DIRNAME_FALLBACK, PROSE_COMMIT, CLEAN_LIB)

        self.assertEqual(SCANNED, self.message_finding(response))

    def test_folder_assigned_inside_an_if_guard_passes_a_clean_lib(self):
        self.assertEqual({}, self.tool_sourcing(GUARDED_ASSIGNMENT, CLEAN_LIB, PROSE_COMMIT))

    def test_folder_assigned_inside_an_if_guard_scans_the_lib(self):
        response = self.tool_sourcing(GUARDED_ASSIGNMENT, PROSE_COMMIT, CLEAN_LIB)

        self.assertEqual(SCANNED, self.message_finding(response))


class SourcedLibParentFolder(CommitGateHome):
    def lib_sourcing(self, sibling, nested_decoy):
        self.place("tools/s.sh", SHEBANG + SOURCE_FROM_DIRNAME)
        self.place("tools/lib/x.sh", PARENT_OF_SOURCED_LIB)
        self.place("tools/lib/y.sh", sibling)
        self.place("tools/lib/lib/y.sh", nested_decoy)
        return self.gate("bash tools/s.sh")

    def test_parent_folder_with_a_return_guard_passes_a_clean_lib(self):
        self.assertEqual({}, self.lib_sourcing(CLEAN_LIB, PROSE_COMMIT))

    def test_parent_folder_with_a_return_guard_scans_the_lib(self):
        self.assertEqual(SCANNED, self.message_finding(self.lib_sourcing(PROSE_COMMIT, CLEAN_LIB)))


class AbsoluteFolderSubstitution(CommitGateHome):
    def tool_by_absolute_folder(self, tool):
        self.place("scripts/tool.sh", SHEBANG + tool)
        return self.gate(f'bash "$(CDPATH=\'\' cd -- "{self.root}/scripts/.." && pwd -P)/scripts/tool.sh"')

    def test_cd_into_a_literal_parent_folder_passes_a_clean_script(self):
        self.assertEqual({}, self.tool_by_absolute_folder(CLEAN_LIB))

    def test_cd_into_a_literal_parent_folder_scans_the_script(self):
        self.assertEqual(SCANNED, self.message_finding(self.tool_by_absolute_folder(PROSE_COMMIT)))


if __name__ == "__main__":
    unittest.main()
