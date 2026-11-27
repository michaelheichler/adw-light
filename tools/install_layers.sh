#!/usr/bin/env bash
set -euo pipefail
unset CDPATH
fail() { printf '%s\n' "$*" >&2; exit 1; }
tools=$(cd "$(dirname "$0")" && pwd -P)
id=adw-light@adw-light
claude_layer=no codex_layer=no repo=michaelheichler/adw-light tag=
while (( $# )); do
  case $1 in
    --claude) claude_layer=yes ;;
    --codex) codex_layer=yes ;;
    --repo) repo=${2:?Give a GitHub owner/repo.}; shift ;;
    --tag) tag=${2:?Give a release tag.}; shift ;;
    *) fail "Unknown option $1" ;;
  esac
  shift
done
[[ $repo =~ ^[a-zA-Z0-9][a-zA-Z0-9-]*/[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || fail 'Give a GitHub owner/repo.'
[[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || fail 'Give a release tag such as v0.1.2.'
[[ ! -e $repo ]] || fail 'The repository name also names a local path.'
[[ $claude_layer == yes || $codex_layer == yes ]] || fail 'Choose --claude, --codex, or both.'
home=$(cd "$HOME" && pwd -P)
config_dir=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
codex_home=${CODEX_HOME:-$HOME/.codex}
install=$home/.adw-lite/install
keep_out=()
IFS=: read -r -a listed_keep_out <<<"${ADW_KEEP_OUT:-}"
for folder in ${listed_keep_out[@]+"${listed_keep_out[@]}"}; do
  [[ -n $folder ]] || continue
  keep_out+=("$folder")
  if [[ -d $folder ]]; then keep_out+=("$(cd "$folder" && pwd -P)"); fi
done
within() { case "$1/" in "$2/"*) return 0 ;; esac; return 1; }
smoke_grammar=go
setup_wait_rounds=360
smoke() (cd "$tools/.." && PYTHONDONTWRITEBYTECODE=1 "$python" tools/smoke_probe.py "$1")
lexer_smoke() {
  local bare runner status=0
  runner=$(newest_python) || runner=$python
  bare=$(mktemp -d "${TMPDIR:-/tmp}/adw-smoke-home.XXXXXX")
  (cd "$tools/.." && HOME=$bare PYTHONDONTWRITEBYTECODE=1 "$runner" tools/smoke_probe.py "$1") || status=$?
  rm -rf "$bare"
  return "$status"
}
newest_python() {
  local name path version best= best_version=0
  if [[ -n ${ADW_PYTHON:-} ]]; then printf '%s\n' "$ADW_PYTHON"; return 0; fi
  for name in python3 python3.{11..20}; do
    path=$(command -v "$name") || continue
    version=$("$path" -I -S -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1] if sys.prefix == sys.base_prefix else 0)' 2>/dev/null) || continue
    if (( version >= 311 && version > best_version )); then best=$path best_version=$version; fi
  done
  [[ -n $best ]] && printf '%s\n' "$best"
}
wait_for_setup() {
  local lock=$install/setup.lock owner round
  for (( round = 0; round < setup_wait_rounds; round++ )); do
    [[ -e $lock ]] || return 0
    owner=$(cat "$lock" 2>/dev/null) || return 0
    [[ $owner =~ ^[0-9]+$ ]] && kill -0 "$owner" 2>/dev/null || return 0
    sleep 5
  done
}
build_layer() {
  local runner log=$install/setup.log
  mkdir -p "$install"
  if ! runner=$(newest_python); then
    printf 'No Python 3.11 or newer was found, so the ADW Lite hooks use the lexer.\n' >&2
    return 0
  fi
  wait_for_setup
  if ! "$runner" -I -S -B "$1/engine/setup_env.py" --language "$smoke_grammar" --log "$log"; then
    printf 'ADW Lite could not build its language environment from %s, so the hooks use the lexer. Details: %s\n' "$1" "$log" >&2
  fi
}
keep_layer() {
  local part
  mkdir -p "$install"
  layer_backup=$(mktemp -d "$install/.layer.XXXXXX")
  for part in venv setup.json; do
    if [[ -e $install/$part ]]; then cp -Rp "$install/$part" "$layer_backup/$part"; fi
  done
}
restore_layer() {
  local part
  wait_for_setup
  for part in venv setup.json; do
    rm -rf "${install:?}/$part"
    if [[ -e $layer_backup/$part ]]; then mv "$layer_backup/$part" "$install/$part"; fi
  done
  rm -rf "$layer_backup"
}
outside_keep_out() {
  local folder
  for folder in ${keep_out[@]+"${keep_out[@]}"}; do
    if within "$1" "$folder"; then fail "$2 runs from $1, inside $folder, which ADW_KEEP_OUT names."; fi
  done
}
claude_cache() {
  claude plugin list --json | jq -er --arg id "$id" '[.[] | select(.id == $id and .scope == "user")] | if length == 1 and .[0].enabled == true then .[0].installPath else error("Check the enabled user plugin \($id)") end'
}
install_claude() {
  local listed cache
  listed=$(claude plugin marketplace list --json | jq -er '[.[] | select(.name == "adw-light")] | length')
  if (( listed )); then claude plugin marketplace remove adw-light; fi
  claude plugin marketplace add "$repo#$tag" --scope user
  claude plugin install "$id" --scope user
  cache=$(claude_cache)
  cache=$(cd "$cache" && pwd -P)
  case "$cache/" in "$(cd "$config_dir" && pwd -P)/plugins/cache/adw-light/adw-light/"?*) ;; *) fail "Unexpected Claude cache $cache" ;; esac
  check_cache "$cache" Claude
  if [[ -f $config_dir/settings.json ]]; then
    "$python" -IB "$cache/hooks/merge-claude-settings.py" --settings "$config_dir/settings.json" --skill-dir "$cache" --strip-only
  fi
}
install_codex() {
  local listed cache hooks=$codex_home/hooks.json
  listed=$(codex plugin marketplace list --json | jq -er '[.marketplaces[] | select(.name == "adw-light")] | length')
  if (( listed )); then codex plugin marketplace remove adw-light; fi
  codex plugin marketplace add "$repo" --ref "$tag" --json >/dev/null
  cache=$(codex plugin add "$id" --json | jq -er '.installedPath')
  cache=$(cd "$cache" && pwd -P)
  case "$cache/" in "$(cd "$codex_home" && pwd -P)/plugins/cache/adw-light/adw-light/"?*) ;; *) fail "Unexpected Codex plugin cache: $cache" ;; esac
  check_cache "$cache" Codex
  "$python" -IB "$cache/hooks/merge-codex-hooks.py" --hooks-json "$hooks" --skill-dir "$cache"
  "$python" -IB "$cache/hooks/merge-codex-hooks.py" --hooks-json "$hooks" --skill-dir "$cache" --remove-legacy
}
check_cache() {
  outside_keep_out "$1" "$2"
  python=$("$1/hooks/resolve-python.sh" "$(cat "$1/.python-version")")
  lexer_smoke "$1"
  if [[ $2 == Claude || $claude_layer == no ]]; then build_layer "$1"; fi
  smoke "$1"
}
git ls-remote --exit-code --refs "https://github.com/$repo.git" "refs/tags/$tag" >/dev/null || { echo "Cannot reach $tag on GitHub." >&2; exit 69; }
keep_layer
trap 'rc=$?; if (( rc )); then restore_layer; exit 1; else rm -rf "$layer_backup"; fi' EXIT
if [[ $claude_layer == yes ]]; then install_claude; fi
if [[ $codex_layer == yes ]]; then install_codex; fi
