from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from .config import MAX_PROJECT_CONFIG_BYTES
    from .config_imports import imported_config_targets
    from .opaque_write import inplace_edit_paths
    from .protected import UnresolvableTildePath, _literal, authorized, is_config_target, path_findings
    from .regular_file import open_regular
    from .shell_expansion import Expansions
    from .shell_parse import LiteralWrite, _journal_words, _redirect_paths, command_expansions, line_segments, literal_writes, write_targets
    from .shell_self_protection import _is_mutating, _segment_cwds, _session_path
    from .shell_test_targets import OUTER_START, Scope, gated_paths
    from .temp_scope import command_targets, joined_paths, landing_directories, trusted_directory
    from .write_shape import shaped_write_findings
    from .write_targets import generated_paths, protected_mutation_targets
except ImportError:
    from config import MAX_PROJECT_CONFIG_BYTES
    from config_imports import imported_config_targets
    from opaque_write import inplace_edit_paths
    from protected import UnresolvableTildePath, _literal, authorized, is_config_target, path_findings
    from regular_file import open_regular
    from shell_expansion import Expansions
    from shell_parse import LiteralWrite, _journal_words, _redirect_paths, command_expansions, line_segments, literal_writes, write_targets
    from shell_self_protection import _is_mutating, _segment_cwds, _session_path
    from shell_test_targets import OUTER_START, Scope, gated_paths
    from temp_scope import command_targets, joined_paths, landing_directories, trusted_directory
    from write_shape import shaped_write_findings
    from write_targets import generated_paths, protected_mutation_targets


def target_findings(command: str, config: dict | None = None, home: str | os.PathLike[str] | None = None, cwd: str | os.PathLike[str] | None = None) -> list[dict]:
    if not command or authorized(config):
        return []
    findings = []
    for path, text in _protected_shell_targets(command, home, Path(cwd or ".").absolute()).items():
        for finding in path_findings(path, config, home, text, cwd=cwd):
            item = dict(finding)
            item["path"] = path
            findings.append(item)
    return findings


def _protected_shell_targets(command: str, home: str | os.PathLike[str] | None, cwd: Path) -> dict[str, str | None]:
    expansions = command_expansions(command, cwd)
    paths = {_session_path(path, cwd) for path in write_paths(command, cwd)}
    for segment, folder in _segment_cwds(command, cwd, home):
        local = expansions._replace(cwd=str(folder))
        if _is_mutating(segment):
            paths.update(_session_path(path, folder) for path in protected_mutation_targets(segment, local))
        paths.update(imported_config_targets(segment, local, folder, cwd))
    named = gated_paths(command, Scope(cwd, OUTER_START, outer=True), landed=True)
    for path in named:
        anchored = _session_path(path, cwd)
        if _config_destination(anchored, home, cwd) is not None:
            paths.add(anchored)
    targets = dict.fromkeys(sorted(paths))
    for path, text in _literal_protected_writes(command, home, cwd).items():
        targets[path] = text
    return targets


def _literal_protected_writes(command: str, home: str | os.PathLike[str] | None, cwd: Path) -> dict[str, str | None]:
    writes = literal_writes(command)
    targets = {}
    for write in writes:
        for path in command_targets(write.path, command, cwd):
            anchored = _session_path(path, cwd)
            resolved = _config_destination(anchored, home, cwd)
            if resolved is not None:
                targets[anchored] = _project_literal_config(command, cwd, resolved, write) if len(writes) == 1 else None
            else:
                targets[anchored] = write.text
    return targets


def _config_destination(path: str, home: str | os.PathLike[str] | None, cwd: Path) -> Path | None:
    try:
        literal = _literal(path, home)
    except UnresolvableTildePath:
        return None
    if literal is None:
        return None
    resolved = Path(os.path.realpath(literal))
    return resolved if is_config_target(literal, resolved, cwd) else None


def _project_literal_config(command: str, cwd: Path, path: Path, write: LiteralWrite) -> str | None:
    segments = line_segments(command)
    if len(segments) == 2 and trusted_directory(command, cwd) is not None:
        segments = segments[1:]
    if len(segments) != 1 or len(segments[0]) < 3:
        return None
    verb, *_, operator, _ = segments[0]
    if verb not in {"echo", "printf"} or operator not in {">", ">>"}:
        return None
    if len(_redirect_paths(segments[0])) != 1:
        return None
    if not write.append:
        return write.text
    try:
        with open_regular(path) as handle:
            before = handle.read(MAX_PROJECT_CONFIG_BYTES + 1)
        if len(before) <= MAX_PROJECT_CONFIG_BYTES:
            return before.decode("utf-8") + write.text
    except (OSError, UnicodeError):
        return None
    return None


def _shell_targets(
    command: str, cwd: str | os.PathLike[str] | None = None, *, created: frozenset[Path] = frozenset(),
) -> dict[str, str | None]:
    targets: dict[str, str | None] = dict.fromkeys(write_paths(command, cwd, created=created))
    targets.update(dict(write_targets(command)))
    return targets


def write_paths(
    command: str, cwd: str | os.PathLike[str] | None = None, *, created: frozenset[Path] = frozenset(),
) -> list[str]:
    expansions = command_expansions(command, cwd)
    segments = line_segments(command)
    directories = landing_directories(segments, cwd, expansions.path, created=created)
    redirected = [
        path for segment, folders in zip(segments, directories)
        for path in joined_paths(_destinations(segment, expansions), folders)
    ]
    return redirected + [target for path in inplace_edit_paths(command) for target in command_targets(path, command, cwd)]


def _destinations(segment: list[str], expansions: Expansions) -> list[str]:
    redirected = [path for path in (expansions.path(word) for word, _ in _journal_words(segment)) if path is not None]
    return redirected + generated_paths(segment, expansions)


def write_findings(command: str, config: dict | None = None, cwd: str | os.PathLike[str] | None = None) -> list[dict]:
    return shaped_write_findings(command, config, None if cwd is None else Path(cwd))[0]
