#!/usr/bin/env bash
set -euo pipefail
unset CDPATH
dev=$(cd "$(dirname "$0")/.." && pwd -P)
fail() { printf '%s\n' "$*" >&2; exit 1; }
host=${ADW_DEPLOY_HOST:-}
retire=()
while (( $# )); do
  case $1 in
    --host) [[ -n ${2:-} ]] || fail 'Name an ssh target after --host.'; host=$2; shift ;;
    --retire) [[ -n ${2:-} ]] || fail 'Name a folder on the remote machine after --retire.'; retire+=("$2"); shift ;;
    *) fail "Unknown argument: $1. Run without arguments for this machine, or add --host TARGET and optional --retire FOLDER for a remote machine." ;;
  esac
  shift
done
[[ -n $host || ${#retire[@]} -eq 0 ]] || fail 'Give --host with --retire, because old copies are retired only on the remote machine.'
git_dir=$(git --git-dir="$dev/.git" --work-tree="$dev" rev-parse --path-format=absolute --git-dir)
common_dir=$(git --git-dir="$dev/.git" --work-tree="$dev" rev-parse --path-format=absolute --git-common-dir)
[[ $git_dir == "$common_dir" ]] || fail "Deploy from the main checkout, not the linked worktree $dev."
changes=$(git --git-dir="$dev/.git" --work-tree="$dev" status --porcelain --untracked-files=all)
[[ -z $changes ]] || fail "Commit every change in $dev before deploying, so the release matches main."
work=$(mktemp -d "${TMPDIR:-/tmp}/adw-deploy.XXXXXX")
release=$work/release
config_dir=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
codex_home=${CODEX_HOME:-$HOME/.codex}
id=adw-light@adw-light
install_root=$(cd "$HOME" && pwd -P)/.adw-lite/install
install_source=$install_root/source
layer_parts=(venv setup.json)
codex_cache=$codex_home/plugins/cache/adw-light
files=("$config_dir/settings.json" "$config_dir/plugins/installed_plugins.json" "$config_dir/plugins/known_marketplaces.json" "$codex_home/hooks.json" "$codex_home/config.toml")
trap 'rm -rf "$work"' EXIT
(cd "$dev" && python3 -m unittest discover -s tests)
ADW_RELEASE="$release" bash "$dev/tools/build_release.sh"
mkdir "$work/smoke-home"
HOME="$work/smoke-home" python3 "$dev/tools/smoke_probe.py" "$release"
previous=$(claude plugin marketplace list --json | jq -er '[.[] | select(.name == "adw-light")] | if length == 0 then "" elif length == 1 and .[0].source == "directory" then .[0].path else error("Unexpected ADW marketplace") end')
old_cache=$(claude plugin list --json | jq -r --arg id "$id" '[.[] | select(.id == $id and .scope == "user" and .enabled == true)] | if length == 1 then .[0].installPath else "" end')
if [[ -n $old_cache ]]; then
  cp -Rp "$old_cache" "$work/old-cache"
fi
for i in "${!files[@]}"; do
  if [[ -f ${files[i]} ]]; then cp -p "${files[i]}" "$work/file-$i"; fi
done
if [[ -d $install_source ]]; then cp -Rp "$install_source" "$work/old-source"; fi
if [[ -d $codex_cache ]]; then cp -Rp "$codex_cache" "$work/old-codex-cache"; fi
mkdir "$work/old-layer"
for part in "${layer_parts[@]}"; do
  if [[ -e $install_root/$part ]]; then cp -Rp "$install_root/$part" "$work/old-layer/$part"; fi
done
restore_layer() {
  local part failed=0
  for part in "${layer_parts[@]}"; do
    rm -rf "${install_root:?}/$part" || failed=1
    if [[ -e $work/old-layer/$part ]]; then cp -Rp "$work/old-layer/$part" "$install_root/$part" || failed=1; fi
  done
  return "$failed"
}
restore_marketplace() {
  if [[ -n $previous ]]; then
    claude plugin marketplace add "$previous" --scope user
  else
    claude plugin uninstall "$id" --scope user || true
    claude plugin marketplace remove adw-light || true
  fi
}
restore_files() {
  local i failed=0
  for i in "${!files[@]}"; do
    if [[ -f $work/file-$i ]]; then
      cp -p "$work/file-$i" "${files[i]}" || failed=1
    else
      rm -f "${files[i]}" || failed=1
    fi
  done
  return "$failed"
}
restore_folder() {
  rm -rf "$2" || return 1
  if [[ -d $1 ]]; then
    mkdir -p "$(dirname "$2")" && cp -Rp "$1" "$2"
  fi
}
restore_codex_cache() {
  local version
  restore_folder "$work/old-codex-cache" "$codex_cache" || return 1
  for version in "$codex_cache"/adw-light/*/; do
    [[ -d $version ]] || continue
    python3 "$dev/tools/smoke_probe.py" "${version%/}" || return 1
  done
}
restore_cache() {
  if ! diff -r "$work/old-cache" "$old_cache" >/dev/null 2>&1; then
    rm -rf "$old_cache" && mkdir -p "$(dirname "$old_cache")" && cp -Rp "$work/old-cache" "$old_cache" || return 1
  fi
  python3 "$dev/tools/smoke_probe.py" "$old_cache"
}
rollback() {
  local status=$? failed=0
  trap - EXIT
  if (( status != 0 )) && [[ -f $work/plugin-ready ]]; then
    restore_folder "$work/old-source" "$install_source" || failed=1
    restore_layer || failed=1
    restore_marketplace || failed=1
    restore_files || failed=1
    restore_codex_cache || failed=1
    if [[ -n $old_cache ]]; then
      restore_cache || failed=1
    fi
    if (( failed )); then
      printf 'Restore failed. Backup remains at %s\n' "$work" >&2
      exit 1
    fi
  fi
  rm -rf "$work"
  exit "$status"
}
trap rollback EXIT
touch "$work/plugin-ready"
bash "$dev/tools/install_layers.sh" --claude --codex "$release"
trap 'rm -rf "$work"' EXIT
deploy_remote() {
  local stage quoted_stage folder options= keep_out=
  stage=$(ssh -o BatchMode=yes "$host" 'mkdir -p "$HOME/.adw-lite/install" && mktemp -d "$HOME/.adw-lite/install/.stage.XXXXXX"' | tail -n 1)
  [[ $stage =~ ^/[[:alnum:]._/-]+/\.adw-lite/install/\.stage\.[[:alnum:]]+$ ]] || fail "Unexpected staging folder on $host: $stage"
  quoted_stage=$(printf '%q' "$stage")
  rsync -a -e 'ssh -o BatchMode=yes' "$release/" "$host:$stage/release/"
  rsync -a -e 'ssh -o BatchMode=yes' "$dev/tools/install_layers.sh" "$dev/tools/smoke_probe.py" "$host:$stage/tools/"
  for folder in ${retire[@]+"${retire[@]}"}; do options+=" --retire $(printf '%q' "$folder")"; done
  if [[ -n ${ADW_KEEP_OUT:-} ]]; then keep_out="ADW_KEEP_OUT=$(printf '%q' "$ADW_KEEP_OUT") "; fi
  ssh -o BatchMode=yes "$host" "${keep_out}bash $quoted_stage/tools/install_layers.sh --claude --codex$options $quoted_stage/release; rc=\$?; rm -rf $quoted_stage; exit \$rc"
}
if [[ -n $host ]]; then
  deploy_remote
fi
printf 'Installed ADW Lite through the Claude and Codex plugin layers%s.\n' "${host:+ here and on $host}"
