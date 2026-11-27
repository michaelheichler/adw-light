from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from lib import config, configure_policy, git_port
from lib.hookio import stop_block
from lib.state_recovery import atomic_write

NAME = "adw-light:config"
CODEX_PREFIX = "$adw-config"
USAGE = "Use rule NAME off|observe|enforce, tests allow --for 30m, or contract add|remove STRING."
WINDOW = re.compile(r"tests allow --for ([1-9][0-9]{0,2})([mh])")
RULE = re.compile(r"rule ([a-z][a-z0-9_]+) (off|observe|enforce)")


def user_arguments(event, payload):
    if payload.get("hook_event_name") != event or payload.get("agent_id") or payload.get("agent_type"):
        return None
    if event == "UserPromptExpansion":
        if (payload.get("expansion_type"), payload.get("command_source"), payload.get("command_name")) != ("slash_command", "plugin", NAME):
            return None
        return payload.get("command_args", "")
    prompt = payload.get("prompt")
    if event == "UserPromptSubmit" and isinstance(prompt, str):
        if prompt == CODEX_PREFIX or prompt.startswith(CODEX_PREFIX + " "):
            return prompt[len(CODEX_PREFIX):].lstrip(" ")
    return None


def _contract_change(arguments: str, settings: dict) -> dict[str, list[str]]:
    operation, _, value = arguments.partition(" ")
    if operation not in {"add", "remove"}:
        raise ValueError(USAGE)
    key = config.CONTRACT_STRINGS_KEY
    configure_policy.bounded_text(value, key)
    current = settings.get(key, [])
    if operation == "add":
        updated = current if value in current else [*current, value]
    else:
        updated = [entry for entry in current if entry != value]
    return {key: configure_policy.string_list(updated, key)}


def changes(arguments, settings, now):
    if not isinstance(arguments, str):
        raise ValueError(USAGE)
    if arguments.startswith("contract "):
        return _contract_change(arguments.removeprefix("contract "), settings)
    if len(arguments) > 256:
        raise ValueError(USAGE)
    if match := WINDOW.fullmatch(arguments):
        seconds = int(match[1]) * (60 if match[2] == "m" else 3600)
        if seconds > 8 * 3600:
            raise ValueError("A tests window lasts at most eight hours.")
        return {"tests_allow_until": int(now) + seconds}
    if match := RULE.fullmatch(arguments):
        name, state = match.groups()
        if name in config.RETIRED_RULES or name in config.FIXED_OBSERVE_RULES:
            raise ValueError("This rule has no editable gate.")
        configure_policy.validate_policy_values({"rule_gates": {name: state}})
        return {"rule_gates": {**settings.get("rule_gates", {}), name: state}}
    raise ValueError(USAGE)


def apply(arguments, cwd, now, payload=None):
    return apply_owner(arguments, cwd, now, payload)[0]


def _changed_keys(before, after):
    keys = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if isinstance(old, dict) and isinstance(new, dict):
            keys.extend(f"{key}.{name}" for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name))
        else:
            keys.append(key)
    return keys


def _baseline_data(path, project, sealed):
    from lib import protected, shell_snapshot

    digest, text = sealed
    if digest == shell_snapshot.ABSENT:
        return {}
    if text is not None and hashlib.sha256(text.encode()).hexdigest() == digest:
        try:
            return config._parse_project_config(path, text.encode()).data
        except ValueError:
            pass
    return {key: value for key, value in project.data.items() if key not in protected.POLICY_KEYS and key != "checks"}


def _owner_base(payload, path, project):
    session = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session, str) or not session:
        return project.data, []
    from lib import after_run_config

    sealed = after_run_config.owner_baseline(session, path)
    if sealed is None:
        return project.data, []
    base = _baseline_data(path, project, sealed)
    return base, _changed_keys(base, project.data)


def apply_owner(arguments, cwd, now, payload=None):
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise ValueError("The hook needs the current project folder.")
    path = config.project_config_path(cwd)
    if not path.exists():
        root = git_port.view().toplevel(Path(cwd)) or path.parent
        path = root / config.CONFIG_NAME
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        project = config.load_project_config(path)
        base, discarded = _owner_base(payload, path, project)
        values = changes(arguments, config.flatten_settings(base), now)
        content = json.dumps({**base, **values}, indent=2) + "\n"
        if len(content.encode()) > config.MAX_PROJECT_CONFIG_BYTES:
            raise ValueError("Project config exceeds the size limit.")
        atomic_write(path, content)
        if payload and payload.get("session_id"):
            _note_owner_write(payload, path, content)
    finally:
        os.close(descriptor)
    return path, discarded


def _note_owner_write(payload, path, content):
    from lib import after_run

    event = {"session_id": payload["session_id"], "cwd": payload["cwd"], "tool_name": "Write",
             "tool_input": {"file_path": str(path)}, "tool_use_id": "owner-config-" + str(time.monotonic_ns())}
    after_run.note_direct_write(event, None, expected=hashlib.sha256(content.encode()).hexdigest())


def main(event):
    payload = json.load(sys.stdin)
    args = user_arguments(event, payload) if isinstance(payload, dict) else None
    if args is None:
        return {}
    try:
        path, discarded = apply_owner(args, payload.get("cwd"), time.time(), payload)
        note = f" It discarded the agent change to {', '.join(discarded)}." if discarded else ""
        return stop_block(f"ADW Lite updated {path}.{note}")
    except (OSError, ValueError) as exc:
        return stop_block(str(exc))


if __name__ == "__main__" and len(sys.argv) == 2:
    print(json.dumps(main(sys.argv[1])))
