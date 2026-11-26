from __future__ import annotations

import functools
import glob
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

LIST_SECONDS = 2
GREP_SECONDS = 5
LITERAL_MAGIC = ":(literal)"
NO_REPOSITORY = "not a git repository"
NO_REPOSITORY_EXIT = 128
GREP_REVISION = "HEAD"
SNAPSHOT_SECONDS = 5


@functools.lru_cache(maxsize=512)
def _no_work_tree(directory: str) -> bool:
    clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        found = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, errors="surrogateescape", timeout=LIST_SECONDS, check=False, env=clean,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return found.returncode == NO_REPOSITORY_EXIT and NO_REPOSITORY in found.stderr


def _git_list(arguments: list[str], directory: Path, timeout: int = LIST_SECONDS) -> list[str] | None:
    try:
        listed = subprocess.run(
            ["git", "-C", str(directory), *arguments],
            capture_output=True, text=True, errors="surrogateescape", timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [path for path in listed.stdout.split("\0") if path] if listed.returncode == 0 else None


def _tree_blob(entry: str) -> tuple[str, str]:
    meta, _, path = entry.partition("\t")
    fields = meta.split()
    return (path, fields[2]) if len(fields) == 3 and fields[1] == "blob" else ("", "")


def _head_matches(root: str, needle: str) -> list[str]:
    command = ["git", "-C", root, "grep", "-l", "-z", "-F", "-e", needle, GREP_REVISION, "--"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=GREP_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    names = result.stdout.decode("utf-8", "replace").split("\0")
    return [name.split(":", 1)[1] for name in names if name.startswith(GREP_REVISION + ":")]


def _env(index_file: Path | None) -> dict[str, str] | None:
    return None if index_file is None else {**os.environ, "GIT_INDEX_FILE": str(index_file)}


def _optional(root: Path, *args: str, timeout: int = 30, errors: str = "replace") -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, errors=errors, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _baseline(root: Path, args: list[str], until: float | None) -> str | None:
    try:
        from .baseline import GIT_TIMEOUT_SECONDS, remaining
    except ImportError:
        from baseline import GIT_TIMEOUT_SECONDS, remaining
    left = remaining(until)
    timeout = GIT_TIMEOUT_SECONDS if left is None else min(GIT_TIMEOUT_SECONDS, left)
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(root), text=True, capture_output=True,
            check=True, timeout=timeout, errors="replace",
        )
    except subprocess.TimeoutExpired:
        remaining(until)
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


def _not_repository(reason: str) -> Exception:
    try:
        from .fixture_prose import NotARepository
    except ImportError:
        from fixture_prose import NotARepository
    return NotARepository(reason)


def _listing(root: Path, args: list[str], index_file: Path | None = None, *, empty: int | None = None) -> list[str]:
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True, env=_env(index_file), timeout=30)
    except FileNotFoundError as exc:
        raise _not_repository("git is not available") from exc
    if done.returncode == empty:
        return []
    if done.returncode == 128:
        raise _not_repository(str(root))
    if done.returncode:
        raise OSError(f"git {args[0]} failed in {root}")
    return [p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def _strict(root: Path, args: list[str], index_file: Path | None = None) -> str | None:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=_env(index_file), timeout=30)
    if done.returncode == 128 and "not a git repository" in done.stderr.lower():
        return None
    done.check_returncode()
    return done.stdout


class CliGitView:
    def has_work_tree(self, directory: Path) -> bool:
        return not _no_work_tree(str(directory))

    def toplevel(
        self, directory: Path, *, strict: bool = False, until: float | None = None, timeout: int = 2,
        errors: str = "replace", newline_only: bool = False,
    ) -> Path | None:
        args = ["rev-parse", "--show-toplevel"]
        if strict:
            text = _strict(directory, args)
        elif until is not None or (timeout == 2 and errors == "replace"):
            text = _baseline(directory, args, until)
        else:
            text = _optional(directory, *args, timeout=timeout, errors=errors)
        if text is None:
            return None
        name = text.rstrip("\n") if newline_only else text.strip()
        return Path(name) if name or newline_only else None

    def common_dir(self, directory: Path) -> Path | None:
        text = _optional(directory, "rev-parse", "--path-format=absolute", "--git-common-dir", timeout=5, errors="surrogateescape")
        return Path(os.path.realpath(text.strip())) if text and text.strip() else None

    def worktree_roots(self, directory: Path) -> list[Path] | None:
        entries = _git_list(["worktree", "list", "--porcelain", "-z"], directory)
        return None if not entries else [Path(row[9:]) for row in entries if row.startswith("worktree ")]

    def state_names(self, root: Path, name: str) -> list[str]:
        if name not in {"MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"}:
            return []
        text = _optional(root, "rev-parse", "--git-path", name)
        if not text or not text.strip():
            return []
        try:
            return (root / text.strip()).read_text(encoding="utf-8").split()
        except (OSError, UnicodeDecodeError):
            return []

    def tracked_paths(self, root: Path, pathspecs: Sequence[str] = (), *, timeout: int = 2) -> list[str] | None:
        args = ["--", *pathspecs] if pathspecs else []
        text = _optional(root, "ls-files", "-z", *args, timeout=timeout, errors="surrogateescape")
        return None if text is None else [name for name in text.split("\0") if name]

    def disk_paths(self, root: Path) -> list[str] | None:
        return _listing(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])

    def clean_blob_ids(self, root: Path) -> dict[str, str] | None:
        dirty = _git_list(["status", "--porcelain=v1", "-z", "--untracked-files=no", "--no-renames"], root, SNAPSHOT_SECONDS)
        if dirty is None:
            return None
        head = _git_list(["ls-tree", "-r", "-z", "--full-tree", "HEAD"], root, SNAPSHOT_SECONDS) or []
        changed = {entry[3:] for entry in dirty}
        return {path: oid for path, oid in map(_tree_blob, head) if path and path not in changed}

    def index_entries(self, root: Path) -> dict[str, str] | None:
        listed = _git_list(["ls-files", "-s", "-z"], root, SNAPSHOT_SECONDS)
        return None if listed is None else {path: meta for meta, _, path in (entry.partition("\t") for entry in listed)}

    def ignored_files(self, root: Path) -> list[str] | None:
        return _git_list(["ls-files", "-z", "--others", "--ignored", "--exclude-standard"], root, SNAPSHOT_SECONDS)

    def index_paths(self, root: Path, index_file: Path | None = None) -> list[str] | None:
        return _listing(root, ["ls-files", "-z"], index_file)

    def tree_paths(self, root: Path, tree: str, pathspecs: Sequence[str]) -> list[str] | None:
        if tree.startswith("-"):
            return None
        return _git_list(["ls-tree", "-r", "-z", "--name-only", tree, "--", *pathspecs], root)

    def matched_paths(self, root: Path, pathspecs: Sequence[str]) -> list[str] | None:
        expanded = [match for pattern in pathspecs for match in glob.glob(pattern, root_dir=root, recursive=True, include_hidden=True)]
        listed = _git_list(["ls-files", "-z", "--", *pathspecs, *(LITERAL_MAGIC + match for match in expanded)], root)
        return None if listed is None else sorted({*expanded, *listed})

    def is_tracked(self, root: Path, path: str) -> bool:
        return _optional(root, "ls-files", "--error-unmatch", "--", path, timeout=5, errors="surrogateescape") is not None

    def ignore_match(self, root: Path, path: str, *, excludes_file: bool = False) -> tuple[str, str] | None:
        flags = ["-c", f"core.excludesFile={os.devnull}"] if excludes_file else []
        text = _optional(root, *flags, "check-ignore", "-v", "--", path, timeout=5, errors="surrogateescape")
        parts = text.partition("\t")[0].split(":", 2) if text else []
        return (parts[0], parts[2]) if len(parts) == 3 else None

    def ignored_paths(self, root: Path, paths: Sequence[str]) -> set[str]:
        if not paths:
            return set()
        try:
            result = subprocess.run(["git", "check-ignore", "--stdin", "-z"], cwd=root, input="\0".join(paths) + "\0",
                                    capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return set()
        return set(paths).intersection(name for name in result.stdout.split("\0") if name)

    def is_unchanged(self, root: Path, path: str) -> bool:
        return _optional(root, "diff", "--quiet", "HEAD", "--", path, timeout=5) is not None

    def staged_paths(self, root: Path, index_file: Path | None = None) -> list[str] | None:
        text = _strict(root, ["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"], index_file)
        return None if text is None else [path for path in text.split("\0") if path]

    def diff_paths(self, root: Path, spans: Sequence[str]) -> list[str] | None:
        if any(span.startswith("-") for span in spans):
            return None
        return _git_list(["diff", "--name-only", "-z", "--no-renames", *spans, "--"], root)

    def commit_parents(self, root: Path, ref: str) -> list[str] | None:
        rows = _git_list(["rev-list", "--parents", "-n", "1", "--end-of-options", ref, "--"], root)
        return rows[0].split()[1:] if rows and len(rows) == 1 else None

    def head_text(
        self, root: Path, path: str, revision: str = "HEAD", *, until: float | None = None,
        errors: str = "replace", timeout: int = 2,
    ) -> str | None:
        if revision.startswith("-"):
            return None
        args = ["show", f"{revision}:{path}"]
        return _baseline(root, args, until) if until is not None or (timeout == 2 and errors == "replace") else _optional(root, *args, errors=errors, timeout=timeout)

    def index_bytes(self, root: Path, path: str, index_file: Path | None = None, *, limit: int | None = None) -> bytes:
        command = ["git", "show", ":" + path]
        if limit is None:
            return subprocess.run(command, cwd=root, env=_env(index_file), capture_output=True, check=True, timeout=30).stdout
        if limit < 0:
            raise ValueError("negative byte limit")
        with subprocess.Popen(command, cwd=root, env=_env(index_file), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            try:
                return process.stdout.read(limit) if process.stdout else b""
            finally:
                process.kill()

    def index_text(self, root: Path, path: str, index_file: Path | None = None) -> str | None:
        try:
            return self.index_bytes(root, path, index_file).decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            return None

    def commit_time(self, root: Path, commit: str) -> int | None:
        text = None if commit.startswith("-") else _optional(root, "show", "-s", "--format=%ct", commit)
        return int(text.strip()) if text and text.strip().isdigit() else None

    def resolve_commit(self, root: Path, spec: str) -> str | None:
        text = _optional(root, "rev-parse", "--verify", "--quiet", "--end-of-options", spec + "^{commit}")
        return text.strip() if text and text.strip() else None

    def ref_heads(self, root: Path) -> dict[str, str] | None:
        text = _optional(root, "for-each-ref", "--format=%(objectname) %(refname)", "refs/heads", timeout=SNAPSHOT_SECONDS, errors="surrogateescape")
        if text is None:
            return None
        return {name: oid for oid, _, name in (line.partition(" ") for line in text.splitlines()) if name}

    def refs_containing(self, root: Path, commit: str, refs: Sequence[str]) -> list[str]:
        if any(ref.startswith("-") for ref in refs):
            return []
        text = _optional(root, "for-each-ref", "--count=1", "--contains", commit, "--format=%(refname)", *refs)
        return text.splitlines() if text else []

    def is_ancestor(self, root: Path, commit: str, descendant: str = "HEAD") -> bool:
        if commit.startswith("-") or descendant.startswith("-"):
            return False
        return _optional(root, "merge-base", "--is-ancestor", commit, descendant) is not None

    def config_value(self, root: Path, key: str, *, flags: Sequence[str] = (), env: Mapping[str, str] | None = None) -> str | None:
        done = subprocess.run(["git", *flags, "config", "--get", key], cwd=root, env=env,
                              capture_output=True, text=True, timeout=30)
        return done.stdout.strip() if done.returncode == 0 else None

    def config_entries(self, root: Path, pattern: str, *, file: str | None = None) -> list[str] | None:
        source = ["-f", file] if file is not None else []
        return _git_list(["config", "--no-includes", "-z", *source, "--get-regexp", "--", pattern], root)

    def grep_tracked(self, root: Path, needle: str) -> list[str]:
        return _head_matches(str(root), needle)

    def grep_index(self, root: Path, needle: str, index_file: Path | None = None) -> list[str]:
        return _listing(root, ["grep", "--cached", "-z", "-l", "-I", "-F", "-e", needle], index_file, empty=1)
