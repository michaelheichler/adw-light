from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Callable, Iterator

try:
    from . import Hit, Rule, RuleSet, Unit, module_scopes, module_tree
    from ._captures import EXCEPTION, LOG, captures
    from ._expectations import ASSERTED, COMPUTED, Bindings, Expected, Scope, dotted, expected_texts, text_of
    from ._layer_wording import LOCATOR, is_layer_transformation, layer_expectations
    from ._prose_words import is_prose
    from ._source_strings import locate
    from ._text_function import is_transformation
    from ._ui_locators import LINK_TEXT_CONSTANTS, is_locator, selector_text, selects
    from ..test_units import PYTHON
except ImportError:
    from test_rules import Hit, Rule, RuleSet, Unit, module_scopes, module_tree
    from test_rules._captures import EXCEPTION, LOG, captures
    from test_rules._expectations import ASSERTED, COMPUTED, Bindings, Expected, Scope, dotted, expected_texts, text_of
    from test_rules._layer_wording import LOCATOR, is_layer_transformation, layer_expectations
    from test_rules._prose_words import is_prose
    from test_rules._source_strings import locate
    from test_rules._text_function import is_transformation
    from test_rules._ui_locators import LINK_TEXT_CONSTANTS, is_locator, selector_text, selects
    from test_units import PYTHON

RULE = "wording_assertion"
EXCEPTION_RULE = "wording_exception_match"
LOG_RULE = "wording_log_assertion"
LOCATION_RULE = "literal_location"
LOCATOR_RULE = "wording_ui_locator"
ENFORCE = "enforce"
KIND_RULES = {ASSERTED: RULE, EXCEPTION: EXCEPTION_RULE, LOG: LOG_RULE, LOCATOR: LOCATOR_RULE}
SELENIUM_FINDERS = frozenset({"find_element", "find_elements"})
LOCATION_SUFFIXES = (
    "py", "pyi", "js", "jsx", "mjs", "cjs", "ts", "tsx", "rs", "go", "java", "kt", "kts", "rb", "php", "c", "h",
    "cc", "cpp", "hpp", "cs", "swift", "sh", "bash", "zsh", "lua", "pl", "sql", "md", "txt", "json", "yaml",
    "yml", "toml", "ini", "cfg", "html", "htm", "css", "vue", "svelte",
)
LOCATION_RE = re.compile(
    r"(?<![\w.\\/~-])(?:[A-Za-z]:|~)?[\\/]?(?:[\w.-]+[\\/])*[\w-][\w.-]*\.(?:"
    + "|".join(LOCATION_SUFFIXES) + r"):\d+(?![\w.])",
    re.IGNORECASE,
)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
TestNode = ast.FunctionDef | ast.AsyncFunctionDef
Transforms = Callable[[str], bool]


def _store_counts(nodes: list[ast.AST]) -> Counter[str]:
    return Counter(
        node.id for root in nodes for node in ast.walk(root)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    )


def _assigned(node: ast.AST) -> ast.expr | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        return node.targets[0]
    return node.target if isinstance(node, ast.AnnAssign) else None


def _string_assignments(nodes: list[ast.AST]) -> Iterator[tuple[str, str]]:
    for root in nodes:
        for node in ast.walk(root):
            target = _assigned(node)
            value = getattr(node, "value", None)
            if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
                yield target.id, value.value


def _single_bindings(nodes: list[ast.AST]) -> Bindings:
    stores = _store_counts(nodes)
    return {name: value for name, value in _string_assignments(nodes) if stores[name] == 1}


def _scope_bindings(test: TestNode, tree: ast.Module) -> Bindings:
    top = [node for node in tree.body if not isinstance(node, (*_FUNCTIONS, ast.ClassDef))]
    local_stores = _store_counts([test])
    params = {arg.arg for arg in ast.walk(test.args) if isinstance(arg, ast.arg)}
    module = {name: value for name, value in _single_bindings(top).items() if name not in local_stores}
    merged = {**module, **_single_bindings([test])}
    return {name: value for name, value in merged.items() if name not in params}


def _test_node(unit: Unit, tree: ast.Module) -> TestNode | None:
    node = module_scopes(tree).get((unit.start, unit.end))
    return node if node is not None and node.name == unit.name else None


def _location(text: str, path: str) -> str | None:
    source = locate(text, path)
    return None if source is None else f"{source.path}:{source.line}"


def _typed_location(typed: str) -> bool:
    for match in LOCATION_RE.finditer(typed.replace(COMPUTED, "0")):
        path_end = typed.rindex(":", match.start(), match.end())
        if COMPUTED not in typed[match.start():path_end]:
            return True
    return False


def _sentence(found: Expected) -> str:
    return " ".join(LOCATION_RE.sub(" ", found.text).split())


def _verdicts(found: Expected, transforms: Transforms, path: str) -> Iterator[tuple[str, str | None]]:
    if _typed_location(found.typed):
        yield LOCATION_RULE, None
    sentence = _sentence(found)
    if not is_prose(sentence):
        return
    location = _location(sentence, path)
    if location is None and found.kind == ASSERTED and transforms(sentence):
        return
    yield KIND_RULES[found.kind], location


def _flagged(found: Expected, transforms: Transforms, path: str) -> bool:
    if _typed_location(found.typed):
        return True
    sentence = _sentence(found)
    if not is_prose(sentence):
        return False
    return found.kind != ASSERTED or not transforms(sentence) or locate(sentence, path) is not None


def _written(lines: list[str], line: int, fallback: str) -> str:
    return lines[line - 1].strip() if line <= len(lines) else fallback


def _call_parts(func: ast.expr) -> list[str]:
    name = dotted(func)
    if name:
        return name.split(".")
    return [func.attr] if isinstance(func, ast.Attribute) else []


def _locator_texts(call: ast.Call, bindings: Bindings) -> list[str | None]:
    parts = _call_parts(call.func)
    values = [*call.args, *(keyword.value for keyword in call.keywords)]
    if is_locator(parts):
        return [text_of(value, bindings) for value in values]
    if parts[-1:] and parts[-1] in SELENIUM_FINDERS and len(call.args) > 1:
        if dotted(call.args[0]).rsplit(".", 1)[-1] in LINK_TEXT_CONSTANTS:
            return [text_of(call.args[1], bindings)]
    if selects(parts):
        return [selector_text(text) for text in (text_of(value, bindings) for value in values) if text]
    return []


def _python_locators(body: list[ast.stmt], bindings: Bindings) -> Iterator[Expected]:
    for root in body:
        for node in ast.walk(root):
            if isinstance(node, ast.Call):
                for text in _locator_texts(node, bindings):
                    if text:
                        yield Expected(node.lineno, text, text, LOCATOR)


def _python_scope(unit: Unit, text: str) -> tuple[list[Expected], Transforms] | None:
    tree = module_tree(text)
    test = _test_node(unit, tree) if tree is not None else None
    if test is None:
        return None
    scope = Scope(_scope_bindings(test, tree), captures(test.body))
    found = sorted({*expected_texts(test.body, scope), *_python_locators(test.body, scope.bindings)})
    return found, lambda sentence: is_transformation(test.body, sentence, lambda node: text_of(node, scope.bindings))


def _scoped(unit: Unit, text: str) -> tuple[list[Expected], Transforms] | None:
    if unit.language == PYTHON:
        return _python_scope(unit, text)
    layered = layer_expectations(unit, text)
    return layered.found, lambda sentence: is_layer_transformation(layered.inputs, sentence)


def wording_lines(unit: Unit, text: str) -> list[tuple[int, str]]:
    scoped = _scoped(unit, text)
    if scoped is None:
        return []
    found, transforms = scoped
    return [(item.line, item.text) for item in found if _flagged(item, transforms, unit.path)]


def check(unit: Unit, text: str) -> list[Hit]:
    scoped = _scoped(unit, text)
    if scoped is None:
        return []
    expectations, transforms = scoped
    lines = text.splitlines()
    hits: dict[tuple[int, str], Hit] = {}
    for found in expectations:
        for rule, location in _verdicts(found, transforms, unit.path):
            hits.setdefault((found.line, rule), Hit(rule, found.line, _written(lines, found.line, found.text), location))
    return [hits[key] for key in sorted(hits)]


def _rule(name: str, detail: str, action: str) -> Rule:
    return Rule(name, detail, action, ENFORCE)


RULE_SET = RuleSet(
    rules=(
        _rule(RULE, "Test expects a prose sentence, so a wording edit turns it red while the code still works",
              "Assert the behavior behind the text, such as a decision field, a rule id, or an exit code. "
              "Delete the test when it checks only wording."),
        _rule(EXCEPTION_RULE,
              "Test matches the message of an exception or warning, so a wording edit turns it red while the code still works",
              "Pass check= to pytest.raises with a callable that tests the exception fields, or raise a more "
              "specific exception type. With unittest, use assertRaises and test the fields of the caught exception."),
        _rule(LOG_RULE, "Test expects a sentence from captured logs, so a wording edit turns it red while the code still works",
              "Assert the log level, the logger name, and the structured fields passed through extra, and leave "
              "the message text alone."),
        _rule(LOCATION_RULE, "Test expects a file and line value typed out as a literal, so it breaks when the fixture moves",
              "Build the file and line value from the fixture variable, for example with an f-string over the "
              "fixture path and the line the fixture defines."),
        _rule(LOCATOR_RULE, "Test finds a UI element by its visible prose, so a copy edit turns it red while the page still works",
              "Find the element by a test id or by its role, for example getByTestId or getByRole, and leave the "
              "visible text out of the locator."),
    ),
    check=check,
)