#!/usr/bin/env bash
set -euo pipefail
unset CDPATH
fail() { printf '%s\n' "$*" >&2; exit 1; }
tools=$(cd "$(dirname "$0")" && pwd -P)
id=adw-light@adw-light
claude_layer=no codex_layer=no release= retire=()
while (( $# )); do
  case $1 in
    --claude) claude_layer=yes ;;
    --codex) codex_layer=yes ;;
    --retire) [[ -n ${2:-} ]] || fail 'Name a folder after --retire.'; retire+=("$2"); shift ;;
    -*) fail "Unknown option: $1" ;;
    *) [[ -z $release ]] || fail 'Give exactly one release folder.'; release=$1 ;;
  esac
  shift
done
[[ -n $release && -f $release/adw_light.py && -f $release/.claude-plugin/plugin.json ]] || fail 'Give a built release folder.'
[[ $claude_layer == yes || $codex_layer == yes ]] || fail 'Choose --claude, --codex, or both.'
home=$(cd "$HOME" && pwd -P)
release=$(cd "$release" && pwd -P)
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
holds_adw_copy() { [[ -f $1/adw_light.py ]] || compgen -G "$1/*/adw_light.py" >/dev/null; }
physical_path() { if [[ -d $1 ]]; then (cd "$1" && pwd -P); else printf '%s\n' "$1"; fi; }
resolved_config_dir=$(physical_path "$config_dir")
resolved_codex_home=$(physical_path "$codex_home")
resolved=()
for folder in ${retire[@]+"${retire[@]}"}; do
  [[ -d $folder ]] || continue
  folder=$(cd "$(dirname "$folder")" && pwd -P)/$(basename "$folder")
  resolved+=("$folder")
  case "$(basename "$folder")" in
    adw-light|adw-light-plugin|adw-light-dev) ;;
    *) fail "Only a folder named adw-light, adw-light-plugin, or adw-light-dev can be retired: $folder" ;;
  esac
  holds_adw_copy "$folder" || fail "Only a folder that holds an ADW Lite copy can be retired: $folder"
  [[ ! -e $folder/.git ]] || fail "Refusing to retire $folder, because it holds a .git entry and looks like a source checkout."
  for guarded in "$home/.adw-lite" "$config_dir" "$codex_home" "$resolved_config_dir" "$resolved_codex_home" "$release"; do
    if within "$folder" "$guarded" || within "$guarded" "$folder"; then fail "Retiring $folder would remove $guarded."; fi
  done
done
python=$("$release/hooks/resolve-python.sh" "$(cat "$release/.python-version")")
source_dir=$install/source
smoke_grammar=go
setup_wait_rounds=360
smoke() { PYTHONDONTWRITEBYTECODE=1 "$python" "$tools/smoke_probe.py" "$1"; }
lexer_smoke() {
  local bare runner status=0
  runner=$(newest_python) || runner=$python
  bare=$(mktemp -d "${TMPDIR:-/tmp}/adw-smoke-home.XXXXXX")
  HOME=$bare PYTHONDONTWRITEBYTECODE=1 "$runner" "$tools/smoke_probe.py" "$1" || status=$?
  rm -rf "$bare"
  return "$status"
}
newest_python() {
  local name path version best= best_version=0
  if [[ -n ${ADW_PYTHON:-} ]]; then printf '%s\n' "$ADW_PYTHON"; return 0; fi
  for name in python3 python3.{11..20} /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
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
  if ! "$runner" -I -S -B "$1/engine/setup_env.py" --language "$smoke_grammar" >>"$log" 2>&1; then
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
same_runtime() {
  local part
  for part in adw_light.py engine hooks skills .python-version; do
    diff -r --exclude __pycache__ --exclude .DS_Store "$source_dir/$part" "$1/$part" >/dev/null || return 1
  done
}
outside_keep_out() {
  local folder
  for folder in ${keep_out[@]+"${keep_out[@]}"}; do
    if within "$1" "$folder"; then fail "$2 runs from $1, inside $folder, which ADW_KEEP_OUT names."; fi
  done
}
place_release() {
  mkdir -p "$install"
  local incoming
  incoming=$(mktemp -d "$install/.incoming.XXXXXX")
  rsync -a --exclude __pycache__ "$release/" "$incoming/"
  rm -rf "$install/.previous"
  if [[ -d $source_dir ]]; then mv "$source_dir" "$install/.previous"; fi
  mv "$incoming" "$source_dir"
  rm -rf "$install/.previous"
}
claude_cache() {
  claude plugin list --json | jq -er --arg id "$id" '[.[] | select(.id == $id and .scope == "user")] | if length == 1 and .[0].enabled == true then .[0].installPath else error("Check the enabled user plugin \($id)") end'
}
install_claude() {
  local listed rows cache
  listed=$(claude plugin marketplace list --json | jq -r '[.[] | select(.name == "adw-light")] | if length == 0 then "" else (.[0].path // .[0].installLocation // "") end')
  if [[ $listed == "$source_dir" ]]; then
    claude plugin marketplace update adw-light
  else
    [[ -z $listed ]] || claude plugin marketplace remove adw-light
    claude plugin marketplace add "$source_dir" --scope user
  fi
  rows=$(claude plugin list --json | jq -r --arg id "$id" '[.[] | select(.id == $id and .scope == "user")] | length')
  if (( rows )); then claude plugin update "$id" --scope user; else claude plugin install "$id" --scope user; fi
  cache=$(claude_cache)
  case "$cache/" in "$config_dir/plugins/cache/adw-light/adw-light/"?*) ;; *) fail "Unexpected Claude plugin cache: $cache" ;; esac
  if ! same_runtime "$cache"; then
    claude plugin uninstall "$id" --scope user
    claude plugin install "$id" --scope user
    cache=$(claude_cache)
    same_runtime "$cache" || fail "Claude plugin cache $cache does not match the tested release."
  fi
  outside_keep_out "$cache" Claude
  build_layer "$cache"
  smoke "$cache"
  if [[ -f $config_dir/settings.json ]]; then
    "$python" -IB "$cache/hooks/merge-claude-settings.py" --settings "$config_dir/settings.json" --skill-dir "$cache" --strip-only
  fi
}
install_codex() {
  local listed cache hooks=$codex_home/hooks.json
  listed=$(codex plugin marketplace list --json | jq -r '[.marketplaces[]? | select(.name == "adw-light")] | if length == 0 then "" else .[0].root end')
  if [[ -n $listed && $listed != "$source_dir" ]]; then codex plugin marketplace remove adw-light; fi
  codex plugin marketplace add "$source_dir" --json >/dev/null
  cache=$(codex plugin add "$id" --json | jq -er '.installedPath')
  cache=$(cd "$cache" && pwd -P)
  case "$cache/" in "$(cd "$codex_home" && pwd -P)/plugins/cache/adw-light/adw-light/"?*) ;; *) fail "Unexpected Codex plugin cache: $cache" ;; esac
  same_runtime "$cache" || fail "Codex plugin cache $cache does not match the tested release."
  outside_keep_out "$cache" Codex
  if [[ $claude_layer == no ]]; then build_layer "$cache"; fi
  smoke "$cache"
  "$python" -IB "$cache/hooks/merge-codex-hooks.py" --hooks-json "$hooks" --skill-dir "$cache"
  "$python" -IB "$cache/hooks/merge-codex-hooks.py" --hooks-json "$hooks" --skill-dir "$cache" --remove-legacy
  local stale=(${keep_out[@]+"${keep_out[@]}"} ${resolved[@]+"${resolved[@]}"})
  (( ${#stale[@]} )) || return 0
  jq -e '[.hooks[]?[]?.hooks[]?.command | strings | select(test("adw[-_]light")) | select(. as $command | any($ARGS.positional[]; . as $folder | $command | contains($folder + "/")))] | length == 0' "$hooks" --args "${stale[@]}" >/dev/null \
    || fail "Codex hooks in $hooks still point into a retired or kept-out folder."
}
lexer_smoke "$release"
keep_layer
if ! { build_layer "$release" && smoke "$release"; }; then
  restore_layer
  fail "The release failed its smoke test after setup, so ADW Lite restored the previous language environment."
fi
rm -rf "$layer_backup"
place_release
if [[ $claude_layer == yes ]]; then install_claude; fi
if [[ $codex_layer == yes ]]; then install_codex; fi
for folder in ${resolved[@]+"${resolved[@]}"}; do
  rm -rf "$folder"
done
printf 'ADW Lite runs from the plugin layers. Release source: %s\n' "$source_dir"
