from __future__ import annotations

PHRASES = {
    "banned_adverb": (
        "Empty intensifiers",
        "Cuts really, just, literally, simply, actually, and similar intensifiers when they add no meaning",
    ),
    "binary_contrast": (
        "Not X but Y framing",
        "Cuts the false either-or that sounds decisive and says little",
    ),
    "business_jargon": (
        "Corporate vocabulary",
        "Cuts navigate the, unpack the, lean into, and the X landscape",
    ),
    "choppy_sentences": (
        "Telegram-style sentences",
        "Flags a run of short main clauses that a comma, because, which, or and would join",
    ),
    "corporate_idiom": (
        "Office idiom",
        "Cuts stock office phrasing that carries no specific claim",
    ),
    "dash_cluster": (
        "Pile of German dashes",
        "Reports German prose that leans on the spaced Gedankenstrich past the density limit",
    ),
    "de_repeated_connector": (
        "Repeated German connector",
        "Flags a paragraph opening more than one sentence on the same connector",
    ),
    "de_parataxis": (
        "German telegram style",
        "Flags a run of short German main clauses that a comma, weil, die, or und would join",
    ),
    "de_uniform_paragraphs": (
        "Uniform German paragraph length",
        "Flags German paragraphs that all run close to the same length",
    ),
}
