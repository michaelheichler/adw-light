#!/usr/bin/env bash
set -euo pipefail
unset CDPATH
fail() { printf '%s\n' "$*" >&2; exit 1; }
if [[ -n ${ADW_REMOTE_TMPDIR:-} ]]; then
  [[ $ADW_REMOTE_TMPDIR == /* && $ADW_REMOTE_TMPDIR != *$'\n'* ]] || fail 'Give ADW_REMOTE_TMPDIR an absolute path without newlines.'
fi
dev=$(cd "$(dirname "$0")/.." && pwd -P)
[[ $(pwd -P) == "$dev" ]] || fail 'Run deploy from the repository root.'
host=${ADW_DEPLOY_HOST:-} repo=michaelheichler/adw-light previous= remote=origin
while (( $# )); do
  case $1 in
    --remote) remote=${2:?Name a release remote.}; shift ;;
    --host) host=${2:?Name an ssh target.}; shift ;;
    --repo) repo=${2:?Give a GitHub owner/repo.}; shift ;;
    --previous-tag) previous=${2:?Give the previous tag.}; shift ;;
    *) fail "Unknown option $1" ;;
  esac
  shift; done
[[ $repo =~ ^[a-zA-Z0-9][a-zA-Z0-9-]*/[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || fail 'Give a GitHub owner/repo.'
[[ $(git branch --show-current) == main ]] || fail 'Deploy from main.'
git_dir=$(git rev-parse --path-format=absolute --git-dir)
common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
[[ $git_dir == "$common_dir" ]] || fail "Deploy from the main checkout, not the linked worktree $dev."
changes=$(git --no-optional-locks status --porcelain --untracked-files=all)
[[ -z $changes ]] || fail 'Commit every change before deploying.'
commit=$(git rev-parse HEAD)
tool_tags=$(git ls-files -v -- tools hooks/merge-codex-hooks.py hooks/merge_hooks.py)
if grep -Eq '^[a-zS] ' <<< "$tool_tags"; then fail 'Remove hidden index flags from tools and the hook helpers before deploying.'; fi
untracked_tools=$(git ls-files --others -- tools hooks)
[[ -z $untracked_tools ]] || fail "Delete these untracked or ignored files before deploying, since Python may load them: $untracked_tools"
python3 -I -S - "$commit" "${ADW_STATE_ROOT:-$HOME/.adw-lite/state}" <<'PY' || fail 'Run python3 tools/run_tests.py for this commit before deploying.'
import json
import sys
from pathlib import Path

commit = sys.argv[1]
state = Path(sys.argv[2]).expanduser()
try:
    stamp = json.loads((state / "suite-passes" / f"{commit}.json").read_text())
except (ValueError, OSError):
    sys.exit(1)
valid = isinstance(stamp, dict) and stamp.get("commit") == commit and type(stamp.get("ran")) is int
sys.exit(not (valid and stamp["ran"] > 0))
PY
tag=v$(cat "$dev/VERSION")
[[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || fail 'Invalid VERSION.'
url="https://github.com/$repo.git"
valid_prior() {
  [[ $1 =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))?$ && $1 != v0.0.* && $1 != v0.1.[01] && $1 != v0.1.[01].* ]] || {
    echo 'Rollback requires v0.1.2 or newer.' >&2; return 1;
  }
}
[[ -z $previous ]] || valid_prior "$previous"
[[ $host != -* ]] || fail 'Invalid ssh host.'
verify_tag() {
  local tagged tip
  git fetch --no-tags "$url" "refs/tags/$tag" || return 1
  tagged=$(git rev-parse 'FETCH_HEAD^{commit}') || return 1
  git fetch --no-tags "$url" refs/heads/release || return 1
  tip=$(git rev-parse FETCH_HEAD) || return 1
  git merge-base --is-ancestor "$tagged" "$tip" &&
    git show "$tagged:engine/RELEASE.json" | jq -e --arg sha "$commit" --arg version "${tag#v}" '.source == $sha and .version == $version' >/dev/null
}
lookup=0
git ls-remote --exit-code --refs "$url" "refs/tags/$tag" >/dev/null || lookup=$?
if (( lookup )); then
  [[ $lookup == 2 ]] || fail "Could not reach $url."
  destination=$(git remote get-url --push --all "$remote")
  case "${destination%.git}" in "https://github.com/$repo"|"git@github.com:$repo"|"ssh://git@github.com/$repo") ;; *) fail 'The push remote must name the install repository.' ;; esac
  bash tools/release_branch.sh --push --remote "$remote" --source-commit "$commit"
fi
verify_tag || fail "Tag $tag does not verify against the stamped commit on the release branch."
prior_tag() {
  claude plugin marketplace list --json | jq -er --arg repo "$repo" '
    [.[] | select(.name == "adw-light")] |
    if length == 0 then "" elif length != 1 then error("Found multiple ADW marketplaces.")
    elif .[0].source == "directory" or .[0].source == "local" then "local"
    elif (.[0].repo // "") == "" then error("The ADW marketplace has no repo.")
    elif (.[0].ref // "") == "" then error("The ADW marketplace has no ref.")
    elif .[0].source != "github" or .[0].repo != $repo then error("The ADW marketplace names another repository.")
    else .[0].ref end'
}
restore_host() {
  local prior=$1 hooks=${CODEX_HOME:-$HOME/.codex}/hooks.json failed=0
  if [[ -n $prior ]]; then
    bash tools/install_layers.sh --claude --codex --repo "$repo" --tag "$prior" || return 1
  else
    if [[ -f $hooks ]]; then
      python3 -I -S -B hooks/merge-codex-hooks.py --hooks-json "$hooks" --strip-only || failed=1
    fi
    claude plugin uninstall adw-light@adw-light --scope user || failed=1
    claude plugin marketplace remove adw-light || failed=1
    codex plugin remove adw-light@adw-light || failed=1
    codex plugin marketplace remove adw-light || failed=1
    return "$failed"
  fi
}
install_host() {
  local status
  if [[ -z $previous ]]; then
    previous=$(prior_tag) || return 1
    if [[ $previous == local ]]; then
      previous=
    elif [[ -z $previous ]]; then
      codex plugin marketplace list --json | jq -e '[.marketplaces[] | select(.name == "adw-light")] | length == 0' >/dev/null || {
        echo 'Give --previous-tag for the existing Codex marketplace.' >&2; return 1;
      }
    fi
  fi
  if [[ -n $previous ]]; then
    valid_prior "$previous" || return 1
    git ls-remote --exit-code --refs "$url" "refs/tags/$previous" >/dev/null || return 1
  fi
  if bash tools/install_layers.sh --claude --codex --repo "$repo" --tag "$tag"; then return 0; else status=$?; fi
  if (( status == 69 )); then return 1; fi
  restore_host "$previous" || echo 'Plugin rollback failed.' >&2
  return 1
}
remote_previous=$previous
install_host
if [[ -n $host ]]; then
  remote_script='set -euo pipefail'
  if [[ -n ${ADW_REMOTE_TMPDIR:-} ]]; then
    remote_script+=$(printf '\nmkdir -p %q\nexport TMPDIR=%q' "$ADW_REMOTE_TMPDIR" "$ADW_REMOTE_TMPDIR")
  fi
  remote_script+=$'\n'$(printf '%s\n' 'stage=$(mktemp -d)' 'trap "rm -rf \"$stage\"" EXIT' 'tar -xf - -C "$stage"' 'cd "$stage"' "$(declare -f valid_prior prior_tag restore_host install_host)")
  remote_script+=$(printf '\nrepo=%q tag=%q previous=%q url=%q\nexport ADW_KEEP_OUT=%q\ninstall_host' "$repo" "$tag" "$remote_previous" "$url" "${ADW_KEEP_OUT:-}")
  quoted=$(printf '%s' "$remote_script" | sed "s/'/'\\\\''/g")
  if ! tar -cf - -C "$dev" tools/install_layers.sh tools/smoke_probe.py hooks/{merge-codex-hooks,merge_hooks}.py | ssh -o BatchMode=yes "$host" "bash -c '$quoted'"; then
    restore_host "$previous" || echo 'Mac rollback failed.' >&2
    fail 'Remote deployment failed.'
  fi
fi
