from __future__ import annotations

import ast
import fnmatch
import re
from collections.abc import Iterator
from pathlib import PurePath
from typing import NamedTuple, cast

try:
    from . import doc_conventions, program_output, scan_input
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
    from . import families, german_punctuation, german_rules, shell_units, test_rules
    from .german_document_rules import scan_german_document
    from .prose_language import ENGLISH, GERMAN, ParagraphLanguage, allowed_languages, paragraph_languages
    from .config import DEFAULTS, calibrated_findings, contract_strings, effective_config, family_enabled
    from .findings import VerdictKind, finding_row
    from .markup import (
        CONFIG_EXTS,
        MARKDOWN_EXTS,
        MIXED_LANGUAGE_EXTS,
        PROSE_EXTS,
        TEMPLATE_EXPRESSION_RE,
        CommentSource,
        FencePolicy,
        heading_line_numbers,
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
    import doc_conventions, program_output, scan_input
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
    import shell_units
    import test_rules
    from german_document_rules import scan_german_document
    from prose_language import ENGLISH, GERMAN, ParagraphLanguage, allowed_languages, paragraph_languages
    from config import DEFAULTS, calibrated_findings, contract_strings, effective_config, family_enabled
    from findings import VerdictKind, finding_row
    from markup import (
        CONFIG_EXTS,
        MARKDOWN_EXTS,
        MIXED_LANGUAGE_EXTS,
        PROSE_EXTS,
        TEMPLATE_EXPRESSION_RE,
        CommentSource,
        FencePolicy,
        heading_line_numbers,
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
try:
    from .file_kinds import (
        CONFIG_BASENAMES,
        ProseBasis,
        _code_file,
        _is_config,
        _is_prose,
        _prose_basis,
    )
    from .scan_lines import _SourceLine, _line_or_blank
    from .punctuation_scan import (
        ColonCount,
        BAD_DASH_RE,
        DASH_BREAK_RE,
        DECADE_APOS_RE,
        GERMAN_PUNCTUATION_RULES,
        HTML_CODE_RE,
        HTML_SCRIPT_STYLE_RE,
        ITS_APOS_RE,
        MINUS_AS_DASH_RE,
        PRONOUN_APOS_RE,
        PROSE_COLON_RE,
        PROSE_DASH_RE,
        PROSE_SEMICOLON_RE,
        PUNCTUATION_RULES,
        SHELL_IN_CONFIG_RE,
        SPACED_HYPHEN_RE,
        URL_RE,
        WORD_JOIN_HYPHEN_RE,
        _PunctuationLine,
        _clean_view,
        _config_quoted_view,
        _first_match,
        _punctuation_prose_part,
        _punctuation_texts,
        _punctuation_views,
        _scan_punctuation,
        _strip_punctuation_blocks,
    )
    from .english_line_scan import ENGLISH_RULES, _scan_english
    from .code_length_scan import (
        _file_length_findings,
        _function_length_findings,
        _long_function_rows,
        _long_functions,
        file_length_findings,
    )
    from .hollow_tests import (
        ASSERT_RE,
        PASS_WORD_RE,
        TEST_START_RE,
        _hollow_test_findings,
        _hollow_test_line_findings,
        _indented_test_body,
        _looks_like_empty_test,
        _scan_hollow_test_blocks,
        _test_block,
    )
except ImportError:
    from file_kinds import (
        CONFIG_BASENAMES,
        ProseBasis,
        _code_file,
        _is_config,
        _is_prose,
        _prose_basis,
    )
    from scan_lines import _SourceLine, _line_or_blank
    from punctuation_scan import (
        ColonCount,
        BAD_DASH_RE,
        DASH_BREAK_RE,
        DECADE_APOS_RE,
        GERMAN_PUNCTUATION_RULES,
        HTML_CODE_RE,
        HTML_SCRIPT_STYLE_RE,
        ITS_APOS_RE,
        MINUS_AS_DASH_RE,
        PRONOUN_APOS_RE,
        PROSE_COLON_RE,
        PROSE_DASH_RE,
        PROSE_SEMICOLON_RE,
        PUNCTUATION_RULES,
        SHELL_IN_CONFIG_RE,
        SPACED_HYPHEN_RE,
        URL_RE,
        WORD_JOIN_HYPHEN_RE,
        _PunctuationLine,
        _clean_view,
        _config_quoted_view,
        _first_match,
        _punctuation_prose_part,
        _punctuation_texts,
        _punctuation_views,
        _scan_punctuation,
        _strip_punctuation_blocks,
    )
    from english_line_scan import ENGLISH_RULES, _scan_english
    from code_length_scan import (
        _file_length_findings,
        _function_length_findings,
        _long_function_rows,
        _long_functions,
        file_length_findings,
    )
    from hollow_tests import (
        ASSERT_RE,
        PASS_WORD_RE,
        TEST_START_RE,
        _hollow_test_findings,
        _hollow_test_line_findings,
        _indented_test_body,
        _looks_like_empty_test,
        _scan_hollow_test_blocks,
        _test_block,
    )
read_scannable = scan_input.read_scannable
scannable_text = scan_input.scannable_text
_int_setting = scan_input.int_setting


PROSE_FINDING_FAMILIES = frozenset({"punctuation", "english"})
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
    except (SyntaxError, MemoryError, RecursionError):
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
    guessed_prose: bool = False


def _scan_context(path: str, text: str, config: dict | None) -> _ScanContext:
    cfg = effective_config(config)
    tree, lines = _python_tree(path, text), text.splitlines() or [""]
    suffix = PurePath(path.lower()).suffix
    basis = _prose_basis(path, text)
    prose = basis is not None or suffix in {".vue", ".svelte"}
    code_file = _code_file(path, text)
    return _ScanContext(
        path=path, text=text, config=cfg, tree=tree, lines=lines,
        prose=prose, code_file=code_file, active_families=_active_families(path, cfg),
        guessed_prose=basis is ProseBasis.CONTENT,
    )


class _LineSources(NamedTuple):
    punctuation: list[str]
    prose_dash: list[str]
    english: list[str]
    comment: list[str]
    languages: list[str]
    paragraphs: list[ParagraphLanguage]


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


def _scan_line_families(
    source_line: _SourceLine, sources: _LineSources, context: _ScanContext, colons: ColonCount | None,
) -> list[dict]:
    findings: list[dict] = []
    if "punctuation" in context.active_families:
        number = source_line.number
        scan_line = _PunctuationLine(_line_or_blank(sources.punctuation, number), _line_or_blank(sources.prose_dash, number))
        language = _line_or_blank(sources.languages, number) or ENGLISH
        if colons is not None and _markdown_heading(context, number):
            colons = None
        findings.extend(_scan_punctuation(source_line, scan_line, context.prose, language, colons))
    german = _line_or_blank(sources.languages, source_line.number) == GERMAN
    if "english" in context.active_families and context.prose and not german:
        scan_line = _line_or_blank(sources.english, source_line.number)
        findings.extend(_scan_english(source_line, scan_line))
    if context.code_file:
        findings.extend(_scan_code_line(source_line, sources.comment, context))
    return findings


def _markdown_heading(context: _ScanContext, number: int) -> bool:
    if PurePath(context.path.lower()).suffix not in MARKDOWN_EXTS:
        return False
    return number in heading_line_numbers(context.text)


def _scan_code_line(source_line: _SourceLine, comment_lines: list[str], context: _ScanContext) -> list[dict]:
    scan_line = _line_or_blank(comment_lines, source_line.number)
    findings = scan_comment_line(source_line.path, source_line.number, scan_line, context.active_families)
    if context.tree is None and "code" in context.active_families:
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
    document: bool = True,
    hidden_ranges: tuple[tuple[int, int], ...] = (),
) -> list[dict]:
    if scan_input.is_binary_content(path, text):
        return []
    if program_output.exempt_tap(path, text):
        return program_output.tap_findings(path, text, lambda unit, body: scan_all(unit, body, config))
    context = _scan_context(path, text, config)
    regions = extract_regions(path, text)
    source = CommentSource(path, text, regions, PurePath(path.lower()).suffix in MIXED_LANGUAGE_EXTS)
    views = comment_views(source) if context.code_file else (comment_scan_source(source),)
    findings = _view_findings(context, source, views[0], hidden_ranges, document)
    for view in views[1:]:
        findings = merged_findings(findings, _view_findings(context, source, view, hidden_ranges, document))
    if context.code_file and "code" in context.active_families and not _is_exempt(path, context.config):
        findings.extend([] if program_output.exempt_output(path, text) else path_findings(path, text))
    return _guessed_prose_observed(context, findings)


def _guessed_prose_observed(context: _ScanContext, findings: list[dict]) -> list[dict]:
    if not context.guessed_prose:
        return findings
    observe_severity = VerdictKind.OBSERVE.value
    return [
        {**row, "severity": observe_severity} if row["family"] in PROSE_FINDING_FAMILIES else row
        for row in findings
    ]


def _colon_count(context: _ScanContext, document: bool) -> ColonCount | None:
    if document and PurePath(context.path.lower()).suffix in MARKDOWN_EXTS:
        return None
    limit = context.config["prose_colon_limit"]
    return ColonCount(limit if type(limit) is int and limit >= 0 else DEFAULTS["prose_colon_limit"])


def _view_findings(
    context: _ScanContext, source: CommentSource, comment_source: str, hidden_ranges: tuple[tuple[int, int], ...],
    document: bool,
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
    colons = _colon_count(context, document)
    for number, line in enumerate(context.lines, 1):
        source_line = _SourceLine(path=context.path, number=number, text=line)
        findings.extend(_scan_line_families(source_line, sources, context, colons))
    if colons is not None:
        findings.extend(colons.findings())
    findings.extend(_scan_document_families(context, masked, sources))
    if context.code_file:
        findings = shape_passed_removed(context.path, comment_source, context.text, findings)
    return calibrated_findings(_with_languages(findings, sources.languages))



def _scan_code_file(context: _ScanContext, text: str) -> list[dict]:
    lines = text.splitlines()
    findings = _function_length_findings(context)
    findings.extend(_hollow_test_findings(context, lines))
    findings.extend(test_rules.check_file(context.path, context.text, contracts=contract_strings(context.config)))
    return findings


