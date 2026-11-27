from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

try:
    from . import git_port
    from .declaration_mask import _literal_end, declaration_view
    from .findings import finding_row
except ImportError:
    import git_port
    from declaration_mask import _literal_end, declaration_view
    from findings import finding_row


DOC_FORM = {'.py': r'\bdocstrings?\b', **dict.fromkeys(('.kt', '.kts', '.java'), r'\b(?:kdoc|javadoc)\b')}
LANGUAGE = {'.py': 'python', '.kt': 'kotlin', '.kts': 'kotlin', '.java': 'java'}
NEGATION = re.compile(r"\b(?:no|never|avoid|don['’]t|do not|without|minimum|minimal|fewer)\b", re.I)
FILES = ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md")
JVM = {".kt", ".kts", ".java"}
REFERENCE = re.compile(r"\[([A-Za-z_]\w*(?:\.\w+)*)\]")
ANNOTATIONS = r"\s*(?:@[\w.]+(?:\([^()]*\))?\s*)*"
KOTLIN_DECL = re.compile(ANNOTATIONS + r"(?:\w+\s+)*(?:fun|val|var|class|object|interface|enum|constructor|typealias)\b")
JAVA_DECL = re.compile(ANNOTATIONS + r"(?!(?:return|throw|new|if|for|while|switch|try|catch)\b)"
                       r"(?:[\w<>?,.\[\]]+\s+)+\w+\s*[(=;{]")


class Doc(NamedTuple):
    line: int
    end: int
    body: str
    start_offset: int = 0
    end_offset: int = 0


@lru_cache(maxsize=128)
def _convention(root: Path, stamps: tuple[tuple[str, int], ...], suffix: str) -> bool:
    if suffix not in DOC_FORM:
        return False
    form = re.compile(DOC_FORM[suffix], re.I)
    language = re.compile(r"\b" + LANGUAGE[suffix] + r"\s+(?:source|code|files?)\b", re.I)
    enabled = False
    for name, _ in stamps:
        for sentence in re.split(r"[.!?\n]+", (root / name).read_text(errors="replace")):
            if NEGATION.search(sentence):
                if form.search(sentence) or language.search(sentence):
                    return False
                if re.search(r"\bcomments\b", sentence, re.I) and not re.search(r"\b(?:python|java|kotlin|xml|strings?)\b", sentence, re.I):
                    return False
            elif form.search(sentence):
                enabled = True
    return enabled


def repo_docs(path: Path) -> bool:
    folder = next(parent for parent in path.resolve().parents if parent.is_dir())
    try:
        root = git_port.view().toplevel(folder, timeout=2, errors="strict", newline_only=True)
        if root is None:
            return False
        stamps = tuple((name, (root / name).stat().st_mtime_ns) for name in FILES if (root / name).is_file())
        return _convention(root, stamps, path.suffix.lower())
    except OSError:
        return False


def _jvm_docs(text: str, suffix: str):
    declaration = JAVA_DECL if suffix == ".java" else KOTLIN_DECL
    index = 0
    line = 1
    while index < len(text):
        end = _literal_end(text, index, suffix) or index + 1
        token = text[index:end]
        last = line + token.count("\n")
        if token.startswith("/**") and token.endswith("*/") and declaration.match(text, end):
            body = re.sub(r"(?m)^[ \t]*\* ?", "", token[3:-2])
            yield Doc(line, last, body, index, end)
        index, line = end, last


def comment_findings(context, source: str, scan_comment_contract, scan_all) -> list[dict]:
    docs = declaration_docs(context.path, context.text, context.tree)
    if not docs:
        return scan_comment_contract(context.path, source, context.tree, context.text)
    lines = {line for doc in docs for line in range(doc.line, doc.end + 1)}
    if Path(context.path).suffix in JVM:
        source = without_docs(source, docs)
    rows = scan_comment_contract(context.path, source, context.tree, context.text)
    doc_rules = {"what_docstring", "docstring_narration", "added_docstring"}
    rows = [row for row in rows if row["rule"] not in doc_rules or row["line"] not in lines]
    code = reference_source(context.text, context.path, context.tree)
    for doc in docs:
        rows.extend(unresolved_links(doc, code))
        for row in scan_all("doc-comment.md", doc.body, context.config, document=False):
            if row["family"] in {"english", "punctuation"}:
                located = dict(row, line=row["line"] + doc.line - 1)
                if "end_line" in row:
                    located["end_line"] = row["end_line"] + doc.line - 1
                rows.append(located)
    return rows


def declaration_docs(path: str, text: str, tree) -> list[Doc]:
    suffix = Path(path).suffix.lower()
    if suffix in JVM:
        return list(_jvm_docs(text, suffix)) if "/**" in text and repo_docs(Path(path)) else []
    if tree is None:
        return []
    scopes = (node for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)))
    docs = [Doc(node.body[0].lineno, node.body[0].end_lineno, body) for node in scopes if (body := ast.get_docstring(node, clean=False))]
    return docs if docs and repo_docs(Path(path)) else []


def without_docs(text: str, docs: list[Doc]) -> str:
    parts = []
    cursor = 0
    for doc in docs:
        parts.extend((text[cursor:doc.start_offset], re.sub(r"[^\n]", " ", text[doc.start_offset:doc.end_offset])))
        cursor = doc.end_offset
    return "".join([*parts, text[cursor:]])


def reference_source(text: str, path: str, tree) -> str:
    if tree is None:
        return declaration_view(text, Path(path).suffix)
    return " ".join(name for node in ast.walk(tree) for key in ("name", "id", "arg", "attr", "asname") if isinstance(name := getattr(node, key, None), str))


def unresolved_links(doc: Doc, code: str) -> list[dict]:
    rows = []
    for offset, line in enumerate(doc.body.splitlines()):
        for match in REFERENCE.finditer(re.sub(r"`[^`]*`", "", line)):
            if not re.search(r"(?<!\w)" + re.escape(match[1]) + r"(?!\w)", code):
                rows.append(finding_row("comment", "unresolved_doc_reference", doc.line + offset,
                    "No matching name in this file", match[0], "Use a name from this file or put the reference in backticks."))
    return rows
