# Commit gate reading

The commit gate runs on every Bash command. It reads the command, its functions and aliases, and every script the command runs or sources, to find a hidden git commit. When it finds one, it scans the staged files and the commit message. When it cannot read a part, it refuses the command, so it fails closed.

## What the gate reads

### Commands and scripts

- Shell payloads from `bash -c`, `eval`, `trap`, here-strings, and pipes into a shell.
- Scripts that a command runs with bash or sh, or runs directly, and files it sources.

### Arguments and functions

- Function bodies, with the call's arguments put in place of `$1` to `$9`, `$@` and `$*`.
- An unconditional shift, which drops leading arguments for the rest of the body. A shift that runs on every path through an if branch or a case arm counts for the rest of that branch.
- `$0` in a script that bash runs, including inside its functions.
- A script that runs itself again in a new process. The gate skips the exact repeat and then reads the whole command once more with every written path marked.
- Every body of a function that has several readable definitions, when all bodies leave the same shell state behind.
- The `${1:-word}` family of argument forms.

### Paths and loops

- The usual script-folder idioms, such as `SCRIPT_DIR` from `cd` and `pwd` on `dirname "$0"` or `BASH_SOURCE`.
- A shell that a command runner starts, such as `xargs sh -c` or `find -exec sh -c`.

## What the gate refuses

- A script path or program name that only exists at run time, such as a path built from CLAUDE_PLUGIN_ROOT or a session id.
- A script that the same command changes before it runs it.
- Shell nesting deeper than 10 levels, or a loop of scripts that does not settle within 3 rounds.
- A command that takes more than 150,000 steps or 8 seconds to read, because the hook times out at 10 seconds.
- A program word that still holds an argument such as `"$@"` after a shift inside an if, so the gate cannot resolve it.
- A function that calls itself in one process.
- A function whose readable definitions leave different shell state behind.

## Known gaps

QA round 1 on 2026-11-24 confirmed these gaps with real shell runs. They also exist in the release before this one, and the owner decides on them separately.

### Hidden commands, highest risk first

- A program word that comes from a command substitution, such as `$(echo git commit -m x)`, hides a commit.
- A command substitution inside an unquoted heredoc body runs unseen.
- Some runners still hide a commit. Examples are `xargs ./s.sh`, `find -exec ./s.sh`, `xargs -I% sh -c %`, `echo commit | xargs git`, awk `system()`, and `perl -e 'system(...)'`.

### Arguments

- The gate leaves `${10}` and higher unresolved.
- A function that changes the folder with `pushd`, and `cd -` or a bare `cd`, can point the gate at the wrong folder.
- Text piped into a shell from `tr` or `head`, or stdin that a script passes on to a shell, runs unseen.
- A script started by name from PATH or through `hash -p` is not read.

### Shell dialect and paths

- The Bash tool runs zsh, and the gate does not model zsh-only syntax. Examples are `=git`, `repeat 1`, `always` blocks, glob qualifiers, `${(e)x}` and `$=1`.
- A quoted heredoc delimiter such as `<<E'X'` confuses the reader.
- An exported `CDPATH` that starts with `.` makes `cd` print the folder, so a script-folder idiom without `CDPATH=''` can point elsewhere.

## Changes on 2026-11-24

Meridian helper scripts that never run git commit hit these false positives, and this release fixes them.

### Misreadings

1. jq counted as a file writer. The jq filter `.` then marked the working folder as changed, so the next sourced script looked stale.
2. The function recursion check crossed process boundaries. A main function in a child bash script looked like a recursive call of the parent's main.
3. The gate ignored shift. After `shift` and `command "$@"`, it read the first argument as the program, which marked the script argument as changed. The same gap hid a real commit in `f() { shift; "$@"; }; f x git commit`.
4. The gate mangled `${1:-}` into `value:-}`.
5. The gate missed several script-folder idioms. Examples are an inline cd on `${BASH_SOURCE[0]%/*}`, a pwd fallback, and an if-not guard. Others are a trailing return guard and cd on a literal path.
6. The segmenter read arithmetic, case patterns, arrays, [[ ]] tests, and escaped punctuation as commands. Words such as `/`, `..`, `~` and `*/..` then marked parent folders as changed. Escaped punctuation also hid a real commit in find with an escaped group and -exec git commit.
7. A command runner that starts a shell hid a real commit, as in `xargs sh -c "git commit"`.
### Design changes approved by the owner

8. A Meridian script that runs itself again through `bash "$0"` hit the nesting limit. The gate now resolves `$0` and skips the exact repeat.
9. Real call chains deeper than 6 levels hit the old limit, so the limit is now 10.
10. The gate refused a fallback stub such as `count_complete_summaries() { echo 0; }` next to the real function as two definitions.
11. A script run with no arguments kept `${1:-default}` unresolved, and the gate recorded the program word itself as a written file.

Before release, two QA rounds found bypasses in the new code, and the fixes close every one. The worst were values inside single quotes that eval splits again, and a shift inside an if followed by `"$@"`. Others were Unicode digits as shift counts, `set --`, a `cd` through a symlink with `..`, `BASH_SOURCE` and `$0` under zsh, and an exponential runner walk.
