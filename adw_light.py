import os
import sys
from argparse import ArgumentParser
from importlib import import_module
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
EVENTS = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "prompt_submit",
    "PreToolUse": "pre_tool",
    "PostToolUse": "record",
    "PostToolBatch": "batch",
    "PostToolUseFailure": "failure",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
    "SessionEnd": "session_end",
}


def observe_reply(event: str, payload: dict[str, Any], settings: dict[str, Any]) -> None:
    config_api = import_module("lib.config")
    reporting = import_module("lib.reporting")
    scanner = import_module("lib.scanner")

    message = payload.get("last_assistant_message")
    session_id = payload.get("session_id")
    if not isinstance(message, str) or not message or not isinstance(session_id, str) or not session_id:
        return
    config = config_api.payload_hook_config(settings, payload)
    markup = import_module("lib.markup")
    prose, _ = markup.reply_lines(markup.markdown_lines(message))
    turn_id = payload.get("turn_id")
    for finding in scanner.scan_all("assistant-reply.md", prose, config):
        if config_api.resolve_outcome(finding, config) != "block":
            continue
        reporting.record_decision(
            session_id=session_id, hook="reply_scan", event=event,
            family=finding.get("family", ""), rule=finding.get("rule", ""), path="assistant-reply.md",
            tool_use_id="", outcome="would_block", duration_ms=0,
            turn_id=turn_id if isinstance(turn_id, str) else "", root=config.get("ledger_root"),
        )


def run(event: str, payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    hookio = import_module("lib.hookio")

    if event in {"Stop", "SubagentStop"}:
        observe_reply(event, payload, settings)
    module_name = EVENTS.get(event)
    if event == "PostToolUse" and payload.get("tool_name", "").startswith("mcp__"):
        module_name = "mcp_success"
    if module_name is None:
        return {}
    module = import_module(module_name)
    handler = module.run
    if event == "SessionStart":
        return handler(payload, settings, drift=lambda: "")
    result = handler(payload, settings)
    if event == "PostToolBatch":
        return module.cli_response(result)
    if event == "PreToolUse":
        return hookio.claude_pretool_response(result)
    if event == "PostToolUse":
        return hookio.claude_feedback_response(result, event)
    return result


def overflow_response(event: str, payload: dict, config: dict) -> dict:
    hookio = import_module("lib.hookio")
    blockers = import_module("lib.blocker_state")
    reason = "ADW Lite could not fit the event in memory. Retry with a smaller tool call."
    blockers.hold_undecidable(payload, config, blockers.RECORD_ERROR_KEY, reason)
    if event == "PreToolUse":
        return hookio.claude_pretool_response(hookio.deny(reason))
    if event in {"PostToolUse", "PostToolBatch"}:
        return hookio.advise(reason, event)
    if event in {"Stop", "SubagentStop"}:
        return hookio.stop_block(reason)
    return hookio.system_message(reason)


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("event", nargs="?", choices=EVENTS)
    parser.add_argument("--host", choices=("claude", "codex"), default="claude")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".state")
    args = parser.parse_args()
    plugin = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin and args.state_dir.resolve().is_relative_to(Path(plugin).resolve()):
        parser.error("Choose a state directory outside the plugin tree.")
    sys.path.insert(0, str(ROOT / "engine"))
    os.umask(0o077)
    state_dir = import_module("lib.config_roots").migrated_state_dir(args.state_dir).resolve()
    os.environ["ADW_STATE_ROOT"] = str(state_dir)
    os.environ["ADW_CODEX_HOOK"] = "1" if args.host == "codex" else ""
    hookio = import_module("lib.hookio")

    payload = hookio.read_payload()
    event = args.event or (payload.get("hook_event_name", "") if isinstance(payload, dict) else "")
    config = {"adw_model": "", "state_root": str(state_dir / "state"),
              "ledger_root": str(state_dir / "ledger")}
    if payload is hookio.INPUT_OVERFLOW:
        result = overflow_response(event, payload, config)
    elif payload is hookio.PARSE_FAILURE:
        reason = "ADW Lite could not read the hook event."
        result = hookio.claude_pretool_response(hookio.deny(reason)) if event == "PreToolUse" else hookio.stop_block(reason)
        if event in {"PostToolUse", "PostToolBatch"}:
            result = hookio.advise(reason, event)
    else:
        result = run(event, payload, config)
    hookio.write_payload(result)


if __name__ == "__main__":
    main()
