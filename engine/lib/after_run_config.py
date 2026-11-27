from __future__ import annotations

import hashlib
import os
import shlex
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from . import config_imports, protected, shell_snapshot
except ImportError:
    import config_imports, protected, shell_snapshot

if TYPE_CHECKING:
    from .after_run import Judged

SEAL_KEYS = frozenset({"seal", "seal_text"})
SHELL_SPECIAL = frozenset(";&|<>()`$\\*?[]{}~#!\n")
RESTORE_FORMS = {
    ("rm", "--"): "absent",
    ("git", "restore", "--"): "index",
    ("git", "checkout", "--"): "index",
    ("git", "restore", "--source=HEAD", "--"): "HEAD",
}


def release_owner_paths(hook, compared):
    from lib import after_run

    paths = {str(compared.root / raw) for raw in compared.paths}
    updates = []

    def release(data):
        pending = dict(data.get("pending", {}))
        for agent, entries in list(pending.items()):
            current = dict(entries)
            for path in paths.intersection(entries):
                prior = entries[path]
                if "seal" not in prior:
                    continue
                rows = [row for row in prior["rows"] if row.get("rule") != "config_seal"]
                if rows:
                    current[path] = {key: value for key, value in prior.items() if key not in SEAL_KEYS} | {"rows": rows}
                else:
                    current.pop(path)
            if current != entries:
                updates.append((entries, current, agent))
                if current:
                    pending[agent] = current
                else:
                    pending.pop(agent)
        return {**data, "pending": pending}

    after_run._save(hook, release)
    for before, after, agent in updates:
        after_run._publish(hook, before, after, agent)


def owner_baseline(session_id: str, path: Path) -> tuple[str, str | None] | None:
    from lib import after_run, session_state

    state = session_state.read_state(session_id, None).get(after_run.STATE, {})
    target = os.path.realpath(path)
    for entries in state.get("pending", {}).values():
        for key, entry in entries.items():
            if "seal" in entry and os.path.realpath(key) == target:
                return entry["seal"], entry.get("seal_text")
    return None


def _commands(path: Path, cwd: Path, baseline: str):
    names = [str(path)]
    if path != cwd and path.is_relative_to(cwd):
        names.append(str(path.relative_to(cwd)))
    for name in map(shlex.quote, names):
        if baseline == shell_snapshot.ABSENT:
            yield "rm -- " + name, "absent"
        else:
            for verb in ("git restore -- ", "git checkout -- "):
                yield verb + name, "index"
            yield "git restore --source=HEAD -- " + name, "HEAD"


def _restore_source(command: object, cwd: str, path: Path, baseline: str) -> str | None:
    if not isinstance(command, str) or SHELL_SPECIAL.intersection(command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    source = RESTORE_FORMS.get(tuple(words[:-1])) if len(words) > 1 else None
    if source is None or (source == "absent") != (baseline == shell_snapshot.ABSENT):
        return None
    named = os.path.realpath(os.path.join(cwd, words[-1]))
    return source if named == os.path.realpath(path) else None


def _matches_restore(payload, cfg, path: Path, content: str | None) -> bool:
    from lib import after_run, git_port, payloads

    hook = after_run._hook(payload, cfg, "PreToolUse", None)
    entry = after_run._stored(hook).get("pending", {}).get(hook.scope.agent_id, {}).get(str(path), {})
    baseline = entry.get("seal")
    if not baseline or baseline == shell_snapshot.UNKNOWN or path.is_symlink():
        return False
    tool = payloads._tool_input(payload)
    if payloads.tool_name(payload) == "Write":
        return isinstance(content, str) and content == tool.get("content") and shell_snapshot.text_digest(content) == baseline
    raw_cwd = payloads.cwd(payload) or "."
    cwd = Path(raw_cwd).resolve()
    source = _restore_source(tool.get("command"), raw_cwd, path, baseline)
    if source == "absent":
        return path.is_file()
    if source not in {"index", "HEAD"}:
        return False
    root = git_port.view().toplevel(cwd)
    if not root or not path.is_relative_to(root):
        return False
    raw = str(path.relative_to(root))
    if any(char in raw for char in "*?[:\\") or not git_port.view().is_tracked(root, raw):
        return False
    blob = git_port.view().head_bytes(root, raw) if source == "HEAD" else git_port.view().index_bytes(
        root, raw, limit=protected.MAX_PROJECT_CONFIG_BYTES + 1)
    return blob is not None and len(blob) <= protected.MAX_PROJECT_CONFIG_BYTES and hashlib.sha256(blob).hexdigest() == baseline


@contextmanager
def restore_scope(payload, config):
    from lib.config import payload_hook_config

    token = protected.restore_check.set(lambda path, content: _matches_restore(payload, payload_hook_config(config, payload), path, content))
    try:
        yield
    finally:
        protected.restore_check.reset(token)


def _restore_action(path, baseline):
    commands = [cmd for cmd, _ in _commands(path, path, baseline)]
    if baseline == shell_snapshot.ABSENT:
        return "Remove the new file with " + commands[0] + ". " + protected.POLICY_ACTION
    return "Write the original bytes, or use a matching Git version: " + " or ".join(commands) + ". " + protected.POLICY_ACTION


def seal_entry(path: Path, root: Path, prior: dict, baseline: str, digest: str,
               text: str | None = None) -> tuple[dict, list]:
    rows = [row for row in prior.get("rows", []) if row.get("rule") != "config_seal"]
    entry = {key: value for key, value in prior.items() if key not in SEAL_KEYS}
    if baseline != digest or digest == shell_snapshot.UNKNOWN:
        row = protected.finding_row(family="self_protection", rule="config_seal", line=1,
                                    detail="Project config changed at " + str(path), snippet=str(path),
                                    action=_restore_action(path, baseline))
        row["path"] = str(path)
        kept = {"seal_text": text} if text is not None and shell_snapshot.text_digest(text) == baseline else {}
        return {**entry, "seal": baseline, **kept, "digest": digest, "root": str(root), "rows": [row, *rows]}, [row]
    return ({**entry, "rows": rows} if rows else {}), []


def seal_changes(compared: shell_snapshot.Comparison, entries: dict, judged: Judged, old: dict | None = None) -> None:
    if protected.authorized():
        return
    candidates = [raw for raw in compared.paths if shell_snapshot.seal_candidate(raw, compared.marks)]
    if old is None:
        old, _ = shell_snapshot.previous_sources(compared, candidates)
    for raw in candidates:
        path = compared.root / raw
        key = str(path)
        prior = entries.get(key, {})
        before, text = old.get(raw, (shell_snapshot.UNKNOWN, None))
        governs = _governs(path, compared, prior)
        if governs is None:
            judged.unsearched.append(key)
        if not governs:
            continue
        digest, after = compared.current(raw) if raw not in compared.late else (shell_snapshot.UNKNOWN, None)
        if "seal" not in prior and (before == digest != shell_snapshot.UNKNOWN or protected.same_enforcement(path, text, after)):
            continue
        baseline_text = prior.get("seal_text") if "seal" in prior else text
        entry, rows = seal_entry(path, compared.root, judged.changes.get(key, prior), prior.get("seal", before), digest,
                                 baseline_text)
        judged.changes[key] = entry
        judged.found.extend(rows)


def _governs(path: Path, compared: shell_snapshot.Comparison, prior: dict) -> bool | None:
    if "seal" in prior:
        return True
    if path.name.casefold() == protected.CONFIG_NAME:
        return protected.is_config_target(path, path.resolve(), Path(compared.before.get("cwd", compared.root)))
    if not path.is_symlink():
        return False
    if time.monotonic() >= compared.until:
        return None
    from lib import stop_scan

    try:
        with stop_scan.scan_alarm(compared.until):
            return config_imports.config_presence(path.parent, path.name, until=compared.until)
    except stop_scan.OutOfTime:
        return None
