from __future__ import annotations

import ast
import fnmatch
import re
from collections.abc import Iterator
from pathlib import PurePath
from typing import NamedTuple, cast

try:
    from . import doc_conventions, scan_input
    from .comment_rules import (
        COMMENT_RE,
        DEFERRED_TAG_RE,
        DIRECTIVE_COMMENT_RE,
        HEADER_COMMENT_RE,
        READABILITY_RULES,
        scan_comment_line,
        scan_comment_contract,
        shape_passed_removed,
    )
    from . import families, german_punctuation, german_rules, test_rules
    from .german_document_rules import scan_german_document
    from .prose_language import ENGLISH, GERMAN, ParagraphLanguage, allowed_languages, paragraph_languages
    from .config import calibrated_findings, effective_config, family_enabled
    from .findings import finding_row, spanning
    from .markup import (
        CONFIG_EXTS,
        MIXED_LANGUAGE_EXTS,
        PROSE_EXTS,
        TEMPLATE_EXPRESSION_RE,
        CommentSource,
        FencePolicy,
        markdown_lines,
        mask_quoted,
        is_table_separator_row as _is_table_separator_row,
        RegionKind,
        _blank_keep_newlines,
        _mask_markup,
        _sniff_prose,
        _strip_english_hidden,
        _strip_inline_code,
        comment_scan_source,
        extract_regions,
        render_regions,
    )
    from .brace_functions import BRACE_LANGUAGE_EXTS, long_brace_functions
    from .code_units import long_rows, merged_rows, unit_scan
    from .comment_free import comment_free_lines
    from .language_views import comment_views, merged_findings
    from .machine_paths import path_findings
    from .parataxis import scan_parataxis
    from .prose_structure import scan_prose_structure
    from .slop_phrase import EnglishRule, PhraseFamily, _temporal_day, phrase_family_matches, phrase_text, scan_slop_phrases
    from .slop_structure import scan_slop_structure
    from .ste_rules import scan_ste
except ImportError:
    import doc_conventions, scan_input
    from comment_rules import (
        COMMENT_RE,
        DEFERRED_TAG_RE,
        DIRECTIVE_COMMENT_RE,
        HEADER_COMMENT_RE,
        READABILITY_RULES,
        scan_comment_line,
        scan_comment_contract,
        shape_passed_removed,
    )
    import families
    import german_punctuation
    import german_rules
    import test_rules
    from german_document_rules import scan_german_document
    from prose_language import ENGLISH, GERMAN, ParagraphLanguage, allowed_languages, paragraph_languages
    from config import calibrated_findings, effective_config, family_enabled
    from findings import finding_row, spanning
    from markup import (
        CONFIG_EXTS,
        MIXED_LANGUAGE_EXTS,
        PROSE_EXTS,
        TEMPLATE_EXPRESSION_RE,
        CommentSource,
        FencePolicy,
        markdown_lines,
        mask_quoted,
        is_table_separator_row as _is_table_separator_row,
        RegionKind,
        _blank_keep_newlines,
        _mask_markup,
        _sniff_prose,
        _strip_english_hidden,
        _strip_inline_code,
        comment_scan_source,
        extract_regions,
        render_regions,
    )
    from brace_functions import BRACE_LANGUAGE_EXTS, long_brace_functions
    from code_units import long_rows, merged_rows, unit_scan
    from comment_free import comment_free_lines
    from language_views import comment_views, merged_findings
    from machine_paths import path_findings
    from parataxis import scan_parataxis
    from prose_structure import scan_prose_structure
    from slop_phrase import EnglishRule, PhraseFamily, _temporal_day, phrase_family_matches, phrase_text, scan_slop_phrases
    from slop_structure import scan_slop_structure
    from ste_rules import scan_ste
read_scannable = scan_input.read_scannable
scannable_text = scan_input.scannable_text
_int_setting = scan_input.int_setting


BAD_DASH_RE = re.compile("[" + "".join(chr(code_point) for code_point in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015)) + "]")
MINUS_AS_DASH_RE = re.compile(r"(?<=[^\W\d_])[ \t]*" + chr(0x2212) + r"[ \t]*(?=[^\W\d_])")
PROSE_DASH_RE = re.compile(r"(?:(?<=\s)|(?<=[^\W\d_]))" + BAD_DASH_RE.pattern[:-1] + chr(0x2212) + r"](?=\s|[^\W\d_])")
WORD_JOIN_HYPHEN_RE = re.compile(r"(?<=\w)" + chr(0x2011) + r"(?=\w)")
CONFIG_BASENAMES = frozenset({
    ".pylintrc", ".editorconfig", ".npmrc", ".yarnrc", ".gitignore", ".gitattributes",
    ".dockerignore", ".flake8", ".coveragerc", ".prettierrc", ".eslintrc", ".babelrc",
})
DASH_BREAK_RE = re.compile(r"\w-{2,} ?\w|\w -{2,} \w")
SPACED_HYPHEN_RE = re.compile(r"\w +- +\w")
PROSE_SEMICOLON_RE = re.compile(r";")
PROSE_COLON_RE = re.compile(r"\w\s*:\s+\w")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
PRONOUN_APOS_RE = re.compile(r"\b(your|their|her|our|its)'s\b", re.IGNORECASE)
ITS_APOS_RE = re.compile(r"(?<![\w\"'])its" + chr(39) + r"(?!\w)", re.IGNORECASE)
DECADE_APOS_RE = re.compile(r"(?:\b\d{3}0|'\d0)'s\b")
HTML_CODE_RE = re.compile(r"<(code|pre)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
HTML_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
PROSE_FINDING_FAMILIES = frozenset({"punctuation", "english"})
ENGLISH_RULES = (
    EnglishRule(PhraseFamily.compile(r"\bsmoking gun\b"), "dead_metaphor", "Name the evidence and what it proves."),
    EnglishRule(PhraseFamily.compile(r"\bat the end of the day\b", literal=_temporal_day), "filler", "State the conclusion directly."),
    EnglishRule(PhraseFamily.compile(r"\b(it'?s worth noting|it'?s important to note|it should be noted)\b"), "throat_clearing", "Start with the point."),
    EnglishRule(PhraseFamily.compile(r"\bneedless to say\b"), "filler", "Delete it or say the point plainly."),
    EnglishRule(PhraseFamily.compile(r"\bin today'?s (fast-paced|digital|modern|ever-changing)\b"), "filler_opener", "Start with the actual point."),
    EnglishRule(PhraseFamily.compile(r"\bdelve into\b"), "ai_tell", "Use a plain verb."),
    EnglishRule(PhraseFamily.compile(r"\b(tapestry|rich tapestry)\b"), "dead_metaphor", "Name the real parts."),
    EnglishRule(PhraseFamily.compile(r"\b(a )?testament to\b"), "inflated_diction", "State what it shows."),
    EnglishRule(PhraseFamily.compile(r"\bnavigat(e|ing) the (complexities|landscape|challenges)\b"), "dead_metaphor", "Say what is hard."),
    EnglishRule(PhraseFamily.compile(r"\butili[sz](?:e|es|ed|ing|ation|ations)\b"), "utilize", "Use 'use'."),
    EnglishRule(PhraseFamily.compile(r"\bleverag(e|es|ed|ing)\b"), "inflated_diction", "Use 'use' or name the action."),
    EnglishRule(PhraseFamily.compile(r"\bin order to\b"), "wordiness", "Use 'to'."),
    EnglishRule(PhraseFamily.compile(r"\bdue to the fact that\b"), "wordiness", "Use 'because'."),
    EnglishRule(PhraseFamily.compile(r"\b(a )?(wide )?(variety|plethora|myriad) of\b"), "vague_quantity", "Give the number or name the items."),
    EnglishRule(PhraseFamily.compile(r"\bThere (is|are)\s+\w[\w\s,]{0,40}?\s+that\b"), "expletive_there", "Lead with the subject and an active verb."),
    EnglishRule(PhraseFamily.compile(r"\b(very|really|quite|extremely)\s+\w+"), "empty_intensifier", "Drop the modifier or choose a stronger word."),
    EnglishRule(PhraseFamily.compile(r"\bgame[- ]chang(er|ing)\b"), "dead_metaphor", "State the concrete effect."),
) + READABILITY_RULES
ASSERT_RE = re.compile(r"\bassert\b|\.assert|expect\(|\bexpect\.(?:poll|soft)\(|raises\(|warns\(|should\b|\.to\b|require\.|verify(?:_\w+)?\(", re.IGNORECASE)
TEST_START_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+test\w*\s*\([^)]*\):|^(\s*)(?:it|test|describe)\s*\(", re.IGNORECASE)
PASS_WORD_RE = re.compile(r"\bpass\b", re.IGNORECASE)
SHELL_IN_CONFIG_RE = re.compile(
    r"""-c\s+["']|;\s*(?:do|then|fi|done)\b|&&|\|\||\$\{|\$\(|\bimport\s+\w+\s*;|\btrap\s"""
    r"""|^\s*[\w.\[\]"']+\s*=(?!=)\s*\S|\s--\s"""
)
SUPPRESSION_MARKER = "craftsman" + "-ignore"
SUPPRESSION_MARKER_RE = re.compile(r"\b" + re.escape(SUPPRESSION_MARKER) + r"\b", re.IGNORECASE)




def _is_exempt(path: str, cfg: dict) -> bool:
    patterns = cfg.get("exempt_paths")
    if not isinstance(patterns, (list, tuple, set, frozenset)):
        return False
    return any(_path_matches(path, pat) for pat in patterns)


def _path_matches(path: str, pattern: object) -> bool:
    if not isinstance(pattern, str):
        return False
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, "*/" + pattern)


def _exempt_families(path: str, cfg: dict) -> frozenset[str]:
    mapping = cfg.get("exempt_families")
    if not isinstance(mapping, dict):
        return frozenset()
    dropped = {
        name
        for pattern, families in mapping.items()
        if _path_matches(path, pattern) and isinstance(families, (list, tuple, set, frozenset))
        for name in families
    }
    return families.leaves_of(dropped)


def _active_families(path: str, cfg: dict) -> frozenset[str]:
    dropped = _exempt_families(path, cfg)
    return frozenset(
        name for name in families.LEAVES if family_enabled(cfg, name) and name not in dropped
    )


def _python_tree(path: str, text: str) -> ast.Module | None:
    if not path.lower().endswith(".py"):
        return None
    try:
        return ast.parse(text)
    except SyntaxError:
        return None

def _unconditional_findings(context: _ScanContext, comment_text: str) -> list[dict]:
    findings = [
        finding_row("code", "suppression_escape_hatch", number,
                 "Craftsman suppression marker in " + context.path, line,
                 "Remove the marker and fix the reported issue.")
        for number, line in enumerate(context.lines, 1) if SUPPRESSION_MARKER_RE.search(line)
    ]
    if not context.code_file:
        return findings
    findings.extend(_file_length_findings(context.path, len(context.lines)))
    findings.extend(doc_conventions.comment_findings(context, comment_text, scan_comment_contract, scan_all))
    return findings

class _ScanContext(NamedTuple):
    path: str
    text: str
    config: dict
    tree: object
    lines: list[str]
    prose: bool
    code_file: bool
    active_families: frozenset[str]


def _scan_context(path: str, text: str, config: dict | None) -> _ScanContext:
    cfg = effective_config(config)
    tree, lines = _python_tree(path, text), text.splitlines() or [""]
    suffix = PurePath(path.lower()).suffix
    prose = _is_prose(path, text) or suffix in {".vue", ".svelte"}
    code_file = _code_file(path, text)
    return _ScanContext(
        path=path, text=text, config=cfg, tree=tree, lines=lines,
        prose=prose, code_file=code_file, active_families=_active_families(path, cfg),
    )


class _LineSources(NamedTuple):
    punctuation: list[str]
    prose_dash: list[str]
    english: list[str]
    comment: list[str]
    languages: list[str]
    paragraphs: list[ParagraphLanguage]


class _SourceLine(NamedTuple):
    path: str
    number: int
    text: str


def _template_free(path: str, text: str) -> str:
    return TEMPLATE_EXPRESSION_RE.sub(_blank_keep_newlines, text) if _is_config(path) else text


def _line_sources(context: _ScanContext, masked: str, comment_source: str) -> _LineSources:
    paragraphs = _prose_paragraphs(context, masked)
    punctuation = _strip_punctuation_blocks(context.path, masked, context.prose)
    return _LineSources(
        punctuation.splitlines() or [""],
        _template_free(context.path, punctuation).splitlines() or [""],
        _strip_english_hidden(masked).splitlines() or [""],
        comment_source.splitlines() or [""],
        _line_languages(context, paragraphs),
        paragraphs,
    )


def _prose_paragraphs(context: _ScanContext, masked: str) -> list[ParagraphLanguage]:
    if not context.prose:
        return []
    return paragraph_languages(masked, allowed_languages(context.config))


def _line_languages(context: _ScanContext, paragraphs: list[ParagraphLanguage]) -> list[str]:
    if not paragraphs:
        return []
    starts = {block.line: block.language for block in paragraphs}
    languages, current = [], ENGLISH
    for number in range(1, len(context.lines) + 1):
        current = starts.get(number, current)
        languages.append(current)
    return languages


def _with_languages(findings: list[dict], languages: list[str]) -> list[dict]:
    if not languages:
        return findings
    return [
        {**row, "language": _line_or_blank(languages, row["line"]) or ENGLISH}
        if row["family"] in PROSE_FINDING_FAMILIES else row
        for row in findings
    ]


def _mask_hidden_ranges(text: str, hidden_ranges: tuple[tuple[int, int], ...]) -> str:
    if not hidden_ranges:
        return text
    visible = list(text)
    for start, end in hidden_ranges:
        if not 0 <= start <= end <= len(text):
            raise ValueError("hidden range falls outside the source text")
        visible[start:end] = ["\n" if char == "\n" else " " for char in text[start:end]]
    return "".join(visible)


def _scan_line_families(source_line: _SourceLine, sources: _LineSources, context: _ScanContext) -> list[dict]:
    findings: list[dict] = []
    if "punctuation" in context.active_families:
        number = source_line.number
        scan_line = _PunctuationLine(_line_or_blank(sources.punctuation, number), _line_or_blank(sources.prose_dash, number))
        language = _line_or_blank(sources.languages, number) or ENGLISH
        findings.extend(_scan_punctuation(source_line, scan_line, context.prose, language))
    german = _line_or_blank(sources.languages, source_line.number) == GERMAN
    if "english" in context.active_families and context.prose and not german:
        scan_line = _line_or_blank(sources.english, source_line.number)
        findings.extend(_scan_english(source_line, scan_line))
    if context.code_file:
        findings.extend(_scan_code_line(source_line, sources.comment, context.active_families))
    return findings


def _scan_code_line(source_line: _SourceLine, comment_lines: list[str], active: frozenset[str]) -> list[dict]:
    scan_line = _line_or_blank(comment_lines, source_line.number)
    findings = scan_comment_line(source_line.path, source_line.number, scan_line, active)
    if "code" in active:
        findings.extend(_hollow_test_line_findings(source_line.path, source_line.number, scan_line))
    return findings


def _english_view(masked: str, languages: list[str]) -> str:
    if GERMAN not in languages:
        return masked
    lines = masked.split("\n")
    return "\n".join(
        "" if _line_or_blank(languages, number) == GERMAN else line for number, line in enumerate(lines, 1)
    )


def _scan_english_families(path: str, masked: str, context: _ScanContext) -> list[dict]:
    if "english" not in context.active_families or not context.prose:
        return []
    findings = scan_prose_structure(path, masked, context.config)
    findings.extend(scan_ste(path, masked, context.config))
    findings.extend(cast(list[dict], scan_slop_structure(path, masked)))
    findings.extend(cast(list[dict], scan_slop_phrases(path, masked, context.config)))
    return findings


def _scan_document_families(context: _ScanContext, masked: str, sources: _LineSources) -> list[dict]:
    findings = _scan_english_families(context.path, _english_view(masked, sources.languages), context)
    if "english" in context.active_families and context.prose:
        findings.extend(scan_parataxis(context.path, _strip_english_hidden(masked), sources.languages))
    german = [paragraph for paragraph in sources.paragraphs if paragraph.language == GERMAN]
    if "english" in context.active_families and german:
        findings.extend(scan_german_document(context.path, german, context.config))
        findings.extend(german_rules.check_paragraphs(context.path, german))
    if "punctuation" in context.active_families:
        findings.extend(german_punctuation.dash_cluster_findings(context.path, sources.punctuation, sources.languages))
    return findings


def scan_all(
    path: str,
    text: str,
    config: dict | None = None,
    *,
    hidden_ranges: tuple[tuple[int, int], ...] = (),
) -> list[dict]:
    if scan_input.is_binary_content(path, text):
        return []
    context = _scan_context(path, text, config)
    regions = extract_regions(path, text)
    source = CommentSource(path, text, regions, PurePath(path.lower()).suffix in MIXED_LANGUAGE_EXTS)
    views = comment_views(source) if context.code_file else (comment_scan_source(source),)
    findings = _view_findings(context, source, views[0], hidden_ranges)
    for view in views[1:]:
        findings = merged_findings(findings, _view_findings(context, source, view, hidden_ranges))
    if context.code_file and "code" in context.active_families and not _is_exempt(path, context.config):
        findings.extend(path_findings(path, text))
    return findings


def _view_findings(
    context: _ScanContext, source: CommentSource, comment_source: str, hidden_ranges: tuple[tuple[int, int], ...],
) -> list[dict]:
    findings = _unconditional_findings(context, comment_source)
    if _is_exempt(context.path, context.config):
        return findings
    if source.mixed:
        masked = render_regions(context.text, source.regions, {RegionKind.VISIBLE_PROSE})
    else:
        masked = _mask_markup(context.path, context.text)
    masked = _mask_hidden_ranges(masked, hidden_ranges)
    sources = _line_sources(context, masked, comment_source)
    if "code" in context.active_families and context.code_file:
        findings.extend(_scan_code_file(context, comment_source))
    for number, line in enumerate(context.lines, 1):
        source_line = _SourceLine(path=context.path, number=number, text=line)
        findings.extend(_scan_line_families(source_line, sources, context))
    findings.extend(_scan_document_families(context, masked, sources))
    if context.code_file:
        findings = shape_passed_removed(context.path, comment_source, context.text, findings)
    return calibrated_findings(_with_languages(findings, sources.languages))


def _line_or_blank(lines: list[str], number: int) -> str:
    return lines[number - 1] if number <= len(lines) else ""


def _is_prose(path: str, text: str | None = None) -> bool:
    if text and text.startswith("#!"):
        return False
    lowered = path.lower()
    if any(lowered.endswith(ext) for ext in PROSE_EXTS):
        return True
    pure = PurePath(path)
    if text is None or pure.name in CONFIG_BASENAMES or _is_config(path):
        return False
    return pure.suffix.lower() not in scan_input.TEXT_SUFFIXES - {"", ".out", ".log"} and _sniff_prose(text)


def _is_config(path: str) -> bool:
    lowered = path.lower()
    if any(lowered.endswith(ext) for ext in CONFIG_EXTS):
        return True
    return PurePath(lowered).name in CONFIG_BASENAMES


def _code_file(path: str, text: str) -> bool:
    if scan_input.is_binary_content(path, text):
        return False
    suffix = PurePath(path.lower()).suffix
    return (not _is_prose(path, text) and not _is_config(path)) or suffix in MIXED_LANGUAGE_EXTS


PUNCTUATION_RULES = (
    ("clean", (BAD_DASH_RE, MINUS_AS_DASH_RE), "banned_dash",
     "Banned dash character in ", "Use ASCII hyphen or rewrite the sentence."),
    ("prose", (DASH_BREAK_RE,), "dash_break",
     "Double hyphen clause break in ", "Use a comma, period, or parentheses."),
    ("prose", (SPACED_HYPHEN_RE,), "spaced_hyphen",
     "Spaced hyphen acts as a dash in ", "Use a comma, period, parentheses, or close up the hyphen."),
    ("semicolon", (PROSE_SEMICOLON_RE,), "prose_semicolon",
     "Semicolon appears in prose in ", "Use a comma, period, or parentheses."),
    ("colon", (PROSE_COLON_RE,), "prose_colon",
     "Colon appears inside a prose sentence in ", "Use a comma, period, or parentheses."),
    ("clean", (PRONOUN_APOS_RE, ITS_APOS_RE), "pronoun_apostrophe",
     "Possessive pronoun has an apostrophe in ", "Use the possessive pronoun without apostrophe."),
    ("clean", (DECADE_APOS_RE,), "decade_apostrophe",
     "Decade written as a possessive in ", "Write the decade as a plural."),
)
GERMAN_PUNCTUATION_RULES = german_punctuation.german_rules(PUNCTUATION_RULES)


class _PunctuationLine(NamedTuple):
    whole: str
    prose_dash: str


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


def _scan_punctuation(source_line: _SourceLine, scan_line: _PunctuationLine, prose: bool, language: str = ENGLISH) -> list[dict]:
    rules = GERMAN_PUNCTUATION_RULES if language == GERMAN else PUNCTUATION_RULES
    texts = _punctuation_texts(source_line.path, _punctuation_views(source_line.path, scan_line, language), prose)
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


def _scan_english(source_line: _SourceLine, scan_line: str) -> list[dict]:
    rows = []
    if source_line.text.lstrip().startswith(">"):
        return rows
    scan_line = phrase_text(_strip_inline_code(scan_line))
    for family, rule, action in ENGLISH_RULES:
        found = next(phrase_family_matches(family.patterns, scan_line, family.literal), None)
        if found:
            rows.append(finding_row(
                "english",
                rule,
                source_line.number,
                "Plain English rule in " + source_line.path,
                source_line.text,
                action,
                match=scan_line[found.start():found.end()],
            ))
    return rows


def _file_length_findings(path: str, count: int) -> list[dict]:
    policy = scan_input.file_length_policy(count)
    if policy is None:
        return []
    rule, action = policy
    return [finding_row("code", rule, 1, f"File has {count} lines in {path}", path, action)]


def file_length_findings(path: str, text: str) -> list[dict]:
    if scan_input.mostly_tool_output(text):
        return []
    return _file_length_findings(path, len(text.splitlines()) or 1) if _code_file(path, text) else []
def _long_functions(tree, func_limit: int) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        span = (getattr(node, "end_lineno", None) or node.lineno) - node.lineno + 1
        if span <= func_limit:
            continue
        yield node

def _long_function_rows(context: _ScanContext) -> list[tuple[int, str, int]]:
    func_limit = _int_setting(context.config, "function_block_lines", "ADW_FUNC_BLOCK_LINES", 80)
    if context.tree is not None:
        return [(node.lineno, node.name, node.end_lineno or node.lineno) for node in _long_functions(context.tree, func_limit)]
    lexer = []
    if PurePath(context.path.lower()).suffix in BRACE_LANGUAGE_EXTS:
        lexer = [(line, name, line + span - 1) for line, name, span in long_brace_functions(context.text, func_limit)]
    scan = unit_scan(context.path, context.text)
    if scan is None:
        return lexer
    rows = long_rows(scan, func_limit)
    return merged_rows(rows, lexer) if scan.partial else rows


def _function_length_findings(context: _ScanContext) -> list[dict]:
    return [
        spanning(finding_row(
            "code", "function_too_long", line,
            "Function is over the length cap in " + context.path,
            name, "Extract helpers until each function does one thing.",
        ), end)
        for line, name, end in _long_function_rows(context)
    ]


def _scan_code_file(context: _ScanContext, text: str) -> list[dict]:
    lines = text.splitlines()
    findings = _function_length_findings(context)
    findings.extend(_hollow_test_findings(context, lines))
    findings.extend(test_rules.check_file(context.path, context.text))
    return findings


def _hollow_test_findings(context: _ScanContext, lines: list[str]) -> list[dict]:
    lexer = _scan_hollow_test_blocks(context.path, lines, comment_free_lines(context.path, context.text))
    scan = None if context.tree is not None else unit_scan(context.path, context.text)
    if scan is None:
        return lexer
    rows = [
        spanning(finding_row(
            "code", "hollow_test", row.line, "Test body has no assertion in " + context.path,
            _line_or_blank(context.lines, row.line), "Add an assertion or delete the hollow test.",
        ), row.end)
        for row in scan.hollow
    ]
    return merged_findings(rows, lexer) if scan.partial else rows


def _hollow_test_line_findings(path: str, line_number: int, line: str) -> list[dict]:
    if not _looks_like_empty_test(line):
        return []
    return [finding_row(
        "code",
        "hollow_test",
        line_number,
        "Test body has no assertion in " + path,
        line,
        "Add an assertion or delete the hollow test.",
    )]


def _scan_hollow_test_blocks(path: str, lines: list[str], code_lines: list[str]) -> list[dict]:
    findings: list[dict] = []
    index = 0
    if len(code_lines) != len(lines):
        code_lines = lines
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        match = TEST_START_RE.match(line)
        if not match:
            index += 1
            continue
        if PASS_WORD_RE.search(stripped):
            index += 1
            continue
        block, next_index = _test_block(code_lines, index)
        if block and not any(ASSERT_RE.search(part) for part in block):
            findings.append(spanning(finding_row(
                "code",
                "hollow_test",
                index + 1,
                "Test body has no assertion in " + path,
                line,
                "Add an assertion or delete the hollow test.",
            ), max(next_index, index + 1)))
        index = max(next_index, index + 1)
    return findings


def _indented_test_body(lines: list[str], start: int, indent: int) -> tuple[list[str], int]:
    body: list[str] = []
    index = start + 1
    while index < len(lines):
        current = lines[index]
        if current.strip() and len(current) - len(current.lstrip()) <= indent:
            break
        body.append(current)
        index += 1
    return body, index


def _test_block(lines: list[str], start: int) -> tuple[list[str], int]:
    line = lines[start]
    if line.rstrip().endswith(":"):
        indent = len(line) - len(line.lstrip())
        return _indented_test_body(lines, start, indent)
    body = [line]
    depth = line.count("{") - line.count("}")
    if depth <= 0:
        return body, start + 1
    index = start + 1
    while index < len(lines):
        body.append(lines[index])
        depth += lines[index].count("{") - lines[index].count("}")
        if depth <= 0:
            return body, index + 1
        index += 1
    return body, index


def _looks_like_empty_test(line: str) -> bool:
    stripped = line.strip()
    if not re.match(r"(def|function|it|test)\b.*\btest", stripped, re.IGNORECASE):
        return False
    return bool(PASS_WORD_RE.search(stripped)) and not ASSERT_RE.search(stripped)


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
