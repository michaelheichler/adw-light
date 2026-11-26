from __future__ import annotations

import re

VOWELS = frozenset("aeiouäöüy")
DIPHTHONGS = frozenset({"ai", "ei", "ie", "au", "eu", "äu"})
AI_HIATUS_RE = re.compile(r"naiv|mosaik|kokain")
LETTER_RE = re.compile(r"[a-zäöüß]+", re.IGNORECASE)


def _is_merged_pair(first: str, second: str) -> bool:
    return first + second in DIPHTHONGS or first == second


def count_syllables(word: str) -> int:
    normalized = word.lower().replace("qu", "q")
    letters = list("".join(LETTER_RE.findall(normalized)))
    hiatus = {found.start() + found.group().index("ai") for found in AI_HIATUS_RE.finditer("".join(letters))}
    count = 0
    index = 0
    total = len(letters)
    while index < total:
        if letters[index] not in VOWELS:
            index += 1
            continue
        count += 1
        if index not in hiatus and index + 1 < total and letters[index + 1] in VOWELS and _is_merged_pair(letters[index], letters[index + 1]):
            index += 2
        else:
            index += 1
    return max(count, 1) if letters else 0
