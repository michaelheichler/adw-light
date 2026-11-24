from __future__ import annotations

GIT_SUBCOMMANDS = frozenset((
    "add", "am", "annotate", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file", "check-attr",
    "check-ignore", "checkout", "cherry", "cherry-pick", "clean", "clone", "commit", "commit-tree", "config",
    "count-objects", "describe", "diff", "diff-files", "diff-index", "diff-tree", "difftool", "fetch",
    "for-each-ref", "format-patch", "fsck", "gc", "grep", "hash-object", "help", "init", "log", "ls-files",
    "ls-remote", "ls-tree", "maintenance", "merge", "merge-base", "mv", "notes", "prune", "pull", "push",
    "range-diff", "read-tree", "rebase", "reflog", "remote", "repack", "replace", "rerere", "reset", "restore",
    "rev-list", "rev-parse", "revert", "rm", "shortlog", "show", "show-ref", "sparse-checkout", "stage", "stash",
    "status", "submodule", "switch", "symbolic-ref", "tag", "update-index", "update-ref", "var", "version",
    "whatchanged", "worktree", "write-tree",
))
SHELL_BUILTINS = frozenset((
    "alias", "bg", "bind", "break", "builtin", "cd", "command", "continue", "declare", "dirs", "disown", "echo",
    "enable", "eval", "exec", "exit", "export", "fc", "fg", "getopts", "hash", "history", "jobs", "kill", "let",
    "local", "logout", "mapfile", "popd", "printf", "pushd", "pwd", "read", "readarray", "readonly", "return",
    "set", "shift", "shopt", "source", "suspend", "test", "times", "trap", "type", "typeset", "ulimit", "umask",
    "unalias", "unset", "wait",
))
SHELL_TOOLS = frozenset((
    "awk", "basename", "bash", "brew", "cargo", "cat", "chgrp", "chmod", "chown", "cmp", "comm", "cp", "curl",
    "cut", "date", "dd", "df", "diff", "dirname", "docker", "du", "env", "expand", "file", "find", "fold", "gawk",
    "git", "gofmt", "grep", "gunzip", "gzip", "head", "hostname", "install", "jq", "kubectl", "less", "ln", "ls",
    "make", "man", "mkdir", "mktemp", "more", "mv", "nl", "node", "npm", "npx", "nohup", "od", "paste", "patch",
    "pip", "pip3", "pnpm", "python", "python3", "readlink", "realpath", "rg", "rm", "rmdir", "rsync", "scp", "sed",
    "seq", "sh", "sha256sum", "shasum", "sleep", "sort", "split", "ssh", "stat", "sudo", "tail", "tar", "tee",
    "timeout", "touch", "tr", "true", "false", "uname", "uniq", "unzip", "uv", "wc", "wget", "which", "xargs",
    "yarn", "yes", "zip", "zsh",
))
COMMAND_WORDS = GIT_SUBCOMMANDS | SHELL_BUILTINS | SHELL_TOOLS


def command_word(word: str) -> bool:
    return word in COMMAND_WORDS


def command_phrase(phrase: str) -> bool:
    words = phrase.split()
    return bool(words) and all(command_word(word) for word in words)
