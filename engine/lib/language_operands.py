from __future__ import annotations

import re
from collections.abc import Callable

try:
    from .language_shapes import COMPUTED, Operand, Role, Shape
except ImportError:
    from language_shapes import COMPUTED, Operand, Role, Shape

Span = Callable[[object], tuple[int, int]]
ARGUMENT_LISTS = frozenset({"argument_list", "arguments", "value_arguments"})
WRAPPERS = frozenset({"argument", "value_argument", "parenthesized_expression", "expression_list", "equals_value_clause"})
STRING_TYPES = frozenset({
    "string", "string_literal", "interpreted_string_literal", "raw_string_literal", "template_string",
    "encapsed_string", "raw_string", "line_string_literal", "multi_line_string_literal", "verbatim_string_literal",
    "text_block", "heredoc_body", "string_value", "char_literal", "character_literal",
})
PATTERN_TYPES = frozenset({"regex", "regex_literal", "regular_expression"})
CONTENT_MARKS = ("content", "fragment", "escape", "text", "pattern")
CONCAT_OPERATORS = frozenset({"+", "."})
COMPARE_TYPES = frozenset({"binary_expression", "equality_expression", "binary", "binary_operator"})
COMPARE_OPERATORS = frozenset({"==", "!=", "===", "!==", "=", "=~", "eq", "ne"})
GUARD_TYPES = frozenset({"if_statement", "if_expression", "if", "unless"})
BINDING_TYPES = frozenset({
    "variable_declarator", "short_var_declaration", "assignment", "assignment_expression",
    "property_declaration", "const_spec", "var_spec", "let_declaration", "variable_assignment",
})
NAME_FIELDS = ("name", "left", "pattern")
VALUE_FIELDS = ("value", "right")
CALL_SUFFIX = "call_suffix"
COMMAND_SKIPPED = frozenset({"file_redirect", "herestring_redirect", "heredoc_redirect", "variable_assignment"})
NAME_ENDINGS = ("identifier", "name", "variable_name", "constant")
DELIMITER_RE = re.compile(r"""^(?:[A-Za-z@$]{0,2})("{3}|'{3}|["'`])(.*)\1$""", re.DOTALL)


class OperandReader:
    def __init__(self, language: str, text: str, span: Span) -> None:
        self.language = language
        self.text = text
        self.span = span

    def source(self, node) -> str:
        start, end = self.span(node)
        return self.text[start:end]

    def arguments(self, node) -> tuple[Operand, ...]:
        if node.type == "macro_invocation":
            return self.token_operands(node)
        found = node.child_by_field_name("arguments") or _argument_list(node)
        if found is None:
            suffix = next((child for child in node.children if child.type == CALL_SUFFIX), None)
            found = _argument_list(suffix) if suffix is not None else None
        if found is None:
            return ()
        return tuple(self.operand(child) for child in found.named_children if "comment" not in child.type)

    def token_operands(self, node) -> tuple[Operand, ...]:
        tree = next((child for child in node.children if child.type == "token_tree"), None)
        if tree is None:
            return ()
        groups: list[list] = [[]]
        for child in tree.children[1:-1]:
            if child.type == ",":
                groups.append([])
            else:
                groups[-1].append(child)
        operands = []
        for group in groups:
            if len(group) == 1:
                operands.append(self.operand(group[0]))
            elif group:
                operands.append(Operand(self.span(group[0])[0], self.span(group[-1])[1]))
        return tuple(operands)

    def operand(self, node) -> Operand:
        node = self.unwrapped(node)
        start, end = self.span(node)
        literal = self.literal(node)
        return Operand(start, end, literal, node.type in PATTERN_TYPES)

    def unwrapped(self, node):
        while node.type in WRAPPERS and node.named_child_count:
            node = node.named_children[-1]
        return node

    def literal(self, node) -> str | None:
        node = self.unwrapped(node)
        if node.type in PATTERN_TYPES or node.type in STRING_TYPES:
            return self.string_text(node)
        if node.type == "word" and self.language == "bash":
            return self.source(node)
        if node.type in COMPARE_TYPES | {"additive_expression"} and node.named_child_count == 2:
            operator = self.operator(node)
            if operator in CONCAT_OPERATORS:
                left, right = (self.literal(child) for child in node.named_children)
                if left is not None or right is not None:
                    return (left if left is not None else COMPUTED) + (right if right is not None else COMPUTED)
        return None

    def string_text(self, node) -> str:
        named = [child for child in node.named_children if "comment" not in child.type]
        if not named:
            whole = DELIMITER_RE.match(self.source(node))
            return whole.group(2) if whole else self.source(node)
        pieces = []
        for child in named:
            if any(mark in child.type for mark in CONTENT_MARKS) and child.type not in STRING_TYPES:
                pieces.append(self.source(child))
            elif child.type not in {"regex_flags"}:
                pieces.append(COMPUTED)
        return "".join(pieces)

    def operator(self, node) -> str:
        found = node.child_by_field_name("operator")
        if found is not None:
            return self.source(found).strip()
        between = [child for child in node.children if not child.is_named]
        return self.source(between[0]).strip() if between else ""

    def shapes(self, node) -> list[Shape]:
        kind = node.type
        if kind == "command" and self.language == "bash":
            return [self.command(node)]
        if kind == "infix_expression" and self.language == "kotlin":
            return self.infix(node)
        if kind in COMPARE_TYPES:
            return self.compare(node)
        if kind == "test_command" and self.language == "bash":
            return [Shape(Role.CHECK, *self.span(node))]
        if kind in GUARD_TYPES:
            return self.guard(node)
        if kind in BINDING_TYPES:
            return self.binding(node)
        return []

    def command(self, node) -> Shape:
        start, end = self.span(node)
        named = node.child_by_field_name("name")
        name = self.source(named).strip() if named is not None else ""
        operands = tuple(
            self.operand(child) for child in node.named_children
            if child.type not in COMMAND_SKIPPED and self.span(child) != self.span(named or node)
        )
        return Shape(Role.CALL, start, end, name, start, (), operands)

    def infix(self, node) -> list[Shape]:
        children = node.named_children
        if len(children) != 3:
            return []
        start, end = self.span(node)
        name = self.source(children[1]).strip()
        return [Shape(Role.CALL, start, end, name, start, (), (self.operand(children[0]), self.operand(children[2])))]

    def compare(self, node) -> list[Shape]:
        children = [child for child in node.named_children if "comment" not in child.type]
        operator = self.operator(node)
        if len(children) != 2 or operator not in COMPARE_OPERATORS:
            return []
        start, end = self.span(node)
        operands = tuple(self.operand(child) for child in children)
        if operator == "=~":
            operands = (operands[0], operands[1]._replace(pattern=True))
        return [Shape(Role.COMPARE, start, end, operator, start, (), operands)]

    def guard(self, node) -> list[Shape]:
        condition = node.child_by_field_name("condition")
        consequence = node.child_by_field_name("consequence") or node.child_by_field_name("body")
        if condition is None or consequence is None:
            return []
        start, end = self.span(condition)
        return [Shape(Role.GUARD, start, end, "", start, (self.span(consequence),))]

    def binding(self, node) -> list[Shape]:
        name, value = _bound_pair(node)
        if name is None or value is None or name.named_child_count > 1:
            return []
        name = self.unwrapped(name)
        if name.named_child_count > 1 or not name.type.endswith(NAME_ENDINGS):
            return []
        operand = self.operand(value)
        if operand.literal is None:
            return []
        start, end = self.span(node)
        return [Shape(Role.BINDING, start, end, bound_name(self.source(name)), start, (), (operand,))]


def bound_name(source: str) -> str:
    return source.strip().lstrip("$")


def _argument_list(node):
    return next((child for child in node.children if child.type in ARGUMENT_LISTS), None)


def _field(node, fields: tuple[str, ...]):
    return next((found for found in map(node.child_by_field_name, fields) if found is not None), None)


def _bound_pair(node):
    name, value = _field(node, NAME_FIELDS), _field(node, VALUE_FIELDS)
    if name is None and node.type == "property_declaration":
        declared = next((child for child in node.named_children if child.type == "variable_declaration"), None)
        name = declared.named_children[0] if declared is not None and declared.named_child_count else None
    if value is None and name is not None and node.named_child_count >= 2:
        value = node.named_children[-1]
    return name, value
