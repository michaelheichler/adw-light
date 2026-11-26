from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

try:
    from .findings import Finding, Rule
    from .opaque_write import MUTATING_VERB_RE
    from .policy_cli_guard import mutates_policy
    from .protected import POLICY_ACTION, authorized, is_install_surface_path
    from .shell_expansion import Expansions
    from .shell_parse import (
        _basename,
        _command_word_index,
        _is_quoted,
        _leading_assignments,
        _redirect_paths,
        _segment_text,
        _segments,
        _words,
        command_expansions,
        executable_text,
        line_segments,
    )
    from .update_policy import _segment_contexts, untrusted_update
    from .write_targets import mutation_targets, writes_by_verb
except ImportError:
    from findings import Finding, Rule
    from opaque_write import MUTATING_VERB_RE
    from policy_cli_guard import mutates_policy
    from protected import POLICY_ACTION, authorized, is_install_surface_path
    from shell_expansion import Expansions
    from shell_parse import (
        _basename,
        _command_word_index,
        _is_quoted,
        _leading_assignments,
        _redirect_paths,
        _segment_text,
        _segments,
        _words,
        command_expansions,
        executable_text,
        line_segments,
    )
    from update_policy import _segment_contexts, untrusted_update
    from write_targets import mutation_targets, writes_by_verb

INSTALLER_NAMES = frozenset({
    "install.sh", "merge-claude-settings.py", "merge-codex-config.py", "merge-codex-hooks.py",
})
CAP_VARS = frozenset({
    "CLEANCODER_FUNC_BLOCK_LINES", "ADW_FUNC_BLOCK_LINES",
    "ADW_SENTENCE_WORD_CAP", "ADW_LIST_ITEM_CAP",
    "ADW_MAX_SCAN_BYTES", "ADW_ALLOW_PROTECTED_EDIT",
})
NO_VERIFY_FLAGS = frozenset({"--no-verify", "-n"})
STATE_DELETE_VERBS = frozenset({"rm", "unlink", "shred"})
STATE_TARGET_RE = re.compile(r"\.adw\b|\.agent-discipline\b|agent-discipline/(?:state|ledger)|adw-unread-markers\b")
WRITE_OR_EDIT_ACTION = "Use the Write or Edit tool for file content."
SAVE_THEN_RUN_ACTION = "Save the script with Write or Edit, then run the file."
RULES: dict[str, Rule] = {
    "install_without_sandbox_home": Rule(
        detail="Installer or updater lacks a trusted installation route",
        action=(
            "Run the updater as one plain command, with the absolute path and host flags only, "
            "for example the full home folder path followed by /.adw/bin/adw update --claude --codex --omp. "
            "Write no ~, $HOME, variable, pipe, redirect, or second command in it. "
            "To install from a checkout, ask the user to run ./install.sh in Terminal. "
            "Test installers only in an isolated HOME."
        ),
    ),
    "commit_gate_bypass": Rule(
        detail="Commit skips the pre-commit gate",
        action="Drop the no-verify flag and repair the reported finding.",
    ),
    "cap_override": Rule(
        detail="Discipline cap or escape overridden on the command line",
        action="Fix the code shape instead of raising the cap.",
    ),
    "state_deletion": Rule(
        detail="Watcher state or gate config deleted",
        action="Leave the state in place and repair the reported finding.",
    ),
    "state_mutation": Rule(
        detail="Watcher state or gate config mutated",
        action="Leave watcher state under host control and repair the reported finding.",
    ),
    "config_seal": Rule(
        detail="Shell command changes the project policy through adw-config",
        action=POLICY_ACTION,
    ),
    "watcher_install_surface": Rule(
        detail="Shell command mutates the live watcher install",
        action="Change the repo source and reinstall instead of editing the live install.",
    ),
    "inline_interpreter_write": Rule(
        detail="Inline interpreter payload can write or is unreadable",
        action=WRITE_OR_EDIT_ACTION,
    ),
    "shell_payload_block": Rule(
        detail="Shell -c payload is unreadable or nested past one level",
        action=WRITE_OR_EDIT_ACTION,
    ),
    "interpreter_heredoc_write": Rule(
        detail="Heredoc or pipe feeding an interpreter's stdin can write or is unreadable",
        action=SAVE_THEN_RUN_ACTION,
    ),
    "dynamic_heredoc_write": Rule(
        detail="Dynamic or unterminated heredoc aimed at a file",
        action=WRITE_OR_EDIT_ACTION,
    ),
    "decode_pipe_write": Rule(
        detail="Decode pipe ends in a file write",
        action=WRITE_OR_EDIT_ACTION,
    ),
    "inplace_edit_write": Rule(
        detail="In-place editor mutates a file outside the Edit tool",
        action=WRITE_OR_EDIT_ACTION,
    ),
    "opaque_source_write": Rule(
        detail="Opaque copy source feeds a file write",
        action=WRITE_OR_EDIT_ACTION,
    ),
    "dynamic_write_target": Rule(
        detail="Shell write destination is unknown before the command runs",
        action="Use an absolute literal destination, or the Write or Edit tool for file content.",
    ),
}


def command_findings(command: str, config: dict | None = None, home: str | os.PathLike[str] | None = None, cwd: str | os.PathLike[str] | None = None) -> list[dict]:
    if not command or authorized(config):
        return []
    segments = _command_segments(command)
    contexts = _segment_cwds(command, cwd, home)
    hits = []
    if any(
        (_runs_installer(segment) and not _sets_home(segment)) or untrusted_update(segment, home, segment_cwd)
        for segment, segment_cwd in contexts
    ):
        hits.append("install_without_sandbox_home")
    if any(_skips_commit_gate(segment) for segment in segments):
        hits.append("commit_gate_bypass")
    if any(_overrides_cap(segment) for segment in segments):
        hits.append("cap_override")
    expansions = command_expansions(command, cwd)
    hits += _protected_target_hits(segments, expansions)
    if any(_mutates_install_surface(segment, home, expansions, folder) for segment, folder in contexts):
        hits.append("watcher_install_surface")
    if any(mutates_policy(segment, segment_cwd) for segment, segment_cwd in contexts):
        hits.append("config_seal")
    return [_finding(rule, command) for rule in hits]


def _protected_target_hits(segments: list[list[str]], expansions: Expansions) -> list[str]:
    hits = []
    if any(_deletes_state(segment, expansions) for segment in segments):
        hits.append("state_deletion")
    elif any(_mutates_state(segment, expansions) for segment in segments):
        hits.append("state_mutation")
    return hits


def _segment_views(command: str) -> list[list[list[str]]]:
    return [_segments(executable_text(command)), line_segments(command)]


def _command_segments(command: str) -> list[list[str]]:
    unique = {tuple(segment): segment for view in _segment_views(command) for segment in view}
    return list(unique.values())


def _segment_cwds(command: str, cwd: str | os.PathLike[str] | None, home: str | os.PathLike[str] | None) -> list[tuple[list[str], Path]]:
    unique = {}
    for view in _segment_views(command):
        for segment, segment_cwd in _segment_contexts(view, cwd, home):
            unique.setdefault((tuple(segment), segment_cwd), (segment, segment_cwd))
    return list(unique.values())


def _session_path(path: str, cwd: str | os.PathLike[str] | None) -> str:
    if cwd is None or path.startswith("~") or os.path.isabs(path):
        return path
    return os.path.join(cwd, path)


def _finding_factory(command: str) -> Callable[[str], dict]:
    def make_finding(rule: str) -> dict:
        return _finding(rule, command)
    return make_finding


def _sets_home(segment: list[str]) -> bool:
    return "HOME" in _leading_assignments(segment)


def _overrides_cap(segment: list[str]) -> bool:
    return any(name in CAP_VARS for name in _leading_assignments(segment))


def _runs_installer(segment: list[str]) -> bool:
    index = _command_word_index(segment)
    return index < len(segment) and _basename(segment[index]) in INSTALLER_NAMES


def _skips_commit_gate(segment: list[str]) -> bool:
    words = _words(segment)
    if "git" not in words or "commit" not in words:
        return False
    if words.index("git") > words.index("commit"):
        return False
    return any(token in NO_VERIFY_FLAGS for token in segment if not _is_quoted(token))


def _deletes_state(segment: list[str], expansions: Expansions) -> bool:
    if not any(_basename(word) in STATE_DELETE_VERBS for word in _words(segment)):
        return False
    return _targets_state(segment, expansions)


def _mutates_state(segment: list[str], expansions: Expansions) -> bool:
    return _is_mutating(segment) and _targets_state(segment, expansions)


def _targets_state(segment: list[str], expansions: Expansions) -> bool:
    return any(STATE_TARGET_RE.search(target) for target in mutation_targets(segment, expansions))


def _is_mutating(segment: list[str]) -> bool:
    return bool(_redirect_paths(segment)) or bool(MUTATING_VERB_RE.search(_segment_text(segment))) or writes_by_verb(segment)


def _mutates_install_surface(segment: list[str], home: str | os.PathLike[str] | None, expansions: Expansions, cwd: Path) -> bool:
    if not _is_mutating(segment):
        return False
    return any(is_install_surface_path(_session_path(path, cwd), home) for path in mutation_targets(segment, expansions))


def _finding(rule: str, command: str) -> dict:
    rule_spec = RULES[rule]
    return Finding(
        family="self_protection",
        rule=rule,
        line=1,
        detail=rule_spec.detail,
        force=True,
        snippet=command.strip()[:180],
        action=rule_spec.action,
        path=None,
        severity=None,
        tool_use_id=None,
    ).to_dict()
