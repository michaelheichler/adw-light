from __future__ import annotations

import ast
import re

Kind = str | tuple[str, tuple["Kind", ...]]

PATH_METHODS: dict[str, Kind] = {
    "absolute": "path", "as_posix": "text", "as_uri": "text", "exists": "value", "expanduser": "path",
    "glob": ("sequence", ("path",)), "is_block_device": "value", "is_char_device": "value", "is_dir": "value",
    "is_fifo": "value", "is_file": "value", "is_mount": "value", "is_relative_to": "value", "is_socket": "value",
    "is_symlink": "value", "iterdir": ("sequence", ("path",)), "lstat": "value", "match": "value",
    "read_bytes": "bytes", "read_text": "text", "relative_to": "path", "resolve": "path",
    "rglob": ("sequence", ("path",)), "samefile": "value", "stat": "value", "with_name": "path",
    "with_suffix": "path",
}
PATH_CLASS_METHODS: dict[str, Kind] = {"cwd": "path", "home": "path"}
FILE_METHODS: dict[str, Kind] = {
    "isatty": "value", "read": "text", "readable": "value", "readline": "text",
    "readlines": ("sequence", ("text",)), "seekable": "value", "tell": "value",
}
JSON_METHODS: dict[str, Kind] = {
    "get": "json",
    "keys": ("sequence", ("json",)),
    "items": ("sequence", (("tuple", ("json", "json")),)),
    "values": ("sequence", ("json",)),
}
TEXT_METHODS = frozenset({
    "casefold", "capitalize", "count", "encode", "endswith", "find", "format", "index", "isalpha", "isdigit",
    "islower", "isspace", "istitle", "isupper", "join", "lower", "lstrip", "partition", "removeprefix",
    "removesuffix", "replace", "rfind", "rindex", "rjust", "rpartition", "rsplit", "rstrip", "split",
    "splitlines", "startswith", "strip", "swapcase", "title", "upper", "zfill",
})
BYTES_METHODS = frozenset({
    "decode", "endswith", "find", "index", "lower", "lstrip", "partition", "removeprefix", "removesuffix",
    "replace", "rfind", "rindex", "rpartition", "rsplit", "rstrip", "split", "splitlines", "startswith",
    "strip", "upper",
})
JSON_ELEMENT_METHODS: dict[str, Kind] = {**{name: "value" for name in TEXT_METHODS | {"count", "index"}}, **JSON_METHODS}

TEXTS: Kind = ("sequence", ("text",))
VALUES: Kind = ("sequence", ("value",))
PAIRS: Kind = ("sequence", (("tuple", ("value", "value")),))
DICT_METHODS: dict[str, Kind] = {"get": "value", "items": PAIRS, "keys": VALUES, "values": VALUES}
MATCHES: Kind = ("sequence", ("re_match",))
RE_FUNCTIONS: dict[str, Kind] = {
    "findall": TEXTS, "finditer": MATCHES, "fullmatch": "re_match", "match": "re_match",
    "search": "re_match", "split": TEXTS, "sub": "text", "subn": ("tuple", ("text", "value")),
}
RE_FLAGS = ("A", "ASCII", "I", "IGNORECASE", "M", "MULTILINE", "S", "DOTALL", "X", "VERBOSE")
MATH_FUNCTIONS = (
    "ceil", "comb", "exp", "fabs", "factorial", "floor", "fsum", "gcd", "isclose", "isinf", "isnan",
    "log", "log10", "log2", "perm", "pow", "prod", "sqrt", "trunc",
)
HASH_FUNCTIONS = ("blake2b", "blake2s", "file_digest", "md5", "sha1", "sha224", "sha256", "sha384", "sha512")
DATETIME_FACTORIES = ("fromisoformat", "fromtimestamp", "now", "strptime", "today", "utcnow")

IMPORTABLE = frozenset({
    "ast", "collections", "datetime", "hashlib", "html", "itertools", "math", "os.path", "pathlib", "re", "tempfile",
    "tomllib", "yaml",
})
INERT_MODULES = frozenset({
    "base64", "binascii", "bisect", "copy", "decimal", "difflib", "enum", "fnmatch", "fractions", "functools", "heapq",
    "keyword", "numbers", "operator", "pprint", "shlex", "statistics", "string", "struct", "textwrap", "time",
    "traceback", "typing", "unicodedata", "zlib",
})
NAMESPACES = IMPORTABLE | {"datetime.date", "datetime.datetime", "datetime.timezone", "pathlib.Path", "pathlib.PurePath"}
FUNCTIONS: dict[str, Kind] = {
    "pathlib.Path": "path", "pathlib.PurePath": "path",
    **{f"pathlib.{name}.{method}": "path" for name in ("Path", "PurePath") for method in PATH_CLASS_METHODS},
    **{f"re.{name}": kind for name, kind in RE_FUNCTIONS.items()},
    "re.compile": "re_pattern", "re.escape": "text",
    "html.escape": "text", "html.unescape": "text",
    **{f"math.{name}": "value" for name in MATH_FUNCTIONS},
    "collections.Counter": "counter", "collections.OrderedDict": "dict",
    **{f"hashlib.{name}": "hash" for name in HASH_FUNCTIONS},
    "tempfile.gettempdir": "text",
    "tomllib.load": "json", "tomllib.loads": "json",
    "yaml.safe_load": "json", "yaml.safe_load_all": ("sequence", ("json",)),
    "ast.parse": "ast_tree", "ast.dump": "text", "ast.literal_eval": "json",
    **{f"os.path.{name}": "value" for name in ("exists", "getsize", "isdir", "isfile")},
    **{f"os.path.{name}": "text" for name in ("abspath", "basename", "dirname", "expanduser", "join")},
    "os.path.splitext": ("tuple", ("text", "text")),
    "traceback.format_exc": "text", "traceback.print_exc": "value",
    **{f"textwrap.{name}": "text" for name in ("dedent", "fill", "indent", "shorten")}, "textwrap.wrap": TEXTS,
    "datetime.datetime": "datetime", "datetime.date": "datetime", "datetime.timedelta": "timedelta",
    **{f"datetime.datetime.{name}": "datetime" for name in DATETIME_FACTORIES},
    **{f"datetime.date.{name}": "datetime" for name in ("fromisoformat", "fromtimestamp", "today")},
}
ITEM_FUNCTIONS = frozenset({"itertools.chain", "itertools.islice"})
CONSTANTS: dict[str, Kind] = {
    **{f"re.{name}": "value" for name in RE_FLAGS},
    **{f"math.{name}": "value" for name in ("e", "inf", "nan", "pi", "tau")},
    "datetime.timezone.utc": "value",
    "yaml.__version__": "text",
}
METHODS: dict[str, dict[str, Kind]] = {
    "re_pattern": RE_FUNCTIONS,
    "re_match": {
        "end": "value", "group": "text", "groupdict": "json", "groups": TEXTS,
        "span": ("tuple", ("value", "value")), "start": "value",
    },
    "dict": DICT_METHODS,
    "counter": {**DICT_METHODS, "elements": VALUES, "most_common": PAIRS, "total": "value"},
    "hash": {"digest": "bytes", "hexdigest": "text"},
    "datetime": {
        "astimezone": "datetime", "date": "datetime", "isoformat": "text", "replace": "datetime",
        "strftime": "text", "timestamp": "value", "weekday": "value",
    },
    "timedelta": {"total_seconds": "value"},
}
ATTRIBUTES: dict[str, dict[str, Kind]] = {
    "datetime": {name: "value" for name in ("day", "hour", "minute", "month", "second", "year")},
    "timedelta": {name: "value" for name in ("days", "seconds")},
}
OBJECT_KINDS = frozenset({"ast_tree", "counter", "datetime", "dict", "hash", "re_match", "re_pattern", "timedelta"})
ITEMS: dict[str, Kind] = {"counter": "value", "dict": "value"}


def text_method_result(base: str, method: str) -> Kind:
    if method in {"split", "splitlines", "rsplit"}:
        return ("sequence", (base,))
    if method in {"partition", "rpartition"}:
        return ("tuple", (base, base, base))
    if method in {"decode", "encode"}:
        return "text" if method == "decode" else "bytes"
    if method.startswith("is") or method in {"count", "find", "index", "rfind", "rindex", "startswith", "endswith"}:
        return "value"
    return base


def import_binding(dotted: str) -> tuple[str, Kind] | None:
    if dotted not in IMPORTABLE and dotted not in INERT_MODULES:
        return None
    root = dotted.partition(".")[0]
    return root, f"ns:{root}"


def from_import_kind(module: str, name: str) -> Kind | None:
    if module not in IMPORTABLE | INERT_MODULES and f"{module}.{name}" not in NAMESPACES:
        return None
    return namespace_member(module, name)


def namespace_member(namespace: str, name: str) -> Kind | None:
    qualified = f"{namespace}.{name}"
    if qualified in NAMESPACES:
        return f"ns:{qualified}"
    if qualified in FUNCTIONS or qualified in ITEM_FUNCTIONS:
        return f"fn:{qualified}"
    return CONSTANTS.get(qualified)


def member_kind(base: Kind | None, name: str) -> Kind | None:
    if isinstance(base, str) and base.startswith("ns:"):
        return namespace_member(base[3:], name)
    if not isinstance(base, str) or base not in OBJECT_KINDS:
        return None
    if name in METHODS.get(base, {}):
        return f"method:{base}.{name}"
    return ATTRIBUTES.get(base, {}).get(name)


def callable_result(callee: Kind | None, item_kinds: list[Kind | None]) -> Kind | None:
    if not isinstance(callee, str):
        return None
    family, _, qualified = callee.partition(":")
    if family == "method":
        base, _, name = qualified.partition(".")
        return METHODS.get(base, {}).get(name)
    if family not in {"ns", "fn"}:
        return None
    if qualified == "itertools.islice":
        return ("sequence", (item_kinds[0] or "value",)) if item_kinds else None
    if qualified == "itertools.chain":
        return ("sequence", (common_item(item_kinds),))
    return FUNCTIONS.get(qualified)


def common_item(kinds: list[Kind | None]) -> Kind:
    return kinds[0] if kinds and kinds[0] is not None and all(kind == kinds[0] for kind in kinds) else "value"


READ_ONLY_OPEN_MODE_CHARS = frozenset("rbtU")
OPEN_KEYWORDS = frozenset({"buffering", "encoding", "errors", "newline", "closefd"})
SAFE_IMPORTS = {"json": "json_module", "sys": "sys_module"}
SYS_ATTRIBUTES: dict[str, Kind] = {
    "stdin": "file", "argv": ("sequence", ("text",)), "executable": "text", "maxsize": "value",
    "platform": "text", "version": "text", "version_info": "value", "exit": "sys_exit",
}
JSON_FUNCTIONS = frozenset({"dumps", "load", "loads"})
DUMPS_KEYWORDS = frozenset({"allow_nan", "ensure_ascii", "indent", "separators", "skipkeys", "sort_keys"})
PATH_CLASSES = frozenset({"Path", "PurePath"})
SORT_HELPERS = frozenset({"max", "min", "sorted"})
CONTAINER_LITERALS = (ast.Dict, ast.DictComp, ast.List, ast.ListComp)
UNASSIGNED_HINT = ", which is never assigned in this payload. Define it or inline the value"
SAFE_BUILTINS = frozenset({
    "abs", "all", "any", "bool", "bytes", "dict", "enumerate", "float", "int", "isinstance", "iter", "len", "list", "max",
    "min", "next", "print", "range", "repr", "round", "set", "sorted", "str", "sum", "tuple", "type", "zip",
})
SAFE_EXCEPTIONS = frozenset({
    "ArithmeticError", "AssertionError", "AttributeError", "Exception", "FileNotFoundError", "IndexError", "KeyError",
    "LookupError", "OSError", "StopIteration", "SystemExit", "TypeError", "UnicodeDecodeError", "ValueError", "ZeroDivisionError",
})
JSON_ERROR = "JSONDecodeError"
FIXED_BUILTIN_KINDS: dict[str, Kind] = {"str": "text", "repr": "text", "bytes": "bytes", "range": ("sequence", ("value",))}
SAFE_LITERAL_TYPES = (str, bytes, int, float, complex, bool, type(None))
ASSIGNABLE_KINDS = frozenset({"bytes", "file", "json", "path", "text", "value"}) | OBJECT_KINDS

SUSPICIOUS_BARE_LITERAL_RE = re.compile(r"(?:\b(?:write|exec|eval)\s*\(|__)")

Comprehension = ast.GeneratorExp | ast.ListComp | ast.SetComp | ast.DictComp


def common_kind(kinds: list[Kind] | tuple[Kind, ...]) -> Kind:
    return kinds[0] if kinds and all(kind == kinds[0] for kind in kinds) else "value"


def item_kind(kind: Kind | None) -> Kind | None:
    if isinstance(kind, tuple):
        return kind[1][0] if kind[0] == "sequence" else common_kind(kind[1])
    return {"text": "text", "bytes": "value", "file": "text", "json": "json", **ITEMS}.get(kind)


def assignable_kind(kind: Kind | None) -> bool:
    if isinstance(kind, tuple):
        return all(assignable_kind(item) for item in kind[1])
    return kind in ASSIGNABLE_KINDS


def describe_node(node: ast.AST) -> str:
    try:
        text = " ".join(ast.unparse(node).split())
    except (RecursionError, ValueError):
        text = ""
    return f"{type(node).__name__} \"{text[:80]}\" on line {getattr(node, 'lineno', '?')}"


def stdout_arguments(node: ast.Call, bindings: dict[str, Kind]) -> list[ast.expr] | None:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr == "write" and len(node.args) == 1 and not node.keywords:
        stream, values = func.value, node.args
    elif (func.attr == "dump" and isinstance(func.value, ast.Name) and bindings.get(func.value.id) == "json_module"
          and len(node.args) == 2 and all(keyword.arg in DUMPS_KEYWORDS for keyword in node.keywords)):
        stream, values = node.args[1], node.args[:1]
    else:
        return None
    if isinstance(stream, ast.Attribute) and stream.attr == "stdout" and isinstance(stream.value, ast.Name):
        if bindings.get(stream.value.id) == "sys_module":
            return values
    return None
