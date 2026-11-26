from __future__ import annotations

import errno
import os
import shutil
import sys
from pathlib import Path

try:
    from .host import CLAUDE, CODEX, COWORK, OMP, current_host
except ImportError:
    from host import CLAUDE, CODEX, COWORK, OMP, current_host

CLAUDE_CONFIG_ENV = "CLAUDE_CONFIG_DIR"
CODEX_HOME_ENV = "CODEX_HOME"
CODEX_CLIENT_DIR = ".codex"
CLAUDE_CACHE_PARTS = ("plugins", "cache", "adw-light", "adw-light")
STATE_ENV = "ADW_STATE_ROOT"
WORDING_INDEX_DIR = "wording_index"
PLUGIN_ROOT_ENV = "CLAUDE_PLUGIN_ROOT"
DEFAULTS_LEAF = "defaults"
HOME_ROOT_HOSTS = (CLAUDE, CODEX, OMP)
CLAUDE_CLIENT_DIRS = (".claude", ".config/claude-code")
PLUGIN_COPY_DIRS = ("plugins/cache/adw-light", "plugins/marketplaces/adw-light")
ADW_HOME_LEAF = ".adw-lite"


def home_root(home: str | os.PathLike[str] | None = None) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def claude_config_candidates(
    environment: dict[str, str] | None = None, home: Path | None = None,
) -> tuple[Path, ...]:
    env = os.environ if environment is None else environment
    base = home_root(home)
    override = env.get(CLAUDE_CONFIG_ENV, "").strip()
    candidates = [Path(override).expanduser()] if override else []
    candidates.extend(base / client for client in CLAUDE_CLIENT_DIRS)
    return tuple(dict.fromkeys(candidates))


def codex_config_candidates(
    environment: dict[str, str] | None = None, home: Path | None = None,
) -> tuple[Path, ...]:
    env = os.environ if environment is None else environment
    override = env.get(CODEX_HOME_ENV, "").strip()
    candidates = [Path(override).expanduser()] if override else []
    candidates.append(home_root(home) / CODEX_CLIENT_DIR)
    return tuple(dict.fromkeys(candidates))


def claude_config_roots(
    environment: dict[str, str] | None = None, home: Path | None = None,
) -> tuple[Path, ...]:
    return tuple(path for path in claude_config_candidates(environment, home) if path.is_dir())


def adw_home(home: str | os.PathLike[str] | None = None) -> Path:
    return home_root(home) / ADW_HOME_LEAF


def data_root(home: str | os.PathLike[str] | None = None) -> Path:
    return adw_home(home) / "state"


def legacy_data_root(home: str | os.PathLike[str] | None = None) -> Path:
    return home_root(home) / ".local/state/adw-lite"


def legacy_install_root(home: str | os.PathLike[str] | None = None) -> Path:
    return home_root(home) / ".local/share/adw-light"


def migrated_state_dir(state_dir: Path, home: str | os.PathLike[str] | None = None) -> Path:
    target = data_root(home)
    legacy = legacy_data_root(home)
    if os.path.realpath(state_dir) != os.path.realpath(target):
        return state_dir
    if _ledger_stranded(legacy, target):
        return legacy
    _attempt(f"move {legacy} to {target}", lambda: _migrate_legacy(legacy, target))
    return state_dir


def _ledger_stranded(legacy: Path, target: Path) -> bool:
    ledger = legacy / "ledger"
    if legacy.is_symlink() or not os.path.lexists(ledger) or os.path.lexists(target / "ledger"):
        return False
    if _same_place(legacy, target):
        return False
    try:
        target.mkdir(parents=True, exist_ok=True)
        _place(ledger, target / "ledger")
    except FileNotFoundError:
        return False
    except OSError as error:
        print(f"ADW Lite could not move {ledger} to {target / 'ledger'}: {error.strerror or error}", file=sys.stderr)
        return True
    _attempt(f"remove {legacy}", lambda: _remove_if_empty(legacy))
    return False


def _attempt(action: str, step) -> None:
    try:
        step()
    except FileNotFoundError:
        return
    except OSError as error:
        print(f"ADW Lite could not {action}: {error.strerror or error}", file=sys.stderr)


def _migrate_legacy(legacy: Path, target: Path) -> None:
    if not _holds_entries(legacy) or _same_place(legacy, target):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if legacy.is_symlink() and _relinked(legacy, target):
        return
    if _same_place(legacy, target):
        return
    target.mkdir(exist_ok=True)
    _merge(legacy, target, nested=False)


def _holds_entries(folder: Path) -> bool:
    return folder.is_dir() and any(folder.iterdir())


def _same_place(legacy: Path, target: Path) -> bool:
    return os.path.realpath(legacy) == os.path.realpath(target)


def _is_real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _relinked(legacy: Path, target: Path) -> bool:
    try:
        os.symlink(os.path.realpath(legacy), target, target_is_directory=True)
    except FileExistsError:
        return False
    legacy.unlink()
    return True


def _merge(source_dir: Path, target_dir: Path, *, nested: bool) -> None:
    for entry in sorted(source_dir.iterdir()):
        _move_entry(entry, target_dir / entry.name, nested=nested)
    _attempt(f"remove {source_dir}", lambda: _remove_if_empty(source_dir))


def _move_entry(source: Path, destination: Path, *, nested: bool) -> None:
    if not os.path.lexists(destination):
        _attempt(f"move {source} to {destination}", lambda: _place(source, destination))
    elif not nested and _is_real_dir(source) and _is_real_dir(destination):
        _attempt(f"merge {source} into {destination}", lambda: _merge(source, destination, nested=True))


def _place(source: Path, destination: Path) -> None:
    try:
        _place_on_device(source, destination)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        _copy_across(source, destination)


def _place_on_device(source: Path, destination: Path) -> None:
    if _is_real_dir(source):
        os.rename(source, destination)
        return
    os.link(source, destination, follow_symlinks=False)
    _retire(source, destination)


def _retire(source: Path, destination: Path) -> None:
    try:
        _remove(source)
    except OSError:
        if not _is_real_dir(destination):
            destination.unlink()
        raise


def _copy_across(source: Path, destination: Path) -> None:
    staging = destination.with_name(f".{destination.name}.migrating-{os.getpid()}")
    if _is_real_dir(source):
        shutil.copytree(source, staging, symlinks=True)
    else:
        shutil.copy2(source, staging, follow_symlinks=False)
    if _settled(staging, destination):
        _retire(source, destination)


def _settled(staging: Path, destination: Path) -> bool:
    try:
        _settle(staging, destination)
    except OSError as error:
        _remove(staging)
        if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
            return False
        raise
    if os.path.lexists(staging):
        staging.unlink()
    return True


def _settle(staging: Path, destination: Path) -> None:
    if _is_real_dir(staging):
        os.rename(staging, destination)
    else:
        os.link(staging, destination, follow_symlinks=False)


def _remove(path: Path) -> None:
    if _is_real_dir(path):
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _remove_if_empty(folder: Path) -> None:
    if any(folder.iterdir()):
        return
    if folder.is_symlink():
        folder.unlink()
    else:
        folder.rmdir()


def install_root(home: str | os.PathLike[str] | None = None) -> Path:
    return adw_home(home) / "install"


def cache_root(home: str | os.PathLike[str] | None = None) -> Path:
    return adw_home(home) / "cache"


def grammar_cache_root(home: str | os.PathLike[str] | None = None) -> Path:
    return cache_root(home) / "grammars"


def dataset_cache_root(home: str | os.PathLike[str] | None = None) -> Path:
    return cache_root(home) / "datasets"


def language_environment(home: str | os.PathLike[str] | None = None) -> Path:
    return install_root(home) / "venv"


def home_relative(text: str, home: str | os.PathLike[str] | None = None) -> Path | None:
    for token in ("~", "$HOME", "${HOME}"):
        if text == token:
            return home_root(home)
        if text.startswith(token + "/"):
            return home_root(home) / text[len(token) + 1:]
    return None


def state_root(
    environment: dict[str, str] | None = None, *, home: str | os.PathLike[str] | None = None,
) -> Path:
    env = os.environ if environment is None else environment
    override = env.get(STATE_ENV, "").strip()
    return Path(override).expanduser() if override else data_root(home)


def _home_roots(name: str, environment: dict[str, str] | None) -> tuple[Path, ...]:
    root = state_root(environment)
    return (root / "hosts" / name, root)


def _bundled_roots(environment: dict[str, str] | None) -> tuple[Path, ...]:
    env = os.environ if environment is None else environment
    plugin_root = env.get(PLUGIN_ROOT_ENV, "").strip()
    return (Path(plugin_root) / DEFAULTS_LEAF,) if plugin_root else ()


def roots_for(name: str, environment: dict[str, str] | None = None) -> tuple[Path, ...]:
    if name == COWORK:
        return _bundled_roots(environment)
    if name in HOME_ROOT_HOSTS:
        return _home_roots(name, environment)
    raise ValueError(f"no configuration roots declared for {name}")


def roots(environment: dict[str, str] | None = None) -> tuple[Path, ...]:
    return roots_for(current_host(environment), environment)


def engine_root() -> Path:
    return Path(__file__).resolve().parents[2]


def release_roots(home: Path) -> tuple[Path, ...]:
    roots = [engine_root(), install_root(home)]
    plugin = os.environ.get(PLUGIN_ROOT_ENV, "").strip()
    if plugin:
        roots.append(Path(plugin).resolve())
    candidates = (*claude_config_candidates(home=home), *codex_config_candidates(home=home))
    configs = dict.fromkeys((*candidates, *(Path(os.path.realpath(config)) for config in candidates)))
    roots.extend(config / copy for config in configs for copy in PLUGIN_COPY_DIRS)
    return tuple(roots)


def protected_roots(home: Path) -> tuple[Path, ...]:
    roots = [adw_home(home), legacy_data_root(home), home / ".agent-discipline", install_root(home), legacy_install_root(home)]
    roots.extend(release_roots(home))
    roots.append(home / ".adw")
    for client in CLAUDE_CLIENT_DIRS:
        base = home / client
        roots.extend(base / relative for relative in (
            "plugins/cache/agent-discipline-watcher",
            "plugins/marketplaces/agent-discipline-watcher",
            "plugins/agent-discipline-watcher",
            "skills/agent-discipline-watcher",
        ))
    roots.extend(home / relative for relative in (
        ".codex/skills/agent-discipline-watcher",
        ".agents/skills/agent-discipline-watcher",
        ".pi/agent/extensions/agent-discipline-watcher",
        ".omp/agent/extensions/agent-discipline-watcher",
        ".local/bin/agent-discipline",
    ))
    return tuple(roots)
