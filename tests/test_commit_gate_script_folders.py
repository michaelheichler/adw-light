import unittest

from commit_view_home import CLEAN_LIB, PROSE_COMMIT, SCANNED, CommitViewHome, script


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
TOOL = "bash tools/s.sh"
IDIOMS = {
    "cd_source_prefix": CD_SOURCE_PREFIX,
    "cd_dirname_source": CD_DIRNAME_SOURCE,
    "pwd_fallback": CD_DIRNAME_ZERO_PWD_FALLBACK,
    "assigned_fallback": ASSIGNED_DIRNAME_FALLBACK,
    "guarded_assignment": GUARDED_ASSIGNMENT,
}


def tool_sourcing(body, folder_lib, working_lib):
    return {"tools/s.sh": script(body), "tools/lib/x.sh": folder_lib, "lib/x.sh": working_lib}


def lib_sourcing(sibling, nested_decoy):
    return {"tools/s.sh": script(SOURCE_FROM_DIRNAME), "tools/lib/x.sh": PARENT_OF_SOURCED_LIB,
            "tools/lib/y.sh": sibling, "tools/lib/lib/y.sh": nested_decoy}


class FolderHome(CommitViewHome):
    projects = {
        **{name + "_clean": tool_sourcing(body, CLEAN_LIB, PROSE_COMMIT) for name, body in IDIOMS.items()},
        **{name + "_prose": tool_sourcing(body, PROSE_COMMIT, CLEAN_LIB) for name, body in IDIOMS.items()},
        "parent_clean": lib_sourcing(CLEAN_LIB, PROSE_COMMIT),
        "parent_prose": lib_sourcing(PROSE_COMMIT, CLEAN_LIB),
        "absolute_clean": {"scripts/tool.sh": script(CLEAN_LIB)},
        "absolute_prose": {"scripts/tool.sh": script(PROSE_COMMIT)},
    }

    def by_absolute_folder(self, name):
        return f'bash "$(CDPATH=\'\' cd -- "{self.project(name)}/scripts/.." && pwd -P)/scripts/tool.sh"'


class ScriptFolderIdioms(FolderHome):
    def test_cd_into_the_source_prefix_passes_a_clean_lib(self):
        self.assertEqual({}, self.gate("cd_source_prefix_clean", TOOL))

    def test_cd_into_the_source_prefix_scans_the_lib(self):
        self.assertEqual(SCANNED, self.message_finding("cd_source_prefix_prose", TOOL))

    def test_cd_into_dirname_of_the_source_passes_a_clean_lib(self):
        self.assertEqual({}, self.gate("cd_dirname_source_clean", TOOL))

    def test_cd_into_dirname_of_the_source_scans_the_lib(self):
        self.assertEqual(SCANNED, self.message_finding("cd_dirname_source_prose", TOOL))

    def test_cd_with_a_pwd_fallback_passes_a_clean_lib(self):
        self.assertEqual({}, self.gate("pwd_fallback_clean", TOOL))

    def test_cd_with_a_pwd_fallback_scans_the_lib(self):
        self.assertEqual(SCANNED, self.message_finding("pwd_fallback_prose", TOOL))

    def test_assigned_folder_with_a_dirname_fallback_passes_a_clean_lib(self):
        self.assertEqual({}, self.gate("assigned_fallback_clean", TOOL))

    def test_assigned_folder_with_a_dirname_fallback_scans_the_lib(self):
        self.assertEqual(SCANNED, self.message_finding("assigned_fallback_prose", TOOL))

    def test_folder_assigned_inside_an_if_guard_passes_a_clean_lib(self):
        self.assertEqual({}, self.gate("guarded_assignment_clean", TOOL))

    def test_folder_assigned_inside_an_if_guard_scans_the_lib(self):
        self.assertEqual(SCANNED, self.message_finding("guarded_assignment_prose", TOOL))


class SourcedLibParentFolder(FolderHome):
    def test_parent_folder_with_a_return_guard_passes_a_clean_lib(self):
        self.assertEqual({}, self.gate("parent_clean", TOOL))

    def test_parent_folder_with_a_return_guard_scans_the_lib(self):
        self.assertEqual(SCANNED, self.message_finding("parent_prose", TOOL))


class AbsoluteFolderSubstitution(FolderHome):
    def test_cd_into_a_literal_parent_folder_passes_a_clean_script(self):
        self.assertEqual({}, self.gate("absolute_clean", self.by_absolute_folder("absolute_clean")))

    def test_cd_into_a_literal_parent_folder_scans_the_script(self):
        self.assertEqual(SCANNED, self.message_finding("absolute_prose", self.by_absolute_folder("absolute_prose")))


if __name__ == "__main__":
    unittest.main()
