from __future__ import annotations

import os
import re
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from lib.baseline import split_against
from lib.commit_command import GitCall, UnreadableCommit, bash_command, git_calls
from lib import payloads, shell_snapshot
from lib.commit_index_source import INDEX_WRITERS, commit_blobs, commit_reads_worktree, index_blobs, staged_additions
from lib.commit_inputs import InvalidCommitFolder, RelocatedCommit, is_dynamic
from lib.config import SURFACE_COMMIT, StorageRoots, effective_config
from lib.fixture_prose import index_scan_config
from lib.git_port import view
from lib.hookio import (
    PARSE_FAILURE, advise, allow, claude_pretool_response, deny, fail_closed, read_payload, write_payload,
)
from lib.merge_state import incoming_commits, revision_text, session_started
from lib.regular_file import read_regular_text
from lib.payloads import cwd
from lib.path_policy import anchored
from lib.reporting import inherited_advice, record_findings, run_with_ledger, verdict_message
from lib.scan_input import is_binary_content
from lib.scanner import file_length_findings, scan_all, scannable_text
from lib.shell_text import decode_ansi_c_value

COMMIT_MESSAGE_PATH = "commit_message.md"
CONVENTIONAL_SUBJECT_RE = re.compile(
    r"\A(?P<prefix>[A-Za-z][A-Za-z0-9_-]*(?:\([^()\r\n]+\))?!?:[ \t]+)"
)
TRAILER_PREFIX_RE = re.compile(
    r"^(?P<prefix>(?:BREAKING CHANGE|[^\s:]+)[ \t]*:[ \t]*)"
)

COMMIT_REFUSED = "agent-discipline-watcher blocked this commit rather than letting it through. Cause: "
STAGE_SEPARATELY = (
    "The commit gate scans the index before this command runs, so it cannot judge content the same command stages "
    "or reads from the worktree. Stage in its own tool call, then commit without paths, -a, -i, -o, or -p."
)
UNREAD_INDEX = (
    "The commit gate could not read the index of {repo}, which lies outside the current project."
)

@dataclass(frozen=True, slots=True)
class _CommitRunContext:
    payload: dict
    config: dict | None
    roots: StorageRoots


def run(
    payload: dict,
    config: dict | None = None,
    ledger_root: str | os.PathLike[str] | None = None,
    state_root: str | os.PathLike[str] | None = None,
) -> dict:
    context = _CommitRunContext(
        payload=payload,
        config=config,
        roots=StorageRoots(state=state_root, ledger=ledger_root),
    )
    return fail_closed("commit", lambda: _checked_run(context), cwd(payload))


def _checked_run(context: _CommitRunContext) -> dict:
    if context.payload is PARSE_FAILURE:
        raise ValueError("unreadable hook payload")
    return _run(context)


def _run(context: _CommitRunContext) -> dict:
    return run_with_ledger(
        hook="pre_commit",
        payload=context.payload,
        gate=lambda turn_id: _index_checked_gate(context, turn_id),
        ledger_root=context.roots.ledger,
        state_root=context.roots.state,
    )


def _index_checked_gate(context: _CommitRunContext, turn_id: str) -> dict:
    try:
        calls = git_calls(bash_command(context.payload), _payload_cwd(context.payload))
    except (InvalidCommitFolder, RelocatedCommit) as exc:
        return deny(f"{COMMIT_REFUSED}{exc}")
    except UnreadableCommit as exc:
        if exc.cwd is None or _covered(context.payload, _repo_root(exc.cwd) or exc.cwd):
            return allow()
        return deny(f"{COMMIT_REFUSED}{exc}")
    return _gate(context, turn_id, calls) if any(call.verb == "commit" for call in calls) else allow()


def _covered(payload: dict, repo: Path, *, named: bool = False) -> bool:
    if not payloads.session_id(payload) or not payloads.tool_use_id(payload):
        return False
    if named:
        return True
    location = shell_snapshot.project_location(_payload_cwd(payload).resolve())
    return location is not None and Path(repo).resolve().is_relative_to(location[0])


def _gate(context: _CommitRunContext, turn_id: str, commit_cwds: list[GitCall]) -> dict:
    started = time.monotonic()
    cwd = _payload_cwd(context.payload)
    outer_cfg = effective_config(context.config, cwd)
    messages, covered = {}, {}
    for i, call in enumerate(commit_cwds):
        if call.verb != "commit":
            continue
        covered[i] = call.new_folder or _covered(context.payload, call.repo() or call.cwd, named=call.parent_repo is not None)
        try:
            messages[i] = _call_message(call)
        except UnreadableCommit as exc:
            if not covered[i]:
                return deny(f"{COMMIT_REFUSED}{exc}")
            messages[i] = None
    try:
        repo_rows = _repo_findings_by_repo(
            commit_cwds, context.config, _session_start(context), covered,
        )
    except UnreadableCommit as exc:
        return deny(f"{COMMIT_REFUSED}{exc}")
    message_findings = [row for text in messages.values() for row in _scan_message(text, outer_cfg)]
    stamp = _RecordStamp(
        session_id=str(context.payload.get("session_id") or ""),
        tool_use_id=str(context.payload.get("tool_use_id") or ""),
        turn_id=turn_id,
        duration_ms=int((time.monotonic() - started) * 1000),
        ledger_root=context.roots.ledger,
    )
    scan = _CommitScan(repo_rows, message_findings, outer_cfg)
    decisions = _record_all(scan, stamp)
    kind, message = verdict_message(decisions, outer_cfg) if decisions else ("release", "")
    if kind == "block":
        return deny(message)
    notice = _inherited_notice(scan)
    if kind == "observe":
        return advise("\n".join(part for part in (message, notice) if part), "PreToolUse")
    return {"systemMessage": notice} if notice else allow()


def _inherited_notice(scan: _CommitScan) -> str:
    notices = (inherited_advice(row.inherited, row.cfg) for row in scan.repository_rows)
    return "\n".join(notice for notice in notices if notice)


class _RecordStamp(NamedTuple):
    session_id: str
    tool_use_id: str
    turn_id: str
    duration_ms: int
    ledger_root: str | os.PathLike[str] | None


@dataclass(frozen=True, slots=True)
class _RepoScan:
    cfg: dict
    owned: list[dict]
    inherited: list[dict]


@dataclass(frozen=True, slots=True)
class _CommitScan:
    repository_rows: list[_RepoScan]
    message_rows: list[dict]
    outer_config: dict


def _record_all(
    scan: _CommitScan, stamp: _RecordStamp
) -> list[tuple[dict, str]]:
    decisions: list[tuple[dict, str]] = []
    for row in scan.repository_rows:
        decisions.extend(record_findings(
            session_id=stamp.session_id, hook="pre_commit", event="PreCommit",
            findings=row.owned, turn_id=stamp.turn_id, tool_use_id=stamp.tool_use_id,
            duration_ms=stamp.duration_ms, root=stamp.ledger_root, config=row.cfg,
        ))
    decisions.extend(record_findings(
        session_id=stamp.session_id, hook="pre_commit", event="PreCommit",
        findings=scan.message_rows, turn_id=stamp.turn_id,
        tool_use_id=stamp.tool_use_id, duration_ms=stamp.duration_ms,
        root=stamp.ledger_root, config=scan.outer_config,
    ))
    return decisions


def _session_start(context: _CommitRunContext) -> float | None:
    session_id = str(context.payload.get("session_id") or "")
    return session_started(session_id, context.roots.state) if session_id else None


def _commit_content(repo: Path, call: GitCall, previous: list[GitCall], covered: bool) -> dict[str, bytes] | None:
    try:
        with _scan_index(call.index_file):
            if not call.index_only:
                added = staged_additions(repo, call, previous, STAGE_SEPARATELY)
                return dict(commit_blobs(repo, call, STAGE_SEPARATELY, added))
    except (UnreadableCommit, OSError, subprocess.SubprocessError):
        pass
    if not covered and (call.index_only or commit_reads_worktree(call.args)
                        or any(earlier.verb in INDEX_WRITERS for earlier in previous)):
        raise UnreadableCommit(STAGE_SEPARATELY)
    try:
        with _scan_index(call.index_file):
            return dict(index_blobs(repo, call, STAGE_SEPARATELY))
    except (UnreadableCommit, OSError, subprocess.SubprocessError) as exc:
        if covered:
            return None
        raise UnreadableCommit(UNREAD_INDEX.format(repo=repo)) from exc


def _repo_findings_by_repo(
    calls: list[GitCall], config: dict | None, started_at: float | None, covered: dict,
) -> list[_RepoScan]:
    rows = []
    seen = set()
    for i, call in enumerate(calls):
        if call.verb != "commit" or call.new_folder or (repo := call.repo()) is None:
            continue
        cfg = effective_config(config, repo)
        blobs = _commit_content(repo, call, calls[:i], covered[i])
        if blobs is None:
            continue
        amended = "--amend" in call.args
        parents = view().commit_parents(repo, "HEAD") if amended else ["HEAD"]
        if parents is None and not covered[i]:
            raise UnreadableCommit(UNREAD_INDEX.format(repo=repo))
        if parents is None:
            continue
        base = parents[:1]
        key = (repo, tuple(base), frozenset(blobs.items()))
        if key not in seen:
            rows.append(_RepoScan(cfg, *_repo_findings(repo, cfg, started_at, blobs, base)))
            seen.add(key)
    return rows


@contextmanager
def _scan_index(path: Path | None):
    if path is None:
        yield
        return
    previous = os.environ.get("GIT_INDEX_FILE")
    os.environ["GIT_INDEX_FILE"] = os.fspath(path)
    try:
        yield
    finally:
        if previous is None:
            del os.environ["GIT_INDEX_FILE"]
        else:
            os.environ["GIT_INDEX_FILE"] = previous


def _repo_findings(repo: Path, cfg: dict, started_at: float | None, blobs: dict[str, bytes], base: list[str]) -> tuple[list[dict], list[dict]]:
    incoming = incoming_commits(repo, started_at)
    findings: list[dict] = []
    inherited: list[dict] = []
    for path, raw in blobs.items():
        if is_binary_content(path, raw):
            continue
        text = raw.decode("utf-8", errors="replace")
        scanned = file_length_findings(path, text) if scannable_text(text, cfg) is None else scan_all(path, text, index_scan_config(cfg, path, repo))
        previous = [revision_text(repo, revision, path) for revision in (*base, *incoming)]
        owned, carried = split_against(previous, path, scanned, cfg, text)
        findings.extend({**finding, "path": path} for finding in owned)
        if incoming:
            inherited.extend({**finding, "path": path} for finding in carried)
    return findings, inherited


def _scan_message(text: str | None, cfg: dict) -> list[dict]:
    if not text:
        return []
    return [
        {**finding, "path": COMMIT_MESSAGE_PATH, "surface": SURFACE_COMMIT}
        for finding in scan_all(
            COMMIT_MESSAGE_PATH, text, cfg, document=False, hidden_ranges=_commit_metadata_ranges(text)
        )
    ]


def _commit_metadata_ranges(text: str) -> tuple[tuple[int, int], ...]:
    subject = CONVENTIONAL_SUBJECT_RE.match(text)
    ranges = [subject.span("prefix")] if subject else []
    ranges.extend(_trailer_metadata_ranges(text))
    return tuple(ranges)


def _trailer_metadata_ranges(text: str) -> tuple[tuple[int, int], ...]:
    lines = text.splitlines(keepends=True)
    end = len(lines) - 1
    while end >= 0 and not lines[end].strip():
        end -= 1
    start = end
    while start > 0 and lines[start - 1].strip():
        start -= 1
    if start <= 0:
        return ()
    offset = sum(len(line) for line in lines[:start])
    ranges = []
    saw_trailer = False
    for line in lines[start:end + 1]:
        match = TRAILER_PREFIX_RE.match(line)
        if match:
            ranges.append((offset + match.start("prefix"), offset + match.end("prefix")))
            saw_trailer = True
        elif not saw_trailer or not line.startswith((" ", "\t")):
            return ()
        offset += len(line)
    return tuple(ranges)


def _call_message(call: GitCall) -> str | None:
    values = _message_values(call.args, call.cwd, call.stdin, literal_only=call.new_folder)
    if values:
        return "\n\n".join(values)
    if not call.new_folder and "--amend" in call.args:
        return view().commit_message(call.parent_repo or call.cwd, "HEAD")
    return None


def _file_message(value: str, cwd: Path | None, stdin: str | None) -> str:
    name = decode_ansi_c_value(value)
    if name == "-" and stdin is not None:
        return stdin
    if not name or name == "-":
        raise UnreadableCommit("commit message stdin is unavailable; use -m or a readable --file")
    base = cwd or Path.cwd()
    path = anchored(name, base)
    literal = base / name
    if literal != path and literal.exists():
        raise UnreadableCommit("commit message path has both literal and expanded home targets; use an absolute path")
    if not base.is_dir():
        path = path.parent.resolve() / path.name
    try:
        return read_regular_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise UnreadableCommit(f"commit message file cannot be read: {path}") from exc


def _message_file_at(tokens: list[str], cursor: int) -> tuple[str | None, int]:
    token = tokens[cursor]
    if token.startswith("--file="):
        return token.partition("=")[2], cursor + 1
    short = token[1:] if token.startswith("-") and not token.startswith("--") else ""
    flags, marker, inline = short.partition("F")
    bundled = bool(marker) and all(char in _BOOLEAN_BUNDLE_CHARS for char in flags)
    if bundled and inline:
        return inline, cursor + 1
    if token == "--file" or bundled:
        if cursor + 1 >= len(tokens):
            raise UnreadableCommit("commit message file is missing")
        return tokens[cursor + 1], cursor + 2
    return None, cursor + 1


def _message_value_at(tokens: list[str], cursor: int, cwd: Path | None = None, stdin: str | None = None, *,
                      literal_only: bool = False) -> tuple[str | None, int]:
    token = tokens[cursor]
    file_name, next_cursor = _message_file_at(tokens, cursor)
    if file_name is not None:
        if literal_only:
            return None, next_cursor
        if is_dynamic(file_name):
            raise UnreadableCommit("Unresolved commit message file.")
        return _file_message(file_name, cwd, stdin), next_cursor
    if token in {"-m", "--message"} or _is_message_bundle(token):
        next_cursor = cursor + 2
        if cursor + 1 >= len(tokens):
            return None, next_cursor
        return decode_ansi_c_value(tokens[cursor + 1]), next_cursor
    name, separator, inline = token.partition("=")
    if separator and name == "--message":
        return decode_ansi_c_value(inline), cursor + 1
    flags, marker, value = token[1:].partition("m")
    if token.startswith("-") and marker and value and all(flag in _BOOLEAN_BUNDLE_CHARS for flag in flags):
        return decode_ansi_c_value(value), cursor + 1
    return None, cursor + 1


def _message_values(tokens: list[str], cwd: Path | None = None, stdin: str | None = None, *,
                    literal_only: bool = False) -> list[str]:
    values: list[str] = []
    cursor = 0
    while cursor < len(tokens):
        if tokens[cursor] == "--":
            break
        if tokens[cursor] in {"--author", "--date", "--cleanup"}:
            cursor += 2
            continue
        previous = cursor
        value, cursor = _message_value_at(tokens, cursor, cwd, stdin, literal_only=literal_only)
        if value is not None:
            if is_dynamic(value) and not _message_file_at(tokens, previous)[0]:
                if literal_only:
                    continue
                raise UnreadableCommit("Unresolved commit message.")
            values.append(value)
    return values


_BOOLEAN_BUNDLE_CHARS = frozenset("ainqvoes")


def _is_message_bundle(token: str) -> bool:
    if not token.startswith("-") or token.startswith("--") or len(token) <= 2:
        return False
    if token[-1] != "m":
        return False
    return all(char in _BOOLEAN_BUNDLE_CHARS for char in token[1:-1])


def _payload_cwd(payload: dict) -> Path:
    return Path(payload.get("cwd") or os.getcwd())


def _repo_root(cwd: Path) -> Path | None:
    return view().toplevel(cwd, strict=True)


if __name__ == "__main__":
    write_payload(claude_pretool_response(run(read_payload())))
