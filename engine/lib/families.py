from __future__ import annotations

from collections.abc import Iterable

FAMILIES = ("prose", "comment", "code")
LEGACY_FAMILIES = ("punctuation", "english", "clean_code")
NAMES = FAMILIES + LEGACY_FAMILIES
SCOPES: dict[str, tuple[str, ...]] = {
    "punctuation": ("punctuation", "prose"),
    "english": ("english", "prose"),
    "comment": ("comment", "clean_code"),
    "code": ("code", "clean_code"),
}
LEAVES = tuple(SCOPES)


def scope(family: str) -> tuple[str, ...]:
    return SCOPES.get(family, (family,))


def leaves_of(names: Iterable[str]) -> frozenset[str]:
    listed = set(names)
    return frozenset(leaf for leaf, scoped in SCOPES.items() if listed.intersection(scoped))


def covered(name: str) -> frozenset[str]:
    return frozenset((name, *(leaf for leaf, scoped in SCOPES.items() if name in scoped)))
