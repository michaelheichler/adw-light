#!/usr/bin/env bash
set -euo pipefail
unset CDPATH
dev=$(cd "$(dirname "$0")/.." && pwd -P)
fail() { printf '%s\n' "$*" >&2; exit 1; }
branch=release
source_branch=main
push=no
remote=
while (( $# )); do
  case $1 in
    --push) push=yes ;;
    --remote) [[ -n ${2:-} ]] || fail 'Name a remote after --remote.'; remote=$2; shift ;;
    --branch) [[ ${2:-} == "$branch" ]] || fail "The release tool only builds and pushes the $branch branch, not ${2:-an empty name}."; shift ;;
    *) fail "Unknown argument: $1. Use --push with an optional --remote NAME." ;;
  esac
  shift
done
repo_git() { git --git-dir="$dev/.git" "$@"; }
repo_git rev-parse --verify --quiet "refs/heads/$source_branch^{commit}" >/dev/null || fail "No $source_branch branch to build the release from."
if [[ $push == yes ]]; then
  remotes=$(repo_git remote)
  [[ -n $remotes ]] || fail 'No git remote is configured. Add one before pushing the release branch.'
  if [[ -z $remote ]]; then
    (( $(wc -l <<<"$remotes") == 1 )) || fail 'Several remotes are configured. Choose one with --remote NAME.'
    remote=$remotes
  fi
  grep -qxF -- "$remote" <<<"$remotes" || fail "Remote $remote is not configured."
  [[ -z $(repo_git config --get-all "remote.$remote.push" || true) ]] || fail "Remote $remote has push refspecs that could push more than $branch."
  [[ $(repo_git config --bool --get "remote.$remote.mirror" || true) != true ]] || fail "Remote $remote mirrors every branch."
fi
if repo_git worktree list --porcelain | grep -qxF "branch refs/heads/$branch"; then
  fail "The $branch branch is checked out in a worktree. Release it there first."
fi
work=$(mktemp -d "${TMPDIR:-/tmp}/adw-release-branch.XXXXXX")
trap 'rm -rf "$work"' EXIT
ADW_REF="refs/heads/$source_branch" ADW_RELEASE="$work/tree" bash "$dev/tools/build_release.sh"
leaks=$(find "$work/tree" -mindepth 1 \( -name tests -o -name tools -o -name docs -o -name CLAUDE.md -o -name AGENTS.md -o -name '.*' \) ! -path "$work/tree/.claude-plugin" ! -path "$work/tree/.claude-plugin/*" ! -path "$work/tree/.python-version" -print)
[[ -z $leaks ]] || fail "The release tree holds files that must not ship: $leaks"
export GIT_INDEX_FILE=$work/index
git --git-dir="$dev/.git" --work-tree="$work/tree" add -A -f .
tree=$(repo_git write-tree)
unset GIT_INDEX_FILE
parent=$(repo_git rev-parse --verify --quiet "refs/heads/$branch^{commit}" || true)
main_sha=$(repo_git rev-parse --verify "refs/heads/$source_branch^{commit}")
if [[ -n $parent && $(repo_git rev-parse "$parent^{tree}") == "$tree" ]]; then
  printf 'The %s branch already holds the runtime of %s %s.\n' "$branch" "$source_branch" "$main_sha"
else
  if [[ -n $parent ]]; then
    made=$(repo_git commit-tree "$tree" -p "$parent" -m "release: runtime of $source_branch $main_sha")
  else
    made=$(repo_git commit-tree "$tree" -m "release: runtime of $source_branch $main_sha")
  fi
  repo_git update-ref "refs/heads/$branch" "$made" ${parent:+"$parent"}
  printf 'Updated %s to %s from %s %s.\n' "$branch" "$made" "$source_branch" "$main_sha"
fi
mkdir "$work/checkout"
repo_git archive "refs/heads/$branch" | tar -xf - -C "$work/checkout"
python3 "$dev/tools/smoke_probe.py" "$work/checkout"
if command -v claude >/dev/null; then
  mkdir "$work/claude-home"
  CLAUDE_CONFIG_DIR="$work/claude-home" claude plugin marketplace add "$work/checkout" --scope user
  CLAUDE_CONFIG_DIR="$work/claude-home" claude plugin install adw-light@adw-light --scope user
  cache=$(CLAUDE_CONFIG_DIR="$work/claude-home" claude plugin list --json | jq -er '[.[] | select(.id == "adw-light@adw-light")] | if length == 1 then .[0].installPath else error("Plugin install from the release branch failed") end')
  python3 "$dev/tools/smoke_probe.py" "$cache"
else
  fail 'The claude command is missing, so the plugin install check from the release branch cannot run.'
fi
if [[ $push == yes ]]; then
  repo_git push --no-follow-tags "$remote" "refs/heads/$branch:refs/heads/$branch"
fi
