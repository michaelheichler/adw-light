from __future__ import annotations

import re
from collections import Counter
from itertools import chain

try:
    from .findings import finding_row
    from .markup import (
        INLINE_CODE_RE,
        _strip_english_hidden,
        _strip_inline_code,
        mask_quoted,
    )
    from .prose_structure import (
        LIST_ITEM_RE,
        WORD_RE,
        _markdown_prose_lines,
        _paragraphs,
        sentence_spans,
    )
    from .slop_phrase import APOSTROPHE_TRANSLATION
    from .slop_structure import _IRREGULAR_PARTICIPLES, _NOT_PARTICIPLES
except ImportError:
    from findings import finding_row
    from markup import (
        INLINE_CODE_RE,
        _strip_english_hidden,
        _strip_inline_code,
        mask_quoted,
    )
    from prose_structure import (
        LIST_ITEM_RE,
        WORD_RE,
        _markdown_prose_lines,
        _paragraphs,
        sentence_spans,
    )
    from slop_phrase import APOSTROPHE_TRANSLATION
    from slop_structure import _IRREGULAR_PARTICIPLES, _NOT_PARTICIPLES


PARTICLES = {'up', 'out', 'off', 'down', 'back', 'away', 'apart'}
AUX = {'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'shall', 'should', 'can', 'could', 'may', 'might', 'must'}
DET = {'a', 'an', 'the', 'this', 'that', 'these', 'those', 'its', 'their', 'your', 'our', 'his', 'her', 'my', 'each', 'every', 'any', 'some', 'all', 'no'}
PREP = {'to', 'for', 'with', 'about', 'on', 'of', 'into', 'onto', 'upon', 'at', 'by', 'from', 'in', 'than'}
OBJECT = {'it', 'them', 'him', 'her', 'us', 'me', 'this', 'that'}
COMPOUND = {'to', 'of', 'from', 'in', 'at', 'by', 'than'}
EXEMPT = re.compile(r'circl\w* back|carr(?:y|ies|ied|ying) out|turn(?:s|ed)? out')
NOMINAL = re.compile(r'\b(?:perform(?:s|ed|ing)?|conduct(?:s|ed|ing)?|carr(?:y|ies|ied|ying) out)\s+(?:a|an|the)\s+\w+(?:tion|sion|ment|ance|ence|ysis)\s+of\b', re.IGNORECASE)
MARKETING = re.compile(r'\b(?:seamless(?:ly)?|robust(?:ly)?|cutting-edge|effortless(?:ly)?|blazing[- ]fast|world-class|state-of-the-art|game-chang(?:ing|er))\b', re.IGNORECASE)
PREDICATE = re.compile(r'\b(?:is|are|was|were|as)\s+(?:the\s+)?(?:[a-z]+est|most\s+[a-z]+|best|worst)\b(?=\s*(?:[.,;:!?)]|$))')
MEASURE = re.compile(r'\d|\b(?:than|among|versus|vs)\b', re.IGNORECASE)
TENSE = re.compile(r'\b(?:has|have|had)\s+(?:been\s+)?([a-z]+(?:ed|en)|' + '|'.join(_IRREGULAR_PARTICIPLES) + r')\b', re.IGNORECASE)
MODAL = re.compile(r"\b(?:(?:may|might|could|should|would|must|can|will|shall)(?:n't)?|cannot|can't|won't|shan't)(?:\s+(?:not|already|also|ever|even|just|never|still|very|well|\w+ly)){0,2}\s+$", re.IGNORECASE)
PATH = re.compile(r'https?://\S+|(?<!\w)(?:[.~\w-]*/)+[\w./-]+|\b[\w-]+\.[A-Za-z][\w-]*\b')
DOTTED_TOKEN = re.compile(r'\b[\w-]+(?:\.[\w-]+)+\b')
FILE_EXTENSIONS = frozenset('py js ts tsx jsx json yml yaml toml md txt sh bash zsh rs go java kt swift rb php c h cc cpp hpp cs css scss html xml csv lock cfg ini log gz tar zip'.split())
END = re.compile(r'\b(?:and|or|but|nor)\s*$', re.IGNORECASE)
GROUPS = ('check verify confirm validate', 'delete remove erase', 'start launch begin initiate', 'stop halt terminate', 'show display', 'use employ', 'fix repair correct', 'send transmit', 'get retrieve fetch obtain', 'change modify alter')
SYNONYMS = {word: group for group in GROUPS for word in group.split()}
REPORT_ROW_CAP = 50
ACTIONS = {
    'phrasal_verb': 'Use one plain verb.',
    'marketing_adjective': 'Remove the claim or give a measurement.',
    'nominalization': 'Use the action verb instead of the noun.',
    'compound_tense': 'Use simple past or present unless the time matters.',
    'sentence_length': 'Split the sentence.',
    'synonym_rotation': 'Pick one verb for the same action.',
    'dangling_conjunction': 'Complete the item or join it with the next item.',
}


def _row(path, rule, number, line, match, detail=None):
    title = detail or rule.replace('_', ' ').capitalize()
    return finding_row('english', 'ste_' + rule, number, f'{title} in {path}', line, ACTIONS[rule], match=match)


def _phrasal(line, tokens, words):
    for index in range(1, len(words)):
        particle, verb = words[index], words[index - 1]
        if particle not in PARTICLES or verb in AUX or verb in PARTICLES or verb in DET or verb in PREP:
            continue
        before = words[max(0, index - 3):index - 1]
        if any(word in AUX or word == 'not' for word in before) or before and before[-1] in DET:
            continue
        pair = line[tokens[index - 1].start():tokens[index].end()]
        if pair.lower() != f'{verb} {particle}' or EXEMPT.fullmatch(pair.lower()):
            continue
        after = words[index + 1] if index + 1 < len(words) else ''
        gap = line[tokens[index].end():tokens[index + 1].start()] if after else ''
        if gap.strip() or not after:
            yield pair
        elif after in COMPOUND and (particle, after) != ('out', 'to'):
            continue
        elif after in DET or after in OBJECT or after in PREP:
            yield pair


def _compound(line):
    for match in TENSE.finditer(line):
        verb = match[1].lower()
        if verb in _NOT_PARTICIPLES or MODAL.search(line, max(0, match.start() - 160), match.start()):
            continue
        if verb.endswith('ed') or verb in ('read', 'set', 'put', 'cut'):
            tail = re.compile(r'\s+([a-z]+)\b').match(line, match.end())
            if tail and tail[1] not in DET and tail[1] not in PREP and not tail[1].endswith('ly'):
                continue
        yield match[0]


def _matches(line, tokens, words):
    for rule, pattern in (('nominalization', NOMINAL), ('marketing_adjective', MARKETING)):
        for match in pattern.finditer(line):
            yield rule, match[0]
    for sentence in re.finditer(r'[^.!?]+', line):
        if not MEASURE.search(sentence[0]):
            for match in PREDICATE.finditer(sentence[0]):
                yield 'marketing_adjective', match[0]
    for phrase in _compound(line):
        yield 'compound_tense', phrase
    for phrase in _phrasal(line, tokens, words):
        yield 'phrasal_verb', phrase


def _dotted_word(match: re.Match[str]) -> str:
    parts = match[0].lower().split('.')
    if any(part.isdecimal() for part in parts):
        return 'X'
    if parts[-1] in FILE_EXTENSIONS and not all(len(part) == 1 for part in parts):
        return 'X'
    return match[0]


def _counted_view(text: str) -> str:
    visible = DOTTED_TOKEN.sub(_dotted_word, mask_quoted(_strip_inline_code(text)))
    return PATH.sub('X', visible).translate(APOSTROPHE_TRANSLATION)


def _lengths(path, sources):
    items = ([(n, line[m.end():])] for n, line in sources if (m := LIST_ITEM_RE.match(line)))
    for paragraph in chain(_paragraphs(sources), items):
        for span in sentence_spans(paragraph):
            count = len(WORD_RE.findall(_counted_view(span.text)))
            if count > 25:
                yield _row(path, 'sentence_length', span.first, span.text, span.text, f'Sentence has {count} words (cap 25)')


def _dangling(path, lines):
    ends = {}
    for n, line in lines:
        indent = len(line.expandtabs()) - len(line.expandtabs().lstrip())
        for start in tuple(ends):
            if line.strip() and start < indent < start + 4:
                ends[start] = (n, line)
                continue
            last, body = ends.pop(start)
            if match := END.search(body):
                yield _row(path, 'dangling_conjunction', last, body, match[0])
        if LIST_ITEM_RE.match(line):
            ends[indent] = (n, line)


def scan_ste(path: str, text: str, config: dict) -> list[dict]:
    sources = []
    items = []
    findings = []
    references = {}
    seen = set()
    for n, raw in _markdown_prose_lines(_strip_english_hidden(text)):
        neutral = INLINE_CODE_RE.sub('X', raw)
        items.append((n, mask_quoted(neutral)))
        sources.append((n, raw))
        line = _counted_view(raw)
        tokens = list(WORD_RE.finditer(line))
        words = [token[0].lower() for token in tokens]
        findings.extend(_row(path, rule, n, raw, phrase) for rule, phrase in _matches(line, tokens, words))
        for word in words:
            if word in seen or word not in SYNONYMS:
                continue
            seen.add(word)
            first = references.setdefault(SYNONYMS[word], word)
            if first != word:
                findings.append(_row(path, 'synonym_rotation', n, raw, word, f'{first} and {word} name the same action'))
    findings.extend(_lengths(path, sources))
    items.append((0, ''))
    findings.extend(_dangling(path, items))
    return _capped(findings)


def _capped(findings):
    counts = Counter()
    kept = []
    for row in findings:
        counts[row['rule']] += 1
        if row['rule'] == 'ste_nominalization' or counts[row['rule']] <= REPORT_ROW_CAP:
            kept.append(row)
    return kept
