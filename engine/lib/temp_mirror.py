from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

try:
    from . import git_port
    from .temp_scope import FIXED_TEMP_ROOTS
    from .tests_policy import is_test_path, project_root
except ImportError:
    import git_port
    from temp_scope import FIXED_TEMP_ROOTS
    from tests_policy import is_test_path, project_root

TEMP_ROOTS = tuple(root for root in FIXED_TEMP_ROOTS if root.endswith("/tmp"))
DYNAMIC_RE = re.compile(r"[$`*?\[{\\\"']")
MIN_NAME_CHARS = 4


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


def tracked_sources(root: str, prefix: str) -> tuple[str, ...]:
    return _view_sources(git_port.view(), root, prefix)


@lru_cache(maxsize=32)
def _view_sources(git_view: git_port.GitView, root: str, prefix: str) -> tuple[str, ...]:
    return tuple(git_view.grep_tracked(Path(root), prefix))
