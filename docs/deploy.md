# Deploy and release

ADW Lite runs only from the Claude and Codex plugin layers. No running copy lives in a source checkout or any other working folder.

## Install

Every machine installs ADW Lite through the plugin marketplace commands.

1. Claude: `claude plugin marketplace add <source>`, then `claude plugin install adw-light@adw-light`.
2. Codex: `codex plugin marketplace add <source>`, then `codex plugin add adw-light@adw-light`.

The source is the repository or a release folder built by `tools/build_release.sh`.

## Deploy

1. Commit every change on main in the main checkout.
2. Run `bash tools/deploy.sh` for this machine.
3. To install on a second machine as well, add `--host TARGET` with any ssh target, for example `bash tools/deploy.sh --host user@build-box`. `ADW_DEPLOY_HOST` sets the same target. Add `--retire FOLDER` once per old ADW Lite copy on that machine that the deploy should remove.

The deploy runs the suite, builds the runtime release in a temporary folder, and smoke-tests it. `tools/install_layers.sh` then copies the release to `~/.adw-lite/install/source`, adds that folder as the adw-light marketplace in Claude and in Codex, installs the plugin in both, and smoke-tests each plugin cache. It points every ADW Lite entry in `~/.codex/hooks.json` at the Codex plugin cache and drops older entries. On a remote host the same script runs over ssh from a staging folder under `~/.adw-lite/install`. It removes the named old copies after both installs pass.

Each install first smoke-tests the release in an empty home folder with the newest installed Python at 3.11 or later outside any environment. That run checks the lexer path and touches nothing. `tools/install_layers.sh` then saves a copy of `~/.adw-lite/install/venv` and `~/.adw-lite/install/setup.json`. It runs first-run setup (`engine/setup_env.py`) from the release with the newest installed Python at 3.11 or later. Setup builds the pinned tree-sitter environment under `~/.adw-lite/install/venv`, from source when no wheel exists, and fetches the Go grammar into `~/.adw-lite/cache/grammars`. Setup output lands in `~/.adw-lite/install/setup.log`. The gating smoke test runs after setup. When an environment exists, every smoke case runs on the environment Python that the live hooks use, and the Go writes must go through tree-sitter.

A failed setup does not stop the install by itself. The smoke test decides:

1. With no environment at all, the smoke test and the hooks use the lexer, and the install goes on. This happens when no Python 3.11 or later exists or the first build fails.
2. When the build fails and an environment with other pins stays in place, the smoke test fails and the install stops.
3. When the environment is ready but the Go grammar fetch fails, for example offline, the smoke test fails and the install stops.

When the smoke test fails, the install restores the saved environment and setup record. It stops before it places the release, so the running plugin keeps its source and its environment. Grammars fetched during the failed run stay in the shared cache. Setup runs once more from the installed Claude plugin cache, or from the Codex cache when the install skips Claude. If a later step fails on the local machine, `tools/deploy.sh` restores the environment and setup record. It also restores the release source, the plugin caches, and the settings files.

The installer retires only a folder named adw-light, adw-light-plugin, or adw-light-dev that holds an ADW Lite copy. It refuses a folder that overlaps `~/.adw-lite`, the Claude and Codex homes, or the release.

## Folders kept free of running copies

`ADW_KEEP_OUT` names folders, separated by colons. The tools build no release there, and no plugin cache or hook may point there. `tools/build_release.sh` and `tools/install_layers.sh` read it at run time. Nothing in the repository names such a folder.

## Release branch

`bash tools/release_branch.sh` builds the runtime of main into the local release branch. The branch holds the entry script, the engine with its license and notice, the hooks, the skills, the plugin manifest folder, and the Python version file. The tool installs the plugin from that branch into a temporary Claude home and smoke-tests the cache.

`bash tools/release_branch.sh --push` also pushes the release branch, and only that branch. The tool refuses when the repository has no remote, when several remotes exist and the caller gives no `--remote`, when the remote mirrors or carries its own push refspecs, and when the caller names any branch other than release.
