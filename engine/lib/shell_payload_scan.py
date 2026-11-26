from __future__ import annotations

import os
from collections.abc import Callable

try:
    from .opaque_write import (
        SHELL_C_INTERPRETERS,
        _bare_interpreter_name,
        _execution_contexts,
        _interpreter_run,
        _run_findings,
        decode_pipe_findings,
        dynamic_heredoc_findings,
        inline_interpreter_findings,
        inplace_edit_findings,
        interpreter_stdin_findings,
        opaque_source_findings,
    )
    from .protected import authorized
    from .shell_lines import shell_pass
    from .shell_parse import (
        _literal_contents,
        _logical_lines,
        _pipeline_groups,
        command_expansions,
        heredoc_events,
        interpreter_invocation,
    )
    from .shell_routes import unplaced_content
    from .shell_scripts import Script, awk_writes, eval_scripts, herestring_scripts
    from .shell_self_protection import (
        command_findings,
        _command_segments,
        _segment_cwds,
        _finding,
        _finding_factory,
    )
    from .shell_write_paths import target_findings, dynamic_target_findings
except ImportError:
    from opaque_write import (
        SHELL_C_INTERPRETERS,
        _bare_interpreter_name,
        _execution_contexts,
        _interpreter_run,
        _run_findings,
        decode_pipe_findings,
        dynamic_heredoc_findings,
        inline_interpreter_findings,
        inplace_edit_findings,
        interpreter_stdin_findings,
        opaque_source_findings,
    )
    from protected import authorized
    from shell_lines import shell_pass
    from shell_parse import (
        _literal_contents,
        _logical_lines,
        _pipeline_groups,
        command_expansions,
        heredoc_events,
        interpreter_invocation,
    )
    from shell_routes import unplaced_content
    from shell_scripts import Script, awk_writes, eval_scripts, herestring_scripts
    from shell_self_protection import (
        command_findings,
        _command_segments,
        _segment_cwds,
        _finding,
        _finding_factory,
    )
    from shell_write_paths import target_findings, dynamic_target_findings

MAX_SHELL_PAYLOAD_DEPTH = 1
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


def opaque_write_findings(command: str, config: dict | None = None, cwd: str | os.PathLike[str] | None = None) -> list[dict]:
    return _opaque_findings(command, config, 0, cwd)


def _opaque_findings(
    command: str,
    config: dict | None,
    depth: int,
    cwd: str | os.PathLike[str] | None = None,
) -> list[dict]:
    if not command or authorized(config):
        return []
    make_finding = _finding_factory(command)
    findings: list[dict] = []
    findings.extend(inline_interpreter_findings(command, make_finding, cwd=cwd))
    findings.extend(interpreter_stdin_findings(command, make_finding, _stdin_recurse(command, config, depth, cwd), cwd=cwd))
    findings.extend(dynamic_heredoc_findings(command, make_finding))
    findings.extend(decode_pipe_findings(command, make_finding))
    findings.extend(inplace_edit_findings(command, make_finding, cwd=cwd))
    findings.extend(opaque_source_findings(command, make_finding))
    findings.extend([make_finding("opaque_source_write")] if unplaced_content(command) else [])
    findings.extend(_shell_payload_findings(command, config, depth, cwd))
    findings.extend(_script_findings(command, config, depth, cwd))
    return findings


def _script_findings(command: str, config: dict | None, depth: int, cwd: str | os.PathLike[str] | None) -> list[dict]:
    findings = [row for body in shell_pass(command).substitutions for row in _recursed_findings(body, config, depth, cwd)]
    expansions = command_expansions(command, cwd)
    contexts = _execution_contexts(command, cwd)
    nested = None if depth >= MAX_SHELL_PAYLOAD_DEPTH else lambda body: _recursed_findings(body, config, depth + 1, cwd)
    for script in [*herestring_scripts(command, expansions), *eval_scripts(command, expansions)]:
        findings += _script_rows(script, command, contexts, nested)
    return findings + ([_finding("inline_interpreter_write", command)] if awk_writes(command, expansions) else [])


def _script_rows(script: Script, command: str, contexts: dict, nested: Callable[[str], list[dict]] | None) -> list[dict]:
    if script.interpreter not in SHELL_SCRIPTS:
        run = _interpreter_run(script.interpreter, script.body, script.segment, contexts)
        return _run_findings(run, _finding_factory(command), "interpreter_heredoc_write")
    if script.body is None or nested is None:
        return [_finding("shell_payload_block", command)]
    return nested(script.body)


def _stdin_recurse(
    command: str,
    config: dict | None,
    depth: int,
    cwd: str | os.PathLike[str] | None,
) -> Callable[[str], list[dict]]:
    def recurse(body: str) -> list[dict]:
        if depth >= MAX_SHELL_PAYLOAD_DEPTH:
            return [_finding("interpreter_heredoc_write", command)]
        return _recursed_findings(body, config, depth + 1, cwd)
    return recurse


def _shell_payload_findings(
    command: str,
    config: dict | None,
    depth: int,
    cwd: str | os.PathLike[str] | None,
) -> list[dict]:
    findings = []
    for segment, segment_cwd in _segment_cwds(command, cwd, None):
        invocation = interpreter_invocation(segment)
        if invocation is None or invocation.interpreter not in SHELL_C_INTERPRETERS:
            continue
        if invocation.payload is None or depth >= MAX_SHELL_PAYLOAD_DEPTH:
            findings.append(_finding("shell_payload_block", command))
            continue
        findings.extend(_recursed_findings(invocation.payload, config, depth + 1, segment_cwd))
    return findings


def _recursed_findings(
    payload: str,
    config: dict | None,
    depth: int,
    cwd: str | os.PathLike[str] | None,
) -> list[dict]:
    return (
        command_findings(payload, config, cwd=cwd)
        + target_findings(payload, config, cwd=cwd)
        + _opaque_findings(payload, config, depth, cwd)
        + dynamic_target_findings(payload, config, cwd)
    )


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
