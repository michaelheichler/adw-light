from __future__ import annotations

import re
from itertools import takewhile
from pathlib import Path

from .archive_commands import STDIN, tar_read
from .archive_targets import TAR_VERBS, _tar_extracts
from .copy_targets import DESTINATION_ALL_ARGS_VERBS, _copy_targets, _destination_slots, _target_slot
from .flag_outputs import FLAG_WRITERS, flag_targets
from .git_targets import GIT_CHANGING_COMMANDS, GIT_VALUE_FLAGS, _GitSource, _git_base, _git_targets
from .git_writes import written_paths
from .commit_inputs import SCRIPT_READERS, is_dynamic, normalized
from .python_payload import is_known_read_only_python
from .python_shell import trusted_python_startup
from .shell_parse import STDIN_PROGRAM_PATHS, stdin_program
from .shell_syntax import _basename, _payload_command_index
from .shell_text import INLINE_INPUT, MARKED_RE, split_redirects
from .write_targets import FIND_CALLBACKS, FIND_FILE_OUTPUTS

READ_ONLY_COMMANDS = frozenset({
    "[", "basename", "cat", "cd", "cmp", "cut", "date", "dirname", "echo", "exit", "false", "file", "grep", "head", "jq", "ls",
    "popd", "printf", "pushd", "pwd", "readlink", "realpath", "rg", "sleep", "sort", "stat", "tail", "test", "tr",
    "true", "type", "uniq", "wc", "which", ".", "source", "[[", "case", "esac", "fi", "done",
    "local", "declare", "typeset", "export", "readonly", "unset", "set", "shift", "return", "break", "continue", ":",
})
SYNTAX_CHECK_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
NODE_NAMES = frozenset({"node", "nodejs"})
NODE_CHECK_FLAGS = frozenset({"--check", "-c"})
COPY_OPTIONS = frozenset({"-r", "-R", "-p", "-f", "--", "-t", "--target-directory"})
METADATA_COMMANDS = frozenset({
    "add", "write-tree", "commit-tree", "hash-object", "update-index", "update-ref", "tag", "show", "cat-file",
    "rev-parse", "push", "fetch", "ls-remote", "for-each-ref", "show-ref", "merge-base", "archive",
})
GIT_STORAGE_ENV = frozenset({"GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"})
CONFIG_READS = frozenset({"--get", "--get-all", "--get-regexp", "--list", "-l"})
CONFIG_MODIFIERS = frozenset({"--bool", "--int", "--local", "--global", "--system", "--worktree", "--includes"})
QUERY_FLAGS = {
    "rev-parse": {"--path-format=absolute", "--git-dir", "--git-common-dir", "--verify", "--quiet"},
    "ls-files": {"--", "-v", "--others"},
    "status": {"--porcelain", "--untracked-files=all"},
    "ls-remote": {"--exit-code", "--refs"},
}


STATUS_METADATA_FLAGS = QUERY_FLAGS["status"] | {
    "--porcelain=v1", "--porcelain=v2", "-z", "--untracked-files=no", "--untracked-files=normal", "-b", "--short",
}

STDOUT_EXPRESSIONS = {
    "perl": re.compile(r"print\s+(?:\d+|'[\w .-]*')\s*;?"),
    "ruby": re.compile(r"(?:print|puts|p)\s+(?:\d+|'[\w .-]*'|ARGV\[\d+\])\s*;?"),
    "node": re.compile(r"console\.log\(\s*(?:\d+|'[\w .-]*'|process\.argv\[\d+\])\s*\)\s*;?"),
}


def reads_stdin_operands(segment: list[str], cwd: Path) -> bool:
    if not segment or not MARKED_RE.fullmatch(segment[-1]):
        return False
    words, redirects = split_redirects(segment)
    if not words or len(redirects) != 1 or words[-1].isdigit():
        return False
    source = redirects[0]
    if source.fd != 0 or source.operator != INLINE_INPUT or is_dynamic(source.target) or "\\" in source.target:
        return False
    name, args = words[0], words[1:]
    if not STDIN_PROGRAM_PATHS.intersection(args) or not stdin_program(name, args):
        return False
    if name.startswith("python"):
        end = next(i for i, word in enumerate(args) if word in STDIN_PROGRAM_PATHS)
        prefix = [name, *args[:end], "-"]
        return trusted_python_startup(prefix, str(cwd)) and is_known_read_only_python(source.target, isolated=True)
    expression = STDOUT_EXPRESSIONS.get(name)
    return expression is not None and expression.fullmatch(source.target.strip()) is not None


def git_environment(name: str) -> bool:
    return name.startswith("GIT_") or name in {"HOME", "XDG_CONFIG_HOME", "PAGER", "EDITOR"}


def write_keys(cwd: Path, word: str):
    path = Path(normalized(cwd, word))
    yield str(path)
    if path.name == ".gitattributes":
        yield ".gitattributes"
    for parent in path.parents:
        if parent.name == ".git":
            yield str(parent)


def _config_reads(args: list[str]) -> bool:
    i = 0
    while i < len(args):
        if args[i] in CONFIG_READS:
            return True
        if args[i] in {"--file", "-f"}:
            i += 2
        elif args[i] in CONFIG_MODIFIERS or args[i].startswith(("--type=", "--file=")):
            i += 1
        else:
            break
    return False


def config_command(words: list[str]) -> bool:
    index = _payload_command_index(words)
    names = words if words[:1] and words[0] in {"export", "declare", "local", "readonly", "typeset", "unset"} else words[:index]
    if any(word.partition("=")[0].startswith("GIT_CONFIG") for word in names):
        return True
    if index >= len(words) or _basename(words[index]) != "git":
        return False
    args = words[index + 1:]
    _, sub = _git_base(args)
    return any(w == "-c" or w.startswith("--config-env") for w in args[:sub]) or (
        args[sub:sub + 1] == ["config"] and not _config_reads(args[sub + 1:])
    )


def config_path(path: str) -> bool:
    parts = Path(path).parts
    return ".git" in parts or parts[-1:] == (".gitattributes",) or parts[-2:] == ("info", "attributes")


def _git_query(args: list[str]) -> bool:
    match args:
        case ["rev-parse" | "ls-remote" | "show" | "cat-file" | "show-ref" | "for-each-ref" | "merge-base", *_]:
            return True
        case ["branch", "--show-current"] | ["worktree", "list"] | ["worktree", "list", "--porcelain"]:
            return True
        case ["config", *rest]:
            return _config_reads(rest)
        case ["remote"]:
            return True
        case ["diff", "--quiet", revision, "--", *paths]:
            return bool(paths) and not revision.startswith("-")
        case [verb, *args] if verb in QUERY_FLAGS:
            return all(arg in QUERY_FLAGS[verb] or not arg.startswith(("-", "$", "`")) for arg in args)
        case ["remote", "get-url", *args]:
            return all(not arg.startswith("-") or arg in {"--push", "--all"} for arg in args)
    return False


def read_only_git_metadata(words: list[str], variables: dict[str, str]) -> bool:
    if any(git_environment(name) or name == "PATH" for name in variables):
        return False
    if words[:3] in (["git", "--no-optional-locks", "status"], ["GIT_OPTIONAL_LOCKS=0", "git", "status"]):
        options = words[3:words.index("--", 3)] if "--" in words[3:] else words[3:]
        return all(option in STATUS_METADATA_FLAGS for option in options)
    if words[:1] != ["git"]:
        return False
    args = words[1:]
    _, sub = _git_base(args)
    if sub and not (sub == 2 and args[0] == "-C"):
        return False
    query = args[sub:]
    if query[:1] in (["status"], ["diff"], ["log"], ["show"]):
        return not written_paths(query[0], query[1:]) and not {"--ext-diff", "--textconv"}.intersection(query)
    if query[:1] in (["branch"], ["ls-files"]):
        return _git_query(query)
    return query in (
        ["rev-parse", "HEAD"],
        ["rev-parse", "--path-format=absolute", "--git-dir"],
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
    )


def changes_files(words: list[str]) -> bool:
    index = _payload_command_index(words)
    if index >= len(words):
        return False
    name = words[index] if words[index] in SCRIPT_READERS else _basename(words[index])
    if name in FLAG_WRITERS:
        return True
    if name == "read" or (name == "for" and words[index + 2:index + 3] == ["in"]):
        return False
    return name not in READ_ONLY_COMMANDS and not _syntax_check(name, words[index + 1:])


def operand_writes(words: list[str], cwd: Path, variables: dict[str, str], configured: bool, prior_writes: bool) -> list[str]:
    index = _payload_command_index(words)
    name, args = _basename(words[index]), words[index + 1:]
    fallback = [word for word in args if not word.startswith("-")]
    if name in FLAG_WRITERS:
        found = flag_targets(name, args, lambda word: None if is_dynamic(word) else word)
        return found.paths + (fallback if found.unknown else [])
    if name == "git":
        env = variables | dict(w.split("=", 1) for w in words[:index] if "=" in w)
        _, sub = _git_base(args)
        unsafe = configured or prior_writes and _git_query(args[sub:])
        unsafe = unsafe or any(git_environment(k) and k not in GIT_STORAGE_ENV for k in env)
        return _git_write_operands(args, cwd, fallback, env, unsafe)
    if name == "find":
        return _find_write_operands(args, fallback)
    if name == "rsync":
        options = [w for w in fallback[:-2] if not re.fullmatch(r"\$\{\w+\[@\]\}", w)]
        return options + fallback[-2:]
    if name in {"cp", "mv", "ln"} and all(
        not word.startswith(("-", "$")) or word in COPY_OPTIONS or word.startswith("--target-directory=") for word in args
    ):
        return _copy_targets(name, args, _target_slot(args), str(cwd))
    if name in DESTINATION_ALL_ARGS_VERBS or name == "dd":
        return [args[index][len(prefix):] for index, prefix in _destination_slots(name, args)]
    if name in TAR_VERBS:
        return _tar_write_operands(args, fallback)
    return fallback


def _find_write_operands(args: list[str], fallback: list[str]) -> list[str]:
    if any(re.fullmatch(r"\$\w+|\$\{\w+\}", arg) for arg in args):
        return fallback
    start = next((i for i, a in enumerate(args) if a not in {"-H", "-L", "-P", "--"}), len(args))
    roots = list(takewhile(lambda a: not a.startswith("-") and a not in {"!", "("}, args[start:])) or ["."]
    found = roots.copy() if "-delete" in args else []
    for i, arg in enumerate(args):
        if arg in FIND_FILE_OUTPUTS:
            found.extend(args[i + 1:i + 2])
        if arg in FIND_CALLBACKS:
            call = list(takewhile(lambda w: w not in {";", "+"}, args[i + 1:]))
            if call[:1] and call[0] in {"echo", "printf", "cat", "true", "false"}:
                continue
            if not call or call[0] not in DESTINATION_ALL_ARGS_VERBS | {"mkdir", "cp", "mv", "ln"}:
                return fallback
            found.extend(w for w in call[1:] if not w.startswith("-") and "{}" not in w)
            if arg.endswith("dir") or any("{}" in w for w in call):
                found.extend(roots)
    return found


def _git_locations(args: list[str], cwd: Path) -> tuple[Path, Path]:
    directory, tree, i = cwd, None, 0
    while i < len(args):
        flag, sep, inline = args[i].partition("=")
        value = inline if sep else next(iter(args[i + 1:i + 2]), "")
        if flag == "-C":
            directory /= value
        elif flag.startswith("-C"):
            directory /= flag[2:]
        if flag == "--work-tree":
            tree = value
        i += 2 if not sep and flag in GIT_VALUE_FLAGS else 1
    return directory, directory / (tree or ".")


def _index_updates(rest: list[str]) -> bool:
    return any(w.startswith("-") and not w.startswith("--") and "u" in w for w in rest)


def _worktree_add_paths(rest: list[str], tree: Path) -> list[str]:
    i = 1
    while i < len(rest):
        word = rest[i]
        if word == "--":
            i += 1
            break
        if not word.startswith("-"):
            break
        i += 2 if word in {"-b", "-B", "--reason"} else 1
    return [str(tree / rest[i])] if i < len(rest) else []


def _git_write_operands(args: list[str], cwd: Path, fallback: list[str], env: dict[str, str], unsafe: bool) -> list[str]:
    _, index = _git_base(args)
    if index >= len(args):
        return fallback
    directory, tree = _git_locations(args[:index], cwd)
    verb, rest = args[index], args[index + 1:]
    outputs = [str(directory / path) for path in written_paths(verb, rest)]
    if verb == "archive":
        outputs.extend(str(directory / w[2:]) for w in rest if w.startswith("-o") and len(w) > 2)
    if verb in {"read-tree", "update-index", "add", "write-tree"} and env.get("GIT_INDEX_FILE"):
        outputs.append(str(directory / env["GIT_INDEX_FILE"]))
    if verb == "read-tree":
        outputs.extend(str(directory / w.split("=", 1)[1]) for w in rest if w.startswith("--index-output="))
        outputs.extend(str(directory / after) for flag, after in zip(rest, rest[1:]) if flag == "--index-output")
    options = rest[:rest.index("--")] if "--" in rest else rest
    opaque = verb in {"archive", "show", "read-tree"} and any(re.fullmatch(r"\$\w+|\$\{\w+\}", w) for w in options)
    if unsafe or opaque or any(w in {"--ext-diff", "--textconv", "--filters"} for w in options):
        if verb == "add" and any(w == "--work-tree" or w.startswith("--work-tree=") for w in args[:index]):
            outputs.extend(w for w in args[:index] if not w.startswith("-"))
        else:
            outputs.extend(fallback)
        outputs.extend(str(tree / w) for w in rest if not w.startswith("-"))
    if verb in METADATA_COMMANDS or _git_query([verb, *rest]) or verb == "read-tree" and not _index_updates(rest):
        return outputs
    if verb not in GIT_CHANGING_COMMANDS:
        return outputs + fallback
    return outputs + _worktree_operands(verb, rest, tree)


def _worktree_operands(verb: str, rest: list[str], tree: Path) -> list[str]:
    if verb == "worktree" and rest[:1] == ["add"]:
        return _worktree_add_paths(rest, tree)
    targets = _git_targets([verb, *rest], _GitSource(True, None, str(tree)))
    if targets.unresolved or targets.archive or targets.worktree is not None or verb == "read-tree" and _index_updates(rest):
        return [str(tree)]
    paths = list(targets.paths)
    if targets.lookup:
        paths.extend(targets.lookup.pathspecs)
    if not paths:
        paths.extend(w for w in rest if not w.startswith("-"))
    return [str(tree / w) for w in paths]


def _tar_write_operands(args: list[str], fallback: list[str]) -> list[str]:
    if _tar_extracts(args) and (archive := tar_read(args, STDIN)) is not None:
        return [archive.directory or "."]
    if args[:1] == ["-cf"] and len(args) >= 2:
        members = args[4:] if args[2:3] == ["-C"] else args[2:]
        if members and all(not member.startswith(("-", "$")) for member in members):
            return [] if args[1] == "-" else [args[1]]
    return fallback


def _syntax_check(name: str, args: list[str]) -> bool:
    options = list(takewhile(lambda arg: arg.startswith("-") and arg != "-", args))
    if name in SYNTAX_CHECK_SHELLS:
        return any(not option.startswith("--") and "n" in option[1:] for option in options)
    return name in NODE_NAMES and any(option in NODE_CHECK_FLAGS for option in options)
