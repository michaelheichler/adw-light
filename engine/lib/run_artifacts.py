from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from lib.path_policy import anchored_identity
from lib.shell_lines import shell_pass
from lib.shell_output import PYTHON_INTERPRETER_RE
from lib.shell_parse import DYNAMIC_RE, _bare, _basename, _payload_command_index, _redirect_paths, _segment_text, _segments
from lib.temp_scope import outside_project_temp

ARTIFACT_SUFFIXES = frozenset({".tap", ".log"})
TEST_RUNNERS = frozenset({"bats", "pytest", "py.test"})
WritePaths = Callable[[str, str | None], list[str]]


def _test_runner(segment: list[str]) -> bool:
    index = _payload_command_index(segment)
    words = [_bare(word) for word in segment[index:]]
    if not words:
        return False
    name = _basename(words[0])
    if name in TEST_RUNNERS:
        return True
    if name == "uvx":
        return _uvx_runner(words[1:])
    return bool(PYTHON_INTERPRETER_RE.fullmatch(name)) and len(words) > 2 and words[1:3] in (["-m", "pytest"], ["-m", "unittest"])


def _uvx_runner(words: list[str]) -> bool:
    index = 0
    while index < len(words) and words[index] in {"--python", "--with", "--from"}:
        index += 2
    return index < len(words) and words[index] in TEST_RUNNERS


def _artifact(path: str, cwd: str | None) -> bool:
    return Path(path).is_absolute() and Path(path).suffix.lower() in ARTIFACT_SUFFIXES and outside_project_temp(path, cwd)


def generated_test_artifacts(command: str, cwd: str | None, write_paths: WritePaths) -> frozenset[str]:
    if DYNAMIC_RE.search(command) or "<(" in command or ">(" in command:
        return frozenset()
    parsed = shell_pass(command)
    if len(parsed.lines) != 1 or parsed.lines[0].bodies:
        return frozenset()
    segments = _segments(parsed.lines[0].text)
    if not all(_test_runner(segment) or _directory_setup(segment, cwd, write_paths) for segment in segments):
        return frozenset()
    return frozenset(
        str(anchored_identity(path, Path(cwd or ".")))
        for index, segment in enumerate(segments) if _test_runner(segment)
        for path in _redirect_paths(segment) if _artifact(path, cwd)
        if not any(_mentions(path, other, cwd) for position, other in enumerate(segments) if position != index)
    )


def _directory_setup(segment: list[str], cwd: str | None, write_paths: WritePaths) -> bool:
    return len(segment) == 2 and _bare(segment[0]) == "cd" and not write_paths(_segment_text(segment), cwd)


def _mentions(path: str, segment: list[str], cwd: str | None) -> bool:
    identity = anchored_identity(path, Path(cwd or "."))
    return any(
        anchored_identity(_bare(word), Path(cwd or ".")) == identity
        for word in segment if Path(_bare(word)).is_absolute()
    )
