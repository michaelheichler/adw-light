from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, NamedTuple

try:
    from .commit_inputs import PLAIN_COMMIT, STALE_SCRIPT, UnreadableCommit
except ImportError:
    from commit_inputs import PLAIN_COMMIT, STALE_SCRIPT, UnreadableCommit

MAX_STEPS = 150000
MAX_SECONDS = 8.0
MAX_ROUNDS = 3
UNSETTLED = f"This command runs scripts in a loop that the commit gate cannot settle. {PLAIN_COMMIT}"
TOO_LARGE = f"This command is too large for the commit gate to read in time. {PLAIN_COMMIT}"


class Touched(set):
    def __init__(self, items: Iterable[str], reads: list[str]) -> None:
        super().__init__(items)
        self.reads = reads
        self.writes: list[str] = []

    def update(self, *groups: Iterable[str]) -> None:
        for group in groups:
            for item in group:
                if not super().__contains__(item):
                    self.writes.append(item)
                    super().add(item)

    def __contains__(self, item: object) -> bool:
        self.reads.append(str(item))
        return super().__contains__(item)


class Visit(NamedTuple):
    calls: tuple[Any, ...]
    added: frozenset[str]
    unseen_writes: bool
    reads: frozenset[str]
    skipped: bool = False


SKIPPED = Visit((), frozenset(), False, frozenset(), True)


@dataclass(slots=True)
class WalkRun:
    git_configured: bool = False
    steps: int = 0
    skipped: bool = False
    reads: list[str] = field(default_factory=list)
    chain: list[Hashable] = field(default_factory=list)
    cache: dict[Hashable, list[tuple[frozenset[Hashable], Visit]]] = field(default_factory=dict)
    targets: set[Hashable] = field(default_factory=set)
    visits: int = 0
    function_walks: dict[Hashable, tuple[Any, frozenset[str]]] = field(default_factory=dict)
    payloads: dict[Hashable, tuple[str | None, frozenset[str]]] = field(default_factory=dict)
    deadline: float = field(default_factory=lambda: monotonic() + MAX_SECONDS)

    def tick(self) -> None:
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise UnreadableCommit(TOO_LARGE)
        self.check_clock()

    def check_clock(self) -> None:
        if monotonic() > self.deadline:
            raise UnreadableCommit(TOO_LARGE)

    def new_round(self) -> None:
        self.skipped = False
        self.reads.clear()
        self.chain.clear()
        self.targets.clear()

    def visit(self, key: Hashable, touched: set[str], walk: Callable[[], tuple[list[Any], bool]], skippable: bool) -> Visit:
        self.visits += 1
        if skippable and key in self.chain:
            self.skipped = True
            self.targets.add(key)
            return SKIPPED
        cached = self._cached(key)
        if cached is not None:
            return self._replay(cached, touched)
        outer, start, before, skipped = self.targets, len(self.reads), set(touched), self.skipped
        self.skipped = False
        self.chain.append(key)
        self.targets = set()
        try:
            calls, unseen_writes = walk()
        finally:
            self.chain.pop()
            inner, self.targets = self.targets, outer
        inner_skipped, self.skipped = self.skipped, skipped or self.skipped
        depends = frozenset(target for target in inner if target in self.chain)
        self.targets |= depends
        visited = Visit(tuple(calls), frozenset(set(touched) - before), unseen_writes, frozenset(self.reads[start:]), inner_skipped)
        self.cache.setdefault(key, []).append((depends, visited))
        return visited

    def payload(self, key: Hashable, touched: set[str], read: Callable[[], str | None]) -> tuple[str | None, frozenset[str]]:
        cached = self.payloads.get(key)
        if cached is not None:
            self._recheck(cached[1], touched)
            return cached
        start = len(self.reads)
        text = read()
        self.payloads[key] = (text, frozenset(self.reads[start:]))
        return self.payloads[key]

    def _recheck(self, reads: frozenset[str], touched: set[str]) -> None:
        if any(set.__contains__(touched, read) for read in reads):
            raise UnreadableCommit(STALE_SCRIPT)
        self.reads.extend(reads)

    def _cached(self, key: Hashable) -> Visit | None:
        for depends, visited in self.cache.get(key, ()):
            if all(target in self.chain for target in depends):
                self.targets |= depends
                return visited
        return None

    def _replay(self, visited: Visit, touched: set[str]) -> Visit:
        self._recheck(visited.reads, touched)
        touched.update(visited.added)
        self.skipped = self.skipped or visited.skipped
        return visited
