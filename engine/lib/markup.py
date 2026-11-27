import io
import json
import re
import tokenize
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import PurePath


try:
    from .markdown_prose import QUOTED_PROSE_RE, mask_phrase_mentions, mask_quoted, reply_lines, reply_location
except ImportError:
    from markdown_prose import QUOTED_PROSE_RE, mask_phrase_mentions, mask_quoted, reply_lines, reply_location
try:
    from .python_string_mask import (
        STRING_TOKEN_TYPES,
        TokenSpan,
        _blank_from,
        _blank_token_span,
        _python_string_spans,
        _token_error_offset,
        _token_span,
        mask_python_strings,
    )
    from .markdown_blocks import (
        BREAK_ONLY_RE,
        BREAK_THEN_HEADING_RE,
        FencePolicy,
        HEADING_RE,
        LABEL_LINE_RE,
        LINK_REFERENCE_RE,
        LIST_ITEM_RE,
        LOOSE_FENCE_RE,
        MARKDOWN_FENCE_RE,
        MARKDOWN_HEADING_RE,
        MarkdownLine,
        PIPE_RE,
        SEPARATOR_CELL_RE,
        SEPARATOR_ROW_RE,
        SETEXT_RE,
        SETEXT_UNDERLINE_RE,
        TABLE_DELIMITER_RE,
        TABLE_SEPARATOR_RE,
        THEMATIC_BREAK,
        _fence_step,
        _setext_title,
        heading_line_numbers,
        is_table_separator_row,
        markdown_heading,
        markdown_lines,
        mask_markdown_fences,
        one_row_table_separator,
    )
except ImportError:
    from python_string_mask import (
        STRING_TOKEN_TYPES,
        TokenSpan,
        _blank_from,
        _blank_token_span,
        _python_string_spans,
        _token_error_offset,
        _token_span,
        mask_python_strings,
    )
    from markdown_blocks import (
        BREAK_ONLY_RE,
        BREAK_THEN_HEADING_RE,
        FencePolicy,
        HEADING_RE,
        LABEL_LINE_RE,
        LINK_REFERENCE_RE,
        LIST_ITEM_RE,
        LOOSE_FENCE_RE,
        MARKDOWN_FENCE_RE,
        MARKDOWN_HEADING_RE,
        MarkdownLine,
        PIPE_RE,
        SEPARATOR_CELL_RE,
        SEPARATOR_ROW_RE,
        SETEXT_RE,
        SETEXT_UNDERLINE_RE,
        TABLE_DELIMITER_RE,
        TABLE_SEPARATOR_RE,
        THEMATIC_BREAK,
        _fence_step,
        _setext_title,
        heading_line_numbers,
        is_table_separator_row,
        markdown_heading,
        markdown_lines,
        mask_markdown_fences,
        one_row_table_separator,
    )


class RegionKind(Enum):
    VISIBLE_PROSE = "visible_prose"
    COMMENT = "comment"
    CODE = "code"
    STYLE = "style"
    SCRIPT = "script"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class Region:
    kind: RegionKind
    start: int
    end: int
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class TextSpan:
    text: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if not 0 <= self.start <= self.end <= len(self.text):
            raise ValueError("text span must fall within its text")


@dataclass(frozen=True, slots=True)
class CommentSource:
    path: str
    text: str
    regions: tuple[Region, ...]
    mixed: bool


MIXED_LANGUAGE_EXTS = frozenset({".html", ".htm", ".xml", ".svg", ".vue", ".svelte"})
BLOCK_TAG_RE = re.compile(
    r"(?P<comment><!--.*?(?:-->|\Z))|"
    r"<(?P<tag>script|style|code|pre)\b[^>]*>.*?(?:</(?P=tag)\s*>|\Z)",
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]*>", re.DOTALL)
TEMPLATE_EXPRESSION_RE = re.compile(r"{{.*?}}|{%.*?%}|{#.*?#}", re.DOTALL)
SCRIPT_STRING_RE = re.compile(r'''(?P<quote>["'`])(?:\\.|(?!\1)[^\\])*\1''', re.DOTALL)


LOG_EXTS = frozenset({".log"})
PLAIN_TEXT_EXTS = frozenset({".txt", ".text", ".out", ""})
_BLANK_ALL_RE = re.compile(r"[^\n]")
_MATCH_LINE = r"^[^\s:]+[:-]\d+[:-].*$"
_LOG_LINE = r"^[ \t]*\[?(?:\d{4}-\d{2}-\d{2}[T ])?\d{2}:\d{2}:\d{2}.*$"
TOOL_LINE_RE = re.compile(f"{_MATCH_LINE}|{_LOG_LINE}", re.MULTILINE)


def _blank_keep_newlines(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _region(kind: RegionKind, span: TextSpan) -> Region:
    end_offset = span.start if span.end <= span.start else span.end - 1
    return Region(
        kind,
        span.start,
        span.end,
        _line_at(span.text, span.start),
        _line_at(span.text, end_offset),
    )


def _append_markup_segment(regions: list[Region], span: TextSpan) -> None:
    cursor = span.start
    for match in TAG_RE.finditer(span.text, span.start, span.end):
        if cursor < match.start():
            _append_template_parts(regions, TextSpan(span.text, cursor, match.start()))
        regions.append(_region(RegionKind.CODE, TextSpan(span.text, match.start(), match.end())))
        cursor = match.end()
    if cursor < span.end:
        _append_template_parts(regions, TextSpan(span.text, cursor, span.end))


def _append_template_parts(regions: list[Region], span: TextSpan) -> None:
    cursor = span.start
    for match in TEMPLATE_EXPRESSION_RE.finditer(span.text, span.start, span.end):
        if cursor < match.start():
            regions.append(_region(RegionKind.VISIBLE_PROSE, TextSpan(span.text, cursor, match.start())))
        regions.append(_region(RegionKind.CODE, TextSpan(span.text, match.start(), match.end())))
        cursor = match.end()
    if cursor < span.end:
        regions.append(_region(RegionKind.VISIBLE_PROSE, TextSpan(span.text, cursor, span.end)))


def _block_kind(match: re.Match) -> RegionKind:
    if match.group("comment") is not None:
        return RegionKind.COMMENT
    tag = match.group("tag").lower()
    if tag == "script":
        return RegionKind.SCRIPT
    if tag == "style":
        return RegionKind.STYLE
    return RegionKind.IGNORED


def extract_regions(path: str, text: str) -> tuple[Region, ...]:
    suffix = PurePath(path.lower()).suffix
    if suffix not in MIXED_LANGUAGE_EXTS:
        return (_region(RegionKind.CODE, TextSpan(text, 0, len(text))),)
    if suffix in {".vue", ".svelte"} and "<" not in text:
        return (_region(RegionKind.SCRIPT, TextSpan(text, 0, len(text))),)
    regions: list[Region] = []
    cursor = 0
    for match in BLOCK_TAG_RE.finditer(text):
        if cursor < match.start():
            _append_markup_segment(regions, TextSpan(text, cursor, match.start()))
        regions.append(_region(_block_kind(match), TextSpan(text, match.start(), match.end())))
        cursor = match.end()
    if cursor < len(text):
        _append_markup_segment(regions, TextSpan(text, cursor, len(text)))
    return tuple(regions)


def render_regions(text: str, regions: tuple[Region, ...], accepted: set[RegionKind]) -> str:
    visible = list(text)
    for region in regions:
        if region.kind in accepted:
            continue
        visible[region.start:region.end] = [
            "\n" if char == "\n" else " " for char in text[region.start:region.end]
        ]
    return "".join(visible)


def mask_script_strings(text: str, regions: tuple[Region, ...]) -> str:
    visible = list(text)
    for region in regions:
        if region.kind is not RegionKind.SCRIPT:
            continue
        segment = text[region.start:region.end]
        masked = SCRIPT_STRING_RE.sub(_blank_keep_newlines, segment)
        visible[region.start:region.end] = masked
    return "".join(visible)


def mask_source_strings(text: str) -> str:
    return SCRIPT_STRING_RE.sub(_blank_keep_newlines, text)


def is_json_lines(text: str) -> bool:
    rows = (line for line in text.split("\n") if line.strip(" \r\t"))
    try:
        for row in rows:
            json.loads(row)
    except (ValueError, RecursionError):
        return False
    return True


def comment_scan_source(source: CommentSource) -> str:
    if source.mixed:
        visible = render_regions(
            source.text,
            source.regions,
            {RegionKind.COMMENT, RegionKind.SCRIPT},
        )
        return mask_script_strings(visible, source.regions)
    suffix = PurePath(source.path.lower()).suffix
    if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
        return mask_source_strings(source.text)
    if suffix == ".py":
        return mask_python_strings(source.text)
    if suffix == ".jsonl" and is_json_lines(source.text):
        return re.sub(r"[^\r\n]", " ", source.text)
    return source.text


MARKDOWN_EXTS = frozenset({".md", ".markdown", ".mdx", ".mdc"})
PROSE_EXTS = MARKDOWN_EXTS | {".rst", ".txt", ".text", ".html", ".htm", ".xml", ".svg", ".tex", ".adoc", ".asciidoc", ".org", ".typ"}
CONFIG_EXTS = frozenset({".json", ".jsonc", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".env", ".properties"})
DATA_TEXT_EXTS = frozenset({".json", ".jsonl", ".csv", ".tsv", ".lock", ".map", ".svg", ".xml", ".sql"})
COMMENTABLE_EXTS = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".cs", ".go", ".h", ".hpp", ".java", ".js",
    ".jsx", ".kt", ".kts", ".mjs", ".php", ".rb", ".rs", ".sh", ".swift", ".ts",
    ".tsx", ".vue", ".svelte", ".scala", ".ex", ".exs", ".dart", ".hh",
})
SCRIPT_EXTS = frozenset({".py", ".pyi", ".bash", ".bats", ".zsh", ".ksh", ".fish", ".pl", ".pm", ".lua", ".ps1", ".css", ".scss"})
FRONTMATTER_RE = re.compile(r"\A-{3}[ \t]*\n.*?^(?:-{3}|\.{3})[ \t]*$", re.DOTALL | re.MULTILINE)


def _mask_frontmatter(suffix: str, text: str) -> str:
    if suffix not in MARKDOWN_EXTS:
        return text
    return FRONTMATTER_RE.sub(_blank_keep_newlines, text, count=1)


def _mask_tool_output(suffix: str, text: str) -> str:
    if suffix in PLAIN_TEXT_EXTS and _json_document(text):
        return _BLANK_ALL_RE.sub(" ", text)
    return text


def _json_document(text: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return False
    return True


def _mask_markup(path: str, text: str) -> str:
    suffix = PurePath(path.lower()).suffix
    text = _mask_tool_output(suffix, text)
    text = _mask_frontmatter(suffix, text)
    if suffix in MARKDOWN_EXTS | PLAIN_TEXT_EXTS:
        text = mask_markdown_fences(text)
    if path == "assistant-reply.md":
        quotes = re.compile(r"(?m)^[ \t]*>[^\r\n]*")
        quote_count = sum(1 for _ in quotes.finditer(text))
        own_count = sum(bool(line.strip()) and not line.lstrip().startswith(">") for line in text.splitlines())
        if own_count >= quote_count:
            text = quotes.sub(_blank_keep_newlines, text)
    if suffix == ".tex":
        text = re.sub(
            r"\\begin\{(verbatim|lstlisting|equation\*?|align\*)\}.*?\\end\{\1\}",
            _blank_keep_newlines,
            text,
            flags=re.DOTALL,
        )
        text = re.sub(r"\$\$.*?\$\$|\$.*?\$|\\\[.*?\\\]", _blank_keep_newlines, text, flags=re.DOTALL)
        text = re.sub(r"(?<!\\)%.*", _blank_keep_newlines, text)
        return re.sub(r"\\[A-Za-z@]+\*?(?:\[[^]]*\])?", _blank_keep_newlines, text)
    if suffix in {".adoc", ".asciidoc"}:
        text = re.sub(r"^(-{4,}|\.{4,})\s*$.*?^\1\s*$", _blank_keep_newlines, text, flags=re.MULTILINE | re.DOTALL)
        return re.sub(r"^//.*$|^:[^:]+:.*$", _blank_keep_newlines, text, flags=re.MULTILINE)
    if suffix == ".org":
        text = re.sub(r"^#\+begin_[^\n]*$.*?^#\+end_[^\n]*$", _blank_keep_newlines, text, flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)
        return re.sub(r"^\s*#.*$", _blank_keep_newlines, text, flags=re.MULTILINE)
    if suffix == ".typ":
        text = re.sub(r"`{3,}.*?`{3,}", _blank_keep_newlines, text, flags=re.DOTALL)
        return re.sub(r"^\s*#.*$", _blank_keep_newlines, text, flags=re.MULTILINE)
    return text


def _sniff_prose(text: str) -> bool:
    head = text[:1024]
    if head.startswith("#!"):
        return False
    letters = sum(char.isalpha() for char in head)
    spaces = sum(char.isspace() for char in head)
    return bool(re.search(r"[.!?](?:\s|$)", head) and letters + spaces and (letters + spaces) / len(head) > 0.7)


HTML_HIDDEN_RE = re.compile(r"<!--.*?-->|<(script|style|code|pre)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
HTML_ENTITY_RE = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]*|#(?:\d+|[xX][0-9A-Fa-f]+));")
INLINE_CODE_RE = re.compile(r"`[^`]*`")


def _strip_english_hidden(text: str) -> str:
    text = HTML_HIDDEN_RE.sub(_blank_keep_newlines, text)
    return TAG_RE.sub(_blank_keep_newlines, text)


def _strip_inline_code(text: str) -> str:
    text = HTML_ENTITY_RE.sub("  ", text)
    return INLINE_CODE_RE.sub("  ", text)
