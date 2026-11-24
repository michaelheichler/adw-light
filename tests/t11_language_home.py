import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
PACK = "tree_sitter_language_pack"
BUNDLE = "tree-sitter-language-pack"
SITE = Path("lib") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
VENV = Path(os.environ.get("ADW_TEST_LANGUAGE_VENV") or Path.home() / ".adw-lite" / "install" / "venv")
GRAMMARS = Path(os.environ.get("ADW_TEST_GRAMMAR_CACHE") or Path.home() / ".adw-lite" / "cache" / "grammars")
LIBRARY_READY = (VENV / SITE / PACK).is_dir() and (GRAMMARS / BUNDLE).is_dir()
BUNDLE_READY = LIBRARY_READY and any((GRAMMARS / BUNDLE).glob("v*/bundles/*"))
BUNDLE_NEEDED = "needs a grammar cache that holds the downloaded parser bundle (ADW_TEST_GRAMMAR_CACHE)"
GRAMMAR_WAIT_SECONDS = 30
LIBRARY_NEEDED = "needs the pinned tree-sitter venv and a filled grammar cache (ADW_TEST_LANGUAGE_VENV, ADW_TEST_GRAMMAR_CACHE)"
DENY = "deny"
COMMENT_RULES = ("narration_comment", "what_comment")
LINE = "// Now we loop over all items here"
HASH_LINE = "# Now we loop over all items here"
GO_RAW = "package main\n\nvar usage = `\n" + LINE + "\n`\n"
NESTED_TEMPLATE = "const help = `${'`'}\n" + LINE + "\n`;\n"


class HookHome:
    def __init__(self, case):
        self.case = case
        workspace = TemporaryDirectory()
        case.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.repo.mkdir()
        self.adw = self.home / ".adw-lite"
        (self.adw / "install").mkdir(parents=True)
        (self.adw / "cache").mkdir(parents=True)

    def link_library(self):
        (self.adw / "install" / "venv").symlink_to(VENV, target_is_directory=True)
        return self

    def link_grammars(self):
        cache = self.adw / "cache" / "grammars"
        cache.mkdir()
        (cache / BUNDLE).symlink_to(GRAMMARS / BUNDLE, target_is_directory=True)
        return self

    def empty_library(self):
        (self.adw / "install" / "venv").mkdir()
        return self

    def bundle_without_grammars(self):
        source = next((GRAMMARS / BUNDLE).glob("v*/bundles")).parent
        self.version_cache = self.adw / "cache" / "grammars" / BUNDLE / source.name
        self.version_cache.mkdir(parents=True)
        shutil.copy(source / "manifest.json", self.version_cache)
        (self.version_cache / "bundles").symlink_to(source / "bundles", target_is_directory=True)
        return self

    def hold_download_lock(self, *languages):
        self.download_lock = (self.version_cache / ".download.lock").open("a")
        fcntl.flock(self.download_lock, fcntl.LOCK_EX)
        self.case.addCleanup(self.wait_for_grammars, *languages)
        self.case.addCleanup(self.release_download_lock)
        return self

    def release_download_lock(self):
        self.download_lock.close()

    def wait_for_grammars(self, *languages):
        deadline = time.monotonic() + GRAMMAR_WAIT_SECONDS
        while not self.grammars_present(languages) and time.monotonic() < deadline:
            time.sleep(0.1)
        return self

    def grammars_present(self, languages):
        return all(any((self.version_cache / "libs").glob(f"libtree_sitter_{language}.*")) for language in languages)

    def block_grammar_cache(self):
        (self.adw / "cache" / "grammars").write_text("not a folder\n", encoding="utf-8")
        return self

    def stub_library(self, version):
        site = self.adw / "install" / "venv" / SITE
        (site / PACK).mkdir(parents=True)
        (site / PACK / "__init__.py").write_text("", encoding="utf-8")
        info = site / f"{PACK}-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {BUNDLE}\nVersion: {version}\n", encoding="utf-8")
        return self

    def write(self, relative, content, agent=None):
        return self.call("Write", {"file_path": str(self.repo / relative), "content": content}, agent)

    def patch(self, files):
        added = "".join(f"*** Add File: {self.repo / name}\n" + "".join(f"+{line}\n" for line in content.splitlines())
                        for name, content in files.items())
        return self.call("apply_patch", {"input": "*** Begin Patch\n" + added + "*** End Patch\n"})

    def call(self, tool, tool_input, agent=None):
        payload = {
            "session_id": "t11", "cwd": str(self.repo), "hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": tool_input,
        }
        if agent:
            payload["agent_type"] = agent
        return subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.state)],
            input=json.dumps(payload), text=True, capture_output=True, timeout=120,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
        )

    def verdict(self, relative, content, agent=None):
        call = self.write(relative, content, agent)
        output = json.loads(call.stdout)
        decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision, self.rule_lines()

    def rule_lines(self):
        rows = [row for report in sorted((self.state / "reports").glob("*.json"))
                for row in json.loads(report.read_text(encoding="utf-8"))]
        return tuple(sorted({(row["rule"], row["line"]) for row in rows}))

    def precision_rows(self):
        rows = [json.loads(line) for ledger in sorted((self.state / "ledger").glob("*.jsonl"))
                for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [{key: row.get(key) for key in ("event", "path", "language", "reason")}
                for row in rows if row.get("hook") == "language_layer"]


def comment_findings(line):
    return tuple((rule, line) for rule in COMMENT_RULES)
