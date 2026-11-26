from __future__ import annotations

import re
from functools import lru_cache

try:
    from .commit_inputs import PLAIN_COMMIT, UnreadableCommit, is_dynamic
    from .commit_segments import is_assignment as _is_assignment
    from .shell_syntax import _basename
    from .shell_syntax import _payload_command_index as _command_index
    from .shell_text import heredoc_value
except ImportError:
    from commit_inputs import PLAIN_COMMIT, UnreadableCommit, is_dynamic
    from commit_segments import is_assignment as _is_assignment
    from shell_syntax import _basename
    from shell_syntax import _payload_command_index as _command_index
    from shell_text import heredoc_value

FILE_NEUTRAL_VERBS = frozenset({
    "[", "[[", "test", "true", "false", ":", "echo", "printf", "pwd", "ls", "cat", "grep", "rg", "head", "tail", "wc",
    "type", "which", "export", "local", "readonly", "declare", "typeset", "set", "shopt", "unset", "exit", "return",
    "read", "sleep", "date", "uname", "id", "whoami", "fi", "done", "esac", "}", "then", "else",
})
DECLARERS = frozenset({"export", "declare", "typeset", "local", "readonly"})
COMMAND_RUNNERS = frozenset({
    "caffeinate", "doas", "find", "flock", "ionice", "parallel", "setsid", "stdbuf", "unbuffer", "watch", "xargs",
})
POSITIONAL_RE = re.compile(r"\$(?:[@*]|\d|\{(?:[@*]|\d+)[^}]*\})")
VARIABLE_RE = re.compile(r"\$(?:\{(\w+)\}|(\w+))")
HIDDEN_COMMIT = f"This command runs git commit through another program, so the commit gate cannot scan it. {PLAIN_COMMIT}"
DYNAMIC_PROGRAM = f"This command takes its program or git subcommand from a variable or substitution, so the commit gate cannot see whether it commits. {PLAIN_COMMIT}"


def _payload_command_index(words: list[str]) -> int:
    return _cached_index(tuple(words))


@lru_cache(maxsize=65536)
def _cached_index(words: tuple[str, ...]) -> int:
    return _command_index(list(words))


def _physical_mode(words: list[str], physical: bool) -> bool:
    if words[:1] != ["set"]:
        return physical
    for index, word in enumerate(words[1:], start=1):
        option = words[index + 1] if index + 1 < len(words) else ""
        if word in {"-o", "+o"} and option == "physical":
            physical = word == "-o"
        elif word[:1] in {"-", "+"} and not word.startswith(("--", "++")) and "P" in word:
            physical = word.startswith("-")
    return physical


def _file_neutral(words: list[str]) -> bool:
    index = _payload_command_index(words)
    return index >= len(words) or words[index] in FILE_NEUTRAL_VERBS


def _expanded(word: str, variables: dict[str, str]) -> str:
    heredoc = heredoc_value(word)
    if heredoc is not None:
        return heredoc
    return VARIABLE_RE.sub(lambda match: variables.get(match.group(1) or match.group(2), match.group()), word)


def _expanded_words(words: list[str], variables: dict[str, str]) -> list[str]:
    expanded = [word if word.startswith("GIT_INDEX_FILE=") else _expanded(word, variables) for word in words]
    index = _payload_command_index(expanded)
    if index < len(expanded) and expanded[index] != words[index] and " " in expanded[index]:
        return [*expanded[:index], *expanded[index].split(), *expanded[index + 1:]]
    return expanded


def _assignments(words: list[str]) -> list[tuple[str, str]]:
    if words[:1] and words[0] in DECLARERS:
        declared = words[1:]
    elif words and all(_is_assignment(word) for word in words):
        declared = words
    else:
        return []
    return [(name, value) for name, separator, value in (word.partition("=") for word in declared) if separator]


def _refuse_hidden_commit(words: list[str], commit_variable: bool) -> None:
    names = [_basename(word) for word in words[_payload_command_index(words):]]
    if not names:
        return
    if POSITIONAL_RE.search(words[_payload_command_index(words)]):
        raise UnreadableCommit(DYNAMIC_PROGRAM)
    if is_dynamic(names[0]) and ("commit" in names or commit_variable):
        raise UnreadableCommit(DYNAMIC_PROGRAM)
    programs = [index for index, name in enumerate(names) if name == "git" or is_dynamic(name)]
    if names[0] in COMMAND_RUNNERS and programs and "commit" in names[programs[0]:]:
        raise UnreadableCommit(HIDDEN_COMMIT)
