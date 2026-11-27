from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path

ADW_TREE_RE = re.compile(r"adw-light(?:-[\w.-]+)?", re.IGNORECASE)
ADW_PARENT_LAYOUTS = (("adw-light-plugin",), ("cache", "adw-light", "adw-light"), (".adw-lite", "install"))


def adw_tree(tree):
    return bool(ADW_TREE_RE.fullmatch(tree.name)) or any(
        tree.parent.parts[-len(layout):] == layout for layout in ADW_PARENT_LAYOUTS
    )


def owned(handler, root, legacy_only, preserve_legacy):
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return False
    try:
        words = shlex.split(handler.get("command", ""))
    except (TypeError, ValueError):
        return False
    if words[:1] == ["ADW_CODEX_HOOK=1"]:
        words = words[1:]
    if not words:
        return False
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(words[0]).name):
        args = iter(words[1:])
        script = next((word for word in args if not re.fullmatch(r"-[ISBE]+", word)), "")
        return not preserve_legacy and Path(script).name == "adw_light.py"
    path = Path(words[0])
    return not legacy_only and path.name in {"run.sh", "adw-config"} and path.parent.name == "hooks" and (
        (root is not None and path.parent.parent == root) or adw_tree(path.parent.parent)
    )


def strip(groups, root, legacy_only, preserve_legacy):
    kept = []
    for group in groups:
        handlers = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(handlers, list) or not handlers:
            kept.append(group)
            continue
        rest = [row for row in handlers if not owned(row, root, legacy_only, preserve_legacy)]
        if rest:
            kept.append({**group, "hooks": rest})
    return kept


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
        json.dump(data, output, indent=2)
        output.write("\n")
    if path.exists():
        shutil.copymode(path, output.name)
    os.replace(output.name, path)


def main(codex):
    parser = argparse.ArgumentParser()
    parser.add_argument("--hooks-json" if codex else "--settings", dest="target", required=True, type=Path)
    parser.add_argument("--skill-dir", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--remove-legacy", action="store_true")
    modes.add_argument("--strip-only", action="store_true")
    args = parser.parse_args()
    if args.skill_dir is None and not args.strip_only:
        parser.error("--skill-dir is required unless --strip-only is given")
    target = args.target.expanduser().resolve()
    root = args.skill_dir.expanduser().resolve() if args.skill_dir is not None else None
    original = json.loads(target.read_text()) if target.exists() else {}
    data = dict(original)
    hooks = dict(data.get("hooks", {}))
    for event, groups in list(hooks.items()):
        if isinstance(groups, list) and groups:
            remaining = strip(groups, root, args.remove_legacy, codex and not (args.remove_legacy or args.strip_only))
            if remaining:
                hooks[event] = remaining
            else:
                del hooks[event]
    if not args.remove_legacy and not args.strip_only:
        install(hooks, root, codex)
    if hooks or "hooks" in original:
        data["hooks"] = hooks
    if data != original:
        save(target, data)


def install(hooks, root, codex):
    source = root / "hooks" / ("codex-hooks.json" if codex else "hooks.json")
    for event, groups in json.loads(source.read_text())["hooks"].items():
        for group in groups:
            for handler in group["hooks"]:
                prefix = "ADW_CODEX_HOOK=1 " if codex else ""
                script = "adw-config" if "/hooks/adw-config" in handler["command"] else "run.sh"
                handler["command"] = prefix + shlex.quote(str(root / "hooks" / script)) + " " + event
        hooks.setdefault(event, []).extend(groups)
