import json
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "adw_light.py"
LISTED_RULE_RE = re.compile(r"^\d+\. .*\((\w+)\)$", re.M)
DENY = "deny"
OBSERVE = "observe"
NONE = "none"


class Case(NamedTuple):
    name: str
    path: str
    content: str
    kind: str
    rules: tuple[str, ...]


class Outcome(NamedTuple):
    kind: str
    rules: tuple[str, ...]


CASES = (
    Case("a1_weighted_slop_marker_three_markers_in_a_short_sentence", "notes.md", "The robust design is pivotal and seamless.\n", OBSERVE, ("ste_marketing_adjective", "weighted_slop_marker")),
    Case("a1_weighted_slop_marker_two_markers_stay_under_the_count", "notes.md", "The robust design is pivotal for the build.\n", OBSERVE, ("ste_marketing_adjective",)),
    Case("a2_formulaic_opener_in_the_first_300_characters", "notes.md", "The rise of caching sped up the build.\n", OBSERVE, ("formulaic_opener", "ste_phrasal_verb")),
    Case("a2_formulaic_opener_different_head_noun", "notes.md", "The growth of caching sped up the build.\n", OBSERVE, ("ste_phrasal_verb",)),
    Case("a2_formulaic_opener_after_the_first_300_characters", "notes.md", "The cache holds each build result for the next run on the same host.\n\nThe cache holds each build result for the next run on the same host.\n\nThe cache holds each build result for the next run on the same host.\n\nThe cache holds each build result for the next run on the same host.\n\nThe cache holds each build result for the next run on the same host.\n\nThe rise of caching sped up the build.\n", OBSERVE, ("ste_phrasal_verb",)),
    Case("a3_formulaic_closer_list_has_no_consumer", "notes.md", "In conclusion, the build passed.\n", NONE, ()),
    Case("a4_formulaic_filler_without_a_doubt", "notes.md", "Without a doubt the build passed.\n", OBSERVE, ("formulaic_filler",)),
    Case("a4_formulaic_filler_without_the_noun", "notes.md", "Without the cache the build passed.\n", NONE, ()),
    Case("a5_it_turns_out_observes_as_throat_clearing_idiom", "notes.md", "It turns out the cache was empty.\n", OBSERVE, ("throat_clearing_idiom",)),
    Case("a5_throat_clearing_opener_past_tense", "notes.md", "It turned out the cache was empty.\n", NONE, ()),
    Case("a6_emphasis_crutch_let_that_sink_in", "notes.md", "The cache was empty. Let that sink in.\n", OBSERVE, ("emphasis_crutch",)),
    Case("a6_emphasis_crutch_plain_let_sentence", "notes.md", "The cache was empty. Let the job refill it.\n", NONE, ()),
    Case("a7_meta_commentary_walk_you_through", "notes.md", "Let me walk you through the build.\n", DENY, ("meta_commentary",)),
    Case("a7_meta_commentary_plain_let_me", "notes.md", "Let me run the build.\n", NONE, ()),
    Case("a8_bare_claim_blocks_on_banned_adverb_alone", "notes.md", "This actually matters.\n", DENY, ("banned_adverb",)),
    Case("a8_telling_not_showing_grounded_by_a_because_clause", "notes.md", "This actually matters because the parser drops every second packet after a reset.\n", NONE, ()),
    Case("a9_vague_declarative_stakes_are_high", "notes.md", "The stakes are high.\n", OBSERVE, ("vague_declarative",)),
    Case("a9_vague_declarative_other_verb", "notes.md", "The stakes stay high.\n", NONE, ()),
    Case("a10_performative_emphasis_i_promise", "notes.md", "I promise the build passes.\n", OBSERVE, ("performative_emphasis",)),
    Case("a10_performative_emphasis_we_promise", "notes.md", "We promise the build passes.\n", NONE, ()),
    Case("a11_banned_adverb_simply_before_a_verb", "notes.md", "The build simply passed.\n", DENY, ("banned_adverb",)),
    Case("a11_banned_adverb_simply_before_a_technical_object", "notes.md", "The parser simply returns the cached value.\n", NONE, ()),
    Case("a12_business_jargon_domain_landscape", "notes.md", "The security landscape shifts every year.\n", DENY, ("business_jargon",)),
    Case("a12_business_jargon_literal_landscape", "notes.md", "A landscape photo hangs in the hall.\n", NONE, ()),
    Case("a13_filler_phrase_when_it_comes_to", "notes.md", "When it comes to caching, the build wins.\n", OBSERVE, ("filler_phrase",)),
    Case("a13_filler_phrase_literal_comes_back", "notes.md", "When the cache comes back, the build wins.\n", OBSERVE, ("ste_phrasal_verb",)),
    Case("a14_technical_object_absent_keeps_completely_flagged", "notes.md", "The job completely fills the room.\n", DENY, ("banned_adverb",)),
    Case("a14_technical_object_buffer_exempts_completely", "notes.md", "The job completely fills the buffer.\n", NONE, ()),
    Case("a15_measurable_property_absent_keeps_simply_flagged", "notes.md", "The build is simply great.\n", DENY, ("banned_adverb",)),
    Case("a15_measurable_property_fast_exempts_simply", "notes.md", "The build is simply fast.\n", NONE, ()),
    Case("a16_false_agency_data_tells_us", "notes.md", "The data tells us the cache works.\n", OBSERVE, ("false_agency",)),
    Case("a16_false_agency_noun_outside_the_list", "notes.md", "The log tells us the cache works.\n", NONE, ()),
    Case("a17_narrator_distance_people_believe", "notes.md", "Many people believe the cache works.\n", DENY, ("narrator_distance",)),
    Case("a17_narrator_distance_users_report", "notes.md", "Many users report the cache works.\n", NONE, ()),
    Case("a17_widely_believed_that_blocks_as_passive_voice", "notes.md", "It is widely believed that the cache works.\n", DENY, ("passive_voice",)),
    Case("a17_impersonal_believed_that_blocks_as_passive_voice_alone", "notes.md", "It is believed that the cache works.\n", DENY, ("passive_voice",)),
    Case("a18_rhetorical_setup_have_you_ever_wondered", "notes.md", "Have you ever wondered why the build fails?\n", OBSERVE, ("rhetorical_setup",)),
    Case("a18_rhetorical_setup_plain_question", "notes.md", "Have you checked why the build fails?\n", NONE, ()),
    Case("a19_weak_sentence_starter_question_word_opener", "notes.md", "Why does the build fail on Mondays?\n", DENY, ("weak_sentence_starter",)),
    Case("a19_weak_sentence_starter_subject_opener", "notes.md", "The build fails on Mondays.\n", NONE, ()),
    Case("a20_ai_closer_hope_this_helps", "notes.md", "The build passed. Hope this helps.\n", OBSERVE, ("ai_closer",)),
    Case("a20_ai_closer_plain_helps", "notes.md", "The build passed. The cache helps.\n", NONE, ()),
    Case("a20_greeting_opener_hello_there", "notes.md", "Hello there, the build passed.\n", OBSERVE, ("greeting_opener",)),
    Case("a20_greeting_opener_hello_world", "notes.md", "Hello world prints first.\n", NONE, ()),
    Case("a20_hedge_stack_two_hedges", "notes.md", "The run might possibly fail.\n", OBSERVE, ("hedge_stack",)),
    Case("a20_hedge_stack_single_modal", "notes.md", "The run might fail.\n", NONE, ()),
    Case("a20_corporate_idiom_circle_back", "notes.md", "We circle back on the build tomorrow.\n", OBSERVE, ("corporate_idiom",)),
    Case("a20_corporate_idiom_circle_back_to_a_technical_object", "notes.md", "We circle back to the parser.\n", NONE, ()),
    Case("a21_what_opener_with_unless_blocks_because_unless_is_no_causal_word", "app.js", "// Returns the row unless the cache is empty\nconst row = 1;\n", DENY, ("weak_why_comment", "what_comment")),
    Case("a21_what_opener_with_a_concrete_because", "app.js", "// Returns early because the vendor feed repeats keys\nconst row = 1;\n", NONE, ()),
    Case("a21_verb_outside_the_opener_list_with_unless_blocks_because_unless_is_no_causal_word", "app.js", "// Retries unless the vendor is down\nconst row = 1;\n", DENY, ("weak_why_comment", "what_comment")),
    Case("a22_vague_reason_word", "app.js", "// Keep the row because reasons\nconst row = 1;\n", DENY, ("weak_why_comment", "what_comment")),
    Case("a22_vague_reason_concrete", "app.js", "// Keep the row because the vendor feed repeats keys\nconst row = 1;\n", NONE, ()),
    Case("a22_generic_reason_word_passes_as_an_accepted_shape", "app.js", "// Keep the row because it breaks parser\nconst row = 1;\n", NONE, ()),
    Case("a22_specific_verb_leaves_two_tokens", "app.js", "// Keep the row because it crashes parser\nconst row = 1;\n", NONE, ()),
    Case("a23_noun_first_opener_with_a_concrete_because_passes", "app.js", "// Probe runs every hour because the token expires\nconst row = 1;\n", NONE, ()),
    Case("a23_bare_subject_opener_skips_an_imperative_first", "app.js", "// Check runs every hour because the token expires\nconst row = 1;\n", NONE, ()),
    Case("a24_version_control_comment_changed_to", "app.js", "// Changed to UTC because the vendor sends local time\nconst row = 1;\n", DENY, ("version_control_comment",)),
    Case("a24_version_control_comment_swapped_to_fires_without_a_verb_list", "app.js", "// Swapped to UTC because the vendor sends local time\nconst row = 1;\n", DENY, ("version_control_comment",)),
    Case("a25_apology_comment_ugly", "app.js", "// ugly because the vendor sends local time\nconst row = 1;\n", OBSERVE, ("apology_comment",)),
    Case("a25_apology_comment_odd", "app.js", "// odd because the vendor sends local time\nconst row = 1;\n", NONE, ()),
    Case("a25_workaround_for_comment_blocks_as_what_comment_alone", "app.js", "// workaround for the vendor clock drift\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("a26_dead_metaphor_smoking_gun", "notes.md", "The log is the smoking gun.\n", OBSERVE, ("dead_metaphor",)),
    Case("a26_dead_metaphor_literal_smoking", "notes.md", "The gun is smoking.\n", NONE, ()),
    Case("a26_filler_needless_to_say", "notes.md", "Needless to say, the build passed.\n", OBSERVE, ("filler",)),
    Case("a26_filler_needless_as_an_adjective", "notes.md", "Needless work slowed the build.\n", NONE, ()),
    Case("a26_filler_end_of_the_day", "notes.md", "At the end of the day, the build passed.\n", OBSERVE, ("filler",)),
    Case("a26_filler_end_of_the_day_as_a_time", "notes.md", "At the end of the day the backup job completes.\n", NONE, ()),
    Case("a26_throat_clearing_worth_noting", "notes.md", "It's worth noting that the build passed.\n", DENY, ("throat_clearing",)),
    Case("a26_throat_clearing_worth_the_wait", "notes.md", "It's worth the wait.\n", NONE, ()),
    Case("a26_filler_opener_in_todays_digital", "notes.md", "In today's digital world the build passed.\n", DENY, ("filler_opener",)),
    Case("a26_filler_opener_other_adjective_leaves_filler_phrase", "notes.md", "In today's run the build passed.\n", OBSERVE, ("filler_phrase",)),
    Case("a26_ai_tell_delve_into", "notes.md", "We delve into the logs.\n", OBSERVE, ("ai_tell",)),
    Case("a26_ai_tell_dig_into", "notes.md", "We dig into the logs.\n", NONE, ()),
    Case("a26_inflated_diction_testament", "notes.md", "The result is a testament to the cache.\n", OBSERVE, ("inflated_diction",)),
    Case("a26_inflated_diction_test_of", "notes.md", "The result is a test of the cache.\n", NONE, ()),
    Case("a26_inflated_diction_leverage", "notes.md", "We leverage the cache.\n", OBSERVE, ("inflated_diction",)),
    Case("a26_utilize", "notes.md", "We utilize the cache.\n", DENY, ("utilize",)),
    Case("a26_utilize_plain_use", "notes.md", "We use the cache.\n", NONE, ()),
    Case("a26_wordiness_in_order_to", "notes.md", "We cache the rows in order to save time.\n", DENY, ("wordiness",)),
    Case("a26_wordiness_plain_to", "notes.md", "We cache the rows to save time.\n", NONE, ()),
    Case("a26_vague_quantity_wide_variety", "notes.md", "The tool reads a wide variety of formats.\n", OBSERVE, ("vague_quantity",)),
    Case("a26_vague_quantity_number", "notes.md", "The tool reads four formats.\n", NONE, ()),
    Case("a26_expletive_there_with_that", "notes.md", "There is one file that fails the check.\n", DENY, ("expletive_there",)),
    Case("a26_expletive_there_without_that", "notes.md", "There is one file in the folder.\n", NONE, ()),
    Case("a26_empty_intensifier_very", "notes.md", "The build is very slow.\n", DENY, ("empty_intensifier",)),
    Case("a26_empty_intensifier_bare_adjective", "notes.md", "The build is slow.\n", NONE, ()),
    Case("a27_quoted_phrase_without_a_mention_word", "notes.md", "The guide uses \"let me walk you through\" twice.\n", OBSERVE, ("meta_commentary_idiom",)),
    Case("a27_quoted_phrase_after_a_mention_word", "notes.md", "The guide uses the phrase \"let me walk you through\" twice.\n", NONE, ()),
    Case("a28_de_repeated_connector_two_openers", "notes.md", "Das gilt für die Tests. Außerdem gilt es für die Pakete. Ferner gilt es für die Builds.\n", OBSERVE, ("de_repeated_connector",)),
    Case("a28_de_repeated_connector_one_opener", "notes.md", "Das gilt für die Tests. Außerdem gilt es für die Pakete. Es gilt auch für die Builds.\n", NONE, ()),
    Case("b_parataxis_english_choppy_run_is_judged", "notes.md", "The hook reads the file. It checks every rule. It writes the log.\n", NONE, ()),
    Case("b_parataxis_german_choppy_run_is_judged", "notes.md", "Der Hook liest die Datei. Er prüft jede Regel. Er schreibt das Protokoll.\n", NONE, ()),
    Case("b_adjective_endings_german_article_run_is_judged", "notes.md", "Der Hook liest die Datei. Die neue Regel prüft alles. Die Datei bleibt offen.\n", NONE, ()),
    Case("b_three_item_list_is_judged", "notes.md", "The hook reads files, checks rules, and writes logs.\n", NONE, ()),
    Case("b_stop_words_keep_english_paragraph_out_of_german_rules", "notes.md", "Außerdem the build passed for the tests. Ferner the cache stayed warm for the runs.\n", NONE, ()),
    Case("b_german_abbreviation_hides_a_sentence_start", "notes.md", "Das gilt für die Tests z. B. Außerdem für die Pakete. Ferner gilt es für die Builds.\n", NONE, ()),
    Case("b_german_abbreviation_control_without_the_abbreviation", "notes.md", "Das gilt für die Tests zum Beispiel. Außerdem für die Pakete. Ferner gilt es für die Builds.\n", OBSERVE, ("de_repeated_connector",)),
    Case("b_german_uniform_rhythm_rule_is_off", "notes.md", "Der Hook liest die Datei sofort. Er prüft jede Regel genau. Er schreibt das Protokoll neu. Er meldet jeden Fehler sofort. Er speichert den Stand dann. Er liest die Liste erneut. Er prüft den Pfad danach. Er schließt die Datei dann. Er beendet den Lauf sofort.\n", NONE, ()),
    Case("b_irregular_participle_in_the_list", "notes.md", "The cache was built by the job.\n", DENY, ("passive_voice",)),
    Case("b_irregular_participle_shown_flags_passive_voice", "notes.md", "The report was shown to the team.\n", DENY, ("passive_voice",)),
    Case("b_not_participles_exempt_open", "notes.md", "The door is open.\n", NONE, ()),
    Case("b_not_participles_regular_participle_still_flags", "notes.md", "The door is opened by the job.\n", DENY, ("passive_voice",)),
    Case("b_lazy_extreme_everyone_knows", "notes.md", "Everyone knows the cache works.\n", DENY, ("lazy_extreme",)),
    Case("b_lazy_extreme_everyone_said", "notes.md", "Everyone said the cache works.\n", NONE, ()),
    Case("b_content_stopwords_leave_one_reason_token", "app.js", "// Keep the row because of the queue\nconst row = 1;\n", DENY, ("weak_why_comment", "what_comment")),
    Case("b_content_stopwords_two_reason_tokens_pass_as_an_accepted_shape", "app.js", "// Keep the row because of queue size\nconst row = 1;\n", NONE, ()),
    Case("b_function_word_first_with_a_pronoun_reason_passes_as_an_accepted_shape", "app.js", "// Only reads the header because the vendor repeats it\nconst row = 1;\n", NONE, ()),
    Case("b_function_word_absent_bare_subject_opener_passes_as_an_accepted_shape", "app.js", "// Loader reads the header because the vendor repeats it\nconst row = 1;\n", NONE, ()),
    Case("b_pronoun_apostrophe_theirs", "notes.md", "The file is their's.\n", DENY, ("pronoun_apostrophe",)),
    Case("b_pronoun_apostrophe_plain_theirs", "notes.md", "The file is theirs.\n", NONE, ()),
    Case("g_heres_the_thing_straight_apostrophe_observes_as_throat_clearing_idiom", "notes.md", "Here's the thing about the cache.\n", OBSERVE, ("throat_clearing_idiom",)),
    Case("g_heres_the_thing_curly_apostrophe_matches_like_the_straight_one", "notes.md", "Here’s the thing about the cache.\n", OBSERVE, ("throat_clearing_idiom",)),
    Case("c_restatement_cast_to_int", "app.js", "// Cast value to int before compare\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("c_restatement_append_to_buffer", "app.js", "// Append line to output buffer\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("c_restatement_scale_to_units", "app.js", "// Scale width to pixel units\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("c_restatement_use_helper_to_build", "app.js", "// Use the helper to build query string\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("c_restatement_call_api_to_fetch", "app.js", "// Call the API to fetch user data\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("c_restatement_loop_to_find", "app.js", "// Loop over items to find match\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("c_restatement_set_flag_to_true", "app.js", "// Set the flag to true for the next request\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("c_restatement_save_to_table", "app.js", "// Save user to database table\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("c_restatement_reset_to_zero", "app.js", "// Reset counter to zero value\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("c_restatement_bind_to_port", "app.js", "// Bind server to local port\nconst row = 1;\n", DENY, ("what_comment",)),
    Case("c_so_as_to_purpose_passes_as_an_accepted_shape", "app.js", "// Call the API so as to fetch user data\nconst row = 1;\n", NONE, ()),
    Case("c_restatement_imperative_lead_before_which", "app.js", "// Read the config, which holds the port number\nconst row = 1;\n", DENY, ("weak_why_comment", "what_comment")),
    Case("c_python_restatement_set_flag_keeps_what_comment", "app.py", "# Set the flag to true for the next request\nflag = True\n", DENY, ("added_comment", "what_comment")),
    Case("c_python_restatement_save_to_table_keeps_what_comment", "app.py", "# Save user to database table\nsession.add(u)\n", DENY, ("added_comment", "what_comment")),
    Case("c_python_restatement_reset_to_zero_keeps_what_comment", "app.py", "# Reset counter to zero value\nn = 0\n", DENY, ("added_comment", "what_comment")),
    Case("c_python_restatement_bind_to_port_keeps_what_comment", "app.py", "# Bind server to local port\nsock.bind(addr)\n", DENY, ("added_comment", "what_comment")),
    Case("c_python_restatement_move_to_end_is_no_change_history", "app.py", "# Move cursor to end of line\ncur = len\n", DENY, ("added_comment", "what_comment")),
    Case("c_real_reason_to_avoid_passes", "app.js", "// Sort keys to avoid nondeterministic output\nconst row = 1;\n", NONE, ()),
    Case("c_real_reason_to_prevent_passes", "app.js", "// Lock first to prevent a double charge\nconst row = 1;\n", NONE, ()),
    Case("c_real_reason_unless_with_a_named_subject_passes", "app.js", "// Safari caches 301s forever unless the URL changes\nconst row = 1;\n", NONE, ()),
    Case("c_real_reason_which_with_a_noun_lead_passes", "app.js", "// The server drops idle sockets, which breaks long polls\nconst row = 1;\n", NONE, ()),
    Case("c_real_reason_above_the_interval_it_explains_passes", "app.js", "// Probe runs every hour because the token expires\nsetInterval(probe, HOUR);\n", NONE, ()),
    Case("qa_label_line_status_is_no_throat_clearing_opener", "notes.md", "Status: passed\n", DENY, ("prose_colon",)),
    Case("qa_label_line_note_is_no_throat_clearing_opener", "notes.md", "Note: rerun after deploy\n", DENY, ("prose_colon",)),
    Case("qa_numbered_label_line_is_no_throat_clearing_opener", "notes.md", "1. Build: passed\n", DENY, ("prose_colon",)),
    Case("qa_comma_aside_after_is_is_no_throat_clearing_opener", "notes.md", "The default is, by design, off.\n", NONE, ()),
    Case("qa_you_can_run_this_but_is_no_reader_frame", "notes.md", "You can run this, but the tests need git.\n", NONE, ()),
    Case("qa_rest_of_this_file_is_no_document_frame", "notes.md", "The rest of this file holds the tests.\n", NONE, ()),
    Case("qa_is_unknown_is_no_passive_voice", "notes.md", "The value is unknown.\n", NONE, ()),
    Case("qa_is_gone_is_no_passive_voice", "notes.md", "The cache is gone.\n", NONE, ()),
    Case("qa_is_fraught_is_no_passive_voice", "notes.md", "The task is fraught.\n", NONE, ()),
    Case("qa_default_to_with_a_reason_is_no_change_history", "app.js", "// Default to UTC because servers log in UTC\nconst row = 1;\n", NONE, ()),
    Case("known_gap_pinned_to_with_a_reason_reads_as_change_history", "app.js", "// Pinned to 3.11 because pip breaks on 3.12\nconst row = 1;\n", DENY, ("version_control_comment",)),
    Case("qa_swapped_to_fires_version_control_comment", "app.js", "// Swapped to UTC\nconst row = 1;\n", DENY, ("version_control_comment", "what_comment")),
    Case("qa_changed_from_fires_version_control_comment", "app.js", "// Changed from list to dict\nconst row = 1;\n", DENY, ("version_control_comment", "what_comment")),
    Case("qa_previously_used_fires_version_control_comment", "app.js", "// Previously used Redis\nconst row = 1;\n", DENY, ("version_control_comment", "what_comment")),
    Case("qa_was_a_list_before_fires_version_control_comment", "app.js", "// Was a list before\nconst row = 1;\n", DENY, ("version_control_comment", "what_comment")),
    Case("c_ste_phrasal_verb_spin_up_before_an_object", "notes.md", "We spin up the job before the run.\n", OBSERVE, ("ste_phrasal_verb",)),
    Case("c_ste_phrasal_verb_reach_out_to", "notes.md", "We reach out to the team.\n", OBSERVE, ("ste_phrasal_verb",)),
    Case("c_ste_phrasal_verb_noun_after_a_determiner_is_no_verb", "notes.md", "The backup up the tree holds old rows.\n", NONE, ()),
    Case("c_ste_phrasal_verb_up_to_a_count_is_a_compound_preposition", "notes.md", "The queue accepts up to 10 items.\n", NONE, ()),
    Case("c_ste_phrasal_verb_up_to_a_byte_limit_is_a_compound_preposition", "notes.md", "The reader reads up to 4096 bytes.\n", NONE, ()),
    Case("c_ste_phrasal_verb_back_to_is_a_compound_preposition", "notes.md", "The setting reverts back to the default.\n", NONE, ()),
    Case("c_ste_phrasal_verb_out_of_is_a_compound_preposition", "notes.md", "The index reads out of bounds.\n", NONE, ()),
    Case("c_ste_phrasal_verb_down_to_is_a_compound_preposition", "notes.md", "The query boils down to a join.\n", NONE, ()),
    Case("c_ste_phrasal_verb_turned_out_is_exempt", "notes.md", "It turned out the cache was stale.\n", NONE, ()),
    Case("c_ste_phrasal_verb_negated_participle_is_no_verb", "notes.md", "The hook is not set up.\n", NONE, ()),
    Case("c_ste_nominalization_perform_an_analysis_of_blocks", "notes.md", "The job performs an analysis of the log.\n", DENY, ("ste_nominalization",)),
    Case("c_ste_nominalization_without_of_passes", "notes.md", "The team performs a transaction.\n", NONE, ()),
    Case("c_ste_nominalization_plain_verb_passes", "notes.md", "The job analyzes the log.\n", NONE, ()),
    Case("c_ste_marketing_adjective_world_class", "notes.md", "The parser is world-class.\n", OBSERVE, ("ste_marketing_adjective",)),
    Case("c_ste_marketing_adjective_unmeasured_superlative", "notes.md", "This parser is the fastest.\n", OBSERVE, ("ste_marketing_adjective",)),
    Case("c_ste_marketing_adjective_superlative_with_a_measure", "notes.md", "This parser is the fastest at 3 ms.\n", NONE, ()),
    Case("c_ste_marketing_adjective_superlative_with_a_count_in_its_sentence", "notes.md", "Among the 3 parsers, this one is the fastest.\n", NONE, ()),
    Case("c_ste_marketing_adjective_request_is_no_superlative", "notes.md", "The handler logs the request.\n", NONE, ()),
    Case("c_ste_marketing_adjective_attributive_latest_is_no_claim", "notes.md", "The latest release adds caching.\n", NONE, ()),
    Case("c_ste_marketing_adjective_at_most_once_is_a_bound", "notes.md", "The job runs at most once.\n", NONE, ()),
    Case("c_ste_marketing_adjective_test_class_name_is_no_superlative", "notes.md", "The failing suite is LoadProfileStoreWorkerTest.\n", NONE, ()),
    Case("c_ste_compound_tense_has_received", "notes.md", "The job has received the report.\n", OBSERVE, ("ste_compound_tense",)),
    Case("c_ste_compound_tense_single_modal_perfect_passes", "notes.md", "The job may have received the report.\n", NONE, ()),
    Case("c_ste_compound_tense_participial_adjective_passes", "notes.md", "The team has limited time.\n", NONE, ()),
    Case("c_ste_compound_tense_has_children_is_possession", "notes.md", "The node has children.\n", NONE, ()),
    Case("c_ste_compound_tense_has_seven_is_possession", "notes.md", "The record has seven fields.\n", NONE, ()),
    Case("c_ste_compound_tense_has_read_access_is_possession", "notes.md", "The user has read access.\n", NONE, ()),
    Case("c_ste_sentence_length_26_words", "notes.md", "The cache keeps one row for each build and one row for each test and one row for each run that starts today and ends today.\n", OBSERVE, ("ste_sentence_length",)),
    Case("c_ste_sentence_length_25_words_passes", "notes.md", "The cache keeps one row for each build and one row for each test and one row for each run that starts today and ends.\n", NONE, ()),
    Case("c_ste_synonym_rotation_check_then_verify", "notes.md", "Check the cache.\n\nVerify the result.\n", OBSERVE, ("ste_synonym_rotation",)),
    Case("c_ste_synonym_rotation_one_member_passes", "notes.md", "Check the cache twice.\n", NONE, ()),
    Case("c_ste_dangling_conjunction_item_breaks_off_after_and", "notes.md", "- Install the tool and\n- Run the test\n", OBSERVE, ("ste_dangling_conjunction",)),
    Case("c_ste_dangling_conjunction_completed_item_passes", "notes.md", "- Install the tool and run it.\n", NONE, ()),
    Case("c_ste_dangling_conjunction_and_between_code_spans_passes", "notes.md", "- Combine `left` and `right`\n- Run the test\n", NONE, ()),
    Case("known_gap_docstring_restating_its_def_with_a_fake_reason_raises_no_what_docstring", "app.py", "def load_config(path):\n    \"\"\"Load the config file because the caller passes a path.\"\"\"\n    return path\n", DENY, ("added_docstring",)),
)


def outcome_of(response):
    output = response.get("hookSpecificOutput", {})
    denied = output.get("permissionDecision") == DENY
    text = output.get("permissionDecisionReason") or output.get("additionalContext") or ""
    kinds = {(True, True): DENY, (False, True): OBSERVE, (False, False): NONE}
    return Outcome(kinds[(denied, bool(text))], tuple(sorted(set(LISTED_RULE_RE.findall(text)))))


class RuleCharacterization(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)

    def write_outcome(self, path, content):
        payload = {"cwd": str(self.root), "session_id": "rule-characterization", "hook_event_name": "PreToolUse",
                   "tool_name": "Write", "tool_input": {"file_path": str(self.root / path), "content": content}}
        call = subprocess.run(
            [sys.executable, "-ISB", str(SCRIPT), "PreToolUse", "--host", "claude", "--state-dir", str(self.root / ".adw-state")],
            input=json.dumps(payload), text=True, capture_output=True, check=True,
        )
        return outcome_of(json.loads(call.stdout))


def characterization(case):
    def test(self):
        self.assertEqual(Outcome(case.kind, case.rules), self.write_outcome(case.path, case.content))
    return test


for _case in CASES:
    setattr(RuleCharacterization, "test_" + _case.name, characterization(_case))


if __name__ == "__main__":
    unittest.main()
