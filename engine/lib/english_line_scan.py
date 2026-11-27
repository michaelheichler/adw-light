from __future__ import annotations

try:
    from .comment_rules import READABILITY_RULES
    from .findings import finding_row
    from .markup import _strip_inline_code
    from .scan_lines import _SourceLine
    from .slop_phrase import (
        EnglishRule,
        PhraseFamily,
        _temporal_day,
        phrase_family_matches,
        phrase_text,
    )
except ImportError:
    from comment_rules import READABILITY_RULES
    from findings import finding_row
    from markup import _strip_inline_code
    from scan_lines import _SourceLine
    from slop_phrase import (
        EnglishRule,
        PhraseFamily,
        _temporal_day,
        phrase_family_matches,
        phrase_text,
    )


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
