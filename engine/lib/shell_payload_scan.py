from __future__ import annotations

import os

try:
    from .opaque_write import _bare_interpreter_name
    from .protected import authorized
    from .shell_lines import shell_pass
    from .shell_parse import (
        SHELL_C_INTERPRETERS, _literal_contents, _logical_lines, _pipeline_groups,
        command_expansions, heredoc_events, interpreter_invocation,
    )
    from .shell_scripts import eval_scripts, herestring_scripts
    from .shell_self_protection import command_findings, _command_segments, _segment_cwds
    from .shell_write_paths import target_findings
except ImportError:
    from opaque_write import _bare_interpreter_name
    from protected import authorized
    from shell_lines import shell_pass
    from shell_parse import (
        SHELL_C_INTERPRETERS, _literal_contents, _logical_lines, _pipeline_groups,
        command_expansions, heredoc_events, interpreter_invocation,
    )
    from shell_scripts import eval_scripts, herestring_scripts
    from shell_self_protection import command_findings, _command_segments, _segment_cwds
    from shell_write_paths import target_findings

MAX_SHELL_PAYLOAD_DEPTH = 12
SHELL_SCRIPTS = SHELL_C_INTERPRETERS | {"eval"}


def _scan_targets(command: str) -> list[str]:
    expansions = command_expansions(command)
    scripts = [*herestring_scripts(command, expansions), *eval_scripts(command, expansions)]
    bodies = [script.body for script in scripts if script.body and script.interpreter in SHELL_SCRIPTS]
    return (
        [command] + _literal_shell_c_payloads(command)
        + _literal_shell_stdin_payloads(command) + _literal_shell_pipe_payloads(command)
        + _substitution_bodies(command) + bodies
    )


def nested_target_findings(command: str, cfg: dict, cwd: str | os.PathLike[str] | None, depth: int = 0) -> list[dict]:
    if depth >= MAX_SHELL_PAYLOAD_DEPTH or authorized(cfg):
        return []
    found = []
    for payload, folder in _nested_shells(command, cwd):
        found.extend(command_findings(payload, cfg, cwd=folder))
        found.extend(target_findings(payload, cfg, cwd=folder))
        found.extend(nested_target_findings(payload, cfg, folder, depth + 1))
    return found


def _nested_shells(command: str, cwd: str | os.PathLike[str] | None):
    direct = set()
    for segment, folder in _segment_cwds(command, cwd, None):
        invocation = interpreter_invocation(segment)
        if invocation and invocation.interpreter in SHELL_C_INTERPRETERS and invocation.payload:
            direct.add(invocation.payload)
            yield invocation.payload, folder
    for payload in _scan_targets(command)[1:]:
        if payload not in direct:
            yield payload, cwd


def _literal_shell_c_payloads(command: str) -> list[str]:
    payloads = []
    for segment in _command_segments(command):
        invocation = interpreter_invocation(segment)
        if invocation is not None and invocation.interpreter in SHELL_C_INTERPRETERS and invocation.payload is not None:
            payloads.append(invocation.payload)
    return list(dict.fromkeys(payloads))


def _substitution_bodies(command: str) -> list[str]:
    found: list[str] = []
    queue = shell_pass(command).substitutions
    while queue:
        body = queue.pop(0)
        found.append(body)
        queue.extend(shell_pass(body).substitutions)
    return found


def _literal_shell_stdin_payloads(command: str) -> list[str]:
    return [
        event.body for event in heredoc_events(command)
        if not event.dynamic and _bare_interpreter_name(event.consumer_segment) in SHELL_C_INTERPRETERS
    ]


def _pipe_stdin_payload(group: list[list[str]]) -> str | None:
    if len(group) < 2 or _bare_interpreter_name(group[-1]) not in SHELL_C_INTERPRETERS:
        return None
    producer_texts = _literal_contents(group[:-1])
    texts = [text for text in producer_texts if text is not None]
    if len(texts) != len(group) - 1 or len(texts) != len(producer_texts):
        return None
    return "\n".join(texts)


def _literal_shell_pipe_payloads(command: str) -> list[str]:
    groups = [group for line, _, _ in _logical_lines(command) for group in _pipeline_groups(line)]
    payloads = [_pipe_stdin_payload(group) for group in groups]
    return [payload for payload in payloads if payload is not None]
