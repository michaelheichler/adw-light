from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from lib.reporting import mask_target


def records(path):
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except ValueError:
                print(f"{path}:{number}: invalid JSON", file=sys.stderr)
                continue
            if isinstance(row, dict):
                yield row


def recent_blocks(paths, days):
    now = datetime.now(UTC)
    for path in paths:
        for row in records(path):
            if row.get("outcome") != "block" or row.get("event") not in {"PreToolUse", "PreCommit"}:
                continue
            if not isinstance(row.get("rule"), str) or not row["rule"].strip() or not isinstance(row.get("ts"), str):
                continue
            try:
                stamp = datetime.fromisoformat(row["ts"])
            except ValueError:
                continue
            if stamp.tzinfo is None:
                continue
            if now - timedelta(days=days) <= stamp <= now:
                yield row


def session_parts(projects, sid):
    paths = [*projects.glob(f"*/{sid}.jsonl"), *projects.glob(f"*/{sid}/subagents/**/*.jsonl")]
    for path in paths:
        for entry in records(path):
            message = entry.get("message") or {}
            if entry.get("sessionId") != sid or not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            for part in message.get("content", []):
                if isinstance(part, dict) and part.get("type") == "tool_use" and isinstance(part.get("input"), dict):
                    yield entry, part


def transcript_calls(projects, rows):
    sessions = defaultdict(list)
    for row in rows:
        sid = row.get("session_id", "")
        if re.fullmatch(r"[\w-]+", sid):
            sessions[sid].append(row)
    matches = {}
    for sid, group in sessions.items():
        match_session(projects, sid, group, matches)
    return matches


def match_session(projects, sid, rows, matches):
    wanted = {row.get("tool_use_id") for row in rows}
    times = defaultdict(list)
    exact = {}
    for entry, part in session_parts(projects, sid):
        try:
            timestamp = datetime.fromisoformat(entry.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if timestamp.tzinfo is None:
            continue
        call = (part.get("name"), part["input"], entry.get("cwd", ""))
        if part.get("id") and part["id"] in wanted:
            exact[part["id"]] = call
        if call[0] in {"Bash", "Write"}:
            times[call[0]].append((timestamp, call))
    for row in rows:
        uid = row.get("tool_use_id")
        key = sid, uid, row["ts"]
        if uid in exact:
            matches[key] = (*exact[uid], "id")
        elif "target" not in row:
            names = {"pre_bash": ["Bash"], "pre_commit": ["Bash"], "pre_write": ["Write"]}.get(row.get("hook"), [])
            candidates = [item for name in names for item in times[name]]
            stamp = datetime.fromisoformat(row["ts"])
            nearest = min(candidates, key=lambda item: abs(stamp - item[0]), default=None)
            if nearest and abs(stamp - nearest[0]) <= timedelta(seconds=30):
                matches[key] = (*nearest[1], "time")


def matched_call(row, calls):
    name, data, cwd = row.get("tool_name"), row.get("tool_input"), row.get("cwd", "")
    if name and cwd and isinstance(data, dict):
        return name, data, cwd, "ledger"
    if name == "Bash" and cwd and row.get("target") and not (row.get("target_truncated") or row.get("target_redacted")):
        return name, {"command": row["target"]}, cwd, "ledger"
    key = row.get("session_id", ""), row.get("tool_use_id"), row["ts"]
    return calls.get(key, (name, data, cwd, "none"))


def replay_call(engine, payload):
    with TemporaryDirectory() as scratch:
        argv = [sys.executable, "-IB", str(engine), "PreToolUse", "--host", "claude", "--state-dir", scratch]
        try:
            run = subprocess.run(argv, input=json.dumps(payload), cwd=scratch, text=True,
                                 capture_output=True, check=True, timeout=60)
            reply = json.loads(run.stdout)
            decision = reply.get("hookSpecificOutput", {}).get("permissionDecision")
            if decision == "deny":
                return "yes", ""
            if reply == {} or decision == "allow" or "additionalContext" in reply.get("hookSpecificOutput", {}):
                return "no", ""
            return "unknown", "unexpected hook reply"
        except (ValueError, AttributeError, subprocess.SubprocessError, OSError) as error:
            return "unknown", type(error).__name__


def replay_rows(engine, rows, calls):
    seen = set()
    for row in rows:
        sid, uid = row.get("session_id", ""), row.get("tool_use_id", "")
        name, data, cwd, matched = matched_call(row, calls)
        args = data if isinstance(data, dict) else {}
        target = row.get("target") or args.get("command") or args.get("file_path") or row.get("path") or ""
        key = (cwd, name, args.get("command") or target or json.dumps(args, sort_keys=True), row["rule"])
        if not data or not cwd:
            key += (sid, uid or row["ts"])
        if key in seen:
            continue
        seen.add(key)
        verdict, note = "unknown", "tool input or cwd unavailable"
        if cwd and not Path(cwd).is_dir():
            note = "cwd unavailable locally"
        elif name and isinstance(data, dict) and cwd:
            payload = {
                "tool_name": name, "tool_input": data, "cwd": cwd, "session_id": sid, "tool_use_id": uid,
                "turn_id": row.get("turn_id", ""), "hook_event_name": "PreToolUse",
            }
            verdict, note = replay_call(engine, payload)
        masked, _ = mask_target(target if isinstance(target, str) else "")
        yield {
            "rule": row["rule"], "target": masked, "cwd": cwd, "still_refused": verdict, "note": note,
            "session_id": sid, "tool_use_id": uid, "matched_by": matched,
        }


def table_cell(value):
    text = " ".join(str(value).split())[:100] or "(none)"
    return "`" + text.replace("`", "&#96;").replace("|", "&#124;") + "`"


def replay_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("ledgers", type=Path, nargs="+")
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--projects", type=Path, default=Path.home() / ".claude/projects")
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    engine = args.tree.resolve() / "adw_light.py"
    if args.days <= 0 or not engine.is_file():
        parser.error("Choose a positive day count and an engine tree containing adw_light.py.")
    rows = list(recent_blocks(args.ledgers, args.days))
    results = list(replay_rows(engine, rows, transcript_calls(args.projects, rows)))
    report = {"engine": str(engine), "days": args.days, "ledger_blocks": len(rows), "results": results}
    args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{len(rows)} refusal rows in {args.days} days. {len(results)} entries after deduplication.\n")
    print("| Rule | Command or path | Project cwd | Still refused | Note | Matched by |\n|---|---|---|---|---|---|")
    for row in results:
        print("| " + " | ".join(table_cell(row[k]) for k in ("rule", "target", "cwd", "still_refused", "note", "matched_by")) + " |")


if __name__ == "__main__":
    replay_cli()
