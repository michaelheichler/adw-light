from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from lib import payloads, tests_policy
from lib.config import effective_hook_config, project_config_path
from lib.baseline import baseline_mode, colon_ownership, split_pending_file
from lib.findings import Finding
from lib.regular_file import open_regular
from lib.path_policy import anchored, anchored_identity, marked_project
from lib.scan_input import _max_scan_bytes, file_length_policy, file_line_count, mostly_tool_output, scannable_text
from lib.scanner import _code_file, scan_all
from lib.shell_parse import LiteralWrite, literal_writes
from lib.temp_scope import command_targets


WriteConfig = Callable[[str], dict]


def write_config(config: object, payload: object, base: dict) -> WriteConfig:
    cwd = Path(payloads.cwd(payload) or ".").absolute()
    physical_cwd = cwd.resolve()
    current = project_config_path(cwd)

    @lru_cache(maxsize=128)
    def for_directory(directory: Path) -> dict:
        physical_directory = directory.resolve()
        if not physical_directory.is_relative_to(physical_cwd) and marked_project(physical_directory) is None:
            return base
        if project_config_path(directory) == current:
            return base
        merged = effective_hook_config(config, directory)
        merged["session_id"] = base.get("session_id", "")
        return merged

    return lambda path: for_directory(anchored(path, cwd).parent)


def grouped_write_rows(rows: list[dict], settings_for: WriteConfig, base: dict) -> list[tuple[dict, list[dict]]]:
    grouped: dict[int, tuple[dict, list[dict]]] = {}
    for row in rows:
        path = row.get("path")
        settings = settings_for(path) if path else base
        grouped.setdefault(id(settings), (settings, []))[1].append(row)
    return list(grouped.values())


@dataclass(frozen=True, slots=True)
class FileLength:
    count: int = 0
    capped: bool = False
    ends_with_newline: bool = True


def shaped_write_findings(
    command: str, config: dict | None, cwd: str | Path | None, *, settings_for: WriteConfig | None = None,
) -> tuple[list[dict], list[dict]]:
    resolved_cwd = Path(cwd) if cwd is not None else Path(".")
    owned: list[dict] = []
    inherited: list[dict] = []
    lengths: dict[Path, FileLength] = {}
    contents: dict[Path, str | None] = {}
    for write in _project_writes(command, resolved_cwd):
        settings = settings_for(write.path) if settings_for else config
        body = scannable_text(write.text, settings or {})
        if body is None:
            continue
        shape = ShapedWrite(write, body, resolved_cwd, settings)
        path = anchored_identity(write.path, resolved_cwd)
        if write.append:
            before = lengths[path] if path in lengths else _file_length(path)
            lengths[path] = _appended_length(before, body)
            old = contents[path] if path in contents else _append_before(path, settings or {})
            contents[path] = None if old is None else scannable_text(old + body, settings or {})
            shape_owned, shape_inherited = _append_shape_findings(shape, before, lengths[path], old, contents[path])
        else:
            lengths[path] = _appended_length(FileLength(), body)
            shape_owned, shape_inherited = _overwrite_shape_findings(shape, contents.get(path))
            contents[path] = body
        owned.extend(shape_owned)
        inherited.extend(shape_inherited)
    return owned, inherited


def _project_writes(command: str, cwd: Path) -> list[LiteralWrite]:
    return [
        write._replace(path=target)
        for write in literal_writes(command)
        for target in command_targets(write.path, command, cwd)
        if not tests_policy.scratch_write(anchored(target, cwd), cwd)
    ]


@dataclass(frozen=True, slots=True)
class ShapedWrite:
    write: LiteralWrite
    body: str
    cwd: Path
    config: dict | None


def _append_before(path: Path, config: dict) -> str | None:
    limit = max(0, _max_scan_bytes(config))
    try:
        with open_regular(path) as stream:
            raw = stream.read(limit + 1)
        return raw.decode("utf-8") if len(raw) <= limit else None
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError):
        return None


def _overwrite_shape_findings(shape: ShapedWrite, before: str | None) -> tuple[list[dict], list[dict]]:
    findings = _stamped_findings(shape.write.path, shape.body, shape.config)
    return split_pending_file(
        anchored(shape.write.path, shape.cwd), findings, shape.config or {}, shape.body, before
    )


def _append_shape_findings(
    shape: ShapedWrite, before: FileLength, after: FileLength, old: str | None, text: str | None,
) -> tuple[list[dict], list[dict]]:
    findings = _label_appended_text([
        finding for finding in _stamped_findings(shape.write.path, shape.body, shape.config)
        if finding["rule"] not in {"file_length_warning", "file_length_critical", "file_too_long"}
        and (text is None or finding["rule"] != "prose_colon")
    ])
    length_finding = _append_length_finding(shape.write.path, shape.body, before, after)
    if length_finding is not None:
        findings.append(length_finding)
    if text is None or ":" not in text:
        return findings, []
    path = shape.write.path
    rows = [row for row in _stamped_findings(path, text, shape.config) if row["rule"] == "prose_colon"]
    _, added, carried = colon_ownership(path, rows, [old or ""], shape.config or {})
    return findings + added, carried if baseline_mode(shape.config or {}) == "report" else []


def _stamped_findings(path: str, body: str, config: dict | None) -> list[dict]:
    return [Finding.from_dict(finding).with_path(path).to_dict() for finding in scan_all(path, body, config)]


def _label_appended_text(findings: list[dict]) -> list[dict]:
    return [
        Finding.from_dict(finding).with_detail(
            finding["detail"] + " (line " + str(finding["line"]) + " of appended text)"
        ).to_dict()
        for finding in findings
    ]


def _append_length_row(finding: Finding) -> dict:
    return {
        "family": finding.family,
        "rule": finding.rule,
        "line": finding.line,
        "detail": finding.detail,
        "force": finding.force,
        "path": finding.path,
        "snippet": finding.snippet,
        "action": finding.action,
    }


def _append_length_finding(path: str, body: str, before: FileLength, after: FileLength) -> dict | None:
    if not _code_file(path, body) or mostly_tool_output(body):
        return None
    policy = file_length_policy(after.count)
    if policy is None or (policy[0] != "file_too_long" and policy == file_length_policy(before.count)):
        return None
    rule, action = policy
    shown = f"at least {after.count}" if after.capped else str(after.count)
    finding = Finding(
        family="code",
        rule=rule,
        line=1,
        detail=f"File has {shown} lines in {path}",
        force=True,
        snippet=path.strip()[:180],
        action=action,
        path=path,
        severity=None,
        tool_use_id=None,
    )
    return _append_length_row(finding)


def _file_length(path: Path) -> FileLength:
    counted = file_line_count(path)
    count, capped = counted if counted is not None else (0, False)
    return FileLength(count, capped, _ends_with_newline(path))


def _appended_length(before: FileLength, body: str) -> FileLength:
    if not body:
        return before
    total = before.count + len(body.splitlines())
    if before.count and not before.ends_with_newline:
        total -= 1
    return FileLength(total, before.capped, body.endswith("\n"))


def _ends_with_newline(resolved_path: Path) -> bool:
    try:
        with open_regular(resolved_path) as handle:
            handle.seek(-1, 2)
            return handle.read(1) == b"\n"
    except OSError:
        return True
