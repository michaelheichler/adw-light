# 0.1.3.5 removal plan: the after-run attribution guess

Status: plan only. I have not changed any engine file. Code starts after the coordinator checks every quote below.

Governing ruling: docs/decisions.md, section "0.1.3.5: no guessing in ADW (owner, 2026-11-27)" (uncommitted, line 49 onward). It says that ADW no longer decides who changed a file from timing windows or from before-and-after snapshots.

Line numbers refer to commit 588a2a7 plus the uncommitted docs/decisions.md change.

## 1. Removal list

### Whole modules (every symbol in them goes)

| Module | Lines | Why it is attribution by timing or snapshots |
|---|---|---|
| engine/lib/after_run.py | 669 | `pre` (170) captures a project snapshot before each shell call, `post` (573) compares it after the call and blames the caller for changed files (`_path_change` 204, `_late_change` 226, `_changes` 311). `_window` (103) and `_windows`/`_windowed` (390-397) use the tests window time as an attribution window. `_publish` (406) writes the `<after-run>` blocker. `batch_reason` (619) blocks PostToolBatch from that state. `note_direct_write` (630) records direct writes so later snapshots can be explained. `prime_ignored` (656) walks ignored files at session start for later snapshots. Rules `unexplained_change` (43) and `after_run_deferred` (44). |
| engine/lib/after_run_snapshot.py | 565 | Snapshot capture and comparison: `turn_snapshots` (45), `compare_snapshot` (209), `direct_comparison` (245), `legacy_clean_path` (331), `writer_explained` (345, the explained-writer records), `advance_snapshot` (462, writes `explained` entries with `at_ns` time stamps), `discover_nested`/`nested_locations` (109-126, nested-repo walks), `_refreshed_listing` (324, ignored listings), `record_call` (552, sets `background` flags). |
| engine/lib/after_run_stop.py | 163 | `stop` (118) and `subagent_stop` (154) publish after-run held rows and the after-run reason at Stop. |
| engine/lib/after_run_state.py | 83 | Stores the `after_run` session-state key (13) that holds snapshots, pending rows and phases. |
| engine/lib/after_run_config.py | 202 | Only used by after-run code and by `pre_tool.run`. `release_owner_paths` (29) is the writer release. `restore_scope` (130) releases held rows when a restore command matches stored after-run entries (reads `after_run._stored` at 104). `seal_entry`/`seal_changes` (147-184) emit `config_seal` after the fact from snapshot diffs. The pre-run `config_seal` emitters stay (see section 2). |
| engine/lib/after_commit.py | 385 | Compares branch heads before and after a shell command (`snapshot_heads` 80, `heads` 89, `compare` 349, `_new_revisions` 203 with `SKEW_SECONDS` 29 as a timing window) and blames the caller for the new commits. Held commit rows: `entry_of` 117, `recheck` 278, `_holds` 243. `_committed_test_pairs` (183) flags committed test paths, which the ruling drops ("A commit of test content is not a test write"). `record_receipt` (54) only feeds `_scan` (166) to skip rescans, so it goes too. |
| engine/lib/commit_test_changes.py | 192 | Every symbol serves attribution: `written_test_rows` (15), `holds_test_row` (111), `capture_index` (29), `moved_sources` (155), `snapshot_sources` (55), `known_target` (149) and their helpers. Callers are after_commit.py and after_run*.py only. |
| engine/lib/nested_repos.py | 72 | Nested-repo walk cache (`CACHE = "nested_repos"` 12). Its only caller is after_run.py:10. |
| engine/lib/ignored_configs.py | 126 | Ignored-file listings for snapshots. Its callers are after_run.py and the after-run parts of shell_snapshot.py only. |

### Partial removals

| File:line | Symbol | Why |
|---|---|---|
| engine/lib/shell_snapshot.py:40-55, 55-145, 172-527 | everything except `project_location` (156-171) and the imports it needs | The walk, marks, digests, `Comparison`, `compare`, `runs_in_background` (109, background windows), `is_worktree_move`, `replays_commits`, `ignored_listing`, `hidden_marks` and `seal_candidate` serve only the snapshot comparison. `project_location` stays for pre_commit.py:101. `PROJECT_MARKERS` stays importable for config.py:433-435. `WalkOverflow` and `check_deadline` (147-153) go if no kept caller remains (current callers: nested_repos.py, after_run*.py, commit_test_changes.py). |
| engine/lib/agent_types.py:40-48 | `known`, `_known` | Only called at after_run.py:214 and :296. `remember` (15) and `type_of` (29) stay for the test-write gate (tests_policy.py:340, subagent_start.py:12). |
| engine/lib/blocker_state.py:16 | `AFTER_RUN_KEY` | The `<after-run>` blocker key. |
| engine/lib/blocker_state.py:137 | special case for `AFTER_RUN_KEY` in `set_pending` | Only exists for the after-run reason. |
| engine/lib/blocker_state.py:21, 399-437 | `HELD_KEY`, `_held`, `hold_files`, `release_files`, `held_files` | Held-file rows. rg finds no caller outside blocker_state.py, so they are dead code left from the held-row design. |
| engine/lib/end_turn.py:57, 61 | `AFTER_RUN_KEY` exclusions | Become unnecessary once the key is dropped on read. |
| engine/pre_tool.py:100-104 | `after_run.pre` call | Takes the before-snapshot. |
| engine/pre_tool.py:113-118 | `after_run_config.restore_scope` wrapper | Writer release built on after-run state. |
| engine/record.py:396-406, 409-419 | `_merge_after_run`, `after_run.post`, `after_run.note_direct_write` | After-shell comparison and explained-writer records. |
| engine/batch.py:500-503 | `after_run.batch_reason`, `LIVE_BLOCK` | Publishes the after-run block on PostToolBatch. |
| engine/stop.py:30-37 | `after_run_stop.stop` | Publishes the after-run reason at Stop. |
| engine/subagent_stop.py:3, 21, 25, 28 | `after_run_stop.subagent_stop` | Same at SubagentStop. |
| engine/session_start.py:23, 39-47 | `_prime_ignored` | Primes ignored listings for snapshots. |
| adw_light.py:26, 77-84 | `AFTER_RUN_MODULES`, the PostToolUseFailure `after_run.post` call, the `_after_run_block` pop | After-run dispatch. The `until` deadline passed to record and batch only serves after_run, so it goes with it. |
| engine/pre_commit.py:137-140 | `record_receipt` call | Feeds only the after-commit rescan skip. `receipts` collection at 219-243 goes with it. |
| engine/pre_commit.py:45 | `UNREAD_INDEX` wording "the project the after-run check covers" | Wording names the removed check. It gets reworded to "the current project". |
| engine/lib/catalog.py:21, catalog_de.py:35, config.py:88, findings.py:52 | `after_run_deferred` | Rule exists only for after-run and after-commit retries. `unexplained_change` has no catalog entry. |
| engine/SOURCE.json:11-16, 50, 81, 109 | inventory rows for after_commit, after_run*, commit_test_changes, ignored_configs, nested_repos | Modules leave the engine. Row 157 (shell_snapshot) stays because the module stays. Row 10 (agent_types) stays. |

### Upgrade path for live sessions

1. blocker_state.`_normalized_bucket` (67-77) drops the legacy pending key `<after-run>` on read. `details`, `scan_state`, `reconcile`, `settle_partial` and `apply_paths` read pending entries through it. The next write then removes it from disk. `_queued_scope` (445-448) reads raw `pending` today, so it switches to `_normalized_bucket` and an `<after-run>`-only bucket no longer shows "pending checks".
2. session_state read drops the legacy top-level keys `after_run`, `commit_scan_receipts` and `nested_repos`. Nothing reads them after removal, so they cannot block. Dropping them on read keeps the state file small and the next locked write removes them.
3. Result: on the next PostToolBatch, batch.py no longer calls `batch_reason`, and on the next Stop, stop.py no longer calls `after_run_stop`. An old `<after-run>` entry is never returned by any reader.

Live files under ~/.adw-lite are not touched.

### Docs

- docs/decisions.md:15 says "After each shell command it compares test files and scanned files with a snapshot taken before the command." I add a note that the 0.1.3.5 section replaces that sentence.
- Flag, not edited: .claude/rules/shell-analysis.md last bullet says "If ADW cannot read a shell target or its content before execution, compare the project snapshot after the command runs." It contradicts the ruling. Dot-folder agent files stay out of git. The owner does not edit them by hand (memory "Owner does nothing by hand"). I report the bullet and leave the file alone.

## 2. Kept checks and the direct fact each one decides from

| Check | File:line | Direct fact |
|---|---|---|
| Test-write gate for Write, Edit, MultiEdit, NotebookEdit | engine/pre_write.py:157-176 (`_gate`), 319-341 (`_test_changes`, `_pending_findings`), and engine/lib/tests_policy.py:361-379 (`findings`) | The tool name and target path in the PreToolUse payload, and the pending content in tool_input. The actor is the caller of this tool call. |
| Test-write gate for parsed shell targets | engine/pre_bash.py:205-223 (`_test_findings`), 307-314 (`_landing`) | The write targets ADW parses from the command text before it runs. |
| Test writer and owner tests window | engine/lib/tests_policy.py:337-342 (`gate_open`), 322-328 (`window_until`) | The agent type recorded at SubagentStart (agent_types.type_of, agent_types.py:29) and the window end the owner set. The window is the owner's explicit permission, not an attribution window. |
| Prose, comment and code scans of tool writes | engine/pre_write.py:225-316, engine/record.py:211-231 (`_scan_paths`), 305-329 (`_gate_for`) | The content in the tool call, and the file at the path the tool call named, read right after that call. |
| Stop rescan of files those tool calls wrote | engine/lib/end_turn.py:102-128 (`_scope_outcome`), engine/lib/stop_scan.py:133-156 (`rescan`) | The path list record.py and batch.py stored for this agent from its own tool calls. |
| Commit scan of committed content | engine/pre_commit.py (PreToolUse on the commit command, 101-140 and 219-243) | The staged diff and message of the commit the agent is about to run. It has no test-path check, so nothing there flags a commit of test content. |
| Self-protection | engine/lib/protected.py:134, 170, 220, 222, 403, engine/lib/shell_self_protection.py:65-108, config.py:47-53 | The write target path of the tool call or parsed shell command. `config_seal` keeps its pre-run emitters (protected.py:403, shell_self_protection.py:89). |
| Incoming test content in a parsed command | engine/pre_bash.py:286, engine/lib/incoming_content.py:39 | The command text and the content it brings in, read before the command runs. No timing and no after-run diff. |

retention.py has no after-run reference (rg for after, snapshot, nested, ignored, agent_types and writer finds nothing), so it does not change.

## 3. Governing quotes

### Remove instead of fix, and remove whole modules instead of leaving dead parts

- ~/.claude/skills/algorithmic-thinking/SKILL.md:64

```text
- **Greedy needs proof.** Greedy choices are fast but frequently wrong (Moneygrubbers, the direct River Jump greedy). When greedy works (River Jump feasibility, Dijkstra itself), the book justifies it with an exchange argument. Default to DP/exhaustive options unless a proof exists.
```
- ~/.claude/skills/algorithmic-thinking/SKILL.md:52

```text
- **Greedy vs. DP.** Greedy commits to the locally best choice and is often provably wrong (Moneygrubbers, River Jump direct-greedy). DP tries all options. Greedy correctness needs an exchange-argument proof, which Chapter 7 shows how to build.
```
- ~/.claude/skills/algorithmic-thinking/SKILL.md:63

```text
- **Sentinels and invariants.** Distinguish "not computed" from "infeasible" from valid answers (-2/-1/≥0). State and maintain the binary-search invariant before deciding to output low or high. Keep size[] accurate only at union-find roots. Most off-by-one bugs in the book trace back to a fuzzy invariant.
```
- ~/.claude/skills/algorithmic-thinking/references/appendix-a-algorithm-runtime.md:9

```text
  Timing a program on a specific machine and test case is informative but insufficient for algorithm analysis. Five key shortcomings: (1) results are machine-dependent and vary run-to-run due to OS scheduling; (2) results are test-case-dependent — small inputs are always fast regardless of algorithm quality; (3) timing requires a complete implementation before any evaluation is possible; (4) timing does not explain *why* a program is slow; (5) timing is hard to communicate to others in a portable, reproducible way.
```
- ~/.claude/skills/algorithmic-thinking/references/appendix-a-algorithm-runtime.md:81

```text
- **Assuming timing on your machine predicts judge performance**: The judge runs on different hardware, under different load. A 30-second result on your laptop for a 3-second time limit is clearly too slow; a borderline 2.8-second result on your laptop is not a safe indicator of passing within 3 seconds on the judge.
```

Applied: a snapshot diff plus a time window is a heuristic choice of actor with no proof, so it goes. What remains decides from one observed fact (the tool call, its parsed target, the staged commit), with the invariant "the actor of a finding is the caller of the tool call that carried the content".

- ~/.claude/skills/clean-code-principles-python-silen/references/ch03-coding-principles.md:84

```text
- **Anti-slop:** LLMs sprinkle restating comments (`# increment counter`, `# loop over items`) and TODO/placeholder comments. Delete them; never comment out dead code (git keeps it).
```
- ~/.claude/skills/clean-code-principles-python-silen/SKILL.md:120

```text
- Leaving commented-out code in place.
```

Applied: I delete modules and symbols outright. Nothing stays commented out or behind a flag.

### Keep the change narrow and the kept behaviour intact

- ~/.claude/skills/clean-code-principles-python-silen/references/ch03-coding-principles.md:192

```text
- **Rule:** Make a single isolated change; never mix two features or a feature and a refactor.
```
- ~/.claude/skills/clean-code-principles-python-silen/references/ch03-coding-principles.md:195

```text
- **Anti-slop:** Do not "while I'm here" tidy unrelated code inside a feature change.
```
- ~/.claude/skills/fluent-python/SKILL.md:24

```text
5. Apply the smallest idiomatic improvement that fits the codebase, not the most advanced feature available.
```
- ~/.claude/skills/fluent-python/SKILL.md:69

```text
When reviewing code, lead with concrete findings and file/line references. When explaining concepts, tie them to a practical example. When refactoring, preserve behavior unless the user explicitly asks for a redesign.
```
- ~/.claude/skills/clean-code-principles-python-silen/SKILL.md:47

```text
2. **Apply** the smallest change that satisfies the principle — explain the *why*, don't cargo-cult.
```

Applied: only after-run code and the code that exists only for it changes. Kept gates keep their behaviour. shell_snapshot.py keeps `project_location` in place rather than moving it.

### Read before change

- ~/.claude/skills/fluent-python/SKILL.md:23

```text
4. Inspect the user's actual code, tests, traceback, or API contract before recommending changes.
```
- ~/.claude/skills/algorithmic-thinking/SKILL.md:24

```text
1. **Read the problem statement precisely.** Pay attention to exact wording ("at least" vs. "exactly"), value ranges, number of test cases, output format. Misreading constraints is a leading cause of wrong answers.
```

Applied: I read every caller (adw_light.py, pre_tool.py, record.py, stop.py, subagent_stop.py, session_start.py, end_turn.py, blocker_state.py, after_commit.py in full, batch.py 441-521) before this plan.

### Legacy state keys are expected data, not bugs

- ~/.claude/skills/clean-code-principles-python-silen/references/ch03-coding-principles.md:140

```text
- **Rule:** Treat errors as expected outcomes you handle; treat exceptions as bugs caught only at boundaries.
```
- ~/.claude/skills/clean-code-principles-python-silen/references/ch03-coding-principles.md:199

```text
- **Rule:** Pick the data structure by its access pattern and cost, not by habit.
```

Applied: an old `<after-run>` entry or `after_run` key is an expected input after the upgrade. The reader drops it with a frozenset membership test instead of raising.

### One responsibility per module after the cut

- ~/.claude/skills/clean-code-principles-python-silen/references/ch02-object-oriented-design-principles.md:48

```text
- **Rule:** A class represents one thing or provides one capability; a function does one thing at one abstraction level.
```

Applied: shell_snapshot.py ends up holding only project location lookup. blocker_state.py loses the held-file functions that no caller uses.

### No new comments, verify with the checker

- ~/.claude/skills/clean-code-principles-python-silen/SKILL.md:119

```text
- Restating-the-code comments and docstrings that paraphrase the signature. Encode intent in names/types instead.
```
- ~/.claude/skills/clean-code-principles-python-silen/references/ch03-coding-principles.md:70

```text
- **Why:** Comments are not checked by the compiler/tests and rot independently of the code; readers trust the code, so a stale comment actively misleads. Only generated library API docs justify them.
```
- ~/.claude/skills/clean-code-principles-python-silen/SKILL.md:48

```text
3. **Verify** by running the checker on what you wrote, and **fix until it reports clean**:
```

Applied: the change adds no comment or docstring. I run clean_check.py on every changed engine file, and the changed lines must come back clean.

### Decisions no skill line covers

| Decision | Source |
|---|---|
| Remove instead of fix, and the scope of what stays | docs/decisions.md, section "0.1.3.5: no guessing in ADW (owner, 2026-11-27)" |
| Drop live state keys on read, do not edit ~/.adw-lite | Coordinator task text ("Ignore or drop those keys when reading state. Do not edit live ~/.adw-lite files yourself.") |
| Removing a block is not fail-open | CLAUDE.md "Hook contract": "fail closed, never open". The removed check no longer exists, so no kept gate opens. Every kept gate keeps its fail_closed path. |
| Leave .claude/rules/shell-analysis.md alone and report it | Memory "Owner does nothing by hand" and CLAUDE.md "CLAUDE.md, AGENTS.md, and dot folders stay out of git." |
| Delete dead held-file functions | Memory "Delete obsolete tests" covers tests only. No skill line covers dead production code beyond ch03:84 ("never comment out dead code (git keeps it)"), which I apply. |
| Remove `until` from record.run and batch.run | adw_light.py:77-78 passes it only because of after_run. Python guidance: ruff ARG001 flags unused arguments (https://docs.astral.sh/ruff/rules/unused-function-argument/). Tests that pass `until=` to these will be listed. |
