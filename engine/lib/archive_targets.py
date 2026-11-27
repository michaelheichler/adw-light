from __future__ import annotations

try:
    from .archive_commands import STDIN, absolute_tar_read, ditto_reads, home_expanded, patch_plan, stdin_source, tar_read, unzip_read
    from .archive_members import ArchiveRead
    from .shell_parse import _bare
    from .shell_syntax import _without_redirects
    from .gated_targets import GatedTargets, NO_TARGETS, UNRESOLVED, _read_targets
except ImportError:
    from archive_commands import STDIN, absolute_tar_read, ditto_reads, home_expanded, patch_plan, stdin_source, tar_read, unzip_read
    from archive_members import ArchiveRead
    from shell_parse import _bare
    from shell_syntax import _without_redirects
    from gated_targets import GatedTargets, NO_TARGETS, UNRESOLVED, _read_targets

UNZIP_READ_FLAGS = frozenset({"-l", "-t", "-p", "-v", "-Z", "-z"})
UNZIP_READ_LETTERS = frozenset("ltpvZz")
TAR_EXTRACT_FLAGS = frozenset({"--extract", "--get"})
TAR_VERBS = frozenset({"tar", "bsdtar", "gtar"})
ARCHIVE_VERBS = TAR_VERBS | {"unzip"}
PAX_VERB = "pax"
DITTO_VERB = "ditto"
TAR_STDOUT_FLAG = "--to-stdout"
TAR_COMMAND_FLAG = "--to-command"


def _patch_targets(raw: list[str]) -> GatedTargets:
    plan = patch_plan([_bare(home_expanded(token)) for token in _without_redirects(raw)], stdin_source(raw))
    return UNRESOLVED if plan is None else GatedTargets(plan.paths, False, archive=plan.read)


def _archive_targets(verb: str, raw: list[str]) -> GatedTargets:
    words = [_bare(home_expanded(token)) for token in _without_redirects(raw)]
    if verb in TAR_VERBS:
        if not _tar_extracts(words) or _tar_to_stdout(words):
            return NO_TARGETS
        return _read_targets(tar_read(words, stdin_source(raw) or STDIN))
    return NO_TARGETS if _unzip_reads(words) else _read_targets(unzip_read(words))


def config_archive_reads(verb: str, raw: list[str], found: ArchiveRead | None) -> list[ArchiveRead]:
    if found is not None:
        return [found]
    words = [_bare(home_expanded(token)) for token in _without_redirects(raw)]
    if verb == DITTO_VERB:
        return ditto_reads(words)
    if verb not in TAR_VERBS or not _tar_extracts(words) or _tar_to_stdout(words):
        return []
    read = absolute_tar_read(words, stdin_source(raw) or STDIN)
    return [] if read is None else [read]


def _pax_reads(raw: list[str]) -> bool:
    return any(word.startswith("-") and not word.startswith("--") and "r" in word[1:] for word in map(_bare, raw))


def _unzip_reads(words: list[str]) -> bool:
    clusters = [word[1:] for word in words if word.startswith("-") and not word.startswith("--")]
    return bool(UNZIP_READ_FLAGS.intersection(words)) or any(UNZIP_READ_LETTERS.intersection(cluster) for cluster in clusters)


def _tar_to_stdout(words: list[str]) -> bool:
    if any(word.startswith(TAR_COMMAND_FLAG) for word in words):
        return False
    clusters = [word[1:] for word in words if word.startswith("-") and not word.startswith("--")]
    old_style = [words[0]] if words and not words[0].startswith("-") else []
    return TAR_STDOUT_FLAG in words or any("O" in cluster for cluster in clusters + old_style)


def _tar_extracts(words: list[str]) -> bool:
    if words and not words[0].startswith("-") and "x" in words[0]:
        return True
    return any(
        word in TAR_EXTRACT_FLAGS or (word.startswith("-") and not word.startswith("--") and "x" in word)
        for word in words
    )
