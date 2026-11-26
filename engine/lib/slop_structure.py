from __future__ import annotations

import re
from typing import NamedTuple, cast

try:
    from .findings import finding_row
    from .findings import FindingDict
    from .slop_phrase import (
        LiteralCheck, _additive_properties, _literal_agency, _ordinary_construction,
        formulaic_filler_opener, phrase_family_matches, phrase_text,
    )
    from .markup import mask_quoted
    from .prose_structure import _markdown_prose_lines
    from .command_words import command_phrase, command_word
except ImportError:
    from findings import finding_row
    from findings import FindingDict
    from slop_phrase import (
        LiteralCheck, _additive_properties, _literal_agency, _ordinary_construction,
        formulaic_filler_opener, phrase_family_matches, phrase_text,
    )
    from markup import mask_quoted
    from prose_structure import _markdown_prose_lines
    from command_words import command_phrase, command_word


class StructureRule(NamedTuple):
    name: str
    pattern: re.Pattern[str]
    detail: str
    action: str
    literal: LiteralCheck | None = None


_BINARY_CONTRAST_RE = re.compile(
    r"\bnot\s+because\b[^.!?\n]{0,80}?[.,]\s*(?:but\s+)?because\b"
    r"|\b(?:is|are|was|were)n[''`]?t\s+the\s+(?:problem|point|issue|question|answer)\b"
    r"|\bthe\s+(?:question|answer|problem|point)\s+is\s*n[''`]?t\b"
    r"|\bit\s+(?:feels|looks|seems)\s+like\b[^.!?\n]{0,60}?\.\s*(?:it[''`]?s|that[''`]?s)\s+(?:actually|really)\b"
    r"|\bit[''`]?s\s+not\s+[^.!?\n]{0,60}?,\s*it[''`]?s\b"
    r"|\bstops?\s+being\b[^.!?\n]{0,40}?\band\s+starts?\s+being\b"
    r"|\bnot\s+just\b[^.!?\n]{0,60}?\bbut\s+also\b"
    r"|\bdoes\s*n[''`]?t\s+mean\b[^.!?\n]{0,60}?\bbut\s+(?:actually|really)\b"
    r"|\b(?:is|are|was|were)n['’`]?t\s+about\b[^.!?\n]{1,60}?[;,.]\s*"
    r"(?:it['’`]?s|(?:it|this|that)\s+is|they['’`]?re)\s+about\b"
    r"|\bthe\s+(?:cause|answer|problem|point|issue|question)\s+(?:is|was)n['’`]?t\b"
    r"[^.!?\n]{1,60}?\.\s*the\s+[^.!?\n]{1,40}?\s+(?:is|was)\s*[.!?]"
    r"|\bstop(?:s|ped|ping)?\s+treating\b[^.!?\n]{1,60}?\band\s+start(?:s|ed|ing)?\s+treating\b",
    re.IGNORECASE,
)
_NEGATIVE_LISTING_RE = re.compile(
    r"\b(?:it\s+)?(?:was|is)\s*n[''`]?t\s+[^.!?\n]{0,50}?\.\s*"
    r"(?:it\s+)?(?:was|is)\s*n[''`]?t\s+[^.!?\n]{0,50}?\.\s*"
    r"(?:it\s+)?(?:was|is)\b"
    r"|\bnot\s+an?\s+[^.!?\n]{0,40}?\.{2,}\s*not\s+an?\b",
    re.IGNORECASE,
)
_DRAMATIC_FRAGMENT_RE = re.compile(
    r"\.\s*That[''`]?s\s+it\.\s*That[''`]?s\s+the\b"
    r"|\bThis\s+unlocks\s+something\s*[.!?](?:\s|$)"
    r"|^\s*[A-Z][\w-]*\.\s+And\s+[\w-]+\.\s+And\s+[\w-]+\.",
)
_FORMULAIC_CONSTRUCTION_RE = re.compile(
    r"\bby\s+the\s+time\b[^.!?\n]{0,60}?,\s*(?:i|we|they|he|she|it)\s+(?:was|were|had)\b"
    r"|\b\w+\s+that\s+is\s*n[''`]?t\s+\w+",
    re.IGNORECASE,
)
_FALSE_AGENCY_RE = re.compile(
    r"\b(?:a|the)\s+(?:complaint|decision|culture|conversation|data|market|"
    r"history|silence|nature|technology|evidence|system|process|team|company)\s+"
    r"(?:often\s+)?(?:becomes?|emerges?|shifts?|moves?\s+toward|tells?\s+us|rewards?|"
    r"lives?\s+or\s+dies|speaks?|says|demands?|refuses?|remembers?|decides?|"
    r"wants?|knows?|believes?|repeats?)\b",
    re.IGNORECASE,
)
_NARRATOR_DISTANCE_RE = re.compile(
    r"\b(?:(?:some|many|most)\s+people|people)\s+"
    r"(?:believe|think|say|argue|claim|tend\s+to)\b"
    r"|^\s*Nobody\s+(?:designed|planned|chose|decided|wanted)\b"
    r"|^\s*This\s+(?:is\s+why|happens\s+because)\b"
    r"|\b(?:they|experts|observers|critics|commentators)\s+(?:say|believe|think|argue|claim)\b",
    re.IGNORECASE,
)
_IRREGULAR_PARTICIPLES = (
    "awoken backbitten beaten begot begotten begun beheld bent beset besought bespoken bestrewn "
    "bestridden bet betaken bethought bid bidden bitten bled blent blown born borne bought bound "
    "bred broadcast broken brought browbeaten built burnt burst cast caught chid chidden chosen "
    "clad cleft cloven clung cost cut dealt done drawn dreamt driven drunk dug eaten fed felt "
    "flown flung forbidden forborne forecast foregone foreseen foretold forgiven forgone "
    "forgotten forsaken forsworn fought found frozen gainsaid gilt given got gotten graven "
    "ground grown handfed handwritten heard held hewn hidden hit hung hurt inbred inlaid "
    "interbred intercut interlaid interwoven kept knit known laid leant leapt learnt led left "
    "lent let lipread lit lost made meant met miscast misheard mislaid misread misspelt mistaken "
    "mown offset outdone overheard overridden overtaken paid partaken preset proofread proven "
    "put read rebuilt recast rent reset retold ridden riven run rung said sawn seen sent set "
    "sewn shaken shaven shed shod shorn shot shown shriven shrunk shut sightread sightseen slain "
    "slid slit slung smelt smitten sold sought sown spat spelt spent spilt split spoilt spoken "
    "spoonfed spread sprung spun stolen stove strewn stricken stridden struck strung stuck stung "
    "sung sunk swept sworn swung taken taught telecast thought thrust told torn trod trodden "
    "typeset typewritten undergone understood undertaken upheld waylaid wed withdrawn withheld "
    "withstood woken won worn wound woven written wrung"
).split()
_NOT_PARTICIPLES = (
    "open", "often", "even", "seven", "eleven", "golden", "wooden", "woolen", "sudden", "heaven", "token",
    "garden", "kitchen", "chicken", "children", "women", "men", "ten", "then", "when", "queen", "green",
    "keen", "screen", "between", "citizen", "oxygen", "specimen", "red", "bed", "need", "speed", "seed",
    "feed", "sacred", "wicked", "naked", "rugged", "ragged", "hundred", "kindred",
)
_PARTICIPLE = (
    r"(?:(?!(?:" + "|".join(_NOT_PARTICIPLES) + r")\b)[a-z]+(?:ed|en)"
    r"|(?:re|over|under|mis|out)?(?:" + "|".join(_IRREGULAR_PARTICIPLES) + r"))"
)
_PARTICIPLE_WORD_RE = re.compile(_PARTICIPLE, re.IGNORECASE)
_BASE_FORM_PARTICIPLE_RE = re.compile(
    r"(?:re|over|under|mis|out)?(?:bet|bid|broadcast|burst|cast|cost|cut|forecast|hit|hurt|knit|"
    r"let|lipread|offset|preset|proofread|put|read|run|set|shed|shut|sightread|slit|split|spread|"
    r"telecast|thrust|typeset|wed)"
)
_FLAG = r"(?:--[A-Za-z][A-Za-z-]*|-[A-Za-z]{1,2})(?![\w-])"
_OPTION_VALUE = r"[^\s,;:!?()]*[^\s,.;:!?()]"
_PREPOSITIONS = ("for", "in", "on", "to", "with", "during", "before", "after", "at", "from", "into", "under", "over")
_PREPOSITION_TAIL = r"(?:\s+(?:" + "|".join(_PREPOSITIONS) + r")\b(?:(?!\bby\b)[^,.;:!?)])*)?"
_BY_LATER_RE = re.compile(r"[^.!?;\n]*?\bby\b", re.IGNORECASE)
_FLAG_AFTER_RE = re.compile(
    r"(?:\s+" + _FLAG + r")+(?:[ =]" + _OPTION_VALUE + r")?" + _PREPOSITION_TAIL + r"\s*(?=[,.;:!?)]|$)",
    re.IGNORECASE,
)
_LIST_TAIL_RE = re.compile(r",[^.!?;:\n]*")
_LIST_SPLIT_RE = re.compile(r"\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+")
_BARE_ITEM_RE = re.compile(r"[a-z][a-z-]*(?: [a-z][a-z-]*)?")
_LIST_FUNCTION_WORDS = frozenset(("then", "now", "also", "not", "too", "again", "later", "so", "but", "yet"))
_PASSIVE_VOICE_RE = re.compile(
    r"\b(?:am|is|are|was|were|be|been|being)\s+(?:being\s+)?" + _PARTICIPLE + r"\b"
    r"|\b(?:mistakes|errors|decisions|choices|changes|promises)\s+(?:were|was)\s+made\b"
    r"|\bit\s+is\s+(?:(?:widely|generally|commonly)\s+)?(?:believed|thought|said|assumed)\s+that\b",
    re.IGNORECASE,
)
_RHETORICAL_SETUP_RE = re.compile(
    r"\b(?:have\s+you\s+ever\s+wondered|here[''`’]?s\s+what\s+i\s+mean"
    r"|think\s+about\s+it|and\s+that[''`’]?s\s+okay)\b"
    r"|^\s*What\s+if\s+[^?\n]{5,80}\?",
    re.IGNORECASE,
)
_WEAK_STARTER_RE = re.compile(
    r"^\s*(?:What|When|Where|Which|Who|Why|How)\b[^?\n]{5,}\?"
    r"|^\s*So\s+(?=(?:basically|actually|really|you\s+see)\b)"
    r"|^\s*Look,\s"
    r"|^\s*[Tt]here\s+(?:is|are)\s+(?:a\s+need\s+to\b|(?:several|many|some)\s+\w+\s+that\b)"
    r"|^\s*[Ii]t\s+is\s+(?:important|necessary|essential|possible)\s+to\b",
)
_LAZY_EXTREME_RE = re.compile(
    r"\b(?:every\s+single|everyone\s+(?:knows|agrees)|nobody\s+(?:ever|wants)"
    r"|always\s+(?:has|have|been)|never\s+(?:once|ever))\b"
    r"|\bevery\s+\w+\s+always\b"
    r"|\b(?:everyone|everybody|nobody)\s+\w+\s+(?:always|never|every)\b",
    re.IGNORECASE,
)

def _command_list(text: str, found: re.Match[str], word: str) -> bool:
    tail = _LIST_TAIL_RE.match(text, found.end())
    if tail is None or not _BASE_FORM_PARTICIPLE_RE.fullmatch(word):
        return False
    items = [item for item in _LIST_SPLIT_RE.split(tail.group()) if item]
    if len(items) < 2 or not all(_BARE_ITEM_RE.fullmatch(item) for item in items):
        return False
    if any(item.split()[0] in _LIST_FUNCTION_WORDS for item in items):
        return False
    if not all(command_phrase(item) for item in items):
        return False
    return any(not _PARTICIPLE_WORD_RE.fullmatch(item.split()[-1]) for item in items)


def _command_name_use(text: str, found: re.Match[str]) -> bool:
    word = found.group().split()[-1]
    if not _PARTICIPLE_WORD_RE.fullmatch(word) or not command_word(word.lower()):
        return False
    flagged = bool(_FLAG_AFTER_RE.match(text, found.end())) and not _BY_LATER_RE.match(text, found.end())
    return flagged or _command_list(text, found, word)


STRUCTURE_CANDIDATES = (
    StructureRule(
        "binary_contrast",
        _BINARY_CONTRAST_RE,
        "Formulaic negation and reversal",
        "State the positive claim directly.",
        _additive_properties,
    ),
    StructureRule(
        "formulaic_construction",
        _FORMULAIC_CONSTRUCTION_RE,
        "Formulaic relative-clause negation",
        "Replace the template with a direct description.",
        _ordinary_construction,
    ),
    StructureRule(
        "false_agency",
        _FALSE_AGENCY_RE,
        "An abstraction receives human agency",
        "Name the person or action responsible.",
        _literal_agency,
    ),
    StructureRule(
        "narrator_distance",
        _NARRATOR_DISTANCE_RE,
        "The narrator generalizes about unnamed people",
        "Name the people or address the reader directly.",
    ),
    StructureRule(
        "passive_voice",
        _PASSIVE_VOICE_RE,
        "Passive voice hides the actor",
        "Name the actor and use an active verb.",
        _command_name_use,
    ),
    StructureRule(
        "rhetorical_setup",
        _RHETORICAL_SETUP_RE,
        "A rhetorical setup delays the claim",
        "Remove the setup and state the claim.",
    ),
    StructureRule(
        "negative_listing",
        _NEGATIVE_LISTING_RE,
        "Accumulated negations build to a reveal",
        "Assert the point without the runway.",
    ),
    StructureRule(
        "dramatic_fragmentation",
        _DRAMATIC_FRAGMENT_RE,
        "Sentence fragments manufacture emphasis",
        "Write complete sentences.",
    ),
    StructureRule(
        "weak_sentence_starter",
        _WEAK_STARTER_RE,
        "Sentence opens on a question word or a filler start",
        "Lead with the subject or the verb.",
        formulaic_filler_opener,
    ),
    StructureRule(
        "lazy_extreme",
        _LAZY_EXTREME_RE,
        "An absolute stands in for a specific claim",
        "Name the actual scope or number.",
    ),
)
STRUCTURE_RULES: tuple[StructureRule, ...] = STRUCTURE_CANDIDATES


def _line_findings(path: str, line_number: int, line: str) -> list[FindingDict]:
    findings: list[FindingDict] = []
    candidate = phrase_text(mask_quoted(line))
    for rule in STRUCTURE_RULES:
        found = next(phrase_family_matches((rule.pattern,), candidate, rule.literal), None)
        if found:
            findings.append(cast(FindingDict, finding_row(
                "english", rule.name, line_number, rule.detail + " in " + path,
                line, rule.action, match=line[found.start():found.end()],
            )))
    return findings


def scan_slop_structure(path: str, text: str) -> list[FindingDict]:
    findings: list[FindingDict] = []
    for line_number, line in _markdown_prose_lines(text):
        findings.extend(_line_findings(path, line_number, line))
    return findings
