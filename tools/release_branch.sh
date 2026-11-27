#!/usr/bin/env bash
set -euo pipefail
unset CDPATH
dev=$(cd "$(dirname "$0")/.." && pwd -P)
fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $(pwd -P) == "$dev" ]] || fail 'Run the release tool from the repository root.'
branch=release
source_branch=main
push=no
remote=
source_commit=
while (( $# )); do
  case $1 in
    --source-commit) source_commit=${2:?Name the stamped commit.}; shift ;;
    --push) push=yes ;;
    --remote) [[ -n ${2:-} ]] || fail 'Name a remote after --remote.'; remote=$2; shift ;;
    --branch) [[ ${2:-} == "$branch" ]] || fail "The release tool only builds and pushes the $branch branch, not ${2:-an empty name}."; shift ;;
    *) fail "Unknown argument: $1. Use --push with an optional --remote NAME." ;;
  esac
  shift
done
git rev-parse --verify --quiet "refs/heads/$source_branch^{commit}" >/dev/null || fail "No $source_branch branch to build the release from."
if [[ $push == yes || -n $remote ]]; then
  remotes=$(git remote)
  [[ -n $remotes ]] || fail 'No git remote is configured. Add one before pushing the release branch.'
  [[ -n $remote ]] || fail 'Choose a remote with --remote NAME.'
  grep -qxF -- "$remote" <<<"$remotes" || fail "Remote $remote is not configured."
  [[ $(git config --bool --get "remote.$remote.mirror" || true) != true ]] || fail "Remote $remote mirrors every branch."
fi
if git worktree list --porcelain | grep -qxF "branch refs/heads/$branch"; then
  fail "The $branch branch is checked out in a worktree. Release it there first."
fi
work=$(mktemp -d "${TMPDIR:-/tmp}/adw-release-branch.XXXXXX")
trap 'rm -rf "$work"' EXIT
main_sha=$(git rev-parse --verify "refs/heads/$source_branch^{commit}")
[[ -z $source_commit || $source_commit == "$main_sha" ]] || fail "The stamped commit no longer matches $source_branch."
ADW_REF="$main_sha" ADW_RELEASE="$work/tree" bash tools/build_release.sh
leaks=$(find "$work/tree" -mindepth 1 \( -name tests -o -name tools -o -name docs -o -name CLAUDE.md -o -name AGENTS.md -o -name '.*' \) ! -path "$work/tree/.claude-plugin" ! -path "$work/tree/.claude-plugin/*" ! -path "$work/tree/.codex-plugin" ! -path "$work/tree/.python-version" -print)
[[ -z $leaks ]] || fail "The release tree holds files that must not ship: $leaks"
export GIT_INDEX_FILE=$work/index
git --git-dir="$dev/.git" --work-tree="$work/tree" add -A -f .
tree=$(git write-tree)
unset GIT_INDEX_FILE
parent=
if [[ -n $remote ]]; then
  remote_head=$(git ls-remote --heads -- "$remote" "refs/heads/$branch")
  if [[ -n $remote_head ]]; then
    git fetch --no-tags --refmap= -- "$remote" "refs/heads/$branch"
    parent=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
  fi
fi
tagged=
tag=v$(cat "$work/tree/VERSION")
if git show-ref --verify --quiet "refs/tags/$tag"; then
  tagged=$(git rev-parse "refs/tags/$tag^{commit}")
  [[ $(git rev-parse "$tagged^{tree}") == "$tree" ]] || fail "Tag $tag already names another release."
  tagged_parents=$(git show -s --format=%P "$tagged")
  [[ $tagged == "$parent" || $tagged_parents == "$parent" ]] || fail "Tag $tag already names another release."
fi
if [[ -n $tagged && $tagged != "$parent" ]]; then
  made=$tagged
elif [[ -n $parent && $(git rev-parse "$parent^{tree}") == "$tree" ]]; then
  made=$parent
  printf 'The %s branch already holds the runtime of %s %s.\n' "$branch" "$source_branch" "$main_sha"
else
  if [[ -n $parent ]]; then
    made=$(git commit-tree "$tree" -p "$parent" -m "Publish the runtime from $source_branch commit $main_sha.")
  else
    made=$(git commit-tree "$tree" -m "Publish the runtime from $source_branch commit $main_sha.")
  fi
fi
mkdir "$work/checkout"
git archive "$made" | tar -xf - -C "$work/checkout"
for manifest in .claude-plugin/{plugin,marketplace}.json .codex-plugin/{plugin,empty-hooks}.json; do
  jq -e 'type == "object"' "$work/checkout/$manifest" >/dev/null || fail "Invalid runtime manifest $manifest."
done
python3 -I -S -m compileall -q "$work/checkout"
python3 -I -S -B tools/smoke_probe.py "$work/checkout"
if [[ $push == yes ]]; then
  git push --atomic --no-follow-tags "$remote" "$made:refs/heads/$branch" "$made:refs/tags/$tag"
fi
if ! git show-ref --verify --quiet "refs/tags/$tag"; then
  git tag "$tag" "$made"
fi
printf '%s\n' "$made"
