# Decisions

Owner decisions that shape ADW Lite. Each entry names the date, the decision and the reason.

## Broad exception handlers in the language layer (2026-11-25)

The language layer keeps its five broad exception handlers. ruff.toml ignores the broad-except rule for that one file and nowhere else.

- Each handler falls back to the lexer or to a stderr note. The scan then runs with lower precision, so no finding slips through.
- The tree-sitter language pack raises error types that it does not document. A narrow list of exceptions could let an unknown one crash the hook.
- The other broad handlers in the engine stay open for review case by case. This decision does not cover them.

## Prose a shell command writes gets checked after the command (2026-11-26)

ADW Lite no longer refuses a shell command only because it cannot see what the command writes. After each shell command it compares test files and scanned files with a snapshot taken before the command.

- A test change by any agent other than adw-test-writer blocks the next step. So does new prose that breaks a rule.
- The eight write-visibility refusals end. So do the 100-character shell write cap and the refusal of script files such as python3 file.py.
- An open tests window exempts the check, the same way it exempts the check before the run.
- The release keeps the refusal of unreadable scripts until this check lands on main.
- While the live gate refuses node, agents may use rg in place of GitNexus to find callers.

## Literal text in f-strings counts, and the owner approves contract strings (2026-11-26)

The wording test rule now judges every literal text part of an f-string and of a plus chain. Another program reads some asserted strings, for example VyOS commands. The owner approves those strings as exact templates in the project config through /adw-light:config.

- {} in a template stands for one value.
- Agents cannot add an entry. The seal compares the new key with the file on disk.

## Agents may not change the governing config, but may change thresholds (2026-11-26)

The config seal stops agents from creating a governing config or changing its enforcement keys by any shell route. Thresholds such as max_rows and list_item_cap stay open to agents.

## The full suite must finish in under five minutes (2026-11-26)

A release needs the full suite to finish in under 300 seconds on a quiet Mac. The test writer may delete a test whose only check is a rule that the release removes. A test that also checks a detection rule or a test-write block keeps that check.

## The full suite runs once per release (2026-11-27)

The owner accepted a full suite of about 316 seconds for 0.1.3. The target stays under five minutes.

- A commit, push and release sequence gets one full suite run. That run is tools/run_tests.py, which writes the pass stamp.
- Runs of single test modules are always allowed, because they are quick.

## Portable home paths only get a note (2026-11-27)

A path under ~/ that is the same on every machine, such as ~/.claude/settings.json, gets the observe-only note hard_coded_system_path. A path that names a real user home stays a hard block.
