import tarfile
import zipfile

CORE = ("tree_sitter", "0.26.1")
PACK = ("tree_sitter_language_pack", "1.25.2")
WHEEL_TAG = "Wheel-Version: 1.0\nGenerator: t12\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
FAKE_PACK = """from pathlib import Path

_state = {}


class PackConfig:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir


def configure(config):
    _state["cache"] = Path(config.cache_dir)


def _libs():
    return _state["cache"] / "libs"


def download(languages):
    _libs().mkdir(parents=True, exist_ok=True)
    for name in languages:
        (_libs() / f"libtree_sitter_{name}.dylib").write_bytes(b"")
        with open(_state["cache"] / "downloads.log", "a", encoding="utf-8") as log:
            log.write(name + "\\n")


def downloaded_languages():
    found = _libs().glob("libtree_sitter_*.dylib") if _libs().is_dir() else ()
    return sorted(path.stem.removeprefix("libtree_sitter_") for path in found)


class _Cursor:
    def __init__(self, node):
        self.node = node

    def goto_first_child(self):
        return False

    def goto_next_sibling(self):
        return False

    def goto_parent(self):
        return False


class _Node:
    type = "source_file"
    is_named = True
    is_error = False
    is_missing = False
    child_count = 0
    children = ()
    named_children = ()
    parent = None
    prev_named_sibling = None

    def __init__(self, size):
        self.start_byte = 0
        self.end_byte = size

    def child_by_field_name(self, name):
        return None

    def walk(self):
        return _Cursor(self)


class _Tree:
    def __init__(self, size):
        self.root_node = _Node(size)


class _Parser:
    def parse(self, source):
        return _Tree(len(source))


def get_parser(language):
    if language not in downloaded_languages():
        raise LookupError(language)
    return _Parser()
"""
UNREACHABLE_DOWNLOAD = """


def download(languages):
    return None
"""
SDIST_BACKEND = """import zipfile
from pathlib import Path

DIST = "tree_sitter-0.26.1.dist-info"
NAME = "tree_sitter-0.26.1-py3-none-any.whl"


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    entries = {
        "tree_sitter/__init__.py": "",
        DIST + "/METADATA": "Metadata-Version: 2.1\\nName: tree-sitter\\nVersion: 0.26.1\\n",
        DIST + "/WHEEL": "Wheel-Version: 1.0\\nGenerator: t12\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n",
    }
    with zipfile.ZipFile(Path(wheel_directory) / NAME, "w") as archive:
        for path, text in entries.items():
            archive.writestr(path, text)
        archive.writestr(DIST + "/RECORD", "".join(path + ",,\\n" for path in [*entries, DIST + "/RECORD"]))
    return NAME
"""
SDIST_PROJECT = '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n'


def build_wheel(wheelhouse, distribution, files):
    name, version = distribution
    dist = f"{name}-{version}.dist-info"
    entries = {**files, f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
               f"{dist}/WHEEL": WHEEL_TAG}
    with zipfile.ZipFile(wheelhouse / f"{name}-{version}-py3-none-any.whl", "w") as archive:
        for path, text in entries.items():
            archive.writestr(path, text)
        archive.writestr(f"{dist}/RECORD", "".join(f"{path},,\n" for path in [*entries, f"{dist}/RECORD"]))


def build_core_sdist(wheelhouse, scratch):
    root = scratch / "sdist" / "tree_sitter-0.26.1"
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(SDIST_PROJECT, encoding="utf-8")
    (root / "backend.py").write_text(SDIST_BACKEND, encoding="utf-8")
    (root / "PKG-INFO").write_text("Metadata-Version: 2.1\nName: tree-sitter\nVersion: 0.26.1\n", encoding="utf-8")
    with tarfile.open(wheelhouse / "tree_sitter-0.26.1.tar.gz", "w:gz") as archive:
        archive.add(root, arcname=root.name)


def make_wheelhouse(scratch, *, core="wheel", reachable=True):
    wheelhouse = scratch / "wheelhouse"
    wheelhouse.mkdir()
    pack = FAKE_PACK if reachable else FAKE_PACK + UNREACHABLE_DOWNLOAD
    build_wheel(wheelhouse, PACK, {"tree_sitter_language_pack/__init__.py": pack})
    if core == "wheel":
        build_wheel(wheelhouse, CORE, {"tree_sitter/__init__.py": ""})
    elif core == "sdist":
        build_core_sdist(wheelhouse, scratch)
    return wheelhouse
