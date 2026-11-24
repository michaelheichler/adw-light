from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NamedTuple

try:
    from .comment_reason import content_words, restates, states_why
    from .findings import finding_row
except ImportError:
    from comment_reason import content_words, restates, states_why
    from findings import finding_row

WORDS_FILE = Path(__file__).with_name("comment_shape_words.json")
WORDS: dict = json.loads(WORDS_FILE.read_text(encoding="utf-8"))
SHAPE_RULE = "comment_shape"
MARKER_RULE = "deferred_work_comment"
AGENT_RULE = "agent_marker_author"
SHAPE_ACTION = (
    "Keep one line of at most 25 words that names the code, a causal link such as because, and the outside reason. "
    "Or use NOTE next to redundant-looking code, a source URL, a standard plus hence, or a tracker reference."
)
MARKER_ACTION = (
    "Write TODO, FIXME, or HACK in uppercase with a colon, then a capitalized verb, a named object, and a to or so purpose. "
    "FIXME names the defect and HACK names when to remove it."
)
AGENT_ACTION = (
    "Drop the AI agent name from the work marker. Keep TODO, FIXME, or HACK with a colon, or name the human who owns the work."
)
URL_RE = re.compile(r"https?://\S+")
VIA_URL_RE = re.compile(r"\bvia\s+https?://", re.IGNORECASE)
NOT_TRACKER_KEYS = frozenset({
    "UTF", "ISO", "RFC", "SHA", "MD", "PEP", "ECMA", "HTTP", "TLS", "SSL", "IPV", "X86", "AES", "IEEE", "CP", "COVID",
})
TRACKER_RE = re.compile(
    r"(?<![\w/&])#\d+\b|\b(?i:issue|ticket|bug|pr|pull request)\s*#?\d+\b|\bGH-\d+\b"
    r"|\b(?P<key>[A-Z][A-Z0-9]+)-\d+\b"
    r"|(?i:https?://\S*(?:/issues/|/pull/|/browse/|/-/issues/|/tickets?/|show_bug\.cgi\?id=)\S*)",
)
SENTENCE_BREAK_RE = re.compile(r"[.!?](?=\s+[A-Z])")
NOTE_RE = re.compile(r"^(?:note\s+that\b|NOTE\b:?)", re.IGNORECASE)
REDUNDANT_CODE_RES = (
    re.compile(r"(?:==|!=)=?\s*(?:null|nil|None|undefined|NULL|nullptr)\b|\b(?:null|nil|None|undefined|NULL|nullptr)\s*(?:==|!=)"),
    re.compile(r"\bis\s+(?:not\s+)?None\b|\.equals\(\s*null\s*\)"),
    re.compile(r"(?:==|!=)=?\s*(?:true|false|True|False)\b"),
    re.compile(r"\b(\w+)\s*(?:==|!=|<=|>=|<|>)[^&|]*(?:\|\||&&|\band\b|\bor\b)\s*!?\1\b"),
    re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}|\bexcept\b[^:]*:\s*(?:pass|\.\.\.)\s*$"),
    re.compile(r"^\s*(?:this\.|self\.)?(\w+)\s*=\s*(?:this\.|self\.)?\1\s*;?\s*$"),
)
SLASH_COMMENT_EXTS = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cs", ".java", ".kt", ".kts", ".scala", ".groovy", ".gradle",
    ".go", ".rs", ".swift", ".dart", ".php", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte",
})
INLINE_SLASH_RE = re.compile(r"(?<=\s)//(?!/)\s*(?P<body>.*)$")
QUOTE_RE = re.compile(r"(?<!\\)[\"'`]")
TAG_RE = re.compile(r"^(?P<tag>[A-Za-z]+)\b(?P<rest>.*)$", re.DOTALL)
LETTER_RE = re.compile(r"[^\W\d_]")
AUTHOR_RE = re.compile(r"^\s*\((?P<author>[^()\n]*[^\W_][^()\n]*)\)")
AUTHOR_WORD_RE = re.compile(r"[^\W_]+")


class CommentRow(NamedTuple):
    line: int
    text: str
    leading: bool
    code: str


class Logical(NamedTuple):
    line: int
    text: str
    snippet: str
    code: str
    start: int
    end: int
    spanned: int


class AcceptedSpan(NamedTuple):
    start: int
    head: int
    end: int


def _phrase_re(phrases: list[str], flags: int = re.IGNORECASE) -> re.Pattern[str]:
    ordered = sorted(phrases, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(phrase).replace("\\ ", r"\s+") for phrase in ordered) + r")\b", flags)


CAUSAL_RE = _phrase_re(WORDS["causal_links"])
ATTRIBUTION_RE = _phrase_re(WORDS["attribution_words"])
STANDARD_RE = _phrase_re(WORDS["standard_names"])
REQUIREMENT_RE = _phrase_re(WORDS["requirement_verbs"], 0)
STANDARD_LINK_RE = _phrase_re(WORDS["standard_links"])
PURPOSE_RE = _phrase_re(WORDS["purpose_links"])
WORD_RE = re.compile(r"[^\W\d_][\w'-]*")
DEFECT_RE = _phrase_re(WORDS["defect_words"])
REMOVAL_RE = _phrase_re(WORDS["removal_words"])
WORK_TAGS = frozenset(WORDS["work_tags"])
BANNED_TAGS = frozenset(WORDS["banned_tags"])
MARKER_VERBS = frozenset(WORDS["marker_verbs"])
PURPOSE_VERBS = frozenset(WORDS["purpose_verbs"]) | {verb.lower() for verb in MARKER_VERBS}
VAGUE_OBJECTS = frozenset(WORDS["vague_objects"])
MAX_WORDS = int(WORDS["max_words"])
WRAPPING_LINES = 2
SHAPE_PASSED_RULES = frozenset({"what_comment", "weak_why_comment", "prose_comment_block", "long_comment", "added_comment"})
CONTINUATION_PASSED_RULES = frozenset({"commented_code"})
DOC_BLOCK_OPEN = "/**"
AGENT_AUTHORS = frozenset(name.lower() for name in WORDS["agent_authors"])


def comment_shape_findings(path: str, rows: list[CommentRow], lines: list[str]) -> list[dict]:
    findings = []
    for comment in _logical_comments(rows, lines):
        findings.extend(_judged(path, comment))
    return findings


def accepted_comment_spans(rows: list[CommentRow], lines: list[str]) -> list[AcceptedSpan]:
    return [
        AcceptedSpan(comment.start, comment.line, comment.end)
        for comment in _logical_comments(rows, lines)
        if _marker_problem(comment.text) is None and not _shape_problem(comment) and not _doc_block(comment, lines)
    ]


def without_shape_passed(findings: list[dict], spans: list[AcceptedSpan]) -> list[dict]:
    return [row for row in findings if not any(_passed_inside(row, span) for span in spans)]


def _passed_inside(row: dict, span: AcceptedSpan) -> bool:
    rule, first = row.get("rule"), row.get("line")
    if rule not in SHAPE_PASSED_RULES | CONTINUATION_PASSED_RULES or not isinstance(first, int):
        return False
    last = row.get("end_line") if isinstance(row.get("end_line"), int) else first
    inside = span.start <= first <= last <= span.end
    return inside and (rule in SHAPE_PASSED_RULES or first > span.head)


def _doc_block(comment: Logical, lines: list[str]) -> bool:
    opened = lines[comment.start - 1].strip() if comment.start <= len(lines) else ""
    return comment.end > comment.start and opened.startswith(DOC_BLOCK_OPEN)


def well_formed_marker(text: str) -> bool:
    return _marker_problem(text) == ""


def inline_slash_rows(path: str, lines: list[str], skip: set[int], directive: re.Pattern[str]) -> list[CommentRow]:
    if Path(path.lower()).suffix not in SLASH_COMMENT_EXTS:
        return []
    rows = []
    for number, line in enumerate(lines, 1):
        match = None if number in skip else INLINE_SLASH_RE.search(line)
        if match is None or not line[:match.start()].strip() or len(QUOTE_RE.findall(line[:match.start()])) % 2:
            continue
        if directive.match("//" + match.group("body")) or directive.match("// " + match.group("body")):
            continue
        rows.append(CommentRow(number, match.group("body").strip(), False, line[:match.start()].strip()))
    return rows


def _logical_comments(rows: list[CommentRow], lines: list[str]) -> list[Logical]:
    numbers = {row.line for row in rows}
    comments: list[Logical] = []
    run: list[CommentRow] = []
    for row in rows:
        if run and (not row.leading or row.line != run[-1].line + 1 or _tagged(row.text)):
            comments.append(_joined(run, lines, numbers))
            run = []
        if row.leading:
            run.append(row)
        elif row.text.strip():
            snippet = lines[row.line - 1] if row.line <= len(lines) else row.text
            comments.append(Logical(row.line, row.text.strip(), snippet, row.code, row.line, row.line, 1))
    if run:
        comments.append(_joined(run, lines, numbers))
    return [comment for comment in comments if LETTER_RE.search(comment.text)]


def _tagged(text: str) -> bool:
    match = TAG_RE.match(text.strip())
    return bool(match) and match.group("tag").upper() in WORK_TAGS | BANNED_TAGS and match.group("tag").isupper()


def _joined(run: list[CommentRow], lines: list[str], numbers: set[int]) -> Logical:
    texts = [row for row in run if row.text.strip()]
    text = " ".join(row.text.strip() for row in texts)
    head = texts[0].line if texts else run[0].line
    snippet = lines[head - 1] if head <= len(lines) else text
    return Logical(head, text, snippet, _following(lines, run[-1].line, numbers), run[0].line, run[-1].line, len(texts))


def _following(lines: list[str], last: int, numbers: set[int]) -> str:
    for number in range(last + 1, len(lines) + 1):
        if number not in numbers and lines[number - 1].strip():
            return lines[number - 1].strip()
    return ""


def _judged(path: str, comment: Logical) -> list[dict]:
    marker = _marker_problem(comment.text)
    if marker is not None:
        marker = marker or _wrap_problem(comment, 1).replace("Comment", "Work marker")
        rows = [finding_row("comment", AGENT_RULE, comment.line, "Work marker names an AI agent as its author in " + path, comment.snippet, AGENT_ACTION)] if _agent_author(comment.text) else []
        return rows + ([finding_row("comment", MARKER_RULE, comment.line, marker + path, comment.snippet, MARKER_ACTION)] if marker else [])
    problem = _shape_problem(comment)
    return [finding_row("comment", SHAPE_RULE, comment.line, problem + path, comment.snippet, SHAPE_ACTION)] if problem else []


def _sentences(text: str) -> int:
    return len(SENTENCE_BREAK_RE.findall(URL_RE.sub("URL", text))) + 1


def _length_problem(text: str) -> str:
    if len(text.split()) > MAX_WORDS:
        return f"Comment runs past {MAX_WORDS} words in "
    if _sentences(text) > 1:
        return "Comment spans more than one sentence in "
    return ""


def _marker_problem(text: str) -> str | None:
    match = TAG_RE.match(text)
    if not match:
        return None
    tag, rest = match.group("tag"), match.group("rest")
    upper = tag.upper()
    if upper not in WORK_TAGS | BANNED_TAGS or not (tag.isupper() or rest.lstrip().startswith((":", "("))):
        return None
    if upper in BANNED_TAGS:
        return "Work marker uses a tag that belongs in git or the tracker in "
    rest = AUTHOR_RE.sub("", rest, count=1)
    if not tag.isupper() or not rest.startswith(":"):
        return "Work marker needs an uppercase tag and a colon in "
    return _marker_body_problem(upper, rest[1:].strip())


def _agent_author(text: str) -> bool:
    match = TAG_RE.match(text)
    author = AUTHOR_RE.match(match.group("rest").lstrip()) if match else None
    return bool(author) and any(word.lower() in AGENT_AUTHORS for word in AUTHOR_WORD_RE.findall(author.group("author")))


def _marker_body_problem(tag: str, body: str) -> str:
    if not LETTER_RE.search(TRACKER_RE.sub(" ", URL_RE.sub(" ", body))):
        return "Work marker holds only a link in "
    length = _length_problem(body)
    if length:
        return length.replace("Comment", "Work marker")
    words = body.split()
    verb = words[0].strip(",.:;") if words else ""
    if verb not in MARKER_VERBS:
        return "Work marker does not open with a capitalized verb from the verb list in "
    after = body[len(words[0]):]
    purpose = _purpose_clause(after)
    named = (after[:purpose.start()] if purpose else after).strip(" ,.:;").lower()
    named = re.sub(r"^(?:the|a|an)\s+", "", named) if named not in VAGUE_OBJECTS else named
    if not named or not LETTER_RE.search(named) or named in VAGUE_OBJECTS or named.split()[0] in VAGUE_OBJECTS:
        return "Work marker names no concrete object in "
    if purpose is None:
        return "Work marker states no to or so purpose in "
    if tag == "FIXME" and not DEFECT_RE.search(body):
        return "FIXME marker does not name the defect in "
    if tag == "HACK" and not REMOVAL_RE.search(body):
        return "HACK marker does not name its removal condition in "
    return ""


def _purpose_clause(after: str) -> re.Match[str] | None:
    for link in PURPOSE_RE.finditer(after):
        following = WORD_RE.findall(after[link.end():])
        if link.group(0).lower().endswith("to"):
            if following and following[0].lower() in PURPOSE_VERBS:
                return link
        elif len(following) >= 2:
            return link
    return None


def _shape_problem(comment: Logical) -> str:
    text = comment.text
    length = _length_problem(text)
    if length:
        return length
    if comment.code and restates(text, comment.code):
        return "Comment restates the adjacent code in "
    if NOTE_RE.match(text):
        if not _note_shape(text, comment.code):
            return "NOTE comment sits next to code that does not look redundant in "
        return _wrap_problem(comment, WRAPPING_LINES)
    if any(shape(text, comment.code) for shape in (_attribution_shape, _standard_shape)):
        return _wrap_problem(comment, WRAPPING_LINES)
    if any(shape(text, comment.code) for shape in (_tracker_shape, _why_shape)):
        return _wrap_problem(comment, 1)
    return "Comment fits none of the six allowed shapes in "


def _wrap_problem(comment: Logical, allowed: int) -> str:
    if comment.spanned <= allowed:
        return ""
    return f"Comment spans {comment.spanned} lines where its shape allows {allowed} in "


def _note_shape(text: str, code: str) -> bool:
    body = NOTE_RE.sub("", text, count=1)
    return len(content_words(body)) >= 2 and any(pattern.search(code) for pattern in REDUNDANT_CODE_RES)


def _attribution_shape(text: str, code: str) -> bool:
    if not URL_RE.search(text):
        return False
    return bool(VIA_URL_RE.search(text) or ATTRIBUTION_RE.search(URL_RE.sub(" ", text)))


def _standard_shape(text: str, code: str) -> bool:
    plain = URL_RE.sub(" ", text)
    named = URL_RE.search(text) or STANDARD_RE.search(plain)
    return bool(named and REQUIREMENT_RE.search(plain) and STANDARD_LINK_RE.search(plain))


def _tracker_shape(text: str, code: str) -> bool:
    found = [match for match in TRACKER_RE.finditer(text) if (match.group("key") or "").upper() not in NOT_TRACKER_KEYS]
    if not found:
        return False
    rest = TRACKER_RE.sub(" ", URL_RE.sub(" ", text))
    return len(content_words(rest)) >= 2


def _why_shape(text: str, code: str) -> bool:
    return states_why(text, code) or _three_part(text, code)


def _three_part(text: str, code: str) -> bool:
    link = CAUSAL_RE.search(text)
    if link is None:
        return False
    lead, reason = text[:link.start()].strip(" ,;"), text[link.end():].strip()
    lead_words = content_words(lead)
    fresh = content_words(reason) - lead_words - content_words(code)
    return bool(lead_words) and len(fresh) >= 2
