from __future__ import annotations

from typing import NamedTuple

try:
    from .catalog_phrases import PHRASES
    from . import config, german_rules
except ImportError:
    from catalog_phrases import PHRASES
    import config
    import german_rules


class Entry(NamedTuple):

    title: str
    description: str


RULES: dict[str, Entry] = {
    "after_run_deferred": Entry(
        "Commit scan incomplete",
        "The commit scan could not finish and must run again at Stop",
    ),
    "ai_closer": Entry(
        "Wrap-up flourish",
        "Cuts the closing gesture that restates the point instead of ending",
    ),
    "assert_in_loop": Entry(
        "Assertion inside a loop",
        "Flags a test loop whose first failing case hides the rest",
    ),
    **{key: Entry(*value) for key, value in PHRASES.items()},
    "de_uniform_rhythm": Entry(
        "Uniform German sentence rhythm",
        "Flags German sentences whose lengths barely vary across the document",
    ),
    "deferred_work_comment": Entry(
        "Deferred work comment",
        "Reports a TODO, FIXME, or HACK marker that misses the tag, verb, object, and purpose shape, and every DONE, XXX, BUG, or OPTIMIZE marker",
    ),
    "agent_marker_author": Entry(
        "Work marker signed by an AI agent",
        "Blocks a new TODO, FIXME, or HACK marker whose author in brackets names an AI agent such as claude or codex",
    ),
    "comment_shape": Entry(
        "Comment fits no allowed shape",
        "Reports a comment that runs past one sentence or 25 words, restates the code, or fits none of the six why shapes",
    ),
    "dramatic_fragmentation": Entry(
        "Fragments used for drama",
        "Cuts the one-word paragraph that leans on white space for weight",
    ),
    "emphasis_crutch": Entry(
        "Manufactured emphasis",
        "Cuts full stop, let that sink in, make no mistake, and this matters because",
    ),
    "false_agency": Entry(
        "Objects acting on their own",
        "Cuts phrasing that hands agency to a thing that cannot act",
    ),
    "file_length_critical": Entry(
        "File far past its limit",
        "Reports a file grown well beyond the configured length",
    ),
    "file_length_warning": Entry(
        "File nearing its limit",
        "Reports a file approaching the configured length before it blocks",
    ),
    "filler_phrase": Entry(
        "Filler openers",
        "Cuts at its core, when it comes to, the reality is, and in today's X",
    ),
    "formulaic_construction": Entry(
        "Template sentence shapes",
        "Cuts the repeated structural mould that marks generated prose",
    ),
    "formulaic_filler": Entry(
        "Balanced-hands filler",
        "Cuts it is important to note, on one hand, and first and foremost",
    ),
    "formulaic_opener": Entry(
        "Stock opening line",
        "Cuts in a world where, imagine this, and whether you are a beginner",
    ),
    "genitive_apostrophe": Entry(
        "English genitive apostrophe",
        "Reports Peter's in German prose, where the genitive takes a bare s",
    ),
    "greeting_opener": Entry(
        "Greeting before the answer",
        "Cuts the salutation that delays the first useful sentence",
    ),
    "hardcoded_literal_in_source": Entry(
        "Test restates the source",
        "Flags a test that reads a source or config file and asserts a literal in its text",
    ),
    "hardcoded_name_presence": Entry(
        "Test pins a hard-coded name",
        "Flags a test that only checks a literal name or value sits in a static table",
    ),
    "hedge_stack": Entry(
        "Stacked hedges",
        "Cuts piled qualifiers that soften a claim until it says nothing",
    ),
    "lazy_extreme": Entry(
        "Absolute claims",
        "Cuts every, always, never, and nothing where the claim is not absolute",
    ),
    "low_sentence_variance": Entry(
        "Uniform sentence length",
        "Reports prose where every sentence runs the same length",
    ),
    "throat_clearing_idiom": Entry(
        "Lead-in idiom",
        "Reports a stock lead-in outside the short clause pattern",
    ),
    "meta_commentary_idiom": Entry(
        "Writing aside",
        "Reports an aside outside the reader and document patterns",
    ),
    "meta_commentary": Entry(
        "Asides about the writing",
        "Cuts hint, plot twist, spoiler, and is a feature not a bug",
    ),
    "multiple_act_sections_in_unit_test": Entry(
        "Second act inside one test",
        "Flags a test that calls the unit under test, asserts, then calls it and asserts again",
    ),
    "narrator_distance": Entry(
        "Narrator steps back",
        "Cuts the move that comments on the subject instead of stating it",
    ),
    "negative_listing": Entry(
        "Defining by what it is not",
        "Cuts the run of negations that describes nothing concrete",
    ),
    "oversized_list": Entry(
        "List too long to scan",
        "Reports a list past the item cap, which defaults to 8 items",
    ),
    "ste_phrasal_verb": Entry(
        "Phrasal verb",
        "Asks for the one plain verb behind spin up, reach out, or kick off",
    ),
    "ste_marketing_adjective": Entry(
        "Marketing adjective",
        "Reports effortless, world-class, state-of-the-art, and an unmeasured superlative",
    ),
    "ste_nominalization": Entry(
        "Action frozen into a noun",
        "Asks for analyze in place of perform an analysis of",
    ),
    "ste_compound_tense": Entry(
        "Compound tense",
        "Reports has or had with a participle where simple past is enough",
    ),
    "ste_sentence_length": Entry(
        "Sentence over 25 words",
        "Reports a sentence longer than the 25-word descriptive cap",
    ),
    "ste_synonym_rotation": Entry(
        "One action, two verbs",
        "Reports check and verify for the same action in one document",
    ),
    "ste_dangling_conjunction": Entry(
        "List item ends on a conjunction",
        "Reports a list item that breaks off after and, or, but, or nor",
    ),
    "passive_voice": Entry(
        "Actor hidden by passive",
        "Asks for the actor and an active verb in place of the passive",
    ),
    "performative_emphasis": Entry(
        "Performed sincerity",
        "Cuts creeps in, they exist I promise, and I promise",
    ),
    "quote_marks": Entry(
        "Quote marks not German",
        "Reports straight or English quote marks in German prose, which takes „…“ and ‚…‘",
    ),
    "rhetorical_setup": Entry(
        "Question asked to answer it",
        "Cuts the rhetorical question that stages a point already underway",
    ),
    "telling_not_showing": Entry(
        "Asserting instead of showing",
        "Cuts this is genuinely hard and actually matters in place of evidence",
    ),
    "test_fixture_reuse_via_constructor": Entry(
        "Constructor-built fixture reused",
        "Flags a test method that calls into an object setUp or __init__ built",
    ),
    "three_item_list": Entry(
        "Three-item rhythm",
        "Sends the triad to a judge, because three items can be real or a cadence tic",
    ),
    "throat_clearing_opener": Entry(
        "Warm-up before the point",
        "Cuts here is the thing, it turns out, and the real X is",
    ),
    "typed_ellipsis": Entry(
        "Ellipsis typed as periods",
        "Reports three typed periods or an extra point where German takes one … character",
    ),
    "uniform_paragraph_endings": Entry(
        "Paragraphs ending alike",
        "Reports prose where paragraph after paragraph closes the same way",
    ),
    "vague_declarative": Entry(
        "Grand claim without content",
        "Cuts the reasons are structural and the implications are significant",
    ),
    "weak_sentence_starter": Entry(
        "Buried subject",
        "Cuts there is and it is openings that push the real subject back",
    ),
    "weighted_slop_marker": Entry(
        "Marker density too high",
        "Reports prose where weighted generated-text markers cluster past the threshold",
    ),
    "config_seal": Entry(
        "Project config is sealed",
        "Blocks creating a governing config or editing its enforcement settings",
    ),
    "docstring_narration": Entry(
        "Docstring narrates the code",
        "States what the code does rather than why it exists",
    ),
    "file_too_long": Entry(
        "File past the hard limit",
        "Grows a file beyond the length the policy allows",
    ),
    "prose_comment_block": Entry(
        "Comment block reads as prose",
        "Carries paragraphs of narration where the code should speak",
    ),
    "state_deletion": Entry(
        "Watcher state deleted",
        "Removes watcher state or gate config instead of repairing the finding",
    ),
    "state_mutation": Entry(
        "Watcher state altered",
        "Edits watcher state or gate config that belongs to the host",
    ),
    "suppression_escape_hatch": Entry(
        "Suppression marker added",
        "Silences a finding through an ignore marker rather than fixing the cause",
    ),
    "test_write_gate": Entry(
        "Test write outside the test writer",
        "Changes a test while the project policy reserves tests for the test writer agent",
    ),
    "wording_debt_lock": Entry(
        "Existing wording tests",
        "Reports existing wording tests without blocking test changes",
    ),
    "wording_test_gap": Entry(
        "Test file the gate could not scan",
        "Reports a test file the gate could not parse and leaves it out of the wording test count",
    ),
    "test_writer_definition": Entry(
        "Test writer definition edited",
        "Rewrites the adw-test-writer agent file that the test gate trusts",
    ),
    "unscannable_file": Entry(
        "File cannot be scanned",
        "Writes content in a form the scanner cannot read and therefore cannot gate",
    ),
    "watcher_install_surface": Entry(
        "Live install edited",
        "Mutates the installed watcher instead of changing the repo and reinstalling",
    ),
    "watcher_wiring_removal": Entry(
        "Hook wiring removed",
        "Unwires a hook event so the gate stops running",
    ),
    "weak_why_comment": Entry(
        "Why comment says nothing",
        "Labels a case by letter or apologises where a reason belongs",
    ),
    "unresolved_doc_reference": Entry("Unknown documentation reference", "Links to a name not found in this file"),
    "what_comment": Entry(
        "Comment restates the code",
        "Narrates the line below instead of explaining why it exists",
    ),
    "what_docstring": Entry(
        "Docstring restates the signature",
        "Repeats the parameters rather than naming why the function exists",
    ),
    "if_statements_in_tests": Entry(
        "Branching test body",
        "Reports a test that picks between two different asserts",
    ),
    "exposing_private_methods_for_testing": Entry(
        "Private method called from a test",
        "Reports a test that calls or patches a name behind one leading underscore",
    ),
    "exposing_private_state_for_testing": Entry(
        "Private attribute read from a test",
        "Reports a test that reads or sets a field behind one leading underscore",
    ),
    "mocking_concrete_classes": Entry(
        "Mock built from a concrete class",
        "Flags a mock or patch built straight from a class the file defines, not an interface",
    ),
    "incomplete_mock_call_verification": Entry(
        "Mock call check skips the arguments",
        "Flags a bare assert_called or assert_called_once with no argument check",
    ),
    "hard_coded_path": Entry(
        "Hard-coded machine path",
        "Flags a literal home path, a drive letter, or a local username",
    ),
    "hard_coded_system_path": Entry(
        "Hard-coded system path",
        "Notes a literal system or temporary path that may differ between machines",
    ),
    "wording_assertion": Entry(
        "Test checks wording",
        "Blocks a test whose expected value is a prose sentence, because a text edit turns it red while the code still works",
    ),
    "test_principle_citation": Entry("Missing test principle", "Requires a verified quote from the unit testing skill on each changed test"),
    "test_builds_repo": Entry("Test builds a repository", "Blocks test code that creates or changes a Git repository"),
    "test_builds_tree": Entry("Setup writes a temporary tree", "Reports per-test setup that writes files into a temporary directory"),
    "snapshot_assertion": Entry(
        "Test compares against a stored copy",
        "Reports a test that checks output against a snapshot or golden file, without blocking the write",
    ),
    "wording_exception_match": Entry(
        "Test matches an exception message",
        "Blocks a test that matches prose in an exception or warning message, because a text edit turns it red while the code still works",
    ),
    "wording_log_assertion": Entry(
        "Test checks a log sentence",
        "Blocks a test that expects a sentence from captured logs, because a text edit turns it red while the logging still works",
    ),
    "literal_location": Entry(
        "Test types out a file and line",
        "Blocks a test that expects a file and line value typed out as a literal instead of built from the fixture variable",
    ),
    "wording_ui_locator": Entry(
        "Test finds an element by its text",
        "Blocks a UI test that finds an element by visible prose, such as getByText or contains, because a copy edit turns it red while the page still works",
    ),
    **{rule.name: Entry(rule.english.title, rule.english.description) for rule in german_rules.declared()},
}

UNGATED_RULES: dict[str, Entry] = {
    "bug_label_comment": Entry("Case label in a comment", "Flags lettered or numbered bug labels where a named test belongs"),
    "apology_comment": Entry("Comment apologises for code", "Flags an apology that replaces a concrete design reason"),
    "commented_code": Entry("Code left in a comment", "Flags disabled code that belongs in version history"),
    "version_control_comment": Entry("Comment records a code change", "Flags change history that belongs in the commit message"),
    "long_comment": Entry("Comment exceeds the length cap", "Flags a comment past 80 characters that needs a shorter reason"),
    "narration_comment": Entry("Comment narrates an operation", "Flags a now-we or this-function opening instead of a design reason"),
    "banned_dash": Entry("Dash character", "Blocks em dash, en dash, and their look-alikes"),
    "dash_break": Entry("Double hyphen as a dash", "Blocks a double hyphen used as a clause break"),
    "spaced_hyphen": Entry("Spaced hyphen as a dash", "Blocks a hyphen with spaces standing in for a dash"),
    "prose_semicolon": Entry("Semicolon in prose", "Blocks a semicolon joining two clauses"),
    "prose_colon": Entry("Colon inside a sentence", "Allows sentence colons in Markdown documents and blocks other text above the configured count"),
    "pronoun_apostrophe": Entry("Apostrophe on a pronoun", "Blocks an apostrophe on a possessive pronoun"),
    "decade_apostrophe": Entry("Apostrophe on a decade", "Blocks a decade written as a possessive"),
    "dead_metaphor": Entry("Worn-out metaphor", "Cuts a stock image that names nothing concrete"),
    "filler": Entry("Filler phrase", "Cuts a phrase that adds words and no meaning"),
    "throat_clearing": Entry("Throat clearing", "Cuts an opener that delays the point"),
    "filler_opener": Entry("Stock opener", "Cuts an opening that sets a scene instead of stating the point"),
    "ai_tell": Entry("Generated-text phrase", "Cuts a phrase typical of generated prose"),
    "inflated_diction": Entry("Inflated word", "Cuts a grand word where a plain one works"),
    "utilize": Entry("Long word for use", "Cuts utilize and its forms"),
    "wordiness": Entry("Wordy phrase", "Cuts a long phrase with a short equivalent"),
    "vague_quantity": Entry("Vague quantity", "Cuts a phrase that hides the real number"),
    "expletive_there": Entry("There-is opening", "Cuts a there-is opening that hides the subject"),
    "empty_intensifier": Entry("Empty intensifier", "Cuts a modifier that adds emphasis and no meaning"),
    "function_too_long": Entry("Function too long", "Blocks a function over the configured line count"),
    "hollow_test": Entry("Test without an assertion", "Blocks a test that cannot fail"),
}

LOCKED_RULES = frozenset(config.ALWAYS_BLOCKING_RULES)

FAMILIES: dict[str, Entry] = {
    "prose": Entry(
        "Prose",
        "Bans dash characters and semicolon splices, and cuts filler and generated-text patterns",
    ),
    "comment": Entry(
        "Comment check",
        "Blocks comments and docstrings that narrate code or apologize, and reports comments and work markers that fit no allowed shape",
    ),
    "code": Entry(
        "Code check",
        "Blocks dead code, hollow tests, suppression markers, and oversized functions and files",
    ),
}

THRESHOLDS: dict[str, Entry] = {
    "max_rows": Entry(
        "Findings shown per file",
        "How many rows one report lists before it points to the full report. At most 5",
    ),
    "list_item_cap": Entry(
        "List item limit",
        "Item count that trips the oversized list rule. Default 8",
    ),
}

RULE_STATES: dict[str, Entry] = {
    "off": Entry("Off", "The rule never runs and reports nothing"),
    "observe": Entry("Reports only", "The finding reaches the report and the write goes through"),
    "enforce": Entry("Blocks", "The finding stops the write until you repair it"),
    "judged": Entry("Model decides", "A judge reads each finding and blocks only what it confirms"),
}

FAMILY_STATES: dict[str, Entry] = {
    "off": Entry("Off", "No rule in this family runs"),
    "observe": Entry("Reports only", "Findings reach the report and writes go through"),
    "enforce": Entry("Blocks", "Findings stop the write until you repair them"),
}

BASELINE_MODES: dict[str, Entry] = {
    "git": Entry(
        "Compare against git",
        "Splits findings against the committed baseline, so inherited debt reports without blocking",
    ),
    "report": Entry("Report everything", "Every finding surfaces with no baseline comparison"),
    "none": Entry("No baseline", "Findings cover the whole file with no inherited-debt allowance"),
}

LOCKED_STATE = Entry(
    "Always blocks",
    "This rule guards the gate itself, and no project config can change it",
)

_UNWRITTEN = "No wording written for this name yet"


def _derived(name: str) -> Entry:
    return Entry(name.replace("_", " ").capitalize(), _UNWRITTEN)


def rule_entry(name: str) -> Entry:
    return RULES.get(name) or UNGATED_RULES.get(name) or _derived(name)


def family_entry(name: str) -> Entry:
    return FAMILIES.get(name) or _derived(name)


def threshold_entry(name: str) -> Entry:
    return THRESHOLDS.get(name) or _derived(name)


def state_entry(name: str, *, locked: bool) -> Entry:
    if locked:
        return LOCKED_STATE
    return RULE_STATES.get(name) or _derived(name)


def family_state_entry(name: str) -> Entry:
    return FAMILY_STATES.get(name) or _derived(name)


def baseline_entry(name: str) -> Entry:
    return BASELINE_MODES.get(name) or _derived(name)
