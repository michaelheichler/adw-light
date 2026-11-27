from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

try:
    from . import german_punctuation
    from .comment_rules import (
        COMMENT_RE,
        DEFERRED_TAG_RE,
        DIRECTIVE_COMMENT_RE,
        HEADER_COMMENT_RE,
    )
    from .file_kinds import _is_config, _is_prose
    from .findings import finding_row, spanning
    from .markup import (
        FencePolicy,
        _blank_keep_newlines,
        _strip_inline_code,
        markdown_lines,
        mask_quoted,
    )
    from .prose_language import ENGLISH, GERMAN
    from .scan_lines import _SourceLine
except ImportError:
    import german_punctuation
    from comment_rules import (
        COMMENT_RE,
        DEFERRED_TAG_RE,
        DIRECTIVE_COMMENT_RE,
        HEADER_COMMENT_RE,
    )
    from file_kinds import _is_config, _is_prose
    from findings import finding_row, spanning
    from markup import (
        FencePolicy,
        _blank_keep_newlines,
        _strip_inline_code,
        markdown_lines,
        mask_quoted,
    )
    from prose_language import ENGLISH, GERMAN
    from scan_lines import _SourceLine


BAD_DASH_RE = re.compile("[" + "".join(chr(code_point) for code_point in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015)) + "]")
MINUS_AS_DASH_RE = re.compile(r"(?<=[^\W\d_])[ \t]*" + chr(0x2212) + r"[ \t]*(?=[^\W\d_])")
PROSE_DASH_RE = re.compile(r"(?:(?<=\s)|(?<=[^\W\d_]))" + BAD_DASH_RE.pattern[:-1] + chr(0x2212) + r"](?=\s|[^\W\d_])")
WORD_JOIN_HYPHEN_RE = re.compile(r"(?<=\w)" + chr(0x2011) + r"(?=\w)")


DASH_BREAK_RE = re.compile(r"\w-{2,} ?\w|\w -{2,} \w")
SPACED_HYPHEN_RE = re.compile(r"\w +- +\w")
PROSE_SEMICOLON_RE = re.compile(r";")
PROSE_COLON_RE = re.compile(r"(?<=\w)\s*:\s+(?=\w)")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
PRONOUN_APOS_RE = re.compile(r"\b(your|their|her|our|its)'s\b", re.IGNORECASE)
ITS_APOS_RE = re.compile(r"(?<![\w\"'])its" + chr(39) + r"(?!\w)", re.IGNORECASE)
DECADE_APOS_RE = re.compile(r"(?:\b\d{3}0|'\d0)'s\b")
HTML_CODE_RE = re.compile(r"<(code|pre)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
HTML_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


SHELL_IN_CONFIG_RE = re.compile(
    r"""-c\s+["']|;\s*(?:do|then|fi|done)\b|&&|\|\||\$\{|\$\(|\bimport\s+\w+\s*;|\btrap\s"""
    r"""|^\s*[\w.\[\]"']+\s*=(?!=)\s*\S|\s--\s"""
)


PUNCTUATION_RULES = (
    ("clean", (BAD_DASH_RE, MINUS_AS_DASH_RE), "banned_dash",
     "Banned dash character in ", "Use ASCII hyphen or rewrite the sentence."),
    ("prose", (DASH_BREAK_RE,), "dash_break",
     "Double hyphen clause break in ", "Use a comma, period, or parentheses."),
    ("prose", (SPACED_HYPHEN_RE,), "spaced_hyphen",
     "Spaced hyphen acts as a dash in ", "Use a comma, period, parentheses, or close up the hyphen."),
    ("semicolon", (PROSE_SEMICOLON_RE,), "prose_semicolon",
     "Semicolon appears in prose in ", "Use a comma, period, or parentheses."),
    ("clean", (PRONOUN_APOS_RE, ITS_APOS_RE), "pronoun_apostrophe",
     "Possessive pronoun has an apostrophe in ", "Use the possessive pronoun without apostrophe."),
    ("clean", (DECADE_APOS_RE,), "decade_apostrophe",
     "Decade written as a possessive in ", "Write the decade as a plural."),
)
GERMAN_PUNCTUATION_RULES = german_punctuation.german_rules(PUNCTUATION_RULES)


class _PunctuationLine(NamedTuple):
    whole: str
    prose_dash: str


@dataclass(slots=True)
class ColonCount:
    limit: int
    total: int = 0
    first: _SourceLine | None = None
    last: int = 0

    def count(self, source: _SourceLine, text: str) -> None:
        hits = sum(1 for _ in PROSE_COLON_RE.finditer(text))
        if hits:
            self.total += hits
            self.first = self.first or source
            self.last = source.number

    def findings(self) -> list[dict]:
        if self.first is None or self.total <= self.limit:
            return []
        noun = "colon" if self.total == 1 else "colons"
        row = finding_row("punctuation", "prose_colon", self.first.number,
                          f"{self.total} sentence {noun} in {self.first.path} (limit {self.limit}).",
                          self.first.text, "Replace excess colons with commas, periods, or parentheses.", match=":")
        return [spanning(dict(row, occurrences=self.total), self.last)]


def _config_quoted_view(text: str) -> str:
    masked = list(mask_quoted(text))
    for found in PROSE_DASH_RE.finditer(text):
        masked[found.start()] = found.group(0)
    return "".join(masked)


def _clean_view(line: str, language: str, config: bool = False) -> str:
    stripped = WORD_JOIN_HYPHEN_RE.sub("-", _strip_inline_code(line))
    clean = _config_quoted_view(stripped) if config else mask_quoted(stripped)
    return german_punctuation.german_view(clean) if language == GERMAN else clean


def _punctuation_views(path: str, scan_line: _PunctuationLine, language: str) -> _PunctuationLine:
    config = _is_config(path)
    whole = _clean_view(scan_line.whole, language, config)
    return _PunctuationLine(whole, _clean_view(scan_line.prose_dash, language) if config else whole)


def _punctuation_texts(path: str, views: _PunctuationLine, prose: bool) -> dict[str, str]:
    prose_part = _punctuation_prose_part(path, views.prose_dash, prose)
    semicolon = "" if _is_config(path) else URL_RE.sub("", prose_part)
    return {"clean": views.whole, "prose": prose_part, "semicolon": semicolon, "colon": semicolon}


def _scan_punctuation(
    source_line: _SourceLine, scan_line: _PunctuationLine, prose: bool, language: str = ENGLISH, colons: ColonCount | None = None,
) -> list[dict]:
    rules = GERMAN_PUNCTUATION_RULES if language == GERMAN else PUNCTUATION_RULES
    texts = _punctuation_texts(source_line.path, _punctuation_views(source_line.path, scan_line, language), prose)
    if colons is not None:
        colons.count(source_line, texts["colon"])
    rows = []
    for target, regexes, rule, detail, action in rules:
        found = _first_match(regexes, texts[target])
        if found is not None:
            rows.append(finding_row(
                "punctuation", rule, source_line.number,
                detail + source_line.path, source_line.text, action, match=found,
            ))
    return rows


def _first_match(regexes: tuple[re.Pattern[str], ...], text: str) -> str | None:
    if not text:
        return None
    for regex in regexes:
        found = regex.search(text)
        if found:
            return found.group(0)
    return None


def _strip_punctuation_blocks(path: str, text: str, prose: bool | None = None) -> str:
    text = HTML_CODE_RE.sub(_blank_keep_newlines, text)
    text = HTML_SCRIPT_STYLE_RE.sub(_blank_keep_newlines, text)
    if not (prose if prose is not None else _is_prose(path, text)):
        return text
    return "\n".join(
        "" if block.hidden or block.punctuation_separator else block.content
        for block in markdown_lines(text, FencePolicy.PUNCTUATION)
    )


def _punctuation_prose_part(path: str, line: str, prose: bool) -> str:
    if _is_config(path):
        return "" if SHELL_IN_CONFIG_RE.search(line) else line
    if prose:
        return line
    leading = COMMENT_RE.match(line)
    if not leading:
        return ""
    body = leading.group(1)
    if DIRECTIVE_COMMENT_RE.match(line.strip()) or HEADER_COMMENT_RE.search(body):
        return ""
    return DEFERRED_TAG_RE.sub("", body, count=1)
