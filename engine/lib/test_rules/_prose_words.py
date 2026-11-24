from __future__ import annotations

import re

WRAPPING = "\"'`.,;:!?()[]*"
IDENTIFIER_MARKS = frozenset("_/\\{}<>")
OPERATOR_CHARS = frozenset("=!<>+-*/%|&^~:")
LONE_OPERATORS = frozenset("=+%|^~<>")
FLAG_RE = re.compile(r"^--?[A-Za-z][A-Za-z0-9-]*(?:=\S*)?$")
APOSTROPHES = "'\u2019"
MIN_WORDS = 2
WORD_LETTER_SHARE = 0.75
WORD_SHARE = 0.6
CONTRACTIONS = {"n't": "not", "'s": "is", "'re": "are", "'ve": "have", "'ll": "will", "'d": "would", "'m": "am"}
SQL_STATEMENTS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "WITH"})
SQL_CLAUSES = frozenset({"FROM", "INTO", "SET", "TABLE", "WHERE", "VALUES"})
STOPWORDS = frozenset({
    "a", "about", "after", "again", "all", "also", "an", "and", "any", "are", "as", "at", "be",
    "been", "before", "being", "but", "by", "can", "cannot", "could", "did", "do", "does", "each",
    "for", "from", "had", "has", "have", "he", "her", "here", "his", "i", "if", "in", "into", "is",
    "it", "its", "may", "me", "more", "most", "must", "my", "no", "not", "of", "on", "only", "or",
    "our", "out", "over", "she", "should", "so", "such", "than", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "those", "to", "under", "up", "was", "we", "were",
    "what", "when", "where", "which", "who", "will", "with", "would", "you", "your",
    "aber", "auf", "dass", "das", "dem", "den", "der", "des", "die", "ein", "eine", "einen", "für",
    "ist", "im", "kein", "keine", "mit", "nicht", "oder", "sind", "und", "von", "wenn", "wird", "zu",
})


def _straight(text: str) -> str:
    return text.replace("\u2019", "'")


def _cores(text: str) -> list[str]:
    return [core for core in (token.strip(WRAPPING) for token in _straight(text).split()) if core]


def _wordlike(core: str) -> bool:
    return sum(char.isalpha() for char in core) >= WORD_LETTER_SHARE * len(core)


def _operator(core: str) -> bool:
    return all(char in OPERATOR_CHARS for char in core) and (len(core) > 1 or core in LONE_OPERATORS)


def _marked(core: str) -> bool:
    return _operator(core) or FLAG_RE.match(core) is not None or any(mark in core for mark in IDENTIFIER_MARKS)


def _query_like(cores: list[str]) -> bool:
    return cores[0] in SQL_STATEMENTS and any(core in SQL_CLAUSES for core in cores[1:])


def _machine_marked(cores: list[str]) -> bool:
    return any(_marked(core) for core in cores) or _query_like(cores)


def _lookup_forms(word: str) -> tuple[str, ...]:
    folded = word.casefold()
    for suffix, expansion in CONTRACTIONS.items():
        if folded.endswith(suffix) and len(folded) > len(suffix):
            return folded, folded[:-len(suffix)], expansion
    return (folded,)


def _stopword(word: str) -> bool:
    return any(form in STOPWORDS for form in _lookup_forms(word))


def _substantial(words: list[str]) -> bool:
    long_words = [word for word in words if len(word) > 1]
    return len(long_words) >= MIN_WORDS or any(not _stopword(word) for word in long_words)


def is_prose(text: str) -> bool:
    cores = _cores(text)
    if len(cores) < MIN_WORDS or _machine_marked(cores):
        return False
    words = [core for core in cores if _wordlike(core)]
    if len(words) < MIN_WORDS or len(words) < WORD_SHARE * len(cores):
        return False
    return _substantial(words) and any(_stopword(word) for word in words)
