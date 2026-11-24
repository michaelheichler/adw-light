import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TOP = frozenset({".claude-plugin", ".python-version", "adw_light.py", "engine", "hooks", "skills"})
CODEX_EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart",
                          "SubagentStop", "Stop", "SessionEnd"})
FORBIDDEN_NAMES = frozenset({"tests", "tools", "docs", "CLAUDE.md", "AGENTS.md"})
SHIPPED_DOT_ENTRIES = frozenset({".claude-plugin", ".python-version"})
SCRUBBED_PREFIXES = ("ADW_", "CLAUDE", "CODEX", "GIT_", "PIP_")
OFFLINE_PIP = {"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": "", "PIP_CONFIG_FILE": os.devnull, "PIP_NO_CACHE_DIR": "1",
               "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
PLUGIN_ID = "adw-light@adw-light"


class Scratch:
    def __init__(self, register_cleanup):
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
        self.git_config.write_text("", encoding="utf-8")

    def env(self, **extra):
        kept = {key: value for key, value in os.environ.items() if not key.startswith(SCRUBBED_PREFIXES)}
        return {**kept, "HOME": str(self.home), "TMPDIR": str(self.tmp), "CLAUDE_CONFIG_DIR": str(self.config_dir),
                "CODEX_HOME": str(self.codex_home), "GIT_CONFIG_GLOBAL": str(self.git_config),
                "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1", **OFFLINE_PIP, **extra}

    def run(self, command, cwd=None, timeout=600, **extra):
        return subprocess.run([str(part) for part in command], capture_output=True, text=True, check=False,
                              cwd=str(cwd or self.root), env=self.env(**extra), timeout=timeout)

    def git(self, repo, *args):
        result = self.run(["git", "-C", repo, "-c", "user.name=Probe", "-c", "user.email=probe@example.invalid",
                           "-c", "commit.gpgsign=false", *args], timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    def commit_all(self, repo, message):
        self.git(repo, "add", "-A", "-f")
        self.git(repo, "commit", "-q", "--no-verify", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")


def source_files(skip_top=()):
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "-z"],
                            capture_output=True, check=True, timeout=60).stdout.decode().split("\0")
    return [name for name in listed if name and (ROOT / name).is_file() and name.split("/")[0] not in skip_top]


def copy_source_repo(scratch, repo, extra_files=None, skip_top=()):
    for name in source_files(skip_top):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    for name, text in (extra_files or {}).items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
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


def all_paths(*roots):
    return sorted(str(path) for root in roots for path in root.rglob("*"))
