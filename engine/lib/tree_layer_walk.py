from __future__ import annotations

import bisect
import re
import time

try:
    from .language_shapes import JS_TEST_HOOKS
    from .language_model import (
        CodeUnit,
        Kind,
        Layer,
        Node,
        UnitMark,
        comment_kind,
    )
except ImportError:
    from language_shapes import JS_TEST_HOOKS
    from language_model import (
        CodeUnit,
        Kind,
        Layer,
        Node,
        UnitMark,
        comment_kind,
    )


CLOCK_STRIDE = 2048


INTERPOLATION_MARKS = ("interpolation", "interpolated_expression", "substitution", "embedded")
EXPANSIONS = frozenset({"expansion", "simple_expansion", "arithmetic_expansion"})
STRING_MARKS = ("string", "str", "char", "rune", "character", "text_block", "sigil")
NON_ASCII_RE = re.compile(r"[^\x00-\x7f]")
FUNCTION_TYPES = frozenset({
    "function_declaration", "function_definition", "function_item", "method_declaration", "method_definition",
    "method", "singleton_method", "constructor_declaration", "init_declaration", "func_literal",
    "arrow_function", "function_expression", "function", "generator_function", "generator_function_declaration",
})
NAME_TYPES = frozenset({"identifier", "simple_identifier", "field_identifier", "property_identifier", "name", "word"})
NAMING_PARENTS = frozenset({"variable_declarator", "assignment_expression", "pair", "public_field_definition"})
MARK_TYPES = frozenset({
    "modifiers", "attribute_list", "attributes", "attribute", "attribute_item", "annotation",
    "marker_annotation", "decorator",
})
CONSTRUCTOR_TYPES = frozenset({"constructor_declaration", "init_declaration"})
JS_TEST_CALLS = frozenset({"it", "test"})
BODY_SUFFIXES = ("body", "block")
TEST_MARK_RE = re.compile(
    r"@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b"
    r"|\[\s*(?:Fact|Theory|Test|TestMethod|DataTestMethod|TestCase)\b"
    r"|#\[\s*(?:\w+\s*::\s*)?test\b|#\[\s*(?:\\?\w+\\)*Test\b",
)
EXPECTED_FAILURE_RE = re.compile(r"\bexpected(?:Exceptions?)?\s*=|\bshould_panic\b|\bExpectedException\b")
GO_TEST_NAME_RE = re.compile(r"^(?:Test|Fuzz)(?![a-z])")
TEST_NAME_RE = re.compile(r"^test(?:[A-Z0-9_]|$)")


class _OverBudget(Exception):
    pass


class _Offsets:
    def __init__(self, text: str) -> None:
        self.starts: list[int] = []
        self.extras: list[int] = []
        extra = 0
        for found in NON_ASCII_RE.finditer(text):
            self.starts.append(found.start() + extra)
            extra += len(found.group().encode("utf-8", "replace")) - 1
            self.extras.append(extra)

    def char(self, byte: int) -> int:
        before = bisect.bisect_left(self.starts, byte)
        return byte - self.extras[before - 1] if before else byte


def _tree_layer(language: str, text: str, tree, deadline: float) -> Layer:
    walk = _TreeWalk(language, text, _Offsets(text), deadline)
    walk.collect(tree.root_node)
    nodes = tuple(sorted(walk.nodes, key=lambda node: (node.start, -node.end)))
    return Layer(language, True, nodes, units=tuple(sorted(walk.units, key=lambda unit: (unit.start, -unit.end))))


def _container(node) -> Kind | None:
    kind = node.type
    if not node.is_named or kind.startswith("concatenated") or _interpolation(node):
        return None
    if "heredoc" in kind or "nowdoc" in kind:
        return Kind.HEREDOC
    if "regex" in kind or kind == "regular_expression":
        return Kind.REGEX
    if "template" in kind and "string" in kind:
        return Kind.TEMPLATE_TEXT
    parts = kind.split("_")
    return Kind.STRING_TEXT if any(mark in parts for mark in STRING_MARKS) else None


def _interpolation(node) -> bool:
    return node.type in EXPANSIONS or any(mark in node.type for mark in INTERPOLATION_MARKS)


class _TreeWalk:
    def __init__(self, language: str, text: str, offsets: _Offsets, deadline: float) -> None:
        self.language = language
        self.text = text
        self.offsets = offsets
        self.deadline = deadline
        self.visits = 0
        self.nodes: list[Node] = []
        self.units: list[CodeUnit] = []

    def tick(self) -> None:
        self.visits += 1
        if self.visits % CLOCK_STRIDE == 0 and time.monotonic() > self.deadline:
            raise _OverBudget

    def span(self, node) -> tuple[int, int]:
        return self.offsets.char(node.start_byte), self.offsets.char(node.end_byte)

    def collect(self, root) -> None:
        cursor = root.walk()
        while True:
            self.tick()
            if self.visit(cursor.node) and cursor.goto_first_child():
                continue
            while not cursor.goto_next_sibling():
                if not cursor.goto_parent():
                    return

    def visit(self, node) -> bool:
        start, end = self.span(node)
        if node.is_error or node.is_missing:
            self.nodes.append(Node(Kind.ERROR, start, max(end, start)))
            return True
        if node.is_named and "comment" in node.type:
            self.nodes.append(Node(comment_kind(self.text[start:end], start), start, end))
            return False
        if self.expansion_text(node, start, end):
            return False
        container = _container(node)
        if container is not None:
            self.container(node, container, start, end)
            return False
        if node.is_named and node.child_count == 0 and node.type.endswith("identifier"):
            self.nodes.append(Node(Kind.IDENTIFIER, start, end))
        if node.type in FUNCTION_TYPES and node.is_named:
            self.unit(node, start, end)
        return True

    def text_of(self, node) -> str:
        start, end = self.span(node)
        return self.text[start:end]

    def unit(self, node, start: int, end: int) -> None:
        call = _test_call(node, self.text_of)
        named = _name_node(node)
        name = self.text_of(named) if named is not None else "anonymous"
        if call is not None:
            header = self.span(call)[0]
        else:
            header = self.span(named)[0] if named is not None and self.span(named)[0] >= start else start
        mark = UnitMark.NAMED if call is not None else self.unit_mark(node, name)
        expects = any(EXPECTED_FAILURE_RE.search(text) for text in self.mark_texts(node))
        self.units.append(CodeUnit(name, header, end, self.body_start(node, named, start), mark, expects))

    def body_start(self, node, named, start: int) -> int:
        body = node.child_by_field_name("body")
        if body is None:
            body = next((child for child in reversed(node.named_children) if child.type.endswith(BODY_SUFFIXES)), None)
        if body is not None:
            return self.span(body)[0]
        return max(start, self.span(named)[1]) if named is not None else start

    def unit_mark(self, node, name: str) -> UnitMark:
        if node.type in CONSTRUCTOR_TYPES:
            return UnitMark.NONE
        if any(TEST_MARK_RE.search(mark) for mark in self.mark_texts(node)):
            return UnitMark.MARKED
        pattern = GO_TEST_NAME_RE if self.language == "go" else TEST_NAME_RE
        named = bool(pattern.match(name.strip("`"))) and name != "TestMain"
        return UnitMark.NAMED if named else UnitMark.NONE

    def mark_texts(self, node) -> list[str]:
        marks = [self.text_of(child) for child in node.children if child.type in MARK_TYPES]
        sibling = node.prev_named_sibling
        while sibling is not None and sibling.type in MARK_TYPES:
            marks.append(self.text_of(sibling))
            sibling = sibling.prev_named_sibling
        return marks

    def expansion_text(self, node, start: int, end: int) -> bool:
        parent = node.parent
        if self.language != "bash" or parent is None or parent.type != "expansion":
            return False
        if node.type == "word":
            self.nodes.append(Node(Kind.STRING_TEXT, start, end))
            return True
        return False

    def container(self, node, kind: Kind, start: int, end: int) -> None:
        if kind is Kind.STRING_TEXT or kind is Kind.TEMPLATE_TEXT:
            self.nodes.append(Node(Kind.STRING, start, end))
        cursor = start
        for child in node.children:
            if not _interpolation(child):
                continue
            child_start, child_end = self.span(child)
            if child_start > cursor:
                self.nodes.append(Node(kind, cursor, child_start))
            self.collect(child)
            cursor = max(cursor, child_end)
        if end > cursor:
            self.nodes.append(Node(kind, cursor, end))


def _name_node(node):
    named = node.child_by_field_name("name")
    if named is None:
        named = next((child for child in node.named_children if child.type in NAME_TYPES), None)
    declarator = node.child_by_field_name("declarator")
    while named is None and declarator is not None:
        if declarator.type in NAME_TYPES:
            named = declarator
        declarator = declarator.child_by_field_name("declarator")
    parent = node.parent
    if named is None and parent is not None and parent.type in NAMING_PARENTS:
        named = parent.child_by_field_name("name") or parent.child_by_field_name("left") or parent.child_by_field_name("key")
    return named


def _test_call(node, text_of):
    arguments = node.parent
    if node.type not in {"arrow_function", "function_expression", "function"} or arguments is None:
        return None
    call = arguments.parent
    if arguments.type != "arguments" or call is None or call.type != "call_expression":
        return None
    callee = call.child_by_field_name("function")
    while callee is not None and callee.type == "member_expression":
        member = callee.child_by_field_name("property")
        if member is not None and text_of(member) in JS_TEST_HOOKS:
            return None
        callee = callee.child_by_field_name("object")
    return call if callee is not None and text_of(callee) in JS_TEST_CALLS else None
