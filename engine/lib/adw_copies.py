from __future__ import annotations

import json
import hashlib
from pathlib import Path

try:
    from . import config_roots as config_paths
except ImportError:
    import config_roots as config_paths
from typing import NamedTuple

PLUGIN_NAME = "adw-light"
HEAD_BYTES = 4096
MAX_ACCOUNT_PLUGINS = 200
ACCOUNT_PLUGINS = "Library/Application Support/Claude/local-agent-mode-sessions/*/*/rpm/*"
CLI_RECORD = ".claude/plugins/installed_plugins.json"
ACCOUNT_LABEL = "Claude account plugin (Desktop and Cowork)"
CLI_LABEL = "Claude Code plugin"
SHARED_LABEL = "Codex and OMP install"
Release = str


class Copy(NamedTuple):
    root: Path
    release: Release
    label: str


def release_of(root: Path) -> Release | None:
    if not is_adw(root):
        return None
    digest = hashlib.sha256()
    try:
        for path in [root / "adw_light.py", *sorted((root / "engine").rglob("*.py"))]:
            digest.update(str(path.relative_to(root)).encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    except OSError:
        return None
    return digest.hexdigest()


def own_root() -> Path:
    return Path(__file__).resolve().parents[2]


def is_adw(root: Path) -> bool:
    try:
        manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")[:HEAD_BYTES])
    except (OSError, ValueError):
        return False
    return type(manifest) is dict and manifest.get("name") == PLUGIN_NAME


def _cli_roots(home: Path) -> list[Path]:
    try:
        record = json.loads((home / CLI_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    plugins = record.get("plugins") if type(record) is dict else None
    entries = plugins.get(f"{PLUGIN_NAME}@{PLUGIN_NAME}") if type(plugins) is dict else None
    paths = [entry.get("installPath") for entry in entries or [] if type(entry) is dict]
    return [Path(path) for path in paths if isinstance(path, str) and path]


def account_roots(home: Path) -> list[Path]:
    found = []
    for index, root in enumerate(home.glob(ACCOUNT_PLUGINS)):
        if index >= MAX_ACCOUNT_PLUGINS:
            break
        if is_adw(root):
            found.append(root)
    return found


def copies(home: Path | None = None) -> list[Copy]:
    base = config_paths.home_root(home)
    labelled = [(root, CLI_LABEL) for root in _cli_roots(base)]
    labelled += [(config_paths.install_root(base), SHARED_LABEL)]
    labelled += [(root, ACCOUNT_LABEL) for root in account_roots(base)]
    found = []
    for root, label in labelled:
        version = release_of(root)
        if version is not None:
            found.append(Copy(root, version, label))
    return found


def stale(home: Path | None = None, own: Path | None = None) -> list[Copy]:
    running = own if own is not None else own_root()
    current = release_of(running)
    if current is None:
        return []
    resolved = running.resolve()
    return [copy for copy in copies(home) if copy.release != current and copy.root.resolve() != resolved]


def _dotted(version: Release) -> str:
    return version[:12]


def notice(found: list[Copy], own: Path | None = None) -> str:
    if not found:
        return ""
    current = release_of(own if own is not None else own_root())
    running = _dotted(current) if current else "this release"
    listed = "\n".join(f"- {_dotted(copy.release)} as the {copy.label} at {copy.root}" for copy in found)
    return (
        f"ADW Lite found a different engine beside {running}. "
        f"Check which copy each host uses.\n{listed}"
    )


def status_lines(home: Path | None = None) -> list[str]:
    rows = [f"  {_dotted(copy.release)} {copy.label}: {copy.root}" for copy in copies(home)]
    return ["ADW copies on this machine:", *rows] if rows else ["ADW copies on this machine: none found"]
