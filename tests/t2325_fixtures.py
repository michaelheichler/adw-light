import atexit
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from functools import cache
from pathlib import Path

from fake_clis import FakeClis

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TOP = frozenset({".claude-plugin", ".codex-plugin", ".python-version", "LICENSE", "README.md", "VERSION", "adw_light.py",
                         "agents", "commands", "engine", "hooks", "skills"})
CODEX_EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart",
                          "SubagentStop", "Stop", "SessionEnd"})
CODEX_CONFIG_EVENT = "UserPromptSubmit"
CODEX_ADW_HOOK_EVENTS = sorted([*CODEX_EVENTS, CODEX_CONFIG_EVENT])
FORBIDDEN_NAMES = frozenset({"tests", "tools", "docs", "CLAUDE.md", "AGENTS.md"})
SHIPPED_DOT_ENTRIES = frozenset({".claude-plugin", ".codex-plugin", ".python-version"})
SCRUBBED_PREFIXES = ("ADW_", "CLAUDE", "CODEX", "GIT_", "PIP_")
OFFLINE_PIP = {"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": "", "PIP_CONFIG_FILE": os.devnull, "PIP_NO_CACHE_DIR": "1",
               "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
PLUGIN_ID = "adw-light@adw-light"
DEFAULT_REPO = "michaelheichler/adw-light"
FORK_REPO = "sample-owner/adw-light-fork"
SMOKE_PROBE = "tools/smoke_probe.py"
SMOKE_RECORD = "smoke-runs.jsonl"
SMOKE_FAILURE = "smoke-fails"
SUITE_PASSES = Path(".adw-lite") / "state" / "suite-passes"
SMALL_PAYLOADS = (("\"A\" * 2_500_000", "\"A\" * 2_500"), ("\"func f() {}\\n\" * 210_000", "\"func f() {}\\n\" * 21"),
                  ("range(3_500)", "range(35)"))
INSTALL_FLOW_DROPS = ("""        failures = [problem for event, body in CASES
                    if (problem := run_case(root, home, event, body, python=python))]
        if problem := tracked_stop(root, home, python):
            failures.append(problem)
""", """        if problem := legacy_test_write(root, home, python):
            failures.append(problem)
""")
NO_VENV_PYTHON = '#!/bin/sh\ncase "$*" in *"-m venv"*) exit 1 ;; esac\nexec {python} "$@"\n'
RECORDING_SMOKE_PROBE = """import json
import os
import sys
from pathlib import Path

with open({record!r}, "a", encoding="utf-8") as log:
    log.write(json.dumps({{"tree": sys.argv[1], "home": os.environ["HOME"]}}) + "\\n")
sys.exit(1 if Path({failure!r}).exists() else 0)
"""


class Scratch:
    def __init__(self, register_cleanup, github=None):
        holder = tempfile.TemporaryDirectory()
        register_cleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        self.home = self.root / "home"
        self.tmp = self.root / "tmp"
        self.config_dir = self.root / "claude-config"
        self.codex_home = self.root / "codex-home"
        self.git_config = self.root / "gitconfig"
        for folder in (self.home, self.tmp, self.config_dir, self.codex_home):
            folder.mkdir()
        self.git_config.write_text("[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n", encoding="utf-8")
        self.fakes = FakeClis(self.root / "fake-cli", github)

    def env(self, **extra):
        kept = {key: value for key, value in os.environ.items() if not key.startswith(SCRUBBED_PREFIXES)}
        return {**kept, "HOME": str(self.home), "TMPDIR": str(self.tmp), "CLAUDE_CONFIG_DIR": str(self.config_dir),
                "CODEX_HOME": str(self.codex_home), "GIT_CONFIG_GLOBAL": str(self.git_config),
                "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1", "PATH": self.fakes.search_path(), **OFFLINE_PIP,
                **extra}

    def run(self, command, cwd=None, timeout=600, **extra):
        return subprocess.run([str(part) for part in command], capture_output=True, text=True, check=False,
                              cwd=str(cwd or self.root), env=self.fakes.require_on(self.env(**extra)), timeout=timeout)

    def git(self, repo, *args):
        result = self.run(["git", "-C", repo, "-c", "user.name=Probe", "-c", "user.email=probe@example.invalid",
                           "-c", "commit.gpgsign=false", *args], timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    def commit_all(self, repo, message):
        self.git(repo, "add", "-A", "-f")
        self.git(repo, "commit", "-q", "--no-verify", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")


class ModuleFixture:
    def __init__(self, build):
        self.build = build
        self.value = None

    def get(self):
        if self.value is None:
            unittest.addModuleCleanup(self.forget)
            self.value = self.build(unittest.addModuleCleanup)
        return self.value

    def forget(self):
        self.value = None


class ProcessFixture:
    def __init__(self, build):
        self.built = cache(lambda: build(atexit.register))

    def get(self):
        return self.built()


def no_venv_python(scratch):
    path = scratch.root / "no-venv-python3"
    path.write_text(NO_VENV_PYTHON.format(python=shlex.quote(sys.executable)), encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def source_files(skip_top=()):
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "-z"],
                            capture_output=True, check=True, timeout=60).stdout.decode().split("\0")
    return [name for name in listed if name and (ROOT / name).is_file() and name.split("/")[0] not in skip_top]


def inside_scratch(scratch, path):
    resolved = Path(path).resolve()
    if resolved == scratch.root or not resolved.is_relative_to(scratch.root):
        raise ValueError(f"Fixture path {resolved} lies outside the scratch root {scratch.root}.")
    return resolved


def staged_target(scratch, repo, name):
    target = inside_scratch(scratch, repo / name)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def recording_probe(repo):
    return RECORDING_SMOKE_PROBE.format(record=str(repo.parent / SMOKE_RECORD), failure=str(repo.parent / SMOKE_FAILURE))


def small_payload_probe():
    text = (ROOT / SMOKE_PROBE).read_text(encoding="utf-8")
    for large, small in SMALL_PAYLOADS:
        if large not in text:
            raise ValueError(f"The smoke probe no longer holds {large}, so the small payload copy would run at full size.")
        text = text.replace(large, small)
    return text


def install_flow_probe():
    text = small_payload_probe()
    for step in INSTALL_FLOW_DROPS:
        if step not in text:
            raise ValueError(f"The smoke probe no longer holds {step!r}, so the install flow copy would run every case.")
        text = text.replace(step, "        failures = []\n" if "CASES" in step else "")
    return text


def recorded_smoke_runs(repo):
    record = repo.parent / SMOKE_RECORD
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()] if record.exists() else []


def smoke_runs(repo, under):
    return [row for row in recorded_smoke_runs(repo) if Path(row["tree"]).resolve().is_relative_to(under)]


def copy_source_repo(scratch, repo, extra_files=None, skip_top=()):
    repo = inside_scratch(scratch, repo)
    staged = {SMOKE_PROBE: recording_probe(repo), **(extra_files or {})}
    for name in source_files(skip_top):
        shutil.copy2(ROOT / name, staged_target(scratch, repo, name))
    for name, text in staged.items():
        staged_target(scratch, repo, name).write_text(text, encoding="utf-8")
    scratch.git(repo.parent, "init", "-q", "-b", "main", repo.name)
    return scratch.commit_all(repo, "fixture baseline")


def shipped_leaks(names):
    return sorted(name for name in names
                  if set(Path(name).parts) & FORBIDDEN_NAMES
                  or any(part.startswith(".") for part in Path(name).parts) and Path(name).parts[0] not in SHIPPED_DOT_ENTRIES)


def tree_names(release):
    return [str(path.relative_to(release)) for path in release.rglob("*")]


def built_version(release):
    return json.loads((release / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


def hook_commands(path):
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return [(event, handler.get("command", "")) for event, groups in data.get("hooks", {}).items()
            for group in groups for handler in group.get("hooks", [])]


def write_suite_stamp(home, commit):
    folder = Path(home) / SUITE_PASSES
    folder.mkdir(parents=True, exist_ok=True)
    record = {"commit": commit, "ran": 1, "wall_seconds": 0.5, "date": "2026-01-01T00:00:00+00:00"}
    (folder / f"{commit}.json").write_text(json.dumps(record), encoding="utf-8")


def all_paths(*roots):
    return sorted(str(path) for root in roots for path in root.rglob("*"))


def github_url(slug):
    return f"https://github.com/{slug}.git"


def stand_in_github(scratch, slug=DEFAULT_REPO):
    bare = scratch.fakes.github_repo(slug)
    bare.parent.mkdir(parents=True, exist_ok=True)
    scratch.git(bare.parent, "init", "-q", "--bare", bare.name)
    return bare


def link_github(scratch, slug, target=DEFAULT_REPO):
    link = scratch.fakes.github_repo(slug)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(scratch.fakes.github_repo(target), target_is_directory=True)
    return link


def source_version(repo):
    return (repo / "VERSION").read_text(encoding="utf-8").strip()


def next_version(version):
    *head, last = version.split(".")
    if len(head) not in (2, 3):
        raise ValueError(version)
    return ".".join([*head, str(int(last) + 1)])


def cut_release(scratch, repo):
    result = scratch.run(["bash", repo / "tools" / "release_branch.sh"], cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    return "v" + source_version(repo)


def commit_version(scratch, repo, version):
    (repo / "VERSION").write_text(version + "\n", encoding="utf-8")
    return scratch.commit_all(repo, "release " + version)


def publish(scratch, repo, slug, *refs):
    scratch.git(repo, "push", "-q", github_url(slug), *refs)


def github_refs(scratch, slug=DEFAULT_REPO):
    listed = scratch.git(scratch.fakes.github_repo(slug), "for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split() for line in listed.splitlines())


def marketplaces(scratch, layer, home=None):
    config = Path(home) if home else {"claude": scratch.config_dir, "codex": scratch.codex_home}[layer]
    table = {"claude": config / "plugins" / "known_marketplaces.json", "codex": config / "fake-codex-marketplaces.json"}[layer]
    data = json.loads(table.read_text(encoding="utf-8")) if table.exists() else {}
    return {name: (entry.get("repo"), entry.get("ref")) for name, entry in data.items()}


def installed_plugins(scratch, layer, home=None):
    config = Path(home) if home else {"claude": scratch.config_dir, "codex": scratch.codex_home}[layer]
    table = {"claude": config / "plugins" / "installed_plugins.json", "codex": config / "fake-codex-plugins.json"}[layer]
    data = json.loads(table.read_text(encoding="utf-8")) if table.exists() else {}
    return {plugin_id: Path(path).name for plugin_id, path in data.items()}


def adw_hook_events(path):
    return sorted(event for event, command in hook_commands(path) if "ADW_CODEX_HOOK=1" in command)
