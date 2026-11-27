import gzip
import io
import os
import sys
import tarfile
import unittest
import zipfile
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
archive_members = import_module("lib.archive_members")
cpio_members = import_module("lib.cpio_members")
folder_trees = import_module("lib.folder_trees")
CONFIG_NAME = ".agent-discipline.json"
LIMIT = 4096
CAP = 10


def odc_member(name, body):
    encoded = name.encode() + b"\0"
    header = b"070707" + b"0" * 42 + b"0" * 11 + b"%06o" % len(encoded) + b"%011o" % len(body)
    return header + encoded + body


def newc_member(name, body):
    encoded = name.encode() + b"\0"
    fields = [0] * 6 + [len(body)] + [0] * 4 + [len(encoded), 0]
    header = b"070701" + b"".join(b"%08X" % value for value in fields)
    named = header + encoded
    return named + b"\0" * (-len(named) % 4) + body + b"\0" * (-len(body) % 4)


def cpio_archive(member, names):
    return b"".join(member(name, b"{}") for name in names) + member("TRAILER!!!", b"")


def cpio_read(data, limit=LIMIT, cap=CAP):
    return cpio_members.cpio_names(io.BytesIO(data), limit, cap)


def tar_with(path, name):
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo(name)
        info.size = 2
        archive.addfile(info, io.BytesIO(b"{}"))


class ConfigMemberLandings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        cls.folder = Path(workspace.name).resolve() / "land"
        cls.folder.mkdir()
        tar_with(cls.folder / "trav.tar", "../" + CONFIG_NAME)
        cls.absolute = cls.folder.parent / "elsewhere" / CONFIG_NAME
        tar_with(cls.folder / "abs.tar", str(cls.absolute))
        tar_with(cls.folder / "deep.tar", "a/b/" + CONFIG_NAME)
        with zipfile.ZipFile(cls.folder / "deep.zip", "w") as archive:
            archive.writestr("a/b/" + CONFIG_NAME, "{}")

    def landings(self, source, **fields):
        kind = archive_members.ZIP if source.endswith(".zip") else archive_members.TAR
        read = archive_members.ArchiveRead(kind, source, **fields)
        return archive_members.config_member_paths(read, self.folder, CONFIG_NAME)

    def test_each_member_spelling_reports_where_the_config_would_land(self):
        """Feed input, check the return value."""
        folder = self.folder
        found = {
            "traversal": str(folder.parent / CONFIG_NAME) in self.landings("trav.tar"),
            "leading_slash": str(folder.joinpath(*self.absolute.parts[1:])) in self.landings("abs.tar"),
            "absolute_names": str(self.absolute) in self.landings("abs.tar", absolute=True),
            "strip_one": str(folder / "b" / CONFIG_NAME) in self.landings("deep.tar", strip=1),
            "strip_two": str(folder / CONFIG_NAME) in self.landings("deep.tar", strip=2),
            "junk_paths": str(folder / "out" / CONFIG_NAME) in self.landings("deep.zip", directory="out", junk_paths=True),
        }
        self.assertEqual(found, dict.fromkeys(found, True))

    def test_stripping_every_folder_and_the_name_itself_lands_nothing(self):
        """Feed input, check the return value."""
        self.assertEqual(self.landings("deep.tar", strip=3), [])


class CpioMemberNames(unittest.TestCase):
    def test_both_header_formats_list_their_members_plain_and_gzipped(self):
        """Feed input, check the return value."""
        names = ["pkg/" + CONFIG_NAME, "pkg/notes.txt"]
        odc, newc = cpio_archive(odc_member, names), cpio_archive(newc_member, names)
        archives = {"odc": odc, "newc": newc, "odc_gzip": gzip.compress(odc), "newc_gzip": gzip.compress(newc)}
        found = {label: cpio_read(data) for label, data in archives.items()}
        self.assertEqual(found, dict.fromkeys(found, names))

    def test_archives_past_the_size_or_member_cap_are_unreadable(self):
        """Feed input, check the return value."""
        names = ["n%d" % index for index in range(CAP + 1)]
        bomb = gzip.compress(cpio_archive(odc_member, ["big"]) + b"\0" * (LIMIT * 4))
        found = {
            "raw_size": cpio_read(cpio_archive(newc_member, ["pkg/x"]) + b"\0" * LIMIT),
            "expanded_size": cpio_read(bomb),
            "members": cpio_read(cpio_archive(odc_member, names)),
            "truncated": cpio_read(cpio_archive(odc_member, ["pkg/x"])[:-20]),
        }
        self.assertEqual(found, dict.fromkeys(found))


class FollowedDescendants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = TemporaryDirectory()
        cls.addClassCleanup(workspace.cleanup)
        base = Path(workspace.name).resolve()
        cls.root = base / "tree"
        (cls.root / "inner").mkdir(parents=True)
        (base / "cfgdir").mkdir()
        (base / "cfgdir" / "settings.txt").touch()
        (cls.root / "keep.txt").touch()
        (cls.root / "linked").symlink_to(base / "cfgdir", target_is_directory=True)
        (cls.root / "inner" / "loop").symlink_to(cls.root, target_is_directory=True)

    def test_walk_follows_folder_links_and_stops_at_a_cycle(self):
        """Feed input, check the return value."""
        found = sorted(os.path.relpath(path, self.root) for path in folder_trees.followed_descendants(str(self.root)))
        self.assertEqual(found, ["keep.txt", os.path.join("linked", "settings.txt")])


if __name__ == "__main__":
    unittest.main()
