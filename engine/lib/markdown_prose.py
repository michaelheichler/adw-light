import re


QUOTED_PROSE_RE = re.compile(
    r"„[^“\n]*“|»[^«\n]*«|›[^‹\n]*‹|‚[^‘\n]*‘|"
    "\"[^\"\n]*\"|“[^”\n]*”|(?<!\\w)'[^'\n]*'(?!\\w)|(?<!\\w)‘[^’\n]*’(?!\\w)"
)


def _blank_quoted_inside(found: re.Match[str]) -> str:
    text = found.group(0)
    return text[0] + " " * (len(text) - 2) + text[-1]


QUOTE_MENTION_CONTEXT_RE = re.compile(
    r"\b(?:called|named|says?|said|wrote|reads?|phrase|term|words?|expression|label)[ \t]*$",
    re.IGNORECASE,
)


def _blank_quoted_mention(found: re.Match[str]) -> str:
    inner = found[0][1:-1]
    line_start = found.string.rfind("\n", 0, found.start()) + 1
    context = found.string[line_start:found.start()]
    if re.search(r"[.!?]", inner) or len(inner.split()) > 8:
        return found[0]
    if QUOTE_MENTION_CONTEXT_RE.search(context) is None:
        return found[0]
    return _blank_quoted_inside(found)


def mask_phrase_mentions(text: str) -> str:
    return QUOTED_PROSE_RE.sub(_blank_quoted_mention, text)


def mask_quoted(text: str) -> str:
    return QUOTED_PROSE_RE.sub(_blank_quoted_inside, text)


TABLE_SOURCE_CELL = re.compile(r"(?:`+[^`]+`+|(?:[\w.~/-]*/[\w./-]+|[\w.-]+:\d+)(?::\d+)?)")
CELL_TOKEN = re.compile(r"(`+).*?\1|\\.|\|")


def table_cells(line: str):
    start = 0
    for token in CELL_TOKEN.finditer(line):
        if token[0] == "|":
            yield line[start:token.start()].strip()
            start = token.end()
    yield line[start:].strip()


def reply_location(row: dict, numbers: list[int]) -> dict:
    mapped = dict(row, path="reply", line=numbers[row["line"] - 1])
    if "end_line" in row:
        mapped["end_line"] = numbers[row["end_line"] - 1]
    return mapped


def reply_lines(blocks):
    lines, numbers = [], []
    in_table = False
    for block in blocks:
        line = block.content
        candidate = not block.hidden and not line.lstrip().startswith(">") and "|" in line
        row = candidate and (line.lstrip().startswith("|") or in_table)
        in_table = candidate and (block.punctuation_separator or in_table)
        cells = list(table_cells(line)) if row and not block.punctuation_separator else [line]
        if row and not block.punctuation_separator:
            cells = [part for cell in cells if cell for part in ("", "" if TABLE_SOURCE_CELL.fullmatch(cell) else cell, "")]
        lines.extend(cells or [""])
        numbers.extend([block.number] * max(1, len(cells)))
    return "\n".join(lines), numbers
