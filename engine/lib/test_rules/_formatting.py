from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator
from string import Formatter

Fill = Callable[[ast.expr | None], str]
Template = Callable[[ast.expr], "str | None"]
FORMAT_METHOD = "format"
PERCENT_ESCAPE = "%%"
PERCENT_RE = re.compile(r"%(?:\((?P<key>[^)]*)\))?[#0 +-]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?[diouxXeEfFgGcrsa%]")
_FIELD_SPLIT_RE = re.compile(r"[.\[]")


def _format_field(field: str, args: list[ast.expr], keywords: dict[str, ast.expr], auto: Iterator[int]) -> ast.expr | None:
    if _FIELD_SPLIT_RE.search(field):
        return None
    if not field:
        index = next(auto)
        return args[index] if index < len(args) else None
    if field.isdigit():
        return args[int(field)] if int(field) < len(args) else None
    return keywords.get(field)


def _render_format(template: str, call: ast.Call, fill: Fill) -> str | None:
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(keyword.arg is None for keyword in call.keywords):
        return None
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
    auto = iter(range(len(call.args) + 1))
    pieces: list[str] = []
    try:
        for literal, field, _spec, _conversion in Formatter().parse(template):
            pieces.append(literal)
            if field is not None:
                pieces.append(fill(_format_field(field, list(call.args), keywords, auto)))
    except (ValueError, StopIteration):
        return None
    return "".join(pieces)


def _percent_values(right: ast.expr) -> tuple[list[ast.expr], dict[str, ast.expr] | None]:
    if isinstance(right, ast.Tuple):
        return list(right.elts), None
    if isinstance(right, ast.Dict):
        keys = [key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None for key in right.keys]
        return [], {key: value for key, value in zip(keys, right.values) if key is not None}
    return [right], None


def _render_percent(template: str, right: ast.expr, fill: Fill) -> str:
    positional, mapping = _percent_values(right)
    values = iter(positional)
    pieces: list[str] = []
    end = 0
    for match in PERCENT_RE.finditer(template):
        pieces.append(template[end:match.start()])
        end = match.end()
        if match.group() == PERCENT_ESCAPE:
            pieces.append("%")
        elif match.group("key") is not None:
            pieces.append(fill(None if mapping is None else mapping.get(match.group("key"))))
        else:
            pieces.append(fill(next(values, None)))
    pieces.append(template[end:])
    return "".join(pieces)


def formatted(node: ast.expr | None, template_of: Template, fill: Fill) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == FORMAT_METHOD:
        template = template_of(node.func.value)
        return None if template is None else _render_format(template, node, fill)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template = template_of(node.left)
        return None if template is None else _render_percent(template, node.right, fill)
    return None
