from __future__ import annotations

import re

PART = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]?[a-z]+|\d+")
FUNCTION = frozenset("a an and as at by for from in into is it of on or the this to with".split())
MARKER = re.compile(r"^why:\s*|^invariant:\s*|\b(?:because|otherwise|unless|since|except when|so that|due to|instead of|rather than)\b|(?P<purpose>\bin order to\b|\bto\b(?=\s+(?:avoid|prevent|ensure|preserve|keep|allow|support)\b))|,\s+(?:so|which)\b", re.I)
ANCHOR = re.compile(r"\d|\b\w+_\w+\b|\b\w*[a-z][A-Z]\w*\b|\b[A-Z]{2,}\b|(?:`[^`\n]+`|'[^'\n]+'|\x22[^\x22\n]+\x22)")
SUBJECT_VERB = re.compile(r"^(?:(?:the|an?|each|its|that|this)\s+)?(?:[\w-]+\s+){1,3}(?:[a-z]+(?:s|ed)|can|cannot|must|may|has|have|are|is|was|were)\b", re.I)
VAGUE = frozenset("break needed necessary reason yes no stuff thing logic".split())
NOUN_SUBJECT = re.compile(r"^(?:the|an?|each|its|that|this)\s+", re.I)


def content_words(text: str) -> set[str]:
    return {p.lower().removesuffix('s') for p in PART.findall(text.replace('_', ' ')) if p.lower() not in FUNCTION}


def restates(text: str, code: str) -> bool:
    words = content_words(text)
    shared = words & content_words(code)
    return bool(shared) and len(shared) * 2 >= len(words)


def _causal_clause(lead: str, reason: str, marker: str) -> bool:
    if not (SUBJECT_VERB.match(reason) or re.match(r"^[a-z]+s\s+[a-z]+\s+", reason, re.I)) or len(content_words(reason)) < 2:
        return False
    if marker.endswith("which"):
        return bool(NOUN_SUBJECT.match(lead)) and not restates(reason, lead)
    if marker.startswith(",") or marker == "so that":
        return bool(re.search(r"\d|\b(?:never|twice|cannot|may|must)\b", lead) or re.search(r"\b(?:must|may|cannot)\b", reason))
    if marker in {"unless", "except when", "since"}:
        return not re.match(r"^\w+(?:s|ing)\b", lead, re.I) and (len(content_words(lead)) == 1 or marker == "since")
    if marker == "because" and (NOUN_SUBJECT.match(lead) or re.match(r"^\w+\s+\w+(?:s|ed)\b", lead, re.I)):
        subject = NOUN_SUBJECT.match(reason) or re.match(r"[A-Z]\w+\s+", reason)
        return bool(subject and not re.search(r"\b(?:is|are)\b|\bone[.!]?$", reason, re.I))
    return marker in {"why:", "invariant:", "because", "otherwise"}


PURPOSE_NONVERBS = FUNCTION | frozenset("i me my mine you your yours he him his she her hers we us our ours they them their theirs its itself myself yourself ourselves themselves those these that any all some every each both either neither no one two three four five six seven eight nine ten".split())


def _purpose_head(reason: str) -> bool:
    head = re.match(r"[a-z]+\b", reason, re.I)
    if not head:
        return False
    word = head.group().lower()
    return word not in PURPOSE_NONVERBS and not word.endswith(("ing", "ed")) and (not word.endswith("s") or word.endswith("ss"))


def _reason_marker(text: str) -> re.Match | None:
    purpose = None
    for match in MARKER.finditer(text):
        if not match.lastgroup:
            return match
        lead = text[:match.start()].strip()
        if purpose is None and lead and not NOUN_SUBJECT.match(lead) and _purpose_head(text[match.end():].lstrip()):
            purpose = match
    return purpose


def states_why(text: str, code: str = "") -> bool:
    match = _reason_marker(text)
    if not match:
        contrast = re.search(r",\s*not\s+", text) and ANCHOR.search(text)
        return bool((contrast or re.match(r"^\d+(?:\.\d+)?\s*(?:ms|s|us|ns)\s+budget\b", text, re.I)) and not restates(text, code))
    lead, reason = text[:match.start()].strip(), text[match.end():].strip()
    if match.lastgroup == "purpose":
        reason = re.split(r"[,;.!?]|\bso\b", reason, maxsplit=1, flags=re.I)[0]
    fresh = content_words(reason) - content_words(code) - content_words(lead)
    if not reason or not fresh or restates(text, code):
        return False
    if match.lastgroup == "purpose":
        return len(fresh - PURPOSE_NONVERBS) >= 2
    if ANCHOR.search(reason):
        return True
    if len(fresh - VAGUE) < 2:
        return False
    if len(fresh) < 3 and re.search(r"\b(?:it|this|that|them)[.!]?$", reason, re.I):
        return False
    return _causal_clause(lead, reason, match.group().strip().lower())
