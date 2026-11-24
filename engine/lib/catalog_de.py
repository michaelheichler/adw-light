from __future__ import annotations

from typing import NamedTuple

try:
    from . import german_rules
except ImportError:
    import german_rules


class GermanEntry(NamedTuple):
    title: str
    description: str
    action: str


BLOCK_LEAD = "agent-discipline-watcher hat diese Befunde blockiert:"
OBSERVE_LEAD = (
    "agent-discipline-watcher beobachtet diese Befunde und blockiert nicht. "
    "Prüf jeden Befund und behebe ihn oder begründe, warum er bleibt."
)
INHERITED_LEAD = (
    "agent-discipline-watcher: Diese Datei hatte schon {count} Befunde, "
    "die du nicht geschrieben hast. Behebe sie, solange du hier arbeitest."
)
IMPORTED_LEAD = (
    "agent-discipline-watcher: kopierter Inhalt, nicht deiner. {count} Befunde in {files} "
    "kopierten Dateien. Nichts blockiert. Behebe sie nur, wenn dir dieser Inhalt jetzt gehört."
)
FULL_REPORT = "Vollständiger Bericht"
MORE_FINDINGS = "weitere Befunde"
PRINCIPLE_LABEL = "Prinzip"

RULES: dict[str, GermanEntry] = {
    "unresolved_doc_reference": GermanEntry("Unbekannter Name", "Der Name fehlt in dieser Datei", "Nutze einen Namen aus der Datei oder Backticks."),
    "banned_dash": GermanEntry(
        "Unzulässiger Strich",
        "Meldet den Geviertstrich und den Halbgeviertstrich ohne Leerzeichen",
        "Nimm einen Bindestrich, ein Komma oder einen Punkt.",
    ),
    "dash_break": GermanEntry(
        "Doppelter Bindestrich als Strich",
        "Meldet zwei Bindestriche, die einen Satz teilen",
        "Setz ein Komma, einen Punkt oder Klammern.",
    ),
    "spaced_hyphen": GermanEntry(
        "Bindestrich mit Leerzeichen",
        "Meldet einen Bindestrich mit Leerzeichen, der einen Gedankenstrich ersetzt",
        "Setz ein Komma oder einen Punkt, oder schließ den Bindestrich an das Wort an.",
    ),
    "pronoun_apostrophe": GermanEntry(
        "Apostroph am Pronomen",
        "Meldet einen Apostroph an einem englischen Possessivpronomen wie your's",
        "Schreib das Possessivpronomen ohne Apostroph.",
    ),
    "decade_apostrophe": GermanEntry(
        "Apostroph am Jahrzehnt",
        "Meldet ein Jahrzehnt wie 1990's mit Apostroph",
        "Schreib das Jahrzehnt ohne Apostroph, etwa „die 1990er“.",
    ),
    "quote_marks": GermanEntry(
        "Anführungszeichen nicht deutsch",
        "Meldet gerade oder englische Anführungszeichen in deutschem Text, der „…“ und ‚…‘ verlangt",
        "Setz die deutschen Paare „…“ oder ‚…‘.",
    ),
    "genitive_apostrophe": GermanEntry(
        "Apostroph im Genitiv",
        "Meldet Peter's in deutschem Text, wo der Genitiv ein bloßes s nimmt",
        "Schreib das Genitiv-s ohne Apostroph.",
    ),
    "typed_ellipsis": GermanEntry(
        "Auslassungspunkte getippt",
        "Meldet drei getippte Punkte oder einen Zusatzpunkt, wo das Deutsche ein einzelnes Zeichen … verlangt",
        "Setz ein einzelnes Zeichen … ohne weiteren Punkt.",
    ),
    "dash_cluster": GermanEntry(
        "Zu viele Gedankenstriche",
        "Meldet deutschen Text, der den Gedankenstrich über die Dichtegrenze hinaus nutzt",
        "Ersetz einige Gedankenstriche durch Komma, Punkt oder Doppelpunkt.",
    ),
    "de_parataxis": GermanEntry(
        "Telegrammstil",
        "Meldet eine Reihe kurzer Hauptsätze ohne Verbindung, die ein Komma, weil, die oder und verbinden würde",
        "Verbinde die Sätze, die zusammengehören: mit einem Nebensatz (weil, wenn, sodass, nachdem), "
        "einem Relativsatz (der, die, das), einer Apposition, einer Partizip- oder Infinitivgruppe "
        "oder mit und und aber. Geviertstrich, Halbgeviertstrich und Semikolon bleiben verboten, "
        "also verbindet ein Komma oder eine Konjunktion. Beispiel: „Wir haben eine Pumpe entwickelt. "
        "Sie misst den Druck.“ wird zu „Wir haben eine Pumpe entwickelt, die den Druck misst.“",
    ),
    "de_uniform_rhythm": GermanEntry(
        "Gleichförmiger Satzrhythmus",
        "Meldet deutsche Sätze, deren Länge im ganzen Dokument kaum schwankt",
        "Wechsle zwischen kurzen und langen Sätzen.",
    ),
    "de_uniform_paragraphs": GermanEntry(
        "Gleich lange Absätze",
        "Meldet deutsche Absätze, die alle fast gleich lang sind",
        "Mach einige Absätze kürzer oder länger als die übrigen.",
    ),
    "de_repeated_connector": GermanEntry(
        "Gehäufte Satzanschlüsse",
        "Meldet einen Absatz, der mehr als einen Satz mit außerdem, ferner oder einem ähnlichen Wort beginnt",
        "Beginn höchstens einen Satz im Absatz mit einem Wort wie „außerdem“.",
    ),
    "dead_metaphor": GermanEntry(
        "Abgegriffenes Bild",
        "Streicht ein Standardbild, das nichts Konkretes benennt",
        "Nenn den konkreten Sachverhalt statt des Bildes.",
    ),
    "filler": GermanEntry(
        "Füllphrase",
        "Streicht eine Wendung, die Wörter und keine Bedeutung bringt",
        "Streich die Wendung und sag den Punkt direkt.",
    ),
    "throat_clearing": GermanEntry(
        "Anlauf vor dem Punkt",
        "Streicht einen Einstieg, der den Punkt hinauszögert",
        "Beginn mit dem Punkt.",
    ),
    "filler_opener": GermanEntry(
        "Floskelhafter Einstieg",
        "Streicht einen Einstieg, der eine Szene malt, statt den Punkt zu nennen",
        "Beginn mit der eigentlichen Aussage.",
    ),
    "ai_tell": GermanEntry(
        "Typische KI-Wendung",
        "Streicht eine Wendung, die typisch für generierten Text ist",
        "Nimm ein schlichtes Verb.",
    ),
    "inflated_diction": GermanEntry(
        "Aufgeblähtes Wort",
        "Streicht ein großes Wort, wo ein schlichtes reicht",
        "Nimm das schlichte Wort.",
    ),
    "utilize": GermanEntry(
        "Langes Wort für use",
        "Streicht utilize und seine Formen",
        "Schreib „use“ oder ein schlichtes deutsches Verb.",
    ),
    "wordiness": GermanEntry(
        "Umständliche Wendung",
        "Streicht eine lange Wendung, für die es eine kurze gibt",
        "Nimm die kurze Form.",
    ),
    "vague_quantity": GermanEntry(
        "Vage Mengenangabe",
        "Streicht eine Wendung, die die echte Zahl verdeckt",
        "Nenn die Zahl oder die einzelnen Dinge.",
    ),
    "expletive_there": GermanEntry(
        "Einstieg mit there is",
        "Streicht einen Einstieg mit there is, der das Subjekt versteckt",
        "Beginn mit dem Subjekt und einem aktiven Verb.",
    ),
    "empty_intensifier": GermanEntry(
        "Leerer Verstärker",
        "Streicht einen Verstärker, der Nachdruck und keine Bedeutung bringt",
        "Streich den Verstärker oder wähl ein treffenderes Wort.",
    ),
    "ai_closer": GermanEntry(
        "Floskel zum Abschluss",
        "Streicht die Schlussgeste, die den Punkt wiederholt, statt zu enden",
        "Hör auf, wenn die Antwort fertig ist.",
    ),
    "banned_adverb": GermanEntry(
        "Leere Verstärkerwörter",
        "Streicht really, just, literally, simply, actually und elf weitere Wörter ohne Bedeutung",
        "Streich das Wort.",
    ),
    "binary_contrast": GermanEntry(
        "Rahmen nicht X, sondern Y",
        "Streicht das falsche Entweder-oder, das entschieden klingt und wenig sagt",
        "Nenn die positive Aussage direkt.",
    ),
    "business_jargon": GermanEntry(
        "Konzernvokabular",
        "Streicht navigate the, unpack the, lean into und the X landscape",
        "Nenn die konkrete Handlung.",
    ),
    "corporate_idiom": GermanEntry(
        "Bürofloskel",
        "Streicht eine Redewendung aus dem Büroalltag ohne konkrete Aussage",
        "Nenn die wörtliche Handlung.",
    ),
    "dramatic_fragmentation": GermanEntry(
        "Fragmente für Dramatik",
        "Streicht den Absatz aus einem Wort, der sein Gewicht aus Leerraum zieht",
        "Schreib vollständige Sätze.",
    ),
    "emphasis_crutch": GermanEntry(
        "Künstliche Betonung",
        "Streicht full stop, let that sink in, make no mistake und this matters because",
        "Streich die Betonungsformel.",
    ),
    "false_agency": GermanEntry(
        "Dinge handeln von selbst",
        "Streicht Formulierungen, die einem Ding eine Handlung zuschreiben, die es nicht ausführen kann",
        "Nenn die Person oder die Handlung, die verantwortlich ist.",
    ),
    "filler_phrase": GermanEntry(
        "Einleitende Füllwendung",
        "Streicht at its core, when it comes to, the reality is und in today's X",
        "Streich die Füllwendung.",
    ),
    "formulaic_construction": GermanEntry(
        "Schablonenhafter Satzbau",
        "Streicht die wiederkehrende Satzschablone, die generierten Text verrät",
        "Ersetz die Schablone durch eine direkte Beschreibung.",
    ),
    "formulaic_filler": GermanEntry(
        "Ausgewogenheitsfloskel",
        "Streicht it is important to note, on one hand und first and foremost",
        "Streich die Floskel und sag den Punkt.",
    ),
    "formulaic_opener": GermanEntry(
        "Standarderöffnung",
        "Streicht in a world where, imagine this und whether you are a beginner",
        "Beginn mit der Aussage.",
    ),
    "greeting_opener": GermanEntry(
        "Gruß vor der Antwort",
        "Streicht die Begrüßung, die den ersten nützlichen Satz verzögert",
        "Beginn mit der Antwort.",
    ),
    "hedge_stack": GermanEntry(
        "Gestapelte Einschränkungen",
        "Streicht gehäufte Einschränkungen, die eine Aussage aufweichen, bis sie nichts mehr sagt",
        "Behalte eine Einschränkung oder nenn die Tatsache.",
    ),
    "lazy_extreme": GermanEntry(
        "Absolute Behauptung",
        "Streicht every, always, never und nothing, wo die Aussage nicht absolut gilt",
        "Nenn den tatsächlichen Umfang oder die Zahl.",
    ),
    "choppy_sentences": GermanEntry(
        "Abgehackte Sätze",
        "Meldet eine Reihe kurzer Hauptsätze ohne Verbindung im englischen Text",
        "Verbinde die Sätze, die zusammengehören, mit einem Nebensatz, einem Relativsatz oder und. "
        "Geviertstrich, Halbgeviertstrich und Semikolon bleiben verboten.",
    ),
    "low_sentence_variance": GermanEntry(
        "Gleich lange Sätze",
        "Meldet Text, in dem jeder Satz gleich lang ist",
        "Wechsle zwischen kurzen und langen Sätzen.",
    ),
    "throat_clearing_idiom": GermanEntry(
        "Einleitende Wendung",
        "Meldet eine feste Einleitung ohne kurzes Satzmuster",
        "Nenne den Sachverhalt direkt.",
    ),
    "meta_commentary_idiom": GermanEntry(
        "Einschub zum Text",
        "Meldet einen Einschub ohne direkte Leseransprache oder Textbezug",
        "Streich den Einschub.",
    ),
    "meta_commentary": GermanEntry(
        "Bemerkung über das Schreiben",
        "Streicht hint, plot twist, spoiler und is a feature not a bug",
        "Streich die Bemerkung.",
    ),
    "narrator_distance": GermanEntry(
        "Erzähler tritt zurück",
        "Streicht den Schritt, der das Thema kommentiert, statt es zu benennen",
        "Nenn die Menschen oder sprich den Leser direkt an.",
    ),
    "negative_listing": GermanEntry(
        "Bestimmung durch Verneinung",
        "Streicht die Reihe von Verneinungen, die nichts Konkretes beschreibt",
        "Nenn den Punkt ohne Anlauf.",
    ),
    "oversized_list": GermanEntry(
        "Liste zu lang zum Überfliegen",
        "Meldet eine Liste über der Höchstzahl, die standardmäßig bei 8 Punkten liegt",
        "Kürz die Liste oder teil sie in Gruppen.",
    ),
    "ste_phrasal_verb": GermanEntry(
        "Verb mit Partikel",
        "Meldet ein Verb mit Partikel wie spin up, reach out oder kick off",
        "Nimm das einfache Verb, etwa start, contact oder begin.",
    ),
    "ste_marketing_adjective": GermanEntry(
        "Werbewort",
        "Meldet Wörter wie effortless oder world-class und einen Superlativ ohne Messwert",
        "Lösch das Wort oder nenn den Messwert, der die Aussage trägt.",
    ),
    "ste_nominalization": GermanEntry(
        "Handlung als Substantiv",
        "Meldet perform an analysis of statt analyze",
        "Schreib das Verb, etwa analyze, prüf oder miss.",
    ),
    "ste_compound_tense": GermanEntry(
        "Zusammengesetzte Zeit",
        "Meldet has oder had mit Partizip, wo die einfache Vergangenheit reicht",
        "Schreib die einfache Zeit, wenn der Bezug zur Gegenwart nicht zählt.",
    ),
    "ste_sentence_length": GermanEntry(
        "Satz über 25 Wörtern",
        "Meldet einen Satz über der Grenze von 25 Wörtern",
        "Teil den Satz in zwei Sätze.",
    ),
    "ste_synonym_rotation": GermanEntry(
        "Eine Handlung, zwei Verben",
        "Meldet check und verify für dieselbe Handlung im selben Dokument",
        "Nimm ein Verb und bleib dabei.",
    ),
    "ste_dangling_conjunction": GermanEntry(
        "Aufzählung endet mit Konjunktion",
        "Meldet einen Punkt, der nach und, oder, aber oder noch abbricht",
        "Vervollständige den Punkt oder verbinde ihn mit dem nächsten.",
    ),
    "prose_semicolon": GermanEntry(
        "Semikolon im Text",
        "Meldet ein Semikolon zwischen zwei Sätzen",
        "Setz ein Komma, einen Punkt oder Klammern.",
    ),
    "passive_voice": GermanEntry(
        "Passiv versteckt den Handelnden",
        "Fragt nach dem Handelnden und einem aktiven Verb statt des Passivs",
        "Nenn den Handelnden und nimm ein aktives Verb.",
    ),
    "performative_emphasis": GermanEntry(
        "Gespielte Aufrichtigkeit",
        "Streicht creeps in, they exist I promise und I promise",
        "Streich die Beteuerung.",
    ),
    "rhetorical_setup": GermanEntry(
        "Frage nur zum Beantworten",
        "Streicht die rhetorische Frage, die einen laufenden Punkt inszeniert",
        "Streich die Frage und nenn die Aussage.",
    ),
    "telling_not_showing": GermanEntry(
        "Behaupten statt zeigen",
        "Streicht this is genuinely hard und actually matters anstelle von Belegen",
        "Zeig den Beleg statt der Behauptung.",
    ),
    "three_item_list": GermanEntry(
        "Dreierrhythmus",
        "Gibt die Dreierreihe an einen Prüfer, weil drei Punkte echt oder ein Taktmuster sein können",
        "Behalte die Dreierreihe nur, wenn es genau drei Punkte gibt.",
    ),
    "throat_clearing_opener": GermanEntry(
        "Aufwärmen vor dem Punkt",
        "Streicht here is the thing, it turns out und the real X is",
        "Beginn mit dem Punkt.",
    ),
    "uniform_paragraph_endings": GermanEntry(
        "Absätze enden gleich",
        "Meldet Text, in dem Absatz um Absatz gleich endet",
        "Lass die Absätze unterschiedlich enden.",
    ),
    "vague_declarative": GermanEntry(
        "Große Aussage ohne Inhalt",
        "Streicht the reasons are structural und the implications are significant",
        "Nenn die konkreten Gründe oder Folgen.",
    ),
    "weak_sentence_starter": GermanEntry(
        "Verstecktes Subjekt",
        "Streicht Einstiege mit there is und it is, die das echte Subjekt nach hinten schieben",
        "Beginn mit dem Subjekt oder dem Verb.",
    ),
    "weighted_slop_marker": GermanEntry(
        "Zu viele KI-Marker",
        "Meldet Text, in dem sich gewichtete Marker für generierten Text über der Schwelle häufen",
        "Ersetz die markierten Wörter durch schlichte.",
    ),
    "assert_in_loop": GermanEntry(
        "Assertion in einer Schleife",
        "Meldet eine Testschleife, deren erster Fehlschlag die übrigen Fälle verdeckt",
        "Parametrisiere die Fälle oder prüf jeden Fall in einem eigenen subTest.",
    ),
    "hardcoded_name_presence": GermanEntry(
        "Test prüft einen festen Namen",
        "Meldet einen Test, der nur prüft, ob ein fester Name oder Wert in einer statischen Tabelle steht",
        "Prüf das Verhalten, das der Name steuert, statt seines Vorhandenseins.",
    ),
    "hardcoded_literal_in_source": GermanEntry(
        "Test wiederholt den Quelltext",
        "Meldet einen Test, der eine Quell- oder Konfigurationsdatei liest und ein Literal in ihrem Text prüft",
        "Prüf das Verhalten, das der Text erzeugt, statt des Textes.",
    ),
    "mocking_concrete_classes": GermanEntry(
        "Mock aus einer konkreten Klasse",
        "Meldet einen Mock oder Patch, der direkt aus einer Klasse dieser Datei statt aus einer Schnittstelle entsteht",
        "Mock die Schnittstelle oder das Protokoll statt des konkreten Typs.",
    ),
    "incomplete_mock_call_verification": GermanEntry(
        "Mock-Prüfung ohne Argumente",
        "Meldet ein nacktes assert_called oder assert_called_once ohne Prüfung der Argumente",
        "Prüf die genauen Argumente, etwa mit assert_called_once_with.",
    ),
    "exposing_private_methods_for_testing": GermanEntry(
        "Private Methode im Test aufgerufen",
        "Meldet einen Test, der einen Namen mit einem führenden Unterstrich aufruft oder patcht",
        "Ruf die öffentliche Methode auf, die diesen Pfad ausführt.",
    ),
    "exposing_private_state_for_testing": GermanEntry(
        "Privates Attribut im Test gelesen",
        "Meldet einen Test, der ein Feld mit einem führenden Unterstrich liest oder setzt",
        "Prüf das öffentliche Ergebnis, das der Aufruf schon liefert.",
    ),
    "if_statements_in_tests": GermanEntry(
        "Verzweigter Testrumpf",
        "Meldet einen Test, der zwischen zwei verschiedenen Assertions wählt",
        "Teil die Zweige in eigene Testmethoden auf.",
    ),
    "multiple_act_sections_in_unit_test": GermanEntry(
        "Zweiter Act im selben Test",
        "Meldet einen Test, der die geprüfte Einheit aufruft, prüft und sie dann erneut aufruft und prüft",
        "Verschieb den zweiten Aufruf samt Prüfung in eine eigene Testmethode.",
    ),
    "test_fixture_reuse_via_constructor": GermanEntry(
        "Fixture aus dem Konstruktor wiederverwendet",
        "Meldet eine Testmethode, die ein Objekt nutzt, das setUp oder __init__ für die ganze Klasse gebaut hat",
        "Verschieb den Aufbau in eine Fabrikmethode, die der Test selbst aufruft.",
    ),
    "comment_shape": GermanEntry(
        "Kommentar ohne erlaubte Form",
        "Meldet einen Kommentar, der länger als ein Satz oder 25 Wörter ist, den Code wiederholt oder keiner der sechs Begründungsformen entspricht",
        "Schreib eine Zeile mit höchstens 25 Wörtern, die den Code nennt, ein kausales Wort wie because und den Grund außerhalb des Codes. Oder nutz NOTE neben redundant wirkendem Code, eine Quell-URL, einen Standard mit hence oder eine Ticketnummer.",
    ),
    "agent_marker_author": GermanEntry(
        "Arbeitsmarker mit KI-Agent als Autor",
        "Blockiert einen neuen TODO-, FIXME- oder HACK-Marker, der in Klammern einen KI-Agenten wie claude oder codex als Autor nennt",
        "Entfern den Agentennamen aus dem Marker. Behalt TODO, FIXME oder HACK mit Doppelpunkt oder nenn den Menschen, dem die Arbeit gehört.",
    ),
    "deferred_work_comment": GermanEntry(
        "Arbeitsmarker ohne erlaubte Form",
        "Meldet einen TODO-, FIXME- oder HACK-Marker ohne Tag, Verb, Objekt und Zweck sowie jeden DONE-, XXX-, BUG- oder OPTIMIZE-Marker",
        "Schreib TODO, FIXME oder HACK groß mit Doppelpunkt, dann ein großgeschriebenes Verb, ein benanntes Objekt und einen Zweck mit to oder so. FIXME nennt den Fehler, HACK nennt die Bedingung zum Entfernen.",
    ),
    "wording_debt_lock": GermanEntry(
        "Testschreiben durch Wortlaut-Schulden gesperrt",
        "Blockiert ein Schreiben von Tests, das die Zahl der Wortlaut-Tests nicht senkt, solange das Projekt noch welche hat",
        "Schreib einen Wortlaut-Test in einen Verhaltenstest um oder lösch ihn, wenn er nur Text prüft. Füg nicht mehr neue Tests hinzu, als du Wortlaut-Tests entfernst.",
    ),
    "hard_coded_path": GermanEntry(
        "Fest verdrahteter Maschinenpfad",
        "Blockiert Code oder Tests mit einem absoluten Pfad, einem Home-Pfad, einem Laufwerksbuchstaben oder einem lokalen Benutzernamen, weil sie auf jedem anderen Rechner scheitern",
        "Bau den Pfad aus einer berechneten Wurzel wie dem Projektverzeichnis, einem Temp-Verzeichnis oder dem Home-Lookup und häng nur repo-relative Teile an.",
    ),
    "wording_assertion": GermanEntry(
        "Test prüft Wortlaut",
        "Blockiert einen Test, dessen erwarteter Wert ein Satz ist, weil eine Textänderung ihn rot macht, obwohl der Code noch funktioniert",
        "Prüf das Verhalten hinter dem Text, etwa ein Entscheidungsfeld, eine Regel-ID oder einen Exit-Code. Lösch den Test, wenn er nur Wortlaut prüft.",
    ),
    "snapshot_assertion": GermanEntry(
        "Test vergleicht mit gespeicherter Kopie",
        "Meldet einen Test, der die Ausgabe mit einem Snapshot oder einer Golden-Datei vergleicht, ohne den Schreibvorgang zu blockieren",
        "Prüf das wichtige Verhalten mit einem ausdrücklichen Erwartungswert und sieh die gespeicherte Kopie bei jeder Änderung durch.",
    ),
    "wording_exception_match": GermanEntry(
        "Test prüft Fehlermeldung",
        "Blockiert einen Test, der Prosa in der Meldung einer Ausnahme oder Warnung abgleicht, weil eine Textänderung ihn rot macht, obwohl der Code noch funktioniert",
        "Gib pytest.raises über check= eine Funktion mit, die die Felder der Ausnahme prüft, oder wirf einen genaueren Ausnahmetyp. Nutz bei unittest assertRaises und prüf die Felder der gefangenen Ausnahme.",
    ),
    "wording_log_assertion": GermanEntry(
        "Test prüft Logsatz",
        "Blockiert einen Test, der einen Satz aus mitgeschnittenen Logs erwartet, weil eine Textänderung ihn rot macht, obwohl das Logging noch funktioniert",
        "Prüf Log-Level, Logger-Namen und die strukturierten Felder aus extra und lass den Meldungstext weg.",
    ),
    "literal_location": GermanEntry(
        "Test tippt Datei und Zeile ab",
        "Blockiert einen Test, der einen Wert aus Datei und Zeile als abgetipptes Literal erwartet, statt ihn aus der Fixture-Variable zu bauen",
        "Bau den Wert aus Datei und Zeile aus der Fixture-Variable, etwa mit einem f-String über den Fixture-Pfad und die Zeile, die die Fixture festlegt.",
    ),
    "wording_ui_locator": GermanEntry(
        "Test sucht ein Element über seinen Text",
        "Blockiert einen UI-Test, der ein Element über sichtbare Prosa findet, etwa mit getByText oder contains, weil eine Textänderung ihn rot macht, obwohl die Seite noch funktioniert",
        "Such das Element über eine Test-ID oder seine Rolle, etwa mit getByTestId oder getByRole, und lass den sichtbaren Text aus dem Locator heraus.",
    ),
    **{rule.name: GermanEntry(*rule.german) for rule in german_rules.declared()},
}
