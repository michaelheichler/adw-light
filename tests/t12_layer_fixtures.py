import json
import shutil
from pathlib import Path

SEED_RECORD = '{"status": "ready", "pins": {"tree-sitter": "0.0.0"}, "version": "3.12", "ts": 1.0, "grammars": ["go"]}\n'
SEED_VENV = {"bin/python": "#!/bin/sh\nexit 1\n", "pyvenv.cfg": "home = /seeded\n"}


def layer_folder(home):
    return Path(home) / ".adw-lite" / "install"


def seed_layer(home):
    install = layer_folder(home)
    for name, text in SEED_VENV.items():
        target = install / "venv" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (install / "setup.json").write_text(SEED_RECORD, encoding="utf-8")


def seed_working_layer(scratch, wheelhouse, tree):
    result = scratch.run(["python3", tree / "engine" / "setup_env.py", "--language", "go"], timeout=600,
                         PIP_FIND_LINKS=str(wheelhouse))
    assert result.returncode == 0, result.stdout + result.stderr
    install = layer_folder(scratch.home)
    record = json.loads((install / "setup.json").read_text(encoding="utf-8"))
    (install / "setup.json").write_text(json.dumps({**record, "pins": {"tree-sitter": "0.0.0"}}), encoding="utf-8")
    (install / "venv" / "seed-marker.txt").write_text("seeded environment\n", encoding="utf-8")


def tree_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def layer_state(home):
    install = layer_folder(home)
    record = install / "setup.json"
    return tree_bytes(install / "venv"), record.read_bytes() if record.is_file() else None


def layer_backups(home):
    return sorted(path.name for path in layer_folder(home).glob(".layer.*"))


def stage_like_remote(stage, tools):
    (stage / "tools").mkdir(parents=True)
    for name in ("install_layers.sh", "smoke_probe.py"):
        shutil.copy2(tools / name, stage / "tools" / name)
    return stage / "tools" / "install_layers.sh"
