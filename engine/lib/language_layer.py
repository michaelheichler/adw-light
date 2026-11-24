from __future__ import annotations

import bisect
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from functools import lru_cache
from importlib import import_module, metadata
from pathlib import Path, PurePath
from types import ModuleType
from typing import Any, Protocol

try:
    from . import config_roots
    from .language_model import (
        PYTHON,
        CodeUnit,
        Kind,
        Layer,
        Node,
        UnitMark,
        comment_kind,
        lexer_layer,
        python_layer,
    )
    from .language_operands import OperandReader
    from .language_shapes import Outline, Role, Shape
except ImportError:
    import config_roots
    from language_model import (
        PYTHON,
        CodeUnit,
        Kind,
        Layer,
        Node,
        UnitMark,
        comment_kind,
        lexer_layer,
        python_layer,
    )
    from language_operands import OperandReader
    from language_shapes import Outline, Role, Shape

PACK = "tree_sitter_language_pack"
REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"
FETCHER = Path(__file__).with_name("grammar_fetch.py")
FETCH_SECONDS = 3.0
PARSE_SECONDS = 1.5
PRECISE_CHARS = 262_144
CLOCK_STRIDE = 2048
RETRY_SECONDS = 300
LIBRARY_MISSING = "library_missing"
VERSION_MISMATCH = "version_mismatch"
DOWNLOAD_FAILED = "download_failed"
DOWNLOAD_PENDING = "download_pending"
PARSER_FAILED = "parser_failed"
SIZE_BUDGET = "size_budget"
TIME_BUDGET = "time_budget"
GRAMMARS = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift", ".rb": "ruby", ".php": "php", ".sh": "bash", ".bash": "bash",
    ".zsh": "bash", ".bats": "bash", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".cs": "csharp", ".scala": "scala",
    ".lua": "lua", ".dart": "dart", ".ex": "elixir", ".exs": "elixir",
}
PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
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


class _Parser(Protocol):
    def parse(self, source: bytes, /) -> Any: ...


_parsers: dict[str, tuple[_Parser | None, str]] = {}
_logged: set[tuple[str, str, str]] = set()


class _OverBudget(Exception):
    pass


class _Budget:
    def __init__(self) -> None:
        self.chars = PRECISE_CHARS
        self.seconds = PARSE_SECONDS
        self.fetch_deadline: float | None = None
        self.charged: set[str] = set()

    def charge(self, text: str) -> bool:
        if not self.charged.isdisjoint((text, text + "\n", text.removesuffix("\n"))):
            return True
        if len(text) > self.chars:
            return False
        self.chars -= len(text)
        self.charged.add(text)
        return True

    def fetch_seconds(self) -> float:
        if self.fetch_deadline is None:
            self.fetch_deadline = time.monotonic() + FETCH_SECONDS
        return max(0.0, self.fetch_deadline - time.monotonic())


_budget = _Budget()


@contextmanager
def own_budget(chars: int, deadline: float):
    global _budget
    saved = _budget
    _budget = _Budget()
    _budget.chars = chars
    _budget.seconds = max(0.0, min(PARSE_SECONDS, deadline - time.monotonic()))
    _budget.fetch_deadline = deadline
    try:
        yield
    finally:
        _budget = saved


def language_of(path: str) -> str | None:
    suffix = PurePath(path.lower()).suffix
    return PYTHON if suffix in PYTHON_SUFFIXES else GRAMMARS.get(suffix)


def pins() -> dict[str, str]:
    rows = (line.strip().partition("==") for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines())
    return {name.strip(): version.strip() for name, _, version in rows if version}


def layer_for(path: str, language: str, text: str) -> Layer:
    layer = parse(language, text)
    if not layer.precise:
        log_reduced_precision(path, language, layer.reason)
    return layer


@lru_cache(maxsize=8)
def parse(language: str, text: str) -> Layer:
    if language == PYTHON:
        return python_layer(text)
    parser, reason = _parser(language)
    if parser is None:
        return lexer_layer(language, reason)
    return _budgeted_layer(language, text, parser)


def _budgeted_layer(language: str, text: str, parser) -> Layer:
    if not _budget.charge(text):
        return lexer_layer(language, SIZE_BUDGET)
    if _budget.seconds <= 0:
        return lexer_layer(language, TIME_BUDGET)
    started = time.monotonic()
    try:
        return _tree_layer(language, text, _syntax_tree(language, text), started + _budget.seconds)
    except _OverBudget:
        return lexer_layer(language, TIME_BUDGET)
    except Exception:
        return lexer_layer(language, PARSER_FAILED)
    finally:
        _budget.seconds -= time.monotonic() - started


def outline_for(path: str, language: str, text: str) -> Outline | None:
    if language == PYTHON:
        return None
    layer = layer_for(path, language, text)
    if not layer.precise:
        return None
    try:
        return Outline(_outline(language, text), bool(layer.errors))
    except Exception:
        log_reduced_precision(path, language, PARSER_FAILED)
        return None


@lru_cache(maxsize=4)
def _syntax_tree(language: str, text: str):
    parser, reason = _parser(language)
    if parser is None:
        raise LookupError(reason)
    return parser.parse(text.encode("utf-8", "replace"))


@lru_cache(maxsize=4)
def _outline(language: str, text: str) -> tuple[Shape, ...]:
    walk = _OutlineWalk(language, text, _Offsets(text))
    walk.collect(_syntax_tree(language, text).root_node)
    return tuple(sorted(walk.shapes, key=lambda shape: (shape.start, -shape.end)))


def log_reduced_precision(path: str, language: str, reason: str) -> None:
    key = (path, language, reason)
    if key in _logged:
        return
    _logged.add(key)
    try:
        reporting = import_module(__package__ + ".reporting" if __package__ else "reporting")
        reporting.append_row({
            "ts": reporting.now_iso(), "hook": "language_layer", "event": "reduced_precision",
            "path": path, "language": language, "reason": reason,
        })
    except Exception as error:
        print(f"ADW Lite scanned {path} with the fallback lexer ({reason}): {error}", file=sys.stderr)


def _parser(language: str) -> tuple[_Parser | None, str]:
    if language not in _parsers:
        try:
            _parsers[language] = _load_parser(language)
        except Exception:
            _parsers[language] = (None, PARSER_FAILED)
    return _parsers[language]


def _load_parser(language: str) -> tuple[_Parser | None, str]:
    pack = _pack()
    if pack is None:
        return None, LIBRARY_MISSING
    if not _pinned():
        return None, VERSION_MISMATCH
    cache = config_roots.grammar_cache_root()
    reason = _grammar_state(pack, cache, language)
    return (pack.get_parser(language), "") if not reason else (None, reason)


def _pack() -> ModuleType | None:
    try:
        return import_module(PACK)
    except ImportError:
        pass
    site = _environment_site()
    if site is None:
        return None
    sys.path.append(str(site))
    try:
        return import_module(PACK)
    except ImportError:
        return None


def _environment_site() -> Path | None:
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = config_roots.language_environment() / "lib" / version / "site-packages"
    return site if (site / PACK).is_dir() else None


def _pinned() -> bool:
    try:
        return all(metadata.version(name) == version for name, version in pins().items())
    except metadata.PackageNotFoundError:
        return False


def _grammar_state(pack: ModuleType, cache: Path, language: str) -> str:
    try:
        pack.configure(pack.PackConfig(cache_dir=str(cache)))
        if language in pack.downloaded_languages():
            return ""
    except Exception:
        return DOWNLOAD_FAILED
    marker = cache / "failed" / language
    if _recent(marker):
        return DOWNLOAD_FAILED
    _mark(marker)
    reason = _fetch(pack, cache, language)
    if not reason and language in pack.downloaded_languages():
        _clear(marker)
        return ""
    return reason or DOWNLOAD_FAILED


def _recent(marker: Path) -> bool:
    try:
        return time.time() - marker.stat().st_mtime < RETRY_SECONDS
    except OSError:
        return False


def _clear(marker: Path) -> None:
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        return


def _mark(marker: Path) -> None:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        return


def _fetch(pack: ModuleType, cache: Path, language: str) -> str:
    origin = pack.__file__
    if origin is None:
        raise TypeError(PACK)
    site = Path(origin).resolve().parents[1]
    command = [sys.executable, "-I", "-S", "-B", str(FETCHER), str(cache), language, str(site)]
    try:
        child = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return "" if child.wait(timeout=_budget.fetch_seconds()) == 0 else DOWNLOAD_FAILED
    except subprocess.TimeoutExpired:
        return DOWNLOAD_PENDING
    except OSError:
        return DOWNLOAD_FAILED


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
        callee = callee.child_by_field_name("object")
    return call if callee is not None and text_of(callee) in JS_TEST_CALLS else None


OUTLINE_FUNCTION_TYPES = frozenset({
    "function_declaration", "function_definition", "method_declaration", "method_definition",
    "function_item", "method", "singleton_method",
})
CLOSURE_TYPES = frozenset({
    "lambda_expression", "arrow_function", "function_expression", "function", "func_literal",
    "closure_expression", "lambda_literal", "anonymous_function", "lambda", "do_block",
    "anonymous_function_creation_expression", "anonymous_method_expression",
})
RUBY_CLOSURE_TYPES = frozenset({"block"})
LUA_CLOSURE_TYPES = frozenset({"function_definition"})
NATIVE_LANGUAGES = frozenset({"c", "cpp"})
CATCH_CASES = frozenset({"TEST_CASE", "TEST_CASE_METHOD", "TEMPLATE_TEST_CASE", "SCENARIO"})
CALL_TYPES = frozenset({
    "call_expression", "method_invocation", "invocation_expression", "call", "member_call_expression",
    "scoped_call_expression", "function_call_expression", "nullsafe_member_call_expression",
    "macro_invocation", "function_call",
})
ASSERT_TYPES = frozenset({"assert_statement"})
LOOP_TYPES = frozenset({
    "for_statement", "enhanced_for_statement", "for_in_statement", "foreach_statement",
    "while_statement", "do_statement", "do_while_statement", "repeat_while_statement",
    "for_expression", "while_expression", "loop_expression", "for_range_loop",
    "c_style_for_statement", "for", "while", "until",
})
BRANCH_TYPES = frozenset({"if_statement", "if_expression", "if", "unless"})
ARGUMENT_TYPES = frozenset({"argument_list", "arguments", "call_suffix", "value_arguments", "token_tree"})
WRAPPER_TYPES = frozenset({
    "block", "statement_list", "statements", "body_statement", "function_body",
    "control_structure_body", "compound_statement", "statement_block",
})
ATTRIBUTE_TYPES = frozenset({"attribute_item"})
CLOSURE_SEARCH_DEPTH = 3
WHITESPACE_RE = re.compile(r"\s+")


def _extra_closures(language: str) -> frozenset[str]:
    if language == "ruby":
        return RUBY_CLOSURE_TYPES
    return LUA_CLOSURE_TYPES if language == "lua" else frozenset()


class _OutlineWalk:
    def __init__(self, language: str, text: str, offsets: _Offsets) -> None:
        self.language = language
        self.text = text
        self.offsets = offsets
        self.closures = CLOSURE_TYPES | _extra_closures(language)
        self.functions = OUTLINE_FUNCTION_TYPES - self.closures
        self.shapes: list[Shape] = []
        self.operands = OperandReader(language, text, self.span)

    def span(self, node) -> tuple[int, int]:
        return self.offsets.char(node.start_byte), self.offsets.char(node.end_byte)

    def collect(self, root) -> None:
        cursor = root.walk()
        while True:
            if cursor.node.is_named:
                self.visit(cursor.node)
            if cursor.goto_first_child():
                continue
            while not cursor.goto_next_sibling():
                if not cursor.goto_parent():
                    return

    def visit(self, node) -> None:
        kind = node.type
        if kind in self.functions:
            self.scope(node, Role.FUNCTION, self.function_start(node), self.function_name(node))
        elif kind in self.closures:
            self.scope(node, Role.CLOSURE, self.span(node)[0], "")
        elif kind in CALL_TYPES:
            self.call(node)
        elif kind in ASSERT_TYPES:
            self.shapes.append(Shape(Role.ASSERT, *self.span(node)))
        elif kind in LOOP_TYPES:
            self.shapes.append(Shape(Role.LOOP, *self.span(node)))
        elif kind in BRANCH_TYPES:
            self.branch(node)
        elif kind == "compound_statement" and self.language in NATIVE_LANGUAGES:
            self.catch_case(node)
        self.shapes.extend(self.operands.shapes(node))

    def function_start(self, node) -> int:
        start = node
        while start.prev_named_sibling is not None and start.prev_named_sibling.type in ATTRIBUTE_TYPES:
            start = start.prev_named_sibling
        return self.span(start)[0]

    def function_name(self, node) -> str:
        named = node.child_by_field_name("name")
        if named is not None:
            return self.flat(named)
        declarator = node.child_by_field_name("declarator")
        if declarator is not None:
            return self.flat(declarator)
        found = next((child for child in node.named_children if child.type.endswith("identifier")), None)
        return self.flat(found) if found is not None else ""

    def flat(self, node) -> str:
        start, end = self.span(node)
        return WHITESPACE_RE.sub(" ", self.text[start:end]).strip()

    def body(self, node):
        body = node.child_by_field_name("body")
        if body is not None:
            return body
        return next((child for child in reversed(node.named_children) if child.type in WRAPPER_TYPES), None)

    def statements(self, body) -> tuple[tuple[int, int], ...]:
        node = body
        while True:
            children = [child for child in node.named_children if "comment" not in child.type]
            if len(children) == 1 and children[0].type in WRAPPER_TYPES:
                node = children[0]
                continue
            break
        if node.type not in WRAPPER_TYPES:
            return (self.span(node),)
        return tuple(self.span(child) for child in children)

    def scope(self, node, role: Role, start: int, name: str) -> None:
        end = self.span(node)[1]
        body = self.body(node)
        if body is None:
            self.shapes.append(Shape(role, start, end, name, end))
            return
        self.shapes.append(Shape(role, start, end, name, self.span(body)[0], self.statements(body)))

    def call(self, node) -> None:
        start, end = self.span(node)
        if node.type == "macro_invocation":
            macro = node.child_by_field_name("macro")
            name = self.flat(macro) if macro is not None else self.callee(node, start, end).lstrip("#")
        else:
            name = self.callee(node, start, end)
        closures = tuple(self.span(found) for found in self.closure_arguments(node, 0))
        self.shapes.append(Shape(Role.CALL, start, end, name, start, closures, self.operands.arguments(node)))

    def catch_case(self, node) -> None:
        head = node.prev_named_sibling
        if head is None or head.type != "expression_statement" or head.named_child_count != 1:
            return
        call = head.named_children[0]
        if call.type != "call_expression" or self.callee(call, *self.span(call)) not in CATCH_CASES:
            return
        start, end = self.span(node)
        name = self.flat(call)
        self.shapes.append(Shape(Role.FUNCTION, self.span(head)[0], end, name, start, self.statements(node)))

    def callee(self, node, start: int, end: int) -> str:
        block = node.child_by_field_name("block")
        cuts = [self.span(child)[0] for child in node.children if child.type in ARGUMENT_TYPES]
        if block is not None:
            cuts.append(self.span(block)[0])
        stop = min(cuts) if cuts else end
        return WHITESPACE_RE.sub("", self.text[start:stop])

    def closure_arguments(self, node, depth: int):
        for child in node.named_children:
            if child.type in self.closures:
                yield child
            elif depth < CLOSURE_SEARCH_DEPTH and child.type not in CALL_TYPES:
                yield from self.closure_arguments(child, depth + 1)

    def branch(self, node) -> None:
        consequence = node.child_by_field_name("consequence") or node.child_by_field_name("body")
        alternative = node.child_by_field_name("alternative")
        if consequence is None or alternative is None:
            consequence, alternative = self.else_sides(node)
        if consequence is None or alternative is None:
            return
        start, end = self.span(node)
        self.shapes.append(Shape(Role.BRANCH, start, end, "", start, (self.span(consequence), self.span(alternative))))

    def else_sides(self, node):
        children = list(node.children)
        marker = next((index for index, child in enumerate(children) if child.type == "else"), None)
        if marker is None:
            return None, None
        before = [child for child in children[:marker] if child.is_named]
        after = [child for child in children[marker + 1:] if child.is_named]
        return (before[-1] if len(before) > 1 else None), (after[0] if after else None)
