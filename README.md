# ADW Lite

ADW Lite checks code, comments and prose through Claude and Codex hooks.

ADW Lite reports existing wording tests without blocking test work. It blocks new or changed wording assertions from every agent, including the test writer.

## Project configuration

In Claude Code, type the command at the start of a new prompt.

~~~text
/adw-light:config rule test_builds_repo enforce
/adw-light:config tests allow --for 30m
~~~

In Codex, send the whole request as a separate prompt.

~~~text
$adw-config rule test_builds_repo enforce
$adw-config tests allow --for 30m
~~~

Choose `off`, `observe`, or `enforce` for an editable rule. A test window accepts whole minutes or hours and lasts at most eight hours.

Use `contract add STRING` or `contract remove STRING` with either command prefix to manage expected strings that another program reads. Type the string without shell quotes. Only an exact match skips the wording assertion check. Case and spacing must match, and the setting does not exempt log assertions or exception message matches.

The hook updates the nearest `.agent-discipline.json` in the current folder or its parents. If none exists, it creates one at the Git worktree root. Outside Git, it uses the current folder.

Agents cannot create a governing project config or change its enforcement settings. An edit that keeps those settings unchanged can still pass.

Both checks start disabled in other projects. This repository blocks tests that build Git repositories and reports tests that build temporary trees.

The state guard checks resolved paths under `~/.adw-lite`, including paths that follow symlinks.
It also protects the active project config. Names inside scratch folders do not make them protected.

## Install from GitHub

Replace vX with a published tag such as v0.1.2.

~~~sh
claude plugin marketplace add michaelheichler/adw-light#vX --scope user
claude plugin install adw-light@adw-light --scope user
codex plugin marketplace add michaelheichler/adw-light --ref vX
codex plugin add adw-light@adw-light --json
~~~

Codex reports the installed cache path. To enable its hooks, use that path in place of CACHE below.

~~~sh
python3 -IB CACHE/hooks/merge-codex-hooks.py --hooks-json "${CODEX_HOME:-$HOME/.codex}/hooks.json" --skill-dir CACHE
python3 -IB CACHE/hooks/merge-codex-hooks.py --hooks-json "${CODEX_HOME:-$HOME/.codex}/hooks.json" --skill-dir CACHE --remove-legacy
~~~
