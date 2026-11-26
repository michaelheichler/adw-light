from __future__ import annotations

import os
from typing import NamedTuple

from lib.archive_commands import stdin_source
from lib.archive_targets import (
    UNZIP_READ_FLAGS,
    UNZIP_READ_LETTERS,
    TAR_EXTRACT_FLAGS,
    TAR_VERBS,
    ARCHIVE_VERBS,
    PAX_VERB,
    TAR_STDOUT_FLAG,
    TAR_COMMAND_FLAG,
    _archive_targets,
    _pax_reads,
    _unzip_reads,
    _tar_to_stdout,
    _tar_extracts,
    _patch_targets,
)
from lib.copy_targets import (
    DESTINATION_LAST_ARG_VERBS,
    DESTINATION_ALL_ARGS_VERBS,
    ARGUMENT_VERBS,
    TARGET_DIR_VERBS,
    TARGET_DIR_FLAGS,
    TARGET_DIR_PREFIX,
    DD_OUTPUT_PREFIX,
    COPY_VERBS,
    Slot,
    _destination_slots,
    _target_slot,
    _target_directory,
    _drop_target_dir,
    _copy_gate,
    _copy_targets,
)
from lib.flag_outputs import FLAG_WRITERS, flag_targets
from lib.gated_targets import GitLookup, GatedTargets, NO_TARGETS, UNRESOLVED, _read_targets
from lib.git_targets import (
    GIT_PATH_COMMANDS,
    GIT_CHANGING_COMMANDS,
    GIT_VALUE_FLAGS,
    GIT_BASE_FLAGS,
    WORK_TREE_PREFIX,
    GLOB_CHARS,
    PATHSPEC_FILE_FLAG,
    GIT_MERGE_FILE,
    MERGE_PRINT_FLAGS,
    MERGE_VALUE_FLAGS,
    GIT_DRY_APPLY_FLAGS,
    RESTORE_STAGED,
    RESTORE_WORKTREE,
    RESTORE_PLAIN_FLAGS,
    _GitSource,
    _GitCall,
    _merged_file,
    _merge_operands,
    _git_changes_paths,
    _git_base,
    _base_value,
    _git_targets,
    _git_command_targets,
    _based,
    _directory_flags_only,
    _pathspec_targets,
    _unstages_only,
    _source_tree,
    _option_value,
    _is_directory_pathspec,
)
from lib.inplace_targets import sed_reads_only, sed_script_known
from lib.shell_expansion import Expansions
from lib.shell_parse import (
    _bare,
    _basename,
    _command_word_index,
    _expand_home,
    _is_file_target,
    _target_words,
)
from lib.shell_syntax import _without_redirects
from lib.xargs_feed import CALLBACK_READERS, READER_HAZARDS

XARGS_VALUE_FLAGS = frozenset({"-I", "-L", "-n", "-P", "-d", "-E", "-s", "-a"})
FIND_CALLBACKS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
FIND_FILE_OUTPUTS = frozenset({"-fprint", "-fprint0", "-fprintf", "-fls"})
RIPGREP_CONFIG = "RIPGREP_CONFIG_PATH"
FIND_CALL_ENDS = frozenset({";", "+"})
SYNTAX_CHECK_FLAGS = {
    **dict.fromkeys(("node", "nodejs"), frozenset({"--check", "-c"})),
    **dict.fromkeys(("sh", "bash", "zsh", "dash", "ksh"), frozenset({"-n"})),
}
SED_SCRIPT_TARGET = "sed script"


class TargetWords(NamedTuple):
    paths: list[str]
    unknown: list[str]


def writes_by_verb(segment: list[str]) -> bool:
    index = _command_word_index(segment)
    verb = _basename(segment[index]) if index < len(segment) else ""
    args = [_bare(token) for token in _without_redirects(segment[index + 1:])]
    if verb == "find":
        return bool(_find_writes(args, READER_HAZARDS))
    if verb == "git":
        return _merged_file(segment[index + 1:]) is not None
    if verb == "sed":
        return not sed_reads_only(segment)
    if verb == "xargs":
        call = _xargs_call(args)
        verb = _basename(call[0]) if call else ""
    return verb in ARGUMENT_VERBS or verb in FLAG_WRITERS


def mutation_targets(segment: list[str], expansions: Expansions) -> list[str]:
    paths = [_expand_home(path) for path in _mutation_words(segment, expansions).paths if _is_file_target(path)]
    return paths + [alias for path in paths for alias in expansions.aliases(path)]


def unknown_targets(segment: list[str], expansions: Expansions) -> list[str]:
    return _mutation_words(segment, expansions).unknown


def _mutation_words(segment: list[str], expansions: Expansions) -> TargetWords:
    found = [(word, expansions.path(word)) for word in _target_words(segment)]
    calls = expansions.calls(segment)
    for call in [segment] if calls is None else calls:
        found += _verb_words(call, expansions)
    tree = expansions.tree(segment)
    if tree is not None:
        found += [(path, path) for path in tree.named] + [(word, None) for word in tree.unknown]
    return TargetWords([path for _, path in found if path is not None], [word for word, path in found if path is None])


def _verb_words(segment: list[str], expansions: Expansions) -> list[tuple[str, str | None]]:
    index = _command_word_index(segment)
    verb = _basename(segment[index]) if index < len(segment) else ""
    if verb in FLAG_WRITERS and FLAG_WRITERS[verb].config_names & expansions.touched:
        return [(f"{verb} config", None)]
    if verb == "git":
        return _merge_words(segment[index + 1:], expansions)
    if verb in FLAG_WRITERS:
        flagged = flag_targets(verb, _without_redirects(segment[index + 1:]), expansions.path)
        named: list[tuple[str, str | None]] = [(path, path) for path in flagged.paths]
        return named + [(word, None) for word in flagged.unknown]
    reading_sed = verb == "sed" and sed_reads_only(segment)
    args = _without_redirects(segment[index + 1:]) if verb in ARGUMENT_VERBS and not reading_sed else []
    words = [expansions.pattern(token) for token in args]
    found: list[tuple[str, str | None]] = [(token, None) for token, word in zip(args, words) if word is None]
    if verb == "sed" and not sed_script_known(segment):
        found.append((SED_SCRIPT_TARGET, None))
    if verb in COPY_VERBS and None not in words:
        gated = _copy_gate(verb, args, expansions)
        if not gated.unresolved:
            return [(path, path) for path in gated.paths]
    known = [word for word in words if word is not None]
    slots: list[Slot] = _destination_slots(verb, known) if len(known) == len(words) else []
    for slot in slots:
        value = known[slot.position] if verb in DESTINATION_ALL_ARGS_VERBS else expansions.path(args[slot.position])
        found.append((args[slot.position], None if value is None else value[len(slot.prefix):]))
    return found


def generated_paths(segment: list[str], expansions: Expansions) -> list[str]:
    calls = expansions.calls(segment) or [segment]
    return [path for call in calls for path in _generated(call, expansions)]


def _generated(call: list[str], expansions: Expansions) -> list[str]:
    index = _command_word_index(call)
    verb = _basename(call[index]) if index < len(call) else ""
    if verb == "git":
        return [path for _, path in _merge_words(call[index + 1:], expansions) if path is not None]
    return flag_targets(verb, _without_redirects(call[index + 1:]), expansions.path).paths if verb in FLAG_WRITERS else []


def _merge_words(args: list[str], expansions: Expansions) -> list[tuple[str, str | None]]:
    merged = _merged_file(_without_redirects(args))
    if merged is None:
        return []
    unproven = [token for token in args if expansions.pattern(token) is None]
    if unproven:
        return [(token, None) for token in unproven]
    base, raw = merged
    path = expansions.path(raw)
    return [(raw, None if path is None else os.path.join(base, path))]


def callback_writes(segment: list[str], expansions: Expansions) -> list[str]:
    index = _command_word_index(segment)
    verb = _basename(segment[index]) if index < len(segment) else ""
    args = [_bare(token) for token in _without_redirects(segment[index + 1:])]
    hazards = {**READER_HAZARDS, "rg": None} if RIPGREP_CONFIG in expansions.touched else READER_HAZARDS
    calls = expansions.calls(segment)
    if calls is not None:
        readers = [[_bare(word) for word in call] for call in calls if _basename(_bare(call[0])) in CALLBACK_READERS]
        return [f"{verb} {call[0]}" for call in readers if not _reads_only(call, hazards, fed=False)]
    if verb == "xargs":
        call = _xargs_call(args)
        return [] if not call or _reads_only(call, hazards, fed=True) else [f"xargs {call[0]}"]
    return _find_writes(args, hazards) if verb == "find" else []


def _xargs_call(args: list[str]) -> list[str]:
    position = 0
    while position < len(args) and args[position].startswith("-"):
        position += 2 if args[position] in XARGS_VALUE_FLAGS else 1
    return args[position:]


def _find_writes(args: list[str], hazards: dict[str, tuple[str, ...] | None]) -> list[str]:
    writes = ["find -delete"] if "-delete" in args else []
    for position, word in enumerate(args[:-1]):
        call = args[position + 1:]
        call = call[:next((end for end, part in enumerate(call) if part in FIND_CALL_ENDS), len(call))]
        if word in FIND_CALLBACKS and not _reads_only(call, hazards, fed=False):
            writes.append(f"find {word} {args[position + 1]}")
        if word in FIND_FILE_OUTPUTS:
            writes.append(f"find {word} {args[position + 1]}")
    return writes


def _reads_only(call: list[str], hazards: dict[str, tuple[str, ...] | None], fed: bool) -> bool:
    name = _basename(call[0])
    if _checks_only(name, call):
        return not fed or "--" in call
    if name not in CALLBACK_READERS or name not in hazards:
        return name in CALLBACK_READERS
    flags = hazards[name]
    options = call[1:call.index("--")] if "--" in call else call[1:]
    flagged = flags is None or any(_flagged(word, flags) for word in options)
    return not flagged and (not fed or "--" in call)


def _checks_only(name: str, call: list[str]) -> bool:
    if name == "sed":
        return sed_reads_only(call)
    flags = SYNTAX_CHECK_FLAGS.get(name)
    operands = call[2:]
    return flags is not None and len(call) > 2 and call[1] in flags and not any(word.startswith("-") and word != "--" for word in operands)


def _flagged(word: str, flags: tuple[str, ...]) -> bool:
    if word.startswith("--"):
        return any(word == flag or word.startswith(flag + "=") for flag in flags if flag.startswith("--"))
    return word.startswith("-") and any(flag[1] in word[1:] for flag in flags if len(flag) == 2)


def gated_test_targets(segment: list[str], expansions: Expansions) -> GatedTargets:
    calls = expansions.calls(segment)
    if calls is not None:
        gated = [gated_test_targets(call, expansions) for call in calls]
        unresolved = any(targets.unresolved or targets.worktree is not None for targets in gated)
        return GatedTargets(tuple(path for targets in gated for path in targets.paths), unresolved)
    index = _command_word_index(segment)
    verb = _basename(segment[index]) if index < len(segment) else ""
    if verb in COPY_VERBS or verb == "git":
        return _copy_or_git(segment, index, expansions)
    if verb in ARCHIVE_VERBS:
        return _archive_targets(verb, segment[index + 1:])
    if verb == PAX_VERB:
        return UNRESOLVED if _pax_reads(segment[index + 1:]) else NO_TARGETS
    return _patch_targets(segment[index + 1:]) if verb == "patch" else NO_TARGETS


def _copy_or_git(segment: list[str], index: int, expansions: Expansions) -> GatedTargets:
    verb = _basename(segment[index])
    args = _without_redirects(segment[index + 1:])
    words = [word for word in (expansions.pattern(token) for token in args) if word is not None]
    if len(words) != len(args):
        return UNRESOLVED if verb in COPY_VERBS or _git_changes_paths(args, expansions) else NO_TARGETS
    if verb == "git":
        return _git_targets(words, _GitSource(plain_call=index == 0, stdin=stdin_source(segment[index + 1:]), cwd=expansions.cwd))
    return _copy_gate(verb, args, expansions)
