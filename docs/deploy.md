# Deploy and release

From the root of a clean main checkout, set VERSION, commit it and run the suite. Deploy requires a passing stamp for that commit. It refuses hidden or uncommitted edits under `tools/`, including untracked files.

~~~sh
python3 tools/run_tests.py
bash tools/deploy.sh --remote origin --host TARGET
~~~

The release tool fetches the remote release branch when it exists and publishes the runtime commit and tag together. It never creates a local release branch. Without `--push`, the release tool prints the commit ID and keeps the tag locally. Without `--remote`, it builds a root commit. The remote defaults to origin. The GitHub repository defaults to michaelheichler/adw-light. Use --repo to select another repository. If the tag exists, deploy checks that the tagged commit belongs to the remote release branch and records the stamped source commit and version. It never moves a tag.

Each host checks that GitHub can serve the new tag before removing its marketplace. The installer adds the tagged repository, merges Codex hooks and probes the installed caches. Omit --host to deploy only on the Mac. The remote host receives the same ADW_KEEP_OUT exclusions.

Set `ADW_REMOTE_TMPDIR` to an absolute path without newlines to choose where the remote host creates temporary files.

## Rollback

The first migration from a local install has no rollback tag. If installation fails, deploy removes both plugins and marketplaces and strips ADW entries from the Codex hooks file. Other hooks stay in place.

`tools/run_tests.py` runs the suite on the committed tree and writes a stamp after a clean pass. The stamp lives under `suite-passes` in the ADW state folder (`ADW_STATE_ROOT` or `~/.adw-lite/state`). The full suite should finish within five minutes. Each module runs in its own process. The default worker count is the CPU count, up to eight. Pass `--jobs N` to override it or `--jobs 1` to run serially. Modules that share the grammar cache run alone at the end and appear in the report. Add `--profile` to see each module’s wall time from that run. Deploy checks the stamp without running the suite.

For an upgrade, deploy reads the previous tag from the Claude marketplace. Use --previous-tag only for an existing GitHub release at v0.1.2 or newer. Earlier releases carry a second set of Codex hooks. Both hosts should start on the same tag.

If the remote upgrade fails, deploy also restores the Mac. Rollback reinstalls from GitHub. A failed network check leaves the old marketplace alone, but a later outage can prevent restoration. If SSH drops, check the remote state before retrying.

To install a published tag without deploying, run this from the tooling checkout.

~~~sh
bash tools/install_layers.sh --claude --codex --tag v0.1.2
~~~
