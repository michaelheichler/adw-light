from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path

try:
    from .archive_members import config_member_paths
    from .archive_targets import config_archive_reads
    from .config import CONFIG_NAME, project_config_path
    from .copy_targets import COPY_VERBS, _drop_target_dir, _target_directory
    from .shell_expansion import Expansions
    from .shell_syntax import _bare, _basename, _command_word_index, _without_redirects
    from .tests_policy import project_root
    from .write_targets import gated_test_targets
    from .folder_trees import _recursive, _split_operands, followed_descendants
except ImportError:
    from archive_members import config_member_paths
    from archive_targets import config_archive_reads
    from config import CONFIG_NAME, project_config_path
    from copy_targets import COPY_VERBS, _drop_target_dir, _target_directory
    from shell_expansion import Expansions
    from shell_syntax import _bare, _basename, _command_word_index, _without_redirects
    from tests_policy import project_root
    from write_targets import gated_test_targets
    from lib.folder_trees import _recursive, _split_operands, followed_descendants

LINK_VERB = "ln"
FOLDER_COPY_VERBS = (COPY_VERBS - {LINK_VERB, "install"}) | {"ditto", "scp"}
TARGET_DIRECTORY_VERBS = frozenset({"cp", "mv", LINK_VERB})
REMOTE_COPY_VERBS = frozenset({"scp", "rsync"})
REMOTE_OPERAND_RE = re.compile(r"^[^/]*:")
LINK_KEEPING_LETTERS = frozenset("nh")
LINK_KEEPING_FLAG = "--no-dereference"
SYMBOLIC_FLAG = "--symbolic"
DITTO_ARCHIVE_LETTERS = frozenset("cx")


def imported_config_targets(segment: list[str], expansions: Expansions, folder: Path, cwd: Path) -> set[str]:
    found: set[str] = set()
    root = _project_root(cwd)
    for call in expansions.calls(segment) or [segment]:
        index = _command_word_index(call)
        verb = _basename(call[index]) if index < len(call) else ""
        found.update(_copied_config_targets(verb, call[index + 1:], expansions, folder, root))
        for read in config_archive_reads(verb, call[index + 1:], gated_test_targets(call, expansions).archive):
            found.update(config_member_paths(read, folder, CONFIG_NAME))
    return found


def _project_root(cwd: Path) -> Path:
    root = Path(project_root(str(cwd)))
    if root == cwd and not (root / ".git").exists():
        root = project_config_path(cwd).parent
    return root.resolve()


def _copied_config_targets(verb: str, raw: list[str], expansions: Expansions, folder: Path, root: Path) -> list[str]:
    if verb not in FOLDER_COPY_VERBS and verb != LINK_VERB:
        return []
    args = _without_redirects(raw)
    target = _target_directory(args) if verb in TARGET_DIRECTORY_VERBS and "--" not in args else None
    flags, operands = _split_operands(verb, _drop_target_dir(args) if target is not None else args)
    if verb == LINK_VERB:
        return _linked_config_targets(flags, operands, target, expansions, folder, root)
    if not _copies_folders(verb, flags) or (verb in REMOTE_COPY_VERBS and any(REMOTE_OPERAND_RE.match(_bare(word)) for word in operands)):
        return []
    sources = operands if target is not None else operands[:-1]
    destination = expansions.path(target or operands[-1]) if sources else None
    if destination is None or not _inside(folder / destination, root):
        return []
    if any(_may_copy_config(folder, expansions.path(word)) for word in sources):
        return [str(folder / destination / CONFIG_NAME)]
    return []


def _linked_config_targets(flags: list[str], operands: list[str], target: str | None, expansions: Expansions, folder: Path, root: Path) -> list[str]:
    letters = "".join(flag[1:] for flag in flags if not flag.startswith("--"))
    if "s" not in letters and SYMBOLIC_FLAG not in flags:
        return []
    keeps = bool(LINK_KEEPING_LETTERS.intersection(letters)) or LINK_KEEPING_FLAG in flags
    single = target is None and len(operands) == 1
    sources = operands if target is not None or single else operands[:-1]
    place_word = target if target is not None else ("." if single else operands[-1])
    place = expansions.path(place_word) if sources else None
    if place is None:
        return []
    found = []
    for source in map(expansions.path, sources):
        link = _link_path(folder / place, source, keeps)
        if link is not None and _inside(link.parent, root) and config_presence(link.parent, source) is True:
            found.append(str(link / CONFIG_NAME))
    return found


def _link_path(place: Path, source: str | None, keeps: bool) -> Path | None:
    if source is None:
        return None
    into = place.is_dir() and not (keeps and place.is_symlink())
    return place / os.path.basename(source.rstrip("/")) if into else place


def _inside(path: Path, root: Path) -> bool:
    return Path(os.path.realpath(path)).is_relative_to(root)


def _copies_folders(verb: str, flags: list[str]) -> bool:
    if verb == "ditto":
        return not DITTO_ARCHIVE_LETTERS.intersection("".join(flag[1:] for flag in flags if not flag.startswith("--")))
    archive = any(flag == "--archive" or (flag.startswith("-") and not flag.startswith("--") and "a" in flag[1:]) for flag in flags)
    return verb == "mv" or archive or _recursive(flags)


def _may_copy_config(folder: Path, source: str | None) -> bool:
    if source is None:
        return False
    root = folder / source
    try:
        if not stat.S_ISDIR(root.stat().st_mode):
            return False
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    files = followed_descendants(str(root))
    return files is None or any(Path(path).name.casefold() == CONFIG_NAME for path in files)


def config_presence(folder: Path, source: str | None, *, until: float | None = None) -> bool | None:
    if source is None:
        return False
    root = folder / source
    try:
        if not stat.S_ISDIR(root.stat().st_mode):
            return False
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return None
    pending = [root]
    seen = set()
    deadline = min(until or float("inf"), time.monotonic() + 0.035)
    try:
        while pending:
            directory = pending.pop()
            if time.monotonic() >= deadline:
                return None
            identity = os.path.realpath(directory)
            if identity in seen:
                continue
            seen.add(identity)
            if (directory / CONFIG_NAME).is_file():
                return True
            with os.scandir(directory) as entries:
                for child in entries:
                    if time.monotonic() >= deadline:
                        return None
                    if child.name.casefold() == CONFIG_NAME and child.is_file():
                        return True
                    if child.is_dir():
                        pending.append(Path(child.path))
    except OSError:
        return None
    return False
