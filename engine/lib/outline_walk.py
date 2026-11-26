from __future__ import annotations

import re

try:
    from .language_operands import OperandReader
    from .language_shapes import DART_TEST_CALLS, Role, Shape
    from .tree_layer_walk import _Offsets
except ImportError:
    from language_operands import OperandReader
    from language_shapes import DART_TEST_CALLS, Role, Shape
    from tree_layer_walk import _Offsets


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
DART_CALLS = DART_TEST_CALLS | frozenset({
    "expect", "equals", "contains", "startsWith", "endsWith", "equalsIgnoringCase", "equalsIgnoringWhitespace", "matches",
})
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
        if self.language == "dart":
            self.dart_prefixes = self.dart_import_prefixes(root)
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
        elif self.language == "dart" and kind == "argument_part":
            self.dart_call(node)
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

    def dart_import_prefixes(self, root) -> set[str]:
        prefixes = set()
        for directive in root.named_children:
            if directive.type != "import_or_export":
                continue
            for library in directive.named_children:
                if library.type != "library_import":
                    continue
                for spec in library.named_children:
                    uri = next((n for n in spec.named_children if n.type == "configurable_uri"), None)
                    if uri is None or self.flat(uri).strip("\"'") not in {"package:test/test.dart", "package:flutter_test/flutter_test.dart"}:
                        continue
                    prefixes.update(self.flat(n) for n in spec.named_children if n.type == "identifier"
                                    and n.prev_sibling is not None and n.prev_sibling.type == "as")
        return prefixes

    def dart_call(self, node) -> None:
        selector = node.parent
        name = selector.prev_named_sibling if selector is not None else None
        if name is None:
            return
        if name.type == "selector":
            prefix = name.prev_named_sibling
            if prefix is None or prefix.type != "identifier" or self.flat(prefix) not in self.dart_prefixes:
                return
            callee = self.flat(name).removeprefix(".").strip()
            name = prefix
        elif name.type == "identifier":
            callee = self.flat(name)
        else:
            return
        if callee not in DART_CALLS:
            return
        start, end = self.span(name)[0], self.span(node)[1]
        operands = self.operands.arguments(node)
        callback = tuple((arg.start, arg.end) for arg in operands[1:2]) if callee in DART_TEST_CALLS else ()
        self.shapes.append(Shape(Role.CALL, start, end, callee, start, callback, operands))

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
