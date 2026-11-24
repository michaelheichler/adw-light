from __future__ import annotations

import re
from pathlib import PurePath

try:
    from .declaration_mask import declaration_view
    from .language_layer import language_of, layer_for
    from .language_model import COMMENT_KINDS, PYTHON, TEXT_KINDS, Kind, Layer, blanked, shifted
    from .markup import CommentSource, Region, RegionKind, comment_scan_source, render_regions
except ImportError:
    from declaration_mask import declaration_view
    from language_layer import language_of, layer_for
    from language_model import COMMENT_KINDS, PYTHON, TEXT_KINDS, Kind, Layer, blanked, shifted
    from markup import CommentSource, Region, RegionKind, comment_scan_source, render_regions

SCRIPT_OPEN_RE = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
SCRIPT_CLOSE = "</script"
LANG_ATTR_RE = re.compile(r"""\blang\s*=\s*["']?(\w+)""", re.IGNORECASE)
SCRIPT_LANGUAGES = {"ts": "typescript", "typescript": "typescript", "tsx": "tsx"}
DEFAULT_SCRIPT = "javascript"
MIXED_GRAMMAR_EXTS = frozenset({".html", ".htm", ".vue", ".svelte"})


def comment_views(source: CommentSource) -> tuple[str, ...]:
    lexer = comment_scan_source(source)
    layers = _source_layers(source)
    if not layers or not all(layer.precise for layer in layers):
        return (lexer,)
    base = render_regions(source.text, source.regions, {RegionKind.COMMENT, RegionKind.SCRIPT}) if source.mixed else source.text
    precise = blanked(base, [span for layer in layers for span in _comment_spans(source.text, layer)])
    return (precise, lexer) if any(layer.errors for layer in layers) else (precise,)


def code_views(path: str, text: str) -> tuple[str, ...]:
    lexer = declaration_view(text, PurePath(path.lower()).suffix)
    language = language_of(path)
    if language is None or language == PYTHON:
        return (lexer,)
    layer = layer_for(path, language, text)
    if not layer.precise:
        return (lexer,)
    precise = code_view(text, layer)
    return (precise, lexer) if layer.errors else (precise,)


def code_view(text: str, layer: Layer) -> str:
    return blanked(text, _code_spans(layer))


def merged_findings(primary: list[dict], extra: list[dict]) -> list[dict]:
    seen = {(row.get("rule"), row.get("line")) for row in primary}
    return primary + [row for row in extra if (row.get("rule"), row.get("line")) not in seen]


def _source_layers(source: CommentSource) -> list[Layer]:
    suffix = PurePath(source.path.lower()).suffix
    if source.mixed:
        return _script_layers(source) if suffix in MIXED_GRAMMAR_EXTS else []
    language = language_of(source.path)
    if language is None or language == PYTHON:
        return []
    return [layer_for(source.path, language, source.text)]


def _script_layers(source: CommentSource) -> list[Layer]:
    layers = []
    for region in source.regions:
        if region.kind is RegionKind.SCRIPT:
            start, end, language = _script_body(source.text, region)
            layers.append(shifted(layer_for(source.path, language, source.text[start:end]), start))
    return layers


def _script_body(text: str, region: Region) -> tuple[int, int, str]:
    opening = SCRIPT_OPEN_RE.match(text, region.start, region.end)
    if opening is None:
        return region.start, region.end, DEFAULT_SCRIPT
    closing = text.rfind(SCRIPT_CLOSE, opening.end(), region.end)
    attribute = LANG_ATTR_RE.search(opening.group(0))
    language = SCRIPT_LANGUAGES.get(attribute.group(1).lower(), DEFAULT_SCRIPT) if attribute else DEFAULT_SCRIPT
    return opening.end(), closing if closing >= 0 else region.end, language


def _comment_spans(text: str, layer: Layer) -> list[tuple[int, int]]:
    spans = layer.spans(TEXT_KINDS)
    spans.extend(
        (node.start, node.end) for node in layer.nodes
        if node.kind is Kind.IDENTIFIER and text.startswith("`", node.start)
    )
    return spans


def _code_spans(layer: Layer) -> list[tuple[int, int]]:
    openings = {node.start for node in layer.nodes if node.kind is Kind.STRING}
    spans = layer.spans(COMMENT_KINDS)
    spans.extend((start + (start in openings), end) for start, end in layer.spans(TEXT_KINDS))
    return spans
