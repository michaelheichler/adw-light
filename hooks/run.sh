#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
python=$("$root/hooks/resolve-python.sh" "$(cat "$root/.python-version")") || exit 2
host=claude
if [ "${ADW_CODEX_HOOK:-}" = 1 ]; then host=codex; fi
export CLAUDE_PLUGIN_ROOT="$root"
exec "$python" -I -S -B "$root/adw_light.py" "$1" --host "$host" --state-dir "${ADW_STATE_ROOT:-$HOME/.adw-lite/state}"
