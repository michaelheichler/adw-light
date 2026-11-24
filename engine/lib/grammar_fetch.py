from __future__ import annotations

import sys
from importlib import import_module


def main(cache: str, language: str, site: str) -> int:
    sys.path.append(site)
    pack = import_module("tree_sitter_language_pack")
    pack.configure(pack.PackConfig(cache_dir=cache))
    pack.download([language])
    return 0 if language in pack.downloaded_languages() else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
