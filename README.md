# ADW Lite

ADW Lite checks code, comments and prose through Claude and Codex hooks.

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
