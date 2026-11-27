# Phase 2 rule tasks

Most rules here report matches without blocking edits. The nominalization rule blocks only the narrow form below. Each row names a gap and a check to try on recorded prose.

| Rule | Gap | Next check |
| --- | --- | --- |
| `emphasis_crutch` | A short clause can state a fact or stress it. | Compare it with the preceding claim. |
| `telling_not_showing` | Adjective endings do not identify judgments. | Look for a measure in the following clause. |
| `vague_declarative` | Build status can fit the same frame. | Compare claims with and without a named result. |
| `performative_emphasis` | A first person verb can describe an action or a promise. | Compare the subject of the next clause. |
| `filler_phrase` | Removing words can change scope. | Compare time and condition clauses before and after removal. |
| `false_agency` | Code and people can both act as subjects. | Check whether the subject names a code symbol. |
| `rhetorical_setup` | A question and answer can teach a procedure. | Compare questions inside and outside help sections. |
| `apology_comment` | A comment can explain a defect or excuse it. | Look for a failure condition. |
| `dead_metaphor` | A literal description can use the same words. | Compare uses near measurements and object names. |
| `ai_tell` | Verb choice does not identify the author. | Measure repeated phrasing within a document. |
| `inflated_diction` | A long word can name a precise concept. | Compare terms against definitions in the text. |
| `vague_quantity` | A count can appear in the next sentence. | Search nearby clauses for units and numbers. |
| `filler` | The same phrase can give a time or add no information. | Compare uses near dates and schedules. |
| `throat_clearing_idiom` | Some lead-ins lack a copula or colon. | Compare the first clause with the claim that follows. |
| `meta_commentary_idiom` | Some asides lack a reader or document reference. | Measure repeated asides across paragraphs. |
| `corporate_idiom` | An office phrase can describe a physical action. | Check nearby object references. |
| `ai_closer` | A closing offer can name a concrete next step. | Compare endings with and without a named task. |
| `weighted_slop_marker` | Repetition does not identify weak word choice. | Compare repeated groups of words against marked passages. |
| `formulaic_opener` | An opening frame can introduce a fact. | Check for dates and quantities in the opening clause. |
| `formulaic_filler` | Grammar cannot show whether removal preserves meaning. | Compare matched passages with shorter versions. |
| `ste_phrasal_verb` | Particles can describe motion or follow nouns. | Review literal motion and missed dive into cases. |
| `ste_marketing_adjective` | A superlative can name a fact. | Compare claims with measurements. |
| `ste_nominalization` | The check requires of after the noun. | Review bare nouns before extending the blocking check. |
| `ste_compound_tense` | A participle can describe an object. | Compare reports against modal and adjective cases. |
| `ste_sentence_length` | Quotes and abbreviations affect sentence boundaries. | Compare counts with hand counts. |
| `ste_synonym_rotation` | Different actions can share an object. | Compare the objects of the two verbs. |
| `ste_dangling_conjunction` | Nested items can share their last line. | Review continuations and nested items. |

## Open implementation tasks

7. Detect `landscape` as a head noun after a determiner and check the literal case against recorded findings.
8. Read identifiers from the original source and compare them with technical terms before replacing the exemption lists.
