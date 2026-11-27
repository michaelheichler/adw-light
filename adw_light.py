import os
import sys
from argparse import ArgumentParser
from contextlib import ExitStack, contextmanager
from importlib import import_module
from pathlib import Path
from time import monotonic
from typing import Any

CLI_STARTED_AT = monotonic() if __name__ == "__main__" else None
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


AFTER_RUN_MODULES = frozenset({"record", "batch"})


def _deadline(started_at: float) -> float:
    return import_module("lib.stop_scan").deadline(started_at)


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
    for finding in scanner.scan_all("assistant-reply.md", prose, config, document=False):
        if config_api.resolve_outcome(finding, config) != "block":
            continue
        reporting.record_decision(
            session_id=session_id, hook="reply_scan", event=event,
            family=finding.get("family", ""), rule=finding.get("rule", ""), path="assistant-reply.md",
            tool_use_id="", outcome="would_block", duration_ms=0,
            turn_id=turn_id if isinstance(turn_id, str) else "", root=config.get("ledger_root"),
        )


def run(
    event: str, payload: dict[str, Any], settings: dict[str, Any], *, started_at: float | None = None,
) -> dict[str, Any]:
    if started_at is None:
        started_at = monotonic()
    hookio = import_module("lib.hookio")

    if event in {"Stop", "SubagentStop"}:
        until = _deadline(started_at)
        observe_reply(event, payload, settings)
        return import_module(EVENTS[event]).run(payload, settings, until=until)
    module_name = EVENTS.get(event)
    if event == "PostToolUse" and payload.get("tool_name", "").startswith("mcp__"):
        module_name = "mcp_success"
    if module_name is None:
        return {}
    module = import_module(module_name)
    handler = module.run
    if event == "SessionStart":
        return handler(payload, settings, drift=lambda: "")
    if module_name in AFTER_RUN_MODULES:
        result = handler(payload, settings, until=_deadline(started_at))
    else:
        result = handler(payload, settings)
    if event == "PostToolUseFailure" and payload.get("tool_name") == "Bash":
        import_module("lib.after_run").post(payload, settings, event, until=_deadline(started_at))
    if result.pop("_after_run_block", False):
        return result
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


def _dispatch(event, payload, config, *, started_at: float | None = None):
    hookio = import_module("lib.hookio")
    if not isinstance(payload, dict):
        payload = hookio.PARSE_FAILURE
    event = event or payload.get("hook_event_name", "")
    if payload is hookio.INPUT_OVERFLOW:
        return overflow_response(event, payload, config)
    if payload is hookio.PARSE_FAILURE:
        reason = "ADW Lite could not read the hook event."
        result = hookio.claude_pretool_response(hookio.deny(reason)) if event == "PreToolUse" else hookio.stop_block(reason)
        if event in {"PostToolUse", "PostToolBatch"}:
            result = hookio.advise(reason, event)
        return result
    return run(event, payload, config, started_at=started_at)


@contextmanager
def _hook_context(host, state_dir):
    plugin = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin and state_dir.resolve().is_relative_to(Path(plugin).resolve()):
        raise ValueError("Choose a state directory outside the plugin tree.")
    if host not in {"claude", "codex"}:
        raise ValueError("Unknown hook host.")
    engine = str(ROOT / "engine")
    previous = {key: os.environ.get(key) for key in ("ADW_STATE_ROOT", "ADW_CODEX_HOOK")}
    sys.path.insert(0, engine)
    mask = os.umask(0o077)
    try:
        state_dir = import_module("lib.config_roots").migrated_state_dir(state_dir).resolve()
        os.environ["ADW_STATE_ROOT"] = str(state_dir)
        os.environ["ADW_CODEX_HOOK"] = "1" if host == "codex" else ""
        yield {"adw_model": "", "state_root": str(state_dir / "state"), "ledger_root": str(state_dir / "ledger")}
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.umask(mask)
        sys.path.remove(engine)


def _with_view(event, payload, config, git_view, *, started_at: float | None = None):
    with import_module("lib.git_port").using_view(git_view):
        return _dispatch(event, payload, config, started_at=started_at)


def run_hook(event, payload, host="claude", state_dir=ROOT / ".state", git_view=None):
    started_at = monotonic()
    with _hook_context(host, Path(state_dir)) as config:
        return _with_view(event, payload, config, git_view, started_at=started_at)


def main() -> None:
    parser = ArgumentParser(allow_abbrev=False)
    parser.add_argument("event", nargs="?", choices=EVENTS)
    parser.add_argument("--host", choices=("claude", "codex"), default="claude")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".state")
    parser.add_argument("--git-view", type=Path)
    with ExitStack() as stack:
        try:
            args = parser.parse_args()
            config = stack.enter_context(_hook_context(args.host, args.state_dir))
            view = import_module("lib.git_port").FakeGitView.from_file(args.git_view) if args.git_view is not None else None
        except ValueError as error:
            parser.error(str(error))
        hookio = import_module("lib.hookio")
        payload = hookio.read_payload()
        hookio.write_payload(_with_view(args.event, payload, config, view, started_at=CLI_STARTED_AT))


if __name__ == "__main__":
    main()
