from __future__ import annotations

import fnmatch
import os
from pathlib import Path

try:
    from .find_matches import NAME_TESTS, PATH_TESTS, PLACEHOLDER, Node, Unsupported, _plan, deletion_starts
    from .findings import Finding, Rule
    from .folder_trees import _split_operands, recursive_operands
    from .opaque_write import MUTATING_VERB_RE
    from .policy_cli_guard import mutates_policy
    from .protected import (
        POLICY_ACTION, _is_state_path, _literal, _resolve, _state_roots, authorized, is_install_surface_path, state_root_forms,
    )
    from .shell_expansion import Expansions
    from .shell_parse import (
        _basename,
        _command_word_index,
        _redirect_paths,
        _segment_text,
        _segments,
        _without_redirects,
        command_expansions,
        executable_text,
        line_segments,
    )
    from .update_policy import _segment_contexts
    from .write_targets import mutation_targets, protected_mutation_targets, writes_by_verb
except ImportError:
    from lib.find_matches import NAME_TESTS, PATH_TESTS, PLACEHOLDER, Node, Unsupported, _plan, deletion_starts
    from findings import Finding, Rule
    from lib.folder_trees import _split_operands, recursive_operands
    from opaque_write import MUTATING_VERB_RE
    from policy_cli_guard import mutates_policy
    from protected import (
        POLICY_ACTION, _is_state_path, _literal, _resolve, _state_roots, authorized, is_install_surface_path, state_root_forms,
    )
    from shell_expansion import Expansions
    from shell_parse import (
        _basename,
        _command_word_index,
        _redirect_paths,
        _segment_text,
        _segments,
        _without_redirects,
        command_expansions,
        executable_text,
        line_segments,
    )
    from update_policy import _segment_contexts
    from write_targets import mutation_targets, protected_mutation_targets, writes_by_verb

STATE_DELETE_VERBS = frozenset({"rm", "unlink", "shred"})
GLOB_MARKS = frozenset("*?[")
RECURSIVE_GLOB = "**"
PARENT_PART = ".."
MAX_BRACE_WORDS = 256
UNBOUNDED_DEPTH = 1 << 30
RULES: dict[str, Rule] = {
    "state_deletion": Rule(
        detail="Watcher state deleted",
        action="Leave the state in place and repair the reported finding.",
    ),
    "state_mutation": Rule(
        detail="Watcher state mutated",
        action="Leave watcher state under host control and repair the reported finding.",
    ),
    "config_seal": Rule(
        detail="Shell command changes project policy",
        action=POLICY_ACTION,
    ),
    "watcher_install_surface": Rule(
        detail="Shell command mutates the live watcher install",
        action="Change the repo source and reinstall instead of editing the live install.",
    ),
}


def command_findings(command: str, config: dict | None = None, home: str | os.PathLike[str] | None = None, cwd: str | os.PathLike[str] | None = None) -> list[dict]:
    if not command or authorized(config):
        return []
    contexts = _segment_cwds(command, cwd, home)
    expansions = command_expansions(command, cwd)
    hits = _protected_target_hits(contexts, expansions, home)
    if any(_mutates_install_surface(segment, home, expansions, folder) for segment, folder in contexts):
        hits.append("watcher_install_surface")
    if any(mutates_policy(segment, segment_cwd) for segment, segment_cwd in contexts):
        hits.append("config_seal")
    return [_finding(rule, command) for rule in hits]


def _protected_target_hits(contexts, expansions: Expansions, home) -> list[str]:
    mutation = False
    for segment, cwd in contexts:
        if _deletes_state_tree(segment, expansions, home, cwd):
            return ["state_deletion"]
        if not _is_mutating(segment):
            continue
        for target in protected_mutation_targets(segment, expansions):
            resolved = _resolve(_session_path(target, cwd), home)
            if resolved is None or not _is_state_path(resolved, home):
                continue
            index = _command_word_index(segment)
            if index < len(segment) and _basename(segment[index]) in STATE_DELETE_VERBS:
                return ["state_deletion"]
            mutation = True
    return ["state_mutation"] if mutation else []


def _deletes_state_tree(segment: list[str], expansions: Expansions, home, cwd: Path) -> bool:
    for start in deletion_starts(segment, expansions.pattern):
        resolved = _resolve(_session_path(start, cwd), home)
        if resolved is not None and any(
            resolved.is_relative_to(root) or resolved == root.parent for root in _state_roots(home)
        ):
            return True
    for operand, follow in recursive_operands(segment):
        target = expansions.path(operand)
        if target is None:
            continue
        path = _literal(_session_path(target, cwd), home)
        if path is None or (not follow and path.is_symlink()):
            continue
        resolved = path.resolve()
        if any(root.is_relative_to(resolved) for root in _state_roots(home)):
            return True
    roots = state_root_forms(home)
    patterns = [_literal(_session_path(word, cwd), home) for word in _removed_patterns(segment, expansions)]
    if any(path is not None and _pattern_reaches_root(path, roots) for path in patterns):
        return True
    return _find_reaches_root(segment, expansions, home, cwd, roots)


def _removed_patterns(segment: list[str], expansions: Expansions) -> list[str]:
    index = _command_word_index(segment)
    verb = _basename(segment[index]) if index < len(segment) else ""
    if verb in STATE_DELETE_VERBS:
        words = _split_operands(verb, _without_redirects(segment[index + 1:]))[1]
    else:
        words = [raw for raw, _ in recursive_operands(segment)]
    spelled = [expansions.pattern(alternative) for word in words for alternative in _brace_words(word)]
    return [pattern for pattern in spelled if pattern is not None and GLOB_MARKS.intersection(pattern)]


def _brace_words(word: str) -> list[str]:
    found = _brace_group(word)
    if found is None:
        return [word]
    head, choices, tail = found
    words: list[str] = []
    for choice in choices:
        words.extend(_brace_words(head + choice + tail))
        if len(words) > MAX_BRACE_WORDS:
            return [head + "*" + tail]
    return words


def _brace_group(word: str) -> tuple[str, list[str], str] | None:
    depth, start, cuts, quote, escaped = 0, 0, [], "", False
    for position, char in enumerate(word):
        if escaped or (quote and char != quote):
            escaped = False
            continue
        if char == "\\" or char in "'\"":
            escaped = char == "\\"
            quote = "" if quote or escaped else char
        elif char == "{":
            start, cuts = (position, []) if depth == 0 else (start, cuts)
            depth += 1
        elif char == "," and depth == 1:
            cuts.append(position)
        elif char == "}" and depth:
            depth -= 1
            inner = word[start + 1:position]
            if depth == 0 and (cuts or ".." in inner):
                bounds = [start, *cuts, position]
                choices = [word[left + 1:right] for left, right in zip(bounds, bounds[1:])] if cuts else ["*"]
                return word[:start], choices, word[position + 1:]
    return None


def _pattern_reaches_root(path: Path, roots: tuple[Path, ...]) -> bool:
    parts = path.parts
    split = next((index for index, part in enumerate(parts) if GLOB_MARKS.intersection(part)), len(parts))
    folder, tail = Path(*parts[:split]), parts[split:]
    if PARENT_PART in tail:
        folder = Path(os.path.abspath(folder.joinpath(*[PARENT_PART] * tail.count(PARENT_PART))))
        tail = ()
    places = {Path(os.path.abspath(folder)), folder.resolve()}
    return any(_names_reach(place, root, tail) for place in places for root in roots)


def _names_reach(place: Path, root: Path, tail: tuple[str, ...]) -> bool:
    if place.is_relative_to(root):
        return True
    if not root.is_relative_to(place):
        return False
    names = root.relative_to(place).parts
    if RECURSIVE_GLOB in tail:
        cut = tail.index(RECURSIVE_GLOB)
        rest = tail[cut + 1:]
        return _matches_prefix(names, tail[:cut]) and (
            not rest or any(_glob_match(name, rest[0]) for name in names[cut:])
        )
    return _matches_prefix(names, tail)


def _matches_prefix(names: tuple[str, ...], patterns: tuple[str, ...]) -> bool:
    return all(_glob_match(name, pattern) for name, pattern in zip(names, patterns))


def _glob_match(name: str, pattern: str, *, folded: bool = False) -> bool:
    spelled = pattern.replace("[^", "[!")
    return fnmatch.fnmatchcase(name.lower(), spelled.lower()) if folded else fnmatch.fnmatchcase(name, spelled)


def _find_reaches_root(segment: list[str], expansions: Expansions, home, cwd: Path, roots: tuple[Path, ...]) -> bool:
    index = _command_word_index(segment)
    if index >= len(segment) or _basename(segment[index]) != "find":
        return False
    words = [PLACEHOLDER if token == PLACEHOLDER else expansions.pattern(token) for token in _without_redirects(segment[index + 1:])]
    if None in words:
        return False
    try:
        plan = _plan(words)
    except Unsupported:
        return False
    branches = _deleting_branches(plan.tree)
    depths = range(plan.limits.get("-mindepth", 0), plan.limits.get("-maxdepth", UNBOUNDED_DEPTH) + 1)
    return bool(branches) and any(
        _find_start_reaches(start, _literal(_session_path(start, cwd), home), branches, depths, roots) for start in plan.starts
    )


def _deleting_branches(node: Node) -> list[list[tuple[str, str]]]:
    if node.kind == "-delete" or (node.kind == "exec" and _basename(node.parts[0][0]) in STATE_DELETE_VERBS):
        return [[]]
    if node.kind == "or":
        return _deleting_branches(node.parts[0]) + _deleting_branches(node.parts[1])
    if node.kind == "and":
        left, right = node.parts
        guards = _name_guards(left)
        return _deleting_branches(left) + [guards + branch for branch in _deleting_branches(right)]
    return []


def _name_guards(node: Node) -> list[tuple[str, str]]:
    if node.kind == "test" and (node.parts[0] in NAME_TESTS or node.parts[0] in PATH_TESTS):
        return [node.parts]
    if node.kind == "and":
        return _name_guards(node.parts[0]) + _name_guards(node.parts[1])
    return []


def _find_start_reaches(
    start: str, path: Path | None, branches: list[list[tuple[str, str]]], depths: range, roots: tuple[Path, ...],
) -> bool:
    if path is None:
        return False
    shown = str(path) if start.startswith("~") else start
    for place in {Path(os.path.abspath(path)), path.resolve()}:
        for root in roots:
            if place.is_relative_to(root):
                return True
            if not root.is_relative_to(place):
                continue
            names = root.relative_to(place).parts
            chain = [os.path.join(shown, *names[:depth]) for depth in range(len(names) + 1) if depth in depths]
            if any(all(_find_test_matches(test, shown_path) for test in branch) for branch in branches for shown_path in chain):
                return True
    return False


def _find_test_matches(test: tuple[str, str], shown: str) -> bool:
    name, pattern = test
    folded = name in {"-iname", "-ipath", "-iwholename"}
    subject = (os.path.basename(shown.rstrip(os.sep)) or shown) if name in NAME_TESTS else shown
    return _glob_match(subject, pattern, folded=folded)


def _segment_views(command: str) -> list[list[list[str]]]:
    return [_segments(executable_text(command)), line_segments(command)]


def _command_segments(command: str) -> list[list[str]]:
    unique = {tuple(segment): segment for view in _segment_views(command) for segment in view}
    return list(unique.values())


def _segment_cwds(command: str, cwd: str | os.PathLike[str] | None, home: str | os.PathLike[str] | None) -> list[tuple[list[str], Path]]:
    unique = {}
    for view in _segment_views(command):
        for segment, segment_cwd in _segment_contexts(view, cwd, home):
            unique.setdefault((tuple(segment), segment_cwd), (segment, segment_cwd))
    return list(unique.values())


def _session_path(path: str, cwd: str | os.PathLike[str] | None) -> str:
    if cwd is None or path.startswith("~") or os.path.isabs(path):
        return path
    return os.path.join(cwd, path)


def _is_mutating(segment: list[str]) -> bool:
    return bool(_redirect_paths(segment)) or bool(MUTATING_VERB_RE.search(_segment_text(segment))) or writes_by_verb(segment)


def _mutates_install_surface(segment: list[str], home: str | os.PathLike[str] | None, expansions: Expansions, cwd: Path) -> bool:
    if not _is_mutating(segment):
        return False
    return any(is_install_surface_path(_session_path(path, cwd), home) for path in mutation_targets(segment, expansions))


def _finding(rule: str, command: str) -> dict:
    rule_spec = RULES[rule]
    return Finding(
        family="self_protection",
        rule=rule,
        line=1,
        detail=rule_spec.detail,
        force=True,
        snippet=command.strip()[:180],
        action=rule_spec.action,
        path=None,
        severity=None,
        tool_use_id=None,
    ).to_dict()
