#!/bin/sh
set -eu
floor=$1
shift
set -- "$HOME/.adw-lite/install/venv/bin/python" python3 python3.15 python3.14 python3.13 python3.12 python3.11 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3
if [ -n "${ADW_PYTHON:-}" ]; then set -- "$ADW_PYTHON"; fi
for candidate do
    if "$candidate" -I -S -B -c 'import sys; sys.exit(sys.version_info[:2] < tuple(map(int, sys.argv[1].split("."))))' "$floor" 2>/dev/null; then
        command -v "$candidate"
        exit 0
    fi
done
printf 'ADW Lite needs Python %s or newer. Check ADW_PYTHON.
' "$floor" >&2
exit 1
