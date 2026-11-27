# Writing prompts for ADW Lite agents

Future agents open this file before they write a prompt. A prompt here means an agent definition or a subagent task, and a skill counts too. Every quote appears word for word, and a source line under it names where it came from.

Three rule sources shaped this file. Prompt Master supplies the structure. The owner book library supplies advice on wording and on evaluation. The unit-testing-principles skill supplies the standard the test writer must meet.

## Prompt Master

Prompt Master is a Claude skill by nidhinjs, released under the MIT license. These are the parts that shaped the ADW Lite agent prompts. Fetch the live file with gh api when this wording looks stale.

### Hard rules

A prompt that breaks these rules does not ship. The block keeps the original emphasis.

```text
**Hard rules — NEVER violate these**

- Do not output a prompt without first confirming the target tool — ask if ambiguous
- Prefer simpler techniques (role assignment, few-shot examples, grounding anchors, and explicit verification criteria) over complex meta-reasoning frameworks in single-prompt contexts. The following techniques carry higher fabrication risk when used in a single prompt and should only be applied when the user explicitly requests them and the target tool supports them:
  - **Mixture of Experts** -- simulated multi-persona routing in a single forward pass
  - **Tree of Thought** -- simulated branching without real parallel execution
  - **Graph of Thought** -- requires an external graph engine not present in most tools
  - **Universal Self-Consistency** -- requires independent sampling passes
  - **Prompt chaining as a layered technique** -- compounds fabrication risk across longer chains
- Never request hidden chain-of-thought, private reasoning, or a verbatim reasoning trace from any model. Ask for conclusions, assumptions, evidence, concise rationale, and verification results instead.
- Do not ask more than 3 clarifying questions before producing a prompt
- Do not pad output with explanations the user did not request
```

*Prompt Master*, SKILL.md, PRIMACY ZONE.

### The nine intent dimensions

Prompt Master extracts nine dimensions before it writes a word. A missing critical dimension triggers a clarifying question, and the skill allows no more than three.

```text
| Dimension | What to extract | Critical? |
|-----------|----------------|-----------|
| **Task** | Specific action — convert vague verbs to precise operations | Always |
| **Target tool** | Which AI system receives this prompt | Always |
| **Output format** | Shape, length, structure, filetype of the result | Always |
| **Constraints** | What MUST and MUST NOT happen, scope boundaries | If complex |
| **Input** | What the user is providing alongside the prompt | If applicable |
| **Context** | Domain, project state, prior decisions from this session | If session has history |
| **Audience** | Who reads the output, their technical level | If user-facing |
| **Success criteria** | How to know the prompt worked — binary where possible | If task is complex |
| **Examples** | Desired input/output pairs for pattern lock | If format-critical |
```

*Prompt Master*, SKILL.md, MIDDLE ZONE, Intent Extraction.

### Claude routing

The Claude route refuses one universal default. It names the advice that holds across current models.

```text
*Durable across current Claude models:*
- Be clear and direct. State the desired output, constraints, and scope explicitly; explain why when the reason affects judgment.
- Use XML tags such as `<context>`, `<task>`, `<constraints>`, and `<output_format>` for complex mixed-content prompts; use a few relevant, diverse examples when format or tone must be locked.
- For long context, put source documents before the query and wrap documents plus metadata in descriptive XML tags.
- Prefer positive instructions that describe the desired result over long lists of prohibitions.
- Do not request hidden reasoning or reproduce thinking. Ask for a concise rationale, evidence, and verification results.
- Current Claude 5 models use adaptive thinking and an effort control. Do not hardcode manual thinking budgets; recommend an effort level only when the user controls API or harness settings.
- Use Template M for complex or agentic tasks.
```

*Prompt Master*, SKILL.md, Tool Routing, Claude.

### Claude Code routing and stop conditions

Claude Code runs tools and edits files on its own, so the route adds stop conditions and review triggers.

```text
**Claude Code**
- Agentic — runs tools, edits files, executes commands autonomously
- Starting state + target state + allowed actions + forbidden actions + stop conditions + checkpoints
- Stop conditions are MANDATORY — runaway loops are the biggest credit killer
- Do not assume the Claude Code model. Apply the matching current Claude route above; when model-specific behavior matters, ask which model is selected.
- Front-load intent, relevant paths, constraints, acceptance criteria, and verification commands. Explicitly request tool use when inspection is required.
- Current Fable/Opus models can over-scope and delegate readily. Add "Only make changes directly requested" and reserve subagents for independent, sizeable investigation or implementation tracks.
- Do not force a separate verifier on Opus 5.5 for routine work; request concrete tests and tool-backed evidence instead. For long Fable 5.1 runs, require progress claims to cite actual tool results.
- Always scope to specific files and directories — never give a global instruction without a path anchor
- Human review triggers required: "Stop and ask before deleting any file, adding any dependency, or affecting the database schema"
- For complex tasks, use Template M. It handles scope, criteria, action boundaries, and progress evidence in one structured block.
```

*Prompt Master*, SKILL.md, Tool Routing, Claude Code.

### Agentic failure checklist

Five failures account for most agentic prompts that go wrong.

```text
**Agentic failures**
- No starting state → add current project state description
- No target state → add specific deliverable description
- Silent agent → request meaningful milestones, blockers, and final verification evidence
- Unrestricted filesystem → add scope lock on which files and directories are touchable
- No human review trigger → add "Stop and ask before: [list destructive actions]"
```

*Prompt Master*, SKILL.md, Diagnostic Checklist, Agentic failures.

### Memory Block

Prior decisions survive attention decay only when they sit near the front of the new prompt.

```
## Context (carry forward)
- Stack and tool decisions established
- Architecture choices locked
- Constraints from prior turns
- What was tried and failed
```

*Prompt Master*, SKILL.md, Memory Block.

### Template M in full

Template M is the brief the ADW Lite test writer follows. It front-loads the outcome, then the scope, then the evidence contract.

```text
## Objective
[What needs to be built, fixed, or produced — one clear sentence. Add WHY if it affects approach.]

## Context
[What exists now — relevant files, current behavior, stack already in place, what was tried and failed]

## Target State
[What done looks like — specific files changed, behavior produced, tests passing. Binary where possible.]

## Scope
- Work only in: [specific files and directories]
- Do NOT touch: [forbidden files — .env, package-lock.json, configs, anything outside scope]

## Constraints
- [Stack version, naming conventions, no new dependencies without asking]
- Only make changes directly requested. Do not add features, abstractions, or files beyond what was asked.

## Acceptance Criteria
- [ ] [Binary check 1]
- [ ] [Binary check 2]
- [ ] [Binary check 3]

## Action Boundaries
- Proceed with reversible, in-scope inspection, edits, and validation.
- Stop and ask before destructive or irreversible actions, external writes, purchases, material scope expansion, or decisions that require user-only input.

## Progress Evidence
For long-running work, report progress only when it changes or when a checkpoint is reached. Ground every completion claim in a tool result, changed artifact, or verification output.
```

*Prompt Master*, references/templates.md, Template M.

### Verification checklist

Run this check before the prompt leaves your desk.

```text
**Before delivering any prompt, verify:**

1. Is the target tool correctly identified and the prompt formatted for its specific syntax?
2. Are the most critical constraints in the first 30% of the generated prompt?
3. Does every instruction use the strongest signal word? MUST over should. NEVER over avoid.
4. Has every fabricated technique been removed?
5. Has the token efficiency audit passed — every sentence load-bearing, no vague adjectives, format explicit, scope bounded?
6. Would this prompt produce the right output on the first attempt?
```

*Prompt Master*, SKILL.md, RECENCY ZONE.

## From the owner book library

The library returned passages from five books. Each label below is the one the search tool printed, and each quote matches the returned text.

### Before you write

AI Engineering treats prompt writing as communication. “Anyone can communicate, but not everyone can communicate well. Prompt engineering is easy to get started, which misleads many into thinking that it’s easy to do it well.”

*AI Engineering*, Ch 155, Prompt Engineering.

Ambiguity is the first enemy. “If there’s an essay the model’s uncertain about, do you want it to pick a score to the best of its ability or to output “I don’t know”?”

*AI Engineering*, Ch 161, Write Clear and Explicit Instructions.

### Wording

Berryman and Ziegler give three rules of thumb for instruction wording. They want positive phrasing, as in “Ask for positives instead of negatives and dos instead of don’ts.” They want a reason attached to each command, as in “Bolster your command with a reason.” They also warn against absolutes, as in “Avoid absolutes.”

*Prompt Engineering for LLMs*, Ch 80, Clarifying Your Question.

### Output shape

Length and structure need an explicit instruction. “If you want the model to be concise, tell it so.” Preamble text needs the same treatment. The authors ask you to “make explicit that you don’t want preambles.” For structured output they add a marker. You should then “Make sure to choose markers that are unlikely to appear in your inputs.”

*AI Engineering*, Ch 165, Specify the output format.

### Examples

Google's prompt guide sets a floor for few-shot work. “As a general rule of thumb, you should use at least three to five examples for few-shot prompting.”

*Google Prompt Engineering*, pp. 13-53, Prompting techniques.

### Agent loops

Suhas Pai describes the system prompt that drives an agent loop. It can carry a condition for a self-check, “for example, when the agent performs the same action consecutively more than three times (which might mean it is stuck in a loop)”. That check has a cost. “The effectiveness of reflection-based methods are overstated. They might do more harm than good if they are invoked too often, causing the model to second-guess solutions.”

*Designing Large Language Model Applications*, Ch 221, Agent Loop Prompt.

### Evaluation

Hamel Husain puts measurement ahead of prompt tweaking. “Error analysis—the single most valuable activity in AI development and consistently the highest-ROI activity.”

*A Practical Guide to Rapidly Improving AI Products*, Ch 1.

## From the unit-testing-principles skill

The skill condenses Khorikov's book for the test writer. These parts set the bar the agent prompt asks for.

### The four pillars

A test earns its place on four scores, and a zero on any one of them sinks it.

```text
Every test scores on four attributes.

1. Protection against regressions. The test fails when a real bug enters the code it exercises.
2. Resistance to refactoring. The test stays green when you restructure the code without changing its behavior.
3. Fast feedback. The test runs quickly.
4. Maintainability. A reader understands the test fast, and the test needs little setup to run.
```

*unit-testing-principles*, SKILL.md, The four pillars.

### Observable behavior versus implementation detail

“A test must verify what the code does for its client, never how it does it. Observable behavior is an operation or a state that helps a client reach one of its goals. Everything else counts as an implementation detail. A test that pins an implementation detail breaks on refactors and produces false alarms. Chapter 5 explains the distinction and its link to mocks.”

*unit-testing-principles*, SKILL.md, Observable behavior versus implementation detail.

### The pre-write checklist

The checklist runs before the first line of test code.

```text
Run every step before you write a test. If a step fails, fix the plan before you write code.

### Decide whether the test earns its place

1. Name the behavior. Write one sentence that a domain expert understands. Put that sentence in the test name.
2. Name the bug. Describe one concrete regression that makes this test fail. If you cannot name one, do not write the test.
3. Place the code in a quadrant. Unit test domain code. Integration test controllers. Skip trivial code. Refactor overcomplicated code first.
4. Pick the style. Use output based if the code returns a value. Use state based if it changes state that a client can see. Use communication based only for calls to unmanaged dependencies.
```

*unit-testing-principles*, SKILL.md, Pre-write checklist.

One step proves the test can fail. “2. Make the test fail first. Break the production code on purpose and watch the test go red. A test that stays green with broken code is hollow.”

*unit-testing-principles*, SKILL.md, Prove the test works.

## How the adw-test-writer prompt was built

The test writer agent carries the whole method in one file. The old version shows why the method matters.

### The old prompt

The old agent lived at `~/.claude/agents/adw-test-writer.md` and held six lines of frontmatter and prose. It sent the agent to a skill inside another repository and listed no skills preload. It set no rule, asked for no citation, and checked nothing, so the agent skipped the reading and wrote tests from memory. The whole file read like this.

```text
---
name: adw-test-writer
description: Writes and maintains meaningful tests under ADW Lite.
---

Read skills/unit-testing-principles/SKILL.md in the ADW Lite checkout. Write tests using native tools. Cover observable behavior and report which regressions the tests catch.
```

The reading was a wish. Nothing in the engine noticed whether the agent opened the skill.

### The new prompt

The new prompt follows Template M for Claude Code. The objective comes first and runs one sentence. The hard rules sit in the first third, where a reader still holds them. Those rules lean on MUST and NEVER rather than hints. The rest of the file carries scope, acceptance criteria, stop conditions, and a report contract.

Template M sets the objective as the first block. The agent opens with it.

“Write or change tests that protect observable behavior.”

*agents/adw-test-writer.md*, Objective.

### The hard rules in the first third

Three rules decide whether a test may exist. The first sends the agent to the skill and gives it a way out when the skill fails to load.

“You MUST read the unit-testing-principles skill before your first test. It loads at spawn. If it did not load, run the Skill tool on adw-light:unit-testing-principles. If that fails too, stop and report.”

The second ties every test to the skill in the one way a machine can check.

“Every test MUST carry one citation, a sentence quoted word for word from the skill.”

The third removes the slow, fragile setup that made old tests expensive.

“NEVER run git init, clone, commit, add, checkout, switch, or worktree add inside a test. NEVER build a temp folder tree per test.”

*agents/adw-test-writer.md*, Hard rules.

### The skills preload

The frontmatter carries the preload, so the skill text arrives at spawn.

```text
---
name: adw-test-writer
description: The only agent allowed to write or edit tests under ADW Lite. It reads the unit-testing-principles skill first, writes small in-memory unit tests, and cites the skill in every test.
effort: medium
skills:
  - adw-light:unit-testing-principles
---
```

*agents/adw-test-writer.md*, frontmatter.

The skills field names `adw-light:unit-testing-principles`. The agent does not have to look the skill up, and the hard rule covers the case where the preload fails.

### Scope, stop conditions, and the report

Scope limits the agent to the test files the task names. The stop conditions name the moments that need a human. The report contract closes the file.

“Stop and ask before you delete a test, weaken an assertion, change an expectation without a reason from the lead, or run the full suite.”

“For each test, name the behavior it protects, the regression it catches, and the quote it cites.”

*agents/adw-test-writer.md*, Stop conditions and Report.

### Enforcement in the engine

The prompt asks and ADW Lite checks. The rule `test_principle_citation` refuses a changed test without a verified quote from the skill. The rule `test_builds_repo` refuses git setup in a test, and `test_builds_tree` reports a folder tree built per test. The `test_write_gate` rule refuses any test write made outside the adw-test-writer agent, unless the owner opens a timed window.

`tools/run_tests.py` runs the suite and writes a stamp for a clean commit. The suite has no time limit.

`tools/deploy.sh` checks the stamp without running the suite.

### The model choice

The owner set no model override and chose medium effort. The frontmatter holds `effort: medium` and no `model` field, which leaves the model to the session.

## Before you ship a prompt

1. Name the target tool before you draft.
2. State the outcome in one sentence and put it first.
3. Place the hard rules in the first third and write them with MUST and NEVER.
4. Lock the scope to named files and directories.
5. Give binary acceptance criteria.
6. Write the stop conditions and the review triggers.
7. Ask for the report the caller needs.
8. Run the verification checklist and one eval pass on real data.

These eight checks condense the three sources above. The sources hold the detail behind each one.

