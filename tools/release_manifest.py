import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
version = (root / "VERSION").read_text().strip()
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?", version):
    sys.exit("Expected a numeric release version")
manifest = root / ".claude-plugin/plugin.json"
plugin = json.loads(manifest.read_text())
plugin["version"] = version
manifest.write_text(json.dumps(plugin, indent=2) + "\n")
codex = root / ".codex-plugin/plugin.json"
codex.parent.mkdir()
(codex.parent / "empty-hooks.json").write_text('{"hooks": {}}\n')
codex.write_text(json.dumps({**plugin, "hooks": "./.codex-plugin/empty-hooks.json"}, indent=2) + "\n")
release = {"version": version, "source": sys.argv[2]}
(root / "engine/RELEASE.json").write_text(json.dumps(release, indent=2) + "\n")
