from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from pathlib import Path

try:
    from . import config_roots
    from .language_layer import GRAMMARS, PYTHON, REQUIREMENTS, language_of, pins
except ImportError:
    import config_roots
    from language_layer import GRAMMARS, PYTHON, REQUIREMENTS, language_of, pins

PLUGIN_ROOT_ENV = "CLAUDE_PLUGIN_ROOT"
PYTHON_ENV = "ADW_PYTHON"
RECORD_NAME = "setup.json"
LOCK_NAME = "setup.lock"
LOG_NAME = "setup.log"
FETCHER = Path(__file__).with_name("grammar_fetch.py")
ENTRY = Path(__file__).resolve().parents[1] / "setup_env.py"
PYTHON_FLOOR = (3, 11)
PYTHON_NAMES = ("python3", *(f"python3.{minor}" for minor in range(11, 21)))
PYTHON_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")
SKIPPED_DIRS = frozenset({"node_modules", "vendor", "venv", "__pycache__", "target", "build", "dist"})
SCAN_LIMIT = 5000
PROBE_SECONDS = 5
PIP_SECONDS = 1800
FETCH_SECONDS = 600
LOCK_SECONDS = 3600
LOCK_GRACE_SECONDS = 60
HEARTBEAT_SECONDS = 300
RETRY_SECONDS = 3600
READY = "ready"
FAILED = "failed"
WHEEL = "wheel"
SOURCE = "source"
VERSION_PROBE = "import sys; print(sys.version_info[0], sys.version_info[1])"
TEXT_FIELDS = ("status", "reason", "python", "version", "build")
LIST_FIELDS = ("grammars", "requested", "fetched")
GRAMMAR_NAMES = frozenset(GRAMMARS.values())


def project_languages(project: Path) -> tuple[str, ...]:
    found: set[str] = set()
    pending = [project]
    seen = 0
    while pending and seen < SCAN_LIMIT:
        for entry in _entries(pending.pop()):
            seen += 1
            if entry.is_dir(follow_symlinks=False):
                if not entry.name.startswith(".") and entry.name not in SKIPPED_DIRS:
                    pending.append(Path(entry.path))
            elif language := language_of(entry.name):
                found.add(language)
    return tuple(sorted(found))


def _entries(folder: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(folder) as listing:
            return list(listing)
    except OSError:
        return []


def grammar_languages(projects: Iterable[Path]) -> tuple[str, ...]:
    languages = {language for project in projects for language in project_languages(project)}
    return tuple(sorted(languages - {PYTHON}))


def python_candidates(environment: dict[str, str] | None = None) -> tuple[str, ...]:
    env = os.environ if environment is None else environment
    override = env.get(PYTHON_ENV, "").strip()
    if override:
        return (override,)
    search = env.get("PATH", "") + os.pathsep + os.pathsep.join(PYTHON_DIRS)
    found = (shutil.which(name, path=search) for name in PYTHON_NAMES)
    install = os.path.realpath(config_roots.install_root())
    kept = [path for path in found if path and not os.path.realpath(path).startswith(install + os.sep)]
    return tuple(dict.fromkeys(kept))


def python_version(path: str) -> tuple[int, int] | None:
    try:
        probe = subprocess.run([path, "-I", "-S", "-c", VERSION_PROBE], capture_output=True, text=True,
                               timeout=PROBE_SECONDS, stdin=subprocess.DEVNULL)
        major, minor = (int(part) for part in probe.stdout.split())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return (major, minor) if probe.returncode == 0 else None


def newest_python(environment: dict[str, str] | None = None) -> tuple[str, tuple[int, int]] | None:
    versions = ((path, python_version(path)) for path in python_candidates(environment))
    usable = [(path, version) for path, version in versions if version and version >= PYTHON_FLOOR]
    return max(usable, key=lambda row: row[1]) if usable else None


def record_path() -> Path:
    return config_roots.install_root() / RECORD_NAME


def read_record() -> dict:
    try:
        data = json.loads(record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and valid_record(data) else {}


def valid_record(record: dict) -> bool:
    texts = all(isinstance(record.get(key, ""), str) for key in TEXT_FIELDS)
    lists = all(_text_list(record.get(key, [])) for key in LIST_FIELDS)
    stamp = record.get("ts", 0)
    timed = isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
    pinned = _text_map(record.get("pins", {}))
    versioned = record.get("status") != READY or bool(record.get("version"))
    return texts and lists and timed and pinned and versioned


def _text_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _text_map(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(item, str) for item in value.values())


def save_record(record: dict) -> None:
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}")
    staging.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staging, path)


def environment_python() -> Path:
    return config_roots.language_environment() / "bin" / "python"


def environment_ready(record: dict) -> bool:
    return record.get("status") == READY and record.get("pins") == pins() and environment_python().exists()


def setup_due(projects: Iterable[Path], record: dict | None = None) -> bool:
    current = read_record() if record is None else record
    if current.get("status") == FAILED and current.get("pins") == pins() and _recent(current.get("ts")):
        return False
    if not environment_ready(current):
        return True
    known = set(current.get("grammars") or ())
    tried = set(current.get("requested") or ()) if _recent(current.get("ts")) else set()
    return bool(set(grammar_languages(projects)) - known - tried)


def _recent(stamp: object) -> bool:
    return isinstance(stamp, (int, float)) and time.time() - stamp < RETRY_SECONDS


def start(project: Path | None, environment: dict[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    if not env.get(PLUGIN_ROOT_ENV, "").strip():
        return False
    projects = [project] if project is not None else []
    if not setup_due(projects):
        return False
    command = [sys.executable, "-I", "-S", "-B", str(ENTRY)]
    command += [argument for item in projects for argument in ("--project", str(item))]
    install = config_roots.install_root()
    install.mkdir(parents=True, exist_ok=True)
    with open(install / LOG_NAME, "ab") as log:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return True


def _acquire(lock: Path) -> bool:
    lock.parent.mkdir(parents=True, exist_ok=True)
    if _stale(lock):
        lock.unlink(missing_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    os.write(descriptor, str(os.getpid()).encode())
    os.close(descriptor)
    return True


def _stale(lock: Path) -> bool:
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    owner = _lock_owner(lock)
    if owner is None:
        return age > LOCK_GRACE_SECONDS
    return age > LOCK_SECONDS or not _alive(owner)


def _lock_owner(lock: Path) -> int | None:
    try:
        return int(lock.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _heartbeat(lock: Path, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        if _lock_owner(lock) != os.getpid():
            return
        try:
            os.utime(lock)
        except OSError:
            return


def _release(lock: Path) -> None:
    if _lock_owner(lock) == os.getpid():
        lock.unlink(missing_ok=True)


def _run(command: list[str], seconds: int) -> bool:
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL, timeout=seconds).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _install(python: str, staging: Path) -> str:
    if not _run([python, "-m", "venv", "--clear", str(staging)], PIP_SECONDS):
        return ""
    pip = [str(staging / "bin" / "python"), "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
    if _run([*pip, "--only-binary=:all:", "-r", str(REQUIREMENTS)], PIP_SECONDS):
        return WHEEL
    return SOURCE if _run([*pip, "-r", str(REQUIREMENTS)], PIP_SECONDS) else ""


def _swap(staging: Path, target: Path) -> None:
    retired = target.with_name(f"{target.name}.old-{os.getpid()}")
    if os.path.lexists(target):
        os.rename(target, retired)
    os.rename(staging, target)
    shutil.rmtree(retired, ignore_errors=True)


def staging_path() -> Path:
    target = config_roots.language_environment()
    return target.with_name(f"{target.name}.staging-{os.getpid()}")


def build_environment() -> dict:
    chosen = newest_python()
    if chosen is None:
        return {"status": FAILED, "reason": "no_python"}
    python, version = chosen
    target = config_roots.language_environment()
    staging = staging_path()
    shutil.rmtree(staging, ignore_errors=True)
    build = _install(python, staging)
    if not build:
        shutil.rmtree(staging, ignore_errors=True)
        return {"status": FAILED, "reason": "install_failed", "python": python, "version": f"{version[0]}.{version[1]}"}
    _swap(staging, target)
    return {"status": READY, "python": python, "version": f"{version[0]}.{version[1]}", "build": build, "grammars": []}


def fetch_grammars(record: dict, languages: Iterable[str]) -> list[str]:
    site = config_roots.language_environment() / "lib" / f"python{record['version']}" / "site-packages"
    cache = config_roots.grammar_cache_root()
    fetched = []
    for language in languages:
        command = [str(environment_python()), "-I", "-S", "-B", str(FETCHER), str(cache), language, str(site)]
        if _run(command, FETCH_SECONDS):
            fetched.append(language)
    return fetched


def wanted_languages(projects: Iterable[Path], languages: Iterable[str] = ()) -> tuple[str, ...]:
    named = {language for language in languages if language in GRAMMAR_NAMES}
    return tuple(sorted({*grammar_languages(projects), *named}))


def setup(projects: Iterable[Path], languages: Iterable[str] = ()) -> dict:
    record = read_record()
    if not environment_ready(record):
        record = build_environment()
    record["pins"] = pins()
    record["ts"] = time.time()
    if record["status"] != READY:
        return record
    requested = [language for language in wanted_languages(projects, languages)
                 if language not in record.get("grammars", [])]
    fetched = fetch_grammars(record, requested)
    record["requested"] = requested
    record["fetched"] = fetched
    record["grammars"] = sorted({*record.get("grammars", []), *fetched})
    return record


def log_setup(record: dict) -> None:
    try:
        from . import reporting
    except ImportError:
        import reporting
    row = {key: record.get(key) for key in ("status", "reason", "python", "version", "build", "requested", "fetched")}
    try:
        reporting.append_row({"ts": reporting.now_iso(), "hook": "first_run_setup", "event": "setup", **row})
    except Exception as error:
        print(f"ADW Lite could not log the setup result: {error}", file=sys.stderr)


def main(argv: list[str]) -> int:
    projects = [Path(value) for value in option_values(argv, "--project")]
    languages = option_values(argv, "--language")
    lock = config_roots.install_root() / LOCK_NAME
    if not _acquire(lock):
        print("ADW Lite setup is already running.", file=sys.stderr)
        return 0
    stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(lock, stop), daemon=True).start()
    try:
        record = _guarded_setup(projects, languages)
    finally:
        stop.set()
        _release(lock)
    log_setup(record)
    return 0 if record.get("status") == READY else 1


def option_values(argv: list[str], option: str) -> list[str]:
    return [argv[index + 1] for index, word in enumerate(argv[:-1]) if word == option]


def _guarded_setup(projects: Iterable[Path], languages: Iterable[str]) -> dict:
    try:
        record = setup(projects, languages)
        save_record(record)
        return record
    except Exception as error:
        print(f"ADW Lite setup stopped on an error: {error!r}", file=sys.stderr)
    shutil.rmtree(staging_path(), ignore_errors=True)
    record = {"status": FAILED, "reason": "setup_error", "pins": pins(), "ts": time.time()}
    try:
        save_record(record)
    except OSError as error:
        print(f"ADW Lite could not save the failed setup record: {error!r}", file=sys.stderr)
    return record
