from __future__ import annotations

import re

from .shell_output import OutputNestingError, OutputPipeline, OutputStage, _output_pipeline_groups
from .shell_parse import (
    PIPE_OPERATORS, SEPARATORS, WRAPPER_COMMANDS, _basename, _journal_words, _literal_contents,
    _payload_command_index, _redirect_words, _segments, _skip_prefixes, _tokens, heredoc_events, line_segments,
)
from .shell_lines import command_lines

KEYWORD_CLOSERS = frozenset({"done", "fi", "esac"})
FUNCTION_RE = re.compile(r"(?:^|[\s;&|(){}])function\s+\S|[^\s;&|()<>$`'\"]\s*\(\s*\)")
HERESTRING = "<<<"
HEREDOC = "<<"
EXEC = "exec"


def unplaced_content(command: str) -> bool:
    segments = line_segments(command)
    literal = _has_literal_producer(command, segments)
    if literal and FUNCTION_RE.search(command) and any(_journal_words(segment) for segment in segments):
        return True
    if literal and any(_keyword_output_escapes(_tokens(line.text)) for line in command_lines(command)):
        return True
    try:
        return _scope_writes(_output_pipeline_groups(command), False)
    except OutputNestingError:
        return True


def _has_literal_producer(command: str, segments: list[list[str]]) -> bool:
    if heredoc_events(command) or HERESTRING in command:
        return True
    return any(_unplaced_producer(segment) for segment in segments)


def _unplaced_producer(tokens: list[str]) -> bool:
    literal = _literal_contents([tokens]) not in ([], [None]) or any(token.startswith(HEREDOC) for token in tokens)
    return literal and not _redirect_words(tokens)


def _scope_writes(groups: list[OutputPipeline], inherited: bool) -> bool:
    persistent = inherited
    for group in groups:
        if _group_writes(group, persistent):
            return True
        if not group.background and len(group.stages) == 1:
            persistent = persistent or _persistent(group.stages[0])
    return False


def _group_writes(group: OutputPipeline, persistent: bool) -> bool:
    carried = False
    for stage in group.stages:
        if _scope_writes(stage.groups, persistent):
            return True
        carried = carried or _holds_literal(stage.groups)
        if (persistent and _unplaced_producer(stage.tokens)) or (carried and _journal_words(stage.tokens)):
            return True
    return False


def _holds_literal(groups: list[OutputPipeline]) -> bool:
    return any(_unplaced_producer(stage.tokens) or _holds_literal(stage.groups) for group in groups for stage in group.stages)


def _persistent(stage: OutputStage) -> bool:
    index = _skip_prefixes(stage.tokens, WRAPPER_COMMANDS - {EXEC})
    if index < len(stage.tokens) and _basename(stage.tokens[index]) == EXEC:
        return _payload_command_index(stage.tokens) == len(stage.tokens) and bool(_journal_words(stage.tokens))
    if stage.subshell:
        return False
    return any(not group.background and len(group.stages) == 1 and _persistent(group.stages[0]) for group in stage.groups)


def _keyword_output_escapes(tokens: list[str]) -> bool:
    for position, token in enumerate(tokens):
        leads = position == 0 or tokens[position - 1] in SEPARATORS
        if token in KEYWORD_CLOSERS and leads and _pipeline_writes(tokens[position + 1:]):
            return True
    return False


def _pipeline_writes(rest: list[str]) -> bool:
    end = next((index for index, token in enumerate(rest) if token in SEPARATORS and token not in PIPE_OPERATORS), len(rest))
    return any(_journal_words(segment) for segment in _segments(" ".join(rest[:end])))
