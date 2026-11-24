from __future__ import annotations

import subprocess

NO_BACKGROUND_MAINTENANCE = (("maintenance.auto", "false"), ("gc.auto", "0"))


def disable_background_maintenance(repo, env=None):
    for key, value in NO_BACKGROUND_MAINTENANCE:
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True, env=env)
