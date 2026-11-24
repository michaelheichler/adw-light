from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache

try:
    from .temp_scope import FIXED_TEMP_ROOTS
    from .tests_policy import is_test_path, project_root
except ImportError:
    from temp_scope import FIXED_TEMP_ROOTS
    from tests_policy import is_test_path, project_root

TEMP_ROOTS = tuple(root for root in FIXED_TEMP_ROOTS if root.endswith("/tmp"))
DYNAMIC_RE = re.compile(r"[$`*?\[{\\\"']")
MIN_NAME_CHARS = 4
GIT_SECONDS = 5
REVISION = "HEAD"


def mirrored_temp(path: str, token: str) -> bool:
    prefix = literal_prefix(token)
    if prefix is None:
        return False
    root = project_root(os.path.dirname(os.path.abspath(path)))
    if not is_test_path(path, root):
        return False
    return any(not is_test_path(name, root) for name in tracked_sources(root, prefix))


def literal_prefix(token: str) -> str | None:
    root = next((item for item in TEMP_ROOTS if token.startswith(item + "/")), None)
    if root is None:
        return None
    found = DYNAMIC_RE.search(token)
    prefix = token[:found.start()] if found else token
    return prefix if len(prefix) - len(root) - 1 >= MIN_NAME_CHARS else None


@lru_cache(maxsize=32)
def tracked_sources(root: str, prefix: str) -> tuple[str, ...]:
    command = ["git", "-C", root, "grep", "-l", "-z", "-F", "-e", prefix, REVISION, "--"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=GIT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()
    names = result.stdout.decode("utf-8", "replace").split("\0")
    return tuple(name.split(":", 1)[1] for name in names if name.startswith(REVISION + ":"))
