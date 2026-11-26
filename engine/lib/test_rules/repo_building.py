from __future__ import annotations

import ast
import re
from pathlib import PurePath

from . import Hit, Rule, RuleSet, Unit, module_tree
from ._expectations import dotted

try:
    from ..brace_functions import block_ends, masked_code
    from ..shell_syntax import _command_word_index
    from ..shell_text import shell_tokens, shell_words
except ImportError:
    from lib.brace_functions import block_ends, masked_code
    from lib.shell_syntax import _command_word_index
    from lib.shell_text import shell_tokens, shell_words

def named_test_path(path):
    try:
        from ..language_units import named_test_path as matches
    except ImportError:
        from language_units import named_test_path as matches
    return matches(path)


REPO = "test_builds_repo"
TREE = "test_builds_tree"
RUNNERS = {"subprocess." + name for name in ("run", "call", "check_call", "check_output", "Popen")}
VERBS = {"init", "clone", "commit", "add", "checkout", "switch"}
JS_CALL = re.compile(r"\b(execSync|exec|spawnSync|spawn|execFileSync)\s*\(")
STRING = re.compile(r'''\s*(['"])([^'"\\]*)\1''')
SETUP = re.compile(r"\b(?:beforeEach|setup)\s*\([^{};]*\)\s*(?:=>\s*)?\{|\bfunction\s+setup\s*\([^)]*\)\s*\{")
LANG = {**dict.fromkeys((".sh", ".bash", ".ksh", ".zsh", ".bats"), "shell"), ".py": "python",
        **dict.fromkeys((".js", ".jsx", ".ts", ".tsx", ".cjs", ".mjs", ".cts", ".mts"), "js")}


def _builds(words):
    if not words or PurePath(words[0]).name != "git":
        return False
    i = 1
    while i < len(words) and words[i].startswith("-"):
        if words[i] in {"--help", "--version", "-h"}:
            return False
        i += 2 if words[i] in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"} else 1
    return i < len(words) and (words[i] in VERBS or words[i:i + 2] == ["worktree", "add"])


def _shell_segment(text):
    raw = list(shell_words(text))
    words = [word.strip("\"'") for word in raw[_command_word_index(raw):]]
    return _builds(words)


def _shell_offsets(text):
    start = 0
    for token in shell_tokens(text):
        match = token.match
        if match and match.lastgroup == "separator":
            if _shell_segment(text[start:match.start()]):
                yield start + len(text[start:match.start()]) - len(text[start:match.start()].lstrip())
            start = token.end
    if _shell_segment(text[start:]):
        yield start + len(text[start:]) - len(text[start:].lstrip())


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _runs_git(call, names):
    root, dot, rest = dotted(call.func).partition(".")
    name = names.get(root, root) + dot + rest
    if name not in RUNNERS and name != "os.system":
        return False
    arg = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg in {"args", "command"}), None)
    if isinstance(arg, (ast.Tuple, ast.List)):
        return _builds([_literal(word) for word in arg.elts])
    shell = any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords)
    return (name == "os.system" or shell) and next(_shell_offsets(_literal(arg)), None) is not None


def _uses_root(node, roots):
    return any(isinstance(n, (ast.Name, ast.Attribute)) and dotted(n) in roots for n in ast.walk(node))


def _tree_write(setup):
    roots = set()
    for node in sorted(ast.walk(setup), key=lambda n: getattr(n, "lineno", 0)):
        if isinstance(node, (ast.Assign, ast.withitem)):
            value = node.value if isinstance(node, ast.Assign) else node.context_expr
            targets = node.targets if isinstance(node, ast.Assign) else [node.optional_vars]
            temp = any(isinstance(n, ast.Call) and dotted(n.func).split(".")[-1] in {"TemporaryDirectory", "mkdtemp"} for n in ast.walk(value))
            if temp or _uses_root(value, roots):
                roots.update(dotted(t) for t in targets if isinstance(t, (ast.Name, ast.Attribute)))
        if isinstance(node, ast.Call) and _writes_into(node, roots):
            return node.lineno
    return None


def _writes_into(call, roots):
    if not isinstance(call.func, ast.Attribute) and dotted(call.func) != "open":
        return False
    name = dotted(call.func).split(".")[-1] if isinstance(call.func, ast.Name) else call.func.attr
    if name in {"write_bytes", "write_text", "touch"}:
        return _uses_root(call.func, roots)
    if name != "open":
        return False
    mode = next((_literal(k.value) for k in call.keywords if k.arg == "mode"), "")
    method = isinstance(call.func, ast.Attribute) and _uses_root(call.func, roots)
    slot = 0 if method else 1
    mode = mode or (_literal(call.args[slot]) if len(call.args) > slot else "")
    return any(c in mode for c in "wax+") and (method or bool(call.args and _uses_root(call.args[0], roots)))


def _python(text):
    tree = module_tree(text)
    if tree is None:
        return
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            prefix = node.module + "." if isinstance(node, ast.ImportFrom) and node.module else ""
            names.update({a.asname or a.name: prefix + a.name for a in node.names})
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _runs_git(node, names):
            yield REPO, node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "setUp":
            if line := _tree_write(node):
                yield TREE, line


def _js_git(text, call):
    first = STRING.match(text, call.end())
    if not first:
        return False
    if call[1] in {"exec", "execSync"}:
        return next(_shell_offsets(first[2]), None) is not None
    array = re.match(r"\s*,\s*\[", text[first.end():])
    if not array:
        return False
    words = [first[2]]
    pos = first.end() + array.end()
    while item := STRING.match(text, pos):
        words.append(item[2])
        comma = re.match(r"\s*,", text[item.end():])
        if not comma:
            break
        pos = item.end() + comma.end()
    return _builds(words)


def _javascript(text):
    code = masked_code(text, javascript=True)
    for call in JS_CALL.finditer(code):
        if _js_git(text, call):
            yield REPO, 1 + code.count("\n", 0, call.start())
    ends = block_ends(text, javascript=True)
    for setup in SETUP.finditer(code):
        body = code[setup.end():ends.get(setup.end() - 1, setup.end())]
        for root in re.finditer(r"\b(\w+)\s*=\s*(?:await\s+)?(?:[\w.]+\.)?mkdtemp(?:Sync)?\s*\(", body):
            write = re.search(r"\bwriteFile(?:Sync)?\s*\([^,;]*\b" + re.escape(root[1]) + r"\b", body[root.end():])
            if write:
                yield TREE, 1 + code.count("\n", 0, setup.end() + root.end() + write.start())


def check(unit, text):
    if unit.language == "shell":
        rows = ((REPO, 1 + text.count("\n", 0, offset)) for offset in _shell_offsets(text))
    else:
        rows = _python(text) if unit.language == "python" else _javascript(text)
    lines = text.splitlines()
    return [Hit(rule, line, lines[line - 1]) for rule, line in sorted(set(rows))]


def file_rows(path, text):
    from . import _set_findings

    suffix = PurePath(path).suffix.lower()
    if suffix not in LANG or not (suffix == ".bats" or named_test_path(path)):
        return []
    unit = Unit(path, "test file", 1, text.count("\n") + 1, text, LANG[suffix])
    return _set_findings(RULE_SET, unit, text)


RULE_SET = RuleSet((Rule(REPO, "Test creates or changes a Git repository", "Pass repository input in memory.", "enforce"),
                    Rule(TREE, "Setup writes files into a temporary directory", "Keep setup data in memory.")), check)
