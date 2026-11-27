#!/usr/bin/env bash
set -euo pipefail
unset CDPATH
dev=$(cd "$(dirname "$0")/.." && pwd -P)
[[ $(pwd -P) == "$dev" ]] || { printf 'Run the release build from the repository root.\n' >&2; exit 1; }
release=${ADW_RELEASE:?Set ADW_RELEASE to a new folder outside the source tree, for example under the system temp folder.}
ref=${ADW_REF:-}
if [[ -e "$release" || -L "$release" ]]; then
  printf 'Choose a new release directory: %s\n' "$release" >&2
  exit 1
fi
parent=$(cd "$(dirname "$release")" && pwd -P)
release=$parent/$(basename "$release")
case "$parent/" in
  "$dev/"*)
    printf 'Build the release outside the source tree: %s\n' "$release" >&2
    exit 1
    ;;
esac
IFS=: read -r -a keep_out <<<"${ADW_KEEP_OUT:-}"
for folder in ${keep_out[@]+"${keep_out[@]}"}; do
  [[ -n $folder ]] || continue
  if [[ -d $folder ]]; then folder=$(cd "$folder" && pwd -P); fi
  case "$parent/" in
    "$folder/"*)
      printf 'Build the release outside %s, which ADW_KEEP_OUT names: %s\n' "$folder" "$release" >&2
      exit 1
      ;;
  esac
done
if [[ -n $ref ]]; then
  git --git-dir="$dev/.git" rev-parse --verify --quiet "$ref^{commit}" >/dev/null || { printf 'Unknown git ref: %s\n' "$ref" >&2; exit 1; }
fi
work=$(mktemp -d "$parent/.adw-build.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir "$work/archive" "$work/release"
if [[ -n $ref ]]; then
  snapshot=$ref
else
  objects=$(git --git-dir="$dev/.git" rev-parse --path-format=absolute --git-common-dir)/objects
  mkdir "$work/objects"
  export GIT_OBJECT_DIRECTORY="$work/objects" GIT_ALTERNATE_OBJECT_DIRECTORIES="$objects"
  GIT_INDEX_FILE="$work/index" git --git-dir="$dev/.git" --work-tree="$dev" add -A
  snapshot=$(GIT_INDEX_FILE="$work/index" git --git-dir="$dev/.git" --work-tree="$dev" write-tree)
fi
if [[ -n $ref ]]; then
  source=$(git --git-dir="$dev/.git" rev-parse "$ref^{commit}")
elif [[ -z $(git --git-dir="$dev/.git" --work-tree="$dev" status --porcelain --untracked-files=all) ]]; then
  source=$(git --git-dir="$dev/.git" rev-parse HEAD)
else
  source="uncommitted tree $snapshot"
fi
git --git-dir="$dev/.git" archive "$snapshot" | tar -xf - -C "$work/archive"
runtime=(--exclude=__pycache__ --exclude=.DS_Store --exclude=CLAUDE.md --exclude=AGENTS.md
  '--include=/.claude-plugin/***' --include=/.python-version '--exclude=.*'
  --include=/adw_light.py '--include=/engine/***' '--include=/hooks/***' '--include=/skills/***' '--include=/commands/***' '--include=/agents/***'
  --include=/VERSION --include=/README.md --include=/LICENSE --include=/NOTICE '--exclude=*')
rsync -a "${runtime[@]}" "$work/archive/" "$work/release/"
cp "$work/archive/engine/LICENSE" "$work/release/LICENSE"
python3 -I -S -B tools/smoke_probe.py "$work/release"
find "$work/release" -name __pycache__ -type d -prune -exec rm -rf {} +
python3 -I -S tools/release_manifest.py "$work/release" "$source"
mv "$work/release" "$release"
