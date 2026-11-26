from __future__ import annotations

import os
from pathlib import Path

try:
    from .opaque_write import SHELL_C_INTERPRETERS, inplace_edit_paths
    from .protected import authorized, path_findings
    from .shell_expansion import Expansions
    from .shell_lines import shell_pass
    from .shell_parse import (
        _bare,
        _content_words,
        _journal_words,
        _logical_lines,
        _segments,
        command_expansions,
        interpreter_invocation,
        line_segments,
        write_targets,
    )
    from .shell_test_targets import OUTER_START
    from .temp_scope import (
        Directories,
        command_targets,
        joined_paths,
        landing_directories,
        scratch_target,
        sealed_temp_tail,
        segment_directories,
    )
    from .write_shape import shaped_write_findings
    from .write_targets import callback_writes, generated_paths, mutation_targets, unknown_targets
    from .shell_self_protection import RULES, _command_segments, _session_path, _is_mutating, _finding
except ImportError:
    from opaque_write import SHELL_C_INTERPRETERS, inplace_edit_paths
    from protected import authorized, path_findings
    from shell_expansion import Expansions
    from shell_lines import shell_pass
    from shell_parse import (
        _bare,
        _content_words,
        _journal_words,
        _logical_lines,
        _segments,
        command_expansions,
        interpreter_invocation,
        line_segments,
        write_targets,
    )
    from shell_test_targets import OUTER_START
    from temp_scope import (
        Directories,
        command_targets,
        joined_paths,
        landing_directories,
        scratch_target,
        sealed_temp_tail,
        segment_directories,
    )
    from write_shape import shaped_write_findings
    from write_targets import callback_writes, generated_paths, mutation_targets, unknown_targets
    from shell_self_protection import RULES, _command_segments, _session_path, _is_mutating, _finding

BASH_WRITE_CAP = 100
OVERSIZE_WRITE = (
    "Shell write of {size} characters exceeds the {cap}-character cap for Bash file writes. "
    "Use the Write or Edit tool for file content."
)


def target_findings(command: str, config: dict | None = None, home: str | os.PathLike[str] | None = None, cwd: str | os.PathLike[str] | None = None) -> list[dict]:
    findings = []
    for path, text in _shell_targets(command, cwd).items():
        for finding in path_findings(_session_path(path, cwd), config, home, text):
            item = dict(finding)
            item["path"] = path
            findings.append(item)
    return findings


def _shell_targets(command: str, cwd: str | os.PathLike[str] | None = None) -> dict[str, str | None]:
    targets: dict[str, str | None] = {path: None for path in _mutation_paths(command, cwd)}
    targets.update({path: None for path in write_paths(command, cwd)})
    targets.update(dict(write_targets(command)))
    return targets


def dynamic_target_findings(command: str, config: dict | None = None, cwd: str | os.PathLike[str] | None = None) -> list[dict]:
    if not command or authorized(config):
        return []
    rule = RULES["dynamic_write_target"]
    return [
        {**_finding("dynamic_write_target", command), "detail": f"{rule.detail}: {word}", "match": word}
        for word in unresolved_write_destinations(command, OUTER_START, cwd)
    ]


def unresolved_write_destinations(command: str, start: Directories = OUTER_START, cwd: str | os.PathLike[str] | None = None) -> list[str]:
    if start == OUTER_START:
        command = sealed_temp_tail(command, cwd)
    expansions = command_expansions(command, cwd)
    segments = line_segments(command)
    words: list[str] = []
    for segment, folders in zip(segments, segment_directories(segments, start, expansions.path)):
        words += unknown_targets(segment, expansions) if _is_mutating(segment) else []
        words += callback_writes(segment, expansions)
        words += [] if folders is not None else _lost_destinations(segment, expansions, cwd)
    return list(dict.fromkeys(map(_bare, words)))


def _lost_destinations(segment: list[str], expansions: Expansions, cwd: str | os.PathLike[str] | None) -> list[str]:
    lost = [path for path in mutation_targets(segment, expansions) if not path.startswith("~") and not os.path.isabs(path)]
    invocation = interpreter_invocation(segment)
    scripts = shell_pass(" ".join(segment)).substitutions
    scripts += [invocation.payload] if invocation and invocation.interpreter in SHELL_C_INTERPRETERS and invocation.payload else []
    return lost + [word for script in scripts for word in unresolved_write_destinations(script, None, cwd)]


def _largest_oversize(commands: list[str], cwd: str | os.PathLike[str] | None = None) -> int | None:
    sizes = [oversize_write(command, cwd) for command in commands]
    known = [size for size in sizes if size is not None]
    return max(known) if known else None


def oversize_write(command: str, cwd: str | os.PathLike[str] | None = None) -> int | None:
    sizes = [len(text) for path, text in write_targets(command) if not scratch_target(path, command, cwd)]
    expansions = command_expansions(command, cwd)
    for line, _, raw_bodies in _logical_lines(command):
        paths = [expansions.path(word) for segment in _segments(line) for word, _ in _content_words(segment)]
        if paths and not all(path is not None and scratch_target(path, command, cwd) for path in paths):
            sizes.extend(len(body) for body in raw_bodies)
    oversized = [size for size in sizes if size > BASH_WRITE_CAP]
    return max(oversized) if oversized else None


def write_paths(command: str, cwd: str | os.PathLike[str] | None = None) -> list[str]:
    expansions = command_expansions(command, cwd)
    segments = line_segments(command)
    redirected = [
        path for segment, folders in zip(segments, landing_directories(segments, cwd, expansions.path))
        for path in joined_paths(_destinations(segment, expansions), folders)
    ]
    return redirected + [target for path in inplace_edit_paths(command) for target in command_targets(path, command, cwd)]


def _destinations(segment: list[str], expansions: Expansions) -> list[str]:
    redirected = [path for path in (expansions.path(word) for word, _ in _journal_words(segment)) if path is not None]
    return redirected + generated_paths(segment, expansions)


def _mutation_paths(command: str, cwd: str | os.PathLike[str] | None = None) -> list[str]:
    expansions = command_expansions(command, cwd)
    return [path for segment in _command_segments(command) if _is_mutating(segment) for path in mutation_targets(segment, expansions)]


def write_findings(command: str, config: dict | None = None, cwd: str | os.PathLike[str] | None = None) -> list[dict]:
    return shaped_write_findings(command, config, None if cwd is None else Path(cwd))[0]
