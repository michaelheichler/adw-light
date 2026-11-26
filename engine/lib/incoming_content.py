from __future__ import annotations

from pathlib import Path

try:
    from . import tests_policy
    from .shell_expansion import Expansions
    from .shell_parse import (
        _basename,
        _command_word_index,
        _printf_text,
        _redirect_words,
        command_expansions,
        line_segments,
    )
    from .shell_syntax import _without_redirects
    from .shell_test_targets import Scope
    from .temp_scope import Directories, segment_directories
    from .test_units import language_of
except ImportError:
    import tests_policy
    from shell_expansion import Expansions
    from shell_parse import (
        _basename,
        _command_word_index,
        _printf_text,
        _redirect_words,
        command_expansions,
        line_segments,
    )
    from shell_syntax import _without_redirects
    from shell_test_targets import Scope
    from temp_scope import Directories, segment_directories
    from test_units import language_of

Route = tuple[str, bool]


def incoming_test_content(command: str, scope: Scope) -> list[str]:
    expansions = command_expansions(command, scope.cwd)
    segments = line_segments(command)
    directories = segment_directories(segments, scope.directories, expansions.path)
    refused: list[str] = []
    for segment, folders in zip(segments, directories):
        words = _without_redirects(segment)
        index = _command_word_index(words)
        if index >= len(words):
            continue
        verb, args = _basename(words[index]), words[index + 1:]
        for raw, append in _incoming_routes(verb, args, segment):
            destination = expansions.path(raw)
            if destination and language_of(destination):
                refused.extend(_inspect_source(verb, args, (destination, append), folders, scope.cwd, expansions))
    return refused


def _incoming_routes(verb: str, args: list[str], segment: list[str]) -> list[Route]:
    if verb == "cp" and len(args) >= 2:
        return [(args[-1], False)]
    if verb == "cat" and args or verb == "printf" and any("$" in word for word in args):
        return _redirect_words(segment)
    return []


def _inspect_source(
    verb: str, args: list[str], route: Route, folders: Directories, cwd: Path, expansions: Expansions,
) -> list[str]:
    destination, append = route
    if folders is None:
        return [destination]
    for folder in folders:
        path = cwd / folder / destination
        if tests_policy.outside_project(str(path), str(cwd)) or tests_policy.temp_scratch_repo(str(path), str(cwd)):
            continue
        text = _incoming_text(verb, args, cwd / folder, expansions)
        before = tests_policy.read_before(path)
        if text is None or append and before is None and path.exists():
            return [str(path)]
        after = (before or "") + text if append else text
        if tests_policy.inline_test_bodies(str(path), after) != tests_policy.inline_test_bodies(str(path), before):
            return [str(path)]
    return []


def _incoming_text(verb: str, args: list[str], folder: Path, expansions: Expansions) -> str | None:
    if verb == "printf":
        values = [expansions.assigned(word) for word in args]
        known = [value for value in values if value is not None]
        return _printf_text(known) if len(known) == len(values) else None
    operands = args[:-1] if verb == "cp" else args
    operands = operands[1:] if operands[:1] == ["--"] else operands
    if len(operands) != 1:
        return None
    name = expansions.path(operands[0])
    return tests_policy.read_before(folder / name) if name and not name.startswith("-") else None
