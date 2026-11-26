from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache


class FencePolicy(StrEnum):
    MARKDOWN = "markdown"
    PROMPT = "prompt"
    LANGUAGE = "language"
    PUNCTUATION = "punctuation"


MARKDOWN_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)")
LOOSE_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
THEMATIC_BREAK = r"(?:-{3,}|\*{3,}|_{3,})"
BREAK_THEN_HEADING_RE = re.compile(r"^" + THEMATIC_BREAK + r"\s+#{1,6}\s")
BREAK_ONLY_RE = re.compile(r"^" + THEMATIC_BREAK + r"$")
TABLE_SEPARATOR_RE = re.compile(r"^\|?[\s:|-]*-[\s:|-]*\|?$")
TABLE_DELIMITER_RE = re.compile(r"^\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$")
SEPARATOR_ROW_RE = re.compile(r"\|(?:\s*:?-{3,}:?\s*\|)+")
SEPARATOR_CELL_RE = re.compile(r":?-{3,}:?")
PIPE_RE = re.compile(r"(?<!\\)\|")
LINK_REFERENCE_RE = re.compile(r"^\s*\[[^]]+\]:\s+\S")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
SETEXT_RE = re.compile(r"^\s{0,3}(?:={2,}|-{2,})\s*$")
SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-+*]|\d{1,9}[.)])(?:\s|$)")
LABEL_LINE_RE = re.compile(r"^\s*[-+*]\s+(?:\[[ xX]\]\s*)?(?:\*{2}[^*]{1,40}\*{2}|`[^`]{1,40}`)\s*:")


@dataclass(frozen=True, slots=True)
class MarkdownLine:
    number: int
    text: str
    hidden: bool

    @property
    def content(self) -> str:
        return self.text.rstrip("\r\n")

    @property
    def prose_boundary(self) -> bool:
        line = self.content
        if line.lstrip().startswith((">", "|")):
            return True
        return any(pattern.match(line) for pattern in (
            TABLE_DELIMITER_RE, LINK_REFERENCE_RE, HEADING_RE, SETEXT_RE, LABEL_LINE_RE,
        ))

    @property
    def punctuation_separator(self) -> bool:
        return is_table_separator_row(self.content)


def _fence_step(line: str, fence: str, policy: FencePolicy) -> tuple[str, bool]:
    if policy in (FencePolicy.LANGUAGE, FencePolicy.PUNCTUATION):
        marker = LOOSE_FENCE_RE.match(line)
        if marker is None:
            return fence, bool(fence)
        kind = marker[1][0]
        closing = bool(fence) and (policy is FencePolicy.LANGUAGE or kind == fence)
        return ("" if closing else fence or kind), True
    marker = MARKDOWN_FENCE_RE.match(line)
    if policy is FencePolicy.PROMPT and marker and marker[1][0] != "`":
        marker = None
    if marker is None:
        return fence, bool(fence)
    tail = line[len(line) - len(line.lstrip(" ")) + len(marker[1]):]
    closing_tail = tail.strip(" \t\r\n") if policy is FencePolicy.PROMPT else marker[2].strip()
    closing = bool(fence and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not closing_tail)
    opening = not fence and (marker[1][0] != "`" or "`" not in tail)
    return (marker[1] if opening else "" if closing else fence), bool(fence) or opening


@lru_cache(maxsize=2)
def markdown_lines(text: str, policy: FencePolicy = FencePolicy.MARKDOWN) -> tuple[MarkdownLine, ...]:
    fence = ""
    result = []
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        fence, hidden = _fence_step(line, fence, policy)
        result.append(MarkdownLine(number, line, hidden))
    return tuple(result)


@lru_cache(maxsize=2)
def heading_line_numbers(text: str) -> frozenset[int]:
    lines = markdown_lines(text)
    numbers = set()
    for index, line in enumerate(lines):
        if line.hidden:
            continue
        underline = lines[index + 1] if index + 1 < len(lines) else None
        if HEADING_RE.match(line.content) or (underline is not None and _setext_title(line, underline)):
            numbers.add(line.number)
    return frozenset(numbers)


def _setext_title(line: MarkdownLine, underline: MarkdownLine) -> bool:
    content = line.content
    if underline.hidden or not content.strip() or not SETEXT_UNDERLINE_RE.match(underline.content):
        return False
    return not (line.prose_boundary or LIST_ITEM_RE.match(content) or content.startswith(("    ", "\t")))


def is_table_separator_row(line: str) -> bool:
    return bool(TABLE_SEPARATOR_RE.fullmatch(line.strip()))


def markdown_heading(text: str) -> re.Match[str] | None:
    return MARKDOWN_HEADING_RE.match(text)


def one_row_table_separator(text: str) -> re.Match[str] | None:
    separator = SEPARATOR_ROW_RE.search(text)
    if not text.startswith("|") or separator is None:
        return None
    width = len(SEPARATOR_CELL_RE.findall(separator[0])) + 1
    return separator if len(PIPE_RE.findall(text)) == width * 3 else None


def mask_markdown_fences(text: str, policy: FencePolicy = FencePolicy.MARKDOWN) -> str:
    blank = r"[^\n]" if policy is FencePolicy.PROMPT else r"[^\r\n]"
    return "".join(re.sub(blank, " ", line.text) if line.hidden else line.text for line in markdown_lines(text, policy))
