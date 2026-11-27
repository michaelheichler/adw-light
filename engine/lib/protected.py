from __future__ import annotations

import json
import os
import re
import stat
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

try:
    from . import config_roots as config_paths
    from .protected_files import AGENT_FILE_SUFFIXES, CLIENT_HOME_DIRS, WIRING_FILES, _protected_identities, _identity_stamp, _existing_metadata
except ImportError:
    import config_roots as config_paths
    from protected_files import AGENT_FILE_SUFFIXES, CLIENT_HOME_DIRS, WIRING_FILES, _protected_identities, _identity_stamp, _existing_metadata

try:
    from .config import (
        CONFIG_NAME, CONTRACT_STRINGS_KEY, DEFAULTS, MAX_PROJECT_CONFIG_BYTES,
        _parse_project_config, load_project_config, project_config_path,
    )
    from .findings import finding_row
    from .path_policy import PROJECT_MARKERS, exited_project, marked_project
    from .regular_file import read_regular_text
    from .temp_scope import cwd_forms, outside_project_temp, scratch_session
except ImportError:
    from config import (
        CONFIG_NAME, CONTRACT_STRINGS_KEY, DEFAULTS, MAX_PROJECT_CONFIG_BYTES,
        _parse_project_config, load_project_config, project_config_path,
    )
    from findings import finding_row
    from path_policy import PROJECT_MARKERS, exited_project, marked_project
    from regular_file import read_regular_text
    from temp_scope import cwd_forms, outside_project_temp, scratch_session

restore_check = ContextVar("config_restore", default=None)
CONFIG_SEAL_BASENAME = CONFIG_NAME
SEAL_MARKERS = PROJECT_MARKERS - {CONFIG_NAME}
POLICY_KEYS = (DEFAULTS.keys() - {"max_rows", "list_item_cap", "prose_colon_limit", "adw_model"}) | {
    CONTRACT_STRINGS_KEY, "function_block_lines", "ledger_root", "max_scan_bytes", "protected_paths_authorized",
    "state_root", "stop_stall_limit", "tests_allow_until",
}
TEST_WRITER_STEM = "adw-test-writer"
AUTH_ENV = "ADW_ALLOW_PROTECTED_EDIT"
TRUTHY = frozenset({"1", "true", "yes", "on"})

WATCHER_NAME_PREFIX = ("agent-discipline", "adw-light")
WATCHER_WIRING_RE = re.compile(r"agent-discipline|adw_light\.py|adw-light|/hooks/run\.sh")
TEST_WRITER_NAME_RE = re.compile(
    r"""^\s*["']?name["']?\s*[:=]\s*["']?adw-test-writer["']?\s*(?:#.*)?$""",
    re.IGNORECASE | re.MULTILINE,
)

INSTALL_ACTION = (
    "Change the repo source and reinstall instead of editing the live install. If this edit "
    "is intentional, ask the human to export "
    + AUTH_ENV
    + " in the hook environment, which releases every self-protection rule, not just this one."
)
WIRING_ACTION = (
    "Keep the agent-discipline-watcher hook entries in place. Edit this file around them, "
    "or reinstall to change how the watcher is wired."
)
POLICY_ACTION = "The user sets this policy. Use /adw-light:config in Claude Code or $adw-config in Codex."
STATE_ACTION = "Leave watcher state under host control and repair the reported finding."
DEFINITION_ACTION = (
    "The test gate trusts this agent file, so the user owns it. Ask the human to edit it in a terminal."
)
SWITCH_ACTION = (
    "Keep the watcher plugin enabled, its hooks on, and the escape variable out of these "
    "settings. Ask the human to make this change in a terminal."
)
@dataclass(frozen=True, slots=True)
class ProtectedWrite:
    path: str
    home: str | os.PathLike[str] | None
    content: str | None
    cwd: Path | None = None


@dataclass(frozen=True, slots=True)
class ResolvedProtectedWrite:
    resolved: Path
    requested: ProtectedWrite


class UnresolvableTildePath(ValueError):
    pass


def _env_authorized() -> bool:
    return os.environ.get(AUTH_ENV, "").strip().lower() in TRUTHY


def authorized(config: dict | None = None) -> bool:
    del config
    return _env_authorized()


def _protected_write(path: str, values: tuple[object, ...], fields: dict[str, object]) -> ProtectedWrite:
    if len(values) > 3:
        raise TypeError("path_findings accepts at most four legacy arguments")
    names = ("config", "home", "content", "cwd")
    legacy = dict(zip(names, values, strict=False))
    for name, value in fields.items():
        if name not in names or name in legacy:
            raise TypeError(f"path_findings got an invalid keyword: {name}")
        legacy[name] = value
    home = legacy.get("home")
    content = legacy.get("content")
    if home is not None and not isinstance(home, (str, os.PathLike)):
        raise TypeError("path_findings home must be a path or None")
    if content is not None and not isinstance(content, str):
        raise TypeError("path_findings content must be a string or None")
    cwd = legacy.get("cwd")
    if cwd is not None and not isinstance(cwd, (str, os.PathLike)):
        raise TypeError("path_findings cwd must be a path or None")
    return ProtectedWrite(path, home, content, Path(cwd) if cwd is not None else None)


def path_findings(write: ProtectedWrite | str, *values: object, **fields: object) -> list[dict]:
    if isinstance(write, str):
        write = _protected_write(write, values, fields)
    elif values or fields:
        raise TypeError("ProtectedWrite cannot be combined with legacy arguments")
    if not write.path or write.path == "<pending>":
        return []
    try:
        resolved = _resolve(write.path, write.home)
    except UnresolvableTildePath:
        if _env_authorized():
            return []
        return [finding_row(family="self_protection", rule="watcher_install_surface", line=1, detail="Unresolvable ~user path in " + write.path, snippet=write.path.strip()[:180], action=INSTALL_ACTION)]
    if resolved is None or _env_authorized():
        return []
    return _resolved_findings(write, resolved) or _hardlink_findings(write, resolved)


def _resolved_findings(write: ProtectedWrite, resolved: Path) -> list[dict]:
    return (
        _definition_findings(write, resolved)
        or _switch_off_findings(write, resolved)
        or _write_target_findings(ResolvedProtectedWrite(resolved, write))
    )


def _hardlink_findings(write: ProtectedWrite, resolved: Path) -> list[dict]:
    try:
        metadata = resolved.stat()
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError:
        return _identity_refusal(write)
    if metadata.st_nlink < 2 or not stat.S_ISREG(metadata.st_mode):
        return []
    identity = (metadata.st_dev, metadata.st_ino)
    try:
        if TEST_WRITER_NAME_RE.search(write.content or "") or TEST_WRITER_NAME_RE.search(read_regular_text(resolved, errors="replace")):
            return [finding_row(family="self_protection", rule="test_writer_definition", line=1, detail="Test writer definition in " + write.path, snippet=write.path.strip()[:180], action=DEFINITION_ACTION)]
        home = config_paths.home_root(write.home)
        candidates = _protected_identities(home, _identity_stamp(home)).get(identity, ())
        candidates = (*candidates, *_seal_aliases(write.cwd or Path.cwd(), identity))
    except OSError:
        return _identity_refusal(write)
    return _alias_findings(write, resolved, candidates)


def _identity_refusal(write: ProtectedWrite) -> list[dict]:
    return [finding_row(family="self_protection", rule="watcher_install_surface", line=1, detail="Cannot verify protected identity for " + write.path, snippet=write.path.strip()[:180], action=INSTALL_ACTION)]


def _alias_findings(write: ProtectedWrite, resolved: Path, candidates: tuple[Path, ...]) -> list[dict]:
    for candidate in candidates:
        normalized = Path(os.path.realpath(candidate))
        if normalized == resolved:
            continue
        alias = ProtectedWrite(str(candidate), write.home, write.content, write.cwd)
        findings = _resolved_findings(alias, normalized)
        if findings:
            return findings
    return []


def _seal_aliases(cwd: Path, identity: tuple[int, int]) -> tuple[Path, ...]:
    start = cwd.resolve()
    aliases = []
    for folder in (start, *start.parents):
        candidate = folder / CONFIG_NAME
        metadata = _existing_metadata(candidate)
        if metadata is not None and (metadata.st_dev, metadata.st_ino) == identity:
            aliases.append(candidate)
    return tuple(aliases)


def _definition_findings(write: ProtectedWrite, resolved: Path) -> list[dict]:
    literal = _literal(write.path, write.home)
    cwd = (write.cwd or Path.cwd()).absolute()
    if not any(_is_test_writer_definition(path, write.content, write.home, cwd) for path in (literal, resolved)):
        return []
    return [finding_row(family="self_protection", rule="test_writer_definition", line=1, detail="Test writer definition in " + write.path, snippet=write.path.strip()[:180], action=DEFINITION_ACTION)]


def _switch_off_findings(write: ProtectedWrite, resolved: Path) -> list[dict]:
    if not _switches_watcher_off((_literal(write.path, write.home), resolved), write.content):
        return []
    return [finding_row(family="self_protection", rule="watcher_wiring_removal", line=1, detail="Write switches the watcher off in " + write.path, snippet=write.path.strip()[:180], action=SWITCH_ACTION)]


def _write_target_findings(write: ResolvedProtectedWrite) -> list[dict]:
    resolved = write.resolved
    path = write.requested.path
    home = write.requested.home
    content = write.requested.content
    if is_config_target(_literal(path, home), resolved, write.requested.cwd or Path.cwd()):
        policy = _config_write_findings(resolved, path, content)
        if policy:
            return policy
    if _is_state_path(resolved, home):
        return [finding_row(family="self_protection", rule="state_mutation", line=1, detail="Watcher state path in " + path, snippet=path.strip()[:180], action=STATE_ACTION)]
    if _reaches_install_surface(_literal(path, home), resolved, home):
        return [finding_row(family="self_protection", rule="watcher_install_surface", line=1, detail="Live watcher install path in " + path, snippet=path.strip()[:180], action=INSTALL_ACTION)]
    if _unwires_watcher(resolved, Path(os.path.realpath(config_paths.home_root(home))), content):
        return [finding_row(family="self_protection", rule="watcher_wiring_removal", line=1, detail="Write drops the watcher hooks from " + path, snippet=path.strip()[:180], action=WIRING_ACTION)]
    return []


def is_install_surface_path(
    path: str,
    home: str | os.PathLike[str] | None = None,
) -> bool:
    try:
        literal = _literal(path, home)
        resolved = _resolve(path, home)
    except UnresolvableTildePath:
        return True
    if resolved is None:
        return False
    return _reaches_install_surface(literal, resolved, home)


def _literal(path: str, home: str | os.PathLike[str] | None) -> Path | None:
    try:
        candidate = Path(path)
    except (TypeError, ValueError):
        return None
    text = str(candidate)
    if text == "~" or text.startswith("~/"):
        candidate = config_paths.home_relative(text, home)
    elif text.startswith("~"):
        candidate = _expand_other_user(text)
    if not candidate.is_absolute():
        candidate = Path(os.getcwd()) / candidate
    return candidate


def _resolve(path: str, home: str | os.PathLike[str] | None) -> Path | None:
    candidate = _literal(path, home)
    return None if candidate is None else Path(os.path.realpath(candidate))


def _expand_other_user(text: str) -> Path:
    expanded = os.path.expanduser(text)
    if expanded == text:
        raise UnresolvableTildePath(text)
    return Path(expanded)


def _relative_parts(path: Path, home: Path) -> list[str] | None:
    try:
        relative = path.relative_to(home)
    except ValueError:
        return None
    return [part.lower() for part in relative.parts]


def _reaches_install_surface(literal: Path | None, resolved: Path, home: str | os.PathLike[str] | None) -> bool:
    if literal is not None and _is_install_surface(literal, config_paths.home_root(home)):
        return True
    return _is_install_surface(resolved, Path(os.path.realpath(config_paths.home_root(home))))


def _is_install_surface(path: Path, home: Path) -> bool:
    folded = Path(str(path).casefold())
    if any(folded.is_relative_to(str(root).casefold()) for root in config_paths.release_roots(home)):
        return True
    parts = _relative_parts(path, home)
    if not parts or parts[0] not in CLIENT_HOME_DIRS:
        return False
    return any(part.startswith(WATCHER_NAME_PREFIX) for part in parts[1:])


def _unwires_watcher(resolved: Path, home: Path, content: str | None) -> bool:
    parts = _relative_parts(resolved, home)
    if parts is None or tuple(parts) not in WIRING_FILES:
        return False
    if not _has_watcher_wiring(resolved):
        return False
    if content is None:
        return True
    if re.search(r"agent-discipline|adw_light\.py|/hooks/run\.sh", content):
        return False
    try:
        settings = json.loads(content)
    except ValueError:
        return True
    plugins = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    return not isinstance(plugins, dict) or plugins.get("adw-light@adw-light") is not True


def _is_claude_settings(path: Path | None) -> bool:
    if path is None:
        return False
    name = path.name.lower()
    return path.parent.name.lower() == ".claude" and name.startswith("settings") and name.endswith(".json")


def _switches_watcher_off(paths: tuple[Path | None, Path], content: str | None) -> bool:
    if not content or not any(_is_claude_settings(path) for path in paths):
        return False
    try:
        settings = json.loads(content)
    except ValueError:
        return False
    if not isinstance(settings, dict):
        return False
    if settings.get("disableAllHooks") not in (None, False):
        return True
    env = settings.get("env")
    if isinstance(env, dict) and AUTH_ENV in env:
        return True
    plugins = settings.get("enabledPlugins")
    return isinstance(plugins, dict) and any(
        key.lower().startswith(WATCHER_NAME_PREFIX) and value is not True
        for key, value in plugins.items()
    )


def _has_watcher_wiring(path: Path) -> bool:
    try:
        return bool(WATCHER_WIRING_RE.search(read_regular_text(path)))
    except FileNotFoundError:
        return False
    except (OSError, UnicodeDecodeError):
        return True


def is_config_target(literal: Path | None, resolved: Path, cwd: Path) -> bool:
    folders = {path.parent.resolve() for path in (literal, resolved) if path is not None and path.name.casefold() == CONFIG_NAME}
    if folders:
        scratch = scratch_session(cwd)
        start = cwd.resolve()
        root = exited_project(cwd.absolute()) or marked_project(start) or cwd.absolute()
        project = () if scratch else cwd_forms(root)
        for folder in folders:
            if not scratch and start.is_relative_to(folder):
                return True
            if not outside_project_temp(folder, cwd.absolute(), project=project) or _inside_marked_project(folder):
                return True
    chosen = project_config_path(cwd)
    return resolved == chosen.resolve() and (chosen.exists() or not scratch_session(cwd))


def _inside_marked_project(folder: Path) -> bool:
    return any(
        (parent / ".git").exists() or any((parent / name).is_file() for name in SEAL_MARKERS)
        for parent in (folder, *folder.parents)
    )


def same_enforcement(path: Path, before: str | None, after: str | None) -> bool:
    if before is None or after is None:
        return False
    try:
        settings = [_parse_project_config(path, text.encode("utf-8")).settings for text in (before, after)
                    if len(text.encode("utf-8")) <= MAX_PROJECT_CONFIG_BYTES]
    except ValueError:
        return False
    return len(settings) == 2 and all(settings[0].get(key) == settings[1].get(key) for key in POLICY_KEYS)


def _config_write_action(path: Path, content: str | None) -> str | None:
    if content is None or len(content) > MAX_PROJECT_CONFIG_BYTES:
        return POLICY_ACTION
    try:
        current = load_project_config(path)
        raw = content.encode("utf-8")
        if len(raw) > MAX_PROJECT_CONFIG_BYTES:
            return POLICY_ACTION
        pending = _parse_project_config(path, raw)
    except (OSError, ValueError):
        return POLICY_ACTION
    if current.exists and all(current.settings.get(key) == pending.settings.get(key) for key in POLICY_KEYS):
        return None
    return POLICY_ACTION


def _config_write_findings(resolved: Path, path: str, content: str | None) -> list[dict]:
    action = _config_write_action(resolved, content)
    check = restore_check.get()
    if action is None or (check is not None and check(resolved, content)):
        return []
    return [finding_row(family="self_protection", rule="config_seal", line=1, detail="Project config write in " + path, snippet=path.strip()[:180], action=action)]


def _is_installed_agent_location(path: Path, home: str | os.PathLike[str] | None, cwd: Path) -> bool:
    base = config_paths.home_root(home)
    for anchor in cwd_forms(base):
        home_parts = _relative_parts(path, anchor)
        if home_parts and home_parts[0] in CLIENT_HOME_DIRS and home_parts[1:2] != ["worktrees"]:
            return True
    if any(path.is_relative_to(root) for root in cwd_forms(config_paths.adw_home(base))):
        return True
    if _reaches_install_surface(path, path.resolve(), home):
        return True
    root = exited_project(cwd) or marked_project(cwd.resolve()) or cwd
    if outside_project_temp(path, cwd, project=cwd_forms(root)):
        return False
    return any(
        directory.parent.name.lower() in CLIENT_HOME_DIRS and directory.name.lower() in {"agent", "agents", "plugins"}
        for directory in path.parents
    )


def _is_test_writer_definition(
    path: Path | None, content: str | None, home: str | os.PathLike[str] | None, cwd: Path,
) -> bool:
    if path is None or path.suffix.lower() not in AGENT_FILE_SUFFIXES:
        return False
    if not any(part.lower() == "agents" for part in path.parent.parts):
        return False
    if not _is_installed_agent_location(path, home, cwd):
        return False
    if path.stem.lower() == TEST_WRITER_STEM or TEST_WRITER_NAME_RE.search(content or ""):
        return True
    return _declares_test_writer_on_disk(path)


def _declares_test_writer_on_disk(path: Path) -> bool:
    try:
        return bool(TEST_WRITER_NAME_RE.search(read_regular_text(path)))
    except (FileNotFoundError, NotADirectoryError):
        return False
    except (OSError, UnicodeDecodeError):
        return True


def _state_roots(home: str | os.PathLike[str] | None) -> tuple[Path, ...]:
    return tuple({root.resolve() for root in _named_state_roots(home)})


def _named_state_roots(home: str | os.PathLike[str] | None) -> tuple[Path, ...]:
    base = config_paths.home_root(home)
    return (config_paths.state_root(home=base), config_paths.data_root(base), config_paths.legacy_data_root(base))


def state_root_forms(home: str | os.PathLike[str] | None) -> tuple[Path, ...]:
    roots = _named_state_roots(home)
    return tuple(dict.fromkeys(form for root in roots for form in (Path(os.path.abspath(root)), root.resolve())))


def _is_state_path(path: Path, home: str | os.PathLike[str] | None) -> bool:
    return any(path.is_relative_to(root) for root in _state_roots(home))
