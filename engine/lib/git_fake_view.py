from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase
from itertools import islice
from pathlib import Path


_MAX_BYTES = 8 * 1024 * 1024


def _map(value):
    if type(value) is not dict or any(type(k) is not str for k in value):
        raise ValueError("Expected an object with string keys")
    return value


def _text(value):
    if type(value) is not str:
        raise ValueError("Expected text")
    return value


def _texts(value):
    if type(value) is not list:
        raise ValueError("Expected a list")
    return [_text(s) for s in value]


def _blob(value):
    if type(value) is str:
        return value.encode("utf-8")
    if set(_map(value)) != {"base64"}:
        raise ValueError("Expected a text or base64 blob")
    return base64.b64decode(_text(value["base64"]), validate=True)


def _blob_id(blob):
    return hashlib.sha1(b"blob %d\0" % len(blob) + blob).hexdigest()


def _tree(value):
    return {key: _blob(blob) for key, blob in _map(value).items()}


def _matches(path, spec):
    literal = spec.startswith(":(literal)")
    name = spec.removeprefix(":(literal)").rstrip("/")
    return path == name or path.startswith(name + "/") or (not literal and fnmatchcase(path, name))


def _paths(tree, specs=()):
    if tree is None:
        return None
    return sorted(p for p in tree if not specs or any(_matches(p, s) for s in specs))


class _FlagWords:
    def __init__(self, flags):
        self._words = iter(flags)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._words)

    def value(self):
        word = next(self._words, None)
        if word is None:
            raise ValueError("Fixture config flag lacks its value")
        return word


def _deadline(until):
    if until is not None:
        try:
            from .baseline import remaining
        except ImportError:
            from baseline import remaining
        remaining(until)


def _repo(data):
    data = _map(data).copy()
    for key in ("revisions", "indexes"):
        data[key] = {n: _tree(t) for n, t in _map(data.get(key, {})).items()}
    for key in ("index", "disk"):
        data[key] = _tree(data.get(key, {}))
    for key in ("parents", "state"):
        data[key] = {k: _texts(v) for k, v in _map(data.get(key, {})).items()}
    for key in ("refs", "config", "messages"):
        data[key] = {k: _text(v) for k, v in _map(data.get(key, {})).items()}
    _validate_metadata(data)
    return data


def _validate_metadata(data):
    known = {"index", "indexes", "disk", "revisions", "parents", "refs", "times", "messages", "state", "config", "config_files", "ignore", "common_dir"}
    if data.keys() - known:
        raise ValueError("Unknown repository fixture field")
    _text(data.get("common_dir", ".git"))
    if any(type(t) is not int for t in _map(data.get("times", {})).values()):
        raise ValueError("Expected integer commit times")
    for entries in _map(data.get("config_files", {})).values():
        for value in _map(entries).values():
            _text(value)
    rules = data.get("ignore", [])
    if type(rules) is not list:
        raise ValueError("Expected a list of ignore rules")
    for rule in rules:
        if set(_map(rule)) - {"source", "pattern", "global"} or type(rule.get("global", False)) is not bool:
            raise ValueError("Invalid ignore rule")
        _text(rule.get("source"))
        _text(rule.get("pattern"))


def _status_column(before: dict, after: dict, path: str) -> str:
    if before.get(path) == after.get(path):
        return " "
    if path not in before:
        return "A"
    return "M" if path in after else "D"


class FakeGitView:
    def __init__(self, data: dict):
        if set(_map(data)) != {"roots"}:
            raise ValueError("Expected a roots map")
        self._roots = {}
        for root, state in _map(data["roots"]).items():
            if not Path(root).is_absolute():
                raise ValueError("Expected an absolute repository root")
            self._roots[Path(root)] = _repo(state)

    @classmethod
    def from_file(cls, path: Path | str) -> FakeGitView:
        with Path(path).open("rb") as stream:
            data = stream.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise ValueError("Git view exceeds size limit")
        return cls(json.loads(data.decode("utf-8")))

    def toplevel(self, directory: Path, *, strict: bool = False, until: float | None = None, timeout: int = 2, errors: str = "surrogateescape", newline_only: bool = False) -> Path | None:
        _deadline(until)
        return next((p for p in (directory, *directory.parents) if p in self._roots), None)

    def has_work_tree(self, directory: Path) -> bool:
        return self.toplevel(directory) is not None

    def _state(self, root):
        return self._roots.get(self.toplevel(root), {})

    def _index(self, root, file):
        state = self._state(root)
        return state.get("index") if file is None else state.get("indexes", {}).get(str(file))

    def _revision(self, root, spec):
        name = self.resolve_commit(root, spec)
        return self._state(root).get("revisions", {}).get(name)

    def common_dir(self, directory: Path) -> Path | None:
        root = self.toplevel(directory)
        return None if root is None else root / self._roots[root].get("common_dir", ".git")

    def worktree_roots(self, directory: Path) -> list[Path] | None:
        shared = self.common_dir(directory)
        return [root for root in self._roots if self.common_dir(root) == shared]

    def state_names(self, root: Path, name: str) -> list[str]:
        return self._state(root).get("state", {}).get(name, []).copy()

    def _scoped(self, root, tree):
        top = self.toplevel(root)
        if tree is None or top is None or root == top:
            return tree
        prefix = root.relative_to(top).as_posix() + "/"
        return {name[len(prefix):]: blob for name, blob in tree.items() if name.startswith(prefix)}

    def tracked_paths(self, root: Path, pathspecs: Sequence[str] = (), *, timeout: int = 2) -> list[str] | None:
        return _paths(self._scoped(root, self._index(root, None)), pathspecs)

    def _listed_index(self, root, index_file=None):
        if self.toplevel(root) is None:
            try:
                from .fixture_prose import NotARepository
            except ImportError:
                from fixture_prose import NotARepository
            raise NotARepository(str(root))
        return self._index(root, index_file) or {}

    def disk_paths(self, root: Path) -> list[str] | None:
        tracked = self._scoped(root, self._listed_index(root))
        disk = self._scoped(root, self._state(root)["disk"])
        return sorted(set(tracked) | (disk.keys() - self.ignored_paths(root, list(disk))))

    def head_bytes(self, root: Path, path: str) -> bytes | None:
        return (self._revision(root, "HEAD") or {}).get(path)

    def head_blobs(self, root: Path, paths: Sequence[str], *, until: float | None = None) -> dict[str, bytes] | None:
        return self.revision_blobs(root, "HEAD", paths, until=until)

    def revision_blobs(self, root: Path, revision: str, paths: Sequence[str], *,
                       until: float | None = None) -> dict[str, bytes] | None:
        if not self._state(root):
            return None
        tree = self._revision(root, revision) or {}
        return {path: tree[path] for path in paths if path in tree}

    def tree_changes(self, root: Path, old: str, new: str, *, until: float | None = None) -> list[str] | None:
        _deadline(until)
        before, after = self._revision(root, old), self._revision(root, new)
        if before is None or after is None:
            return None
        return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))

    def porcelain_status(self, root: Path, *, until: float | None = None) -> list[str] | None:
        state = self._state(root)
        if not state:
            return None
        index, disk = state["index"], state["disk"]
        head = self._revision(root, "HEAD") or {}
        rows = []
        for path in sorted(head.keys() | index.keys() | disk.keys()):
            if path not in head and path not in index:
                if not self.ignore_match(root, path):
                    rows.append("?? " + path)
                continue
            staged = _status_column(head, index, path)
            unstaged = _status_column(index, disk, path)
            if staged != " " or unstaged != " ":
                rows.append(staged + unstaged + " " + path)
        return rows

    def clean_blob_ids(self, root: Path) -> dict[str, str] | None:
        head = self._scoped(root, self._revision(root, "HEAD")) or {}
        index = self._scoped(root, self._listed_index(root)) or {}
        disk = self._scoped(root, self._state(root)["disk"])
        return {path: _blob_id(blob) for path, blob in head.items() if index.get(path) == blob and disk.get(path) == blob}

    def index_entries(self, root: Path) -> dict[str, str] | None:
        return {path: _blob_id(blob) for path, blob in self._scoped(root, self._listed_index(root)).items()}

    def ignored_files(self, root: Path, *, config_only: bool = False, directory: bool = False,
                      timeout: float = 2) -> list[str] | None:
        disk = self._scoped(root, self._state(root)["disk"])
        tracked = self._scoped(root, self._listed_index(root))
        paths = [path for path in disk if path not in tracked and (not config_only or Path(path).name.casefold() == ".agent-discipline.json")]
        ignored = sorted(self.ignored_paths(root, paths))
        if directory:
            return sorted({path.split("/")[0] + "/" if "/" in path else path for path in ignored})
        return ignored

    def index_paths(self, root: Path, index_file: Path | None = None) -> list[str] | None:
        return sorted(self._scoped(root, self._listed_index(root, index_file)))

    def tree_paths(self, root: Path, tree: str, pathspecs: Sequence[str], *, until: float | None = None) -> list[str] | None:
        _deadline(until)
        return _paths(self._scoped(root, self._revision(root, tree)), [":(literal)" + spec for spec in pathspecs])

    def matched_paths(self, root: Path, pathspecs: Sequence[str]) -> list[str] | None:
        tracked = self.tracked_paths(root, pathspecs)
        if tracked is None:
            return None
        disk = self._scoped(root, self._state(root)["disk"])
        return sorted(set(tracked) | {p for p in disk if any(fnmatchcase(p, s) for s in pathspecs)})

    def is_tracked(self, root: Path, path: str) -> bool:
        if Path(path).is_absolute():
            if not Path(path).is_relative_to(root):
                return False
            path = Path(path).relative_to(root).as_posix()
        return bool(self.tracked_paths(root, [path]))

    def ignore_match(self, root: Path, path: str, *, excludes_file: bool = False) -> tuple[str, str] | None:
        if self.is_tracked(root, path):
            return None
        top = self.toplevel(root)
        path = str(root.relative_to(top) / path) if top is not None else path
        for rule in reversed(self._state(root).get("ignore", [])):
            source, pattern = _text(rule["source"]), _text(rule["pattern"])
            if excludes_file and rule.get("global", False):
                continue
            base = Path(".") if rule.get("global") or source == ".git/info/exclude" else Path(source).parent
            relative = Path(path).relative_to(base) if Path(path).is_relative_to(base) else None
            name = pattern.removeprefix("!").lstrip("/")
            if relative is not None and (_matches(str(relative), name) or ("/" not in pattern and any(fnmatchcase(p, name) for p in relative.parts))):
                return source, pattern
        return None

    def ignored_paths(self, root: Path, paths: Sequence[str]) -> set[str]:
        return {p for p in paths if (match := self.ignore_match(root, p)) and not match[1].startswith("!")}

    def is_unchanged(self, root: Path, path: str) -> bool:
        head = self._scoped(root, self._revision(root, "HEAD"))
        if head is None:
            return False
        if Path(path).is_absolute():
            if not Path(path).is_relative_to(root):
                return False
            path = Path(path).relative_to(root).as_posix()
        disk = self._scoped(root, self._state(root)["disk"])
        return head.get(path) == (disk.get(path) if self.is_tracked(root, path) else None)

    def staged_paths(self, root: Path, index_file: Path | None = None) -> list[str] | None:
        index = self._index(root, index_file)
        head = self._revision(root, "HEAD") or {}
        return None if index is None else sorted(p for p in index if index[p] != head.get(p))

    def _lineage(self, root, commit):
        todo = [commit] if commit else []
        seen = set()
        while todo:
            name = todo.pop(0)
            if name not in seen:
                yield name
                seen.add(name)
                todo.extend(self._state(root)["parents"].get(name, []))

    def diff_paths(self, root: Path, spans: Sequence[str]) -> list[str] | None:
        if len(spans) == 1 and "..." in spans[0]:
            a, b = spans[0].split("...", 1)
            ancestors = set(self._lineage(root, self.resolve_commit(root, b)))
            a = next((n for n in self._lineage(root, self.resolve_commit(root, a)) if n in ancestors), "")
        elif len(spans) == 2:
            a, b = spans
        else:
            return None
        left, right = self._revision(root, a), self._revision(root, b)
        if left is None or right is None:
            return None
        return sorted(p for p in left.keys() | right.keys() if left.get(p) != right.get(p))

    def commit_parents(self, root: Path, ref: str, *, until: float | None = None) -> list[str] | None:
        _deadline(until)
        name = self.resolve_commit(root, ref)
        return None if name is None else self._state(root)["parents"].get(name, []).copy()

    def head_text(self, root: Path, path: str, revision: str = "HEAD", *, until: float | None = None, errors: str = "replace", timeout: int = 30) -> str | None:
        _deadline(until)
        blob = (self._revision(root, revision) or {}).get(path)
        return None if blob is None else blob.decode("utf-8", errors).replace("\r\n", "\n").replace("\r", "\n")

    def index_bytes(self, root: Path, path: str, index_file: Path | None = None, *, limit: int | None = None) -> bytes:
        blob = (self._index(root, index_file) or {}).get(path)
        if limit is not None and limit < 0:
            raise ValueError("negative byte limit")
        if blob is None and limit is None:
            raise subprocess.CalledProcessError(128, ["git", "show", ":" + path])
        return (blob or b"")[:limit]

    def index_text(self, root: Path, path: str, index_file: Path | None = None) -> str | None:
        blob = (self._index(root, index_file) or {}).get(path)
        return None if blob is None else blob.decode("utf-8", "replace")

    def commit_bytes(self, root: Path, revision: str, path: str, *, until: float | None = None) -> bytes | None:
        _deadline(until)
        return (self._revision(root, revision) or {}).get(path)

    def commit_head(self, root: Path, *, until: float | None = None) -> str | None:
        _deadline(until)
        return (self.resolve_commit(root, "HEAD") or "") if self._state(root) else None

    def head_tips(self, root: Path, *, until: float | None = None) -> tuple[str, dict[str, str]] | None:
        head = self.commit_head(root, until=until)
        tips = self.ref_tips(root, until=until) if head is not None else None
        return None if head is None or tips is None else (head, tips)

    def ref_tips(self, root: Path, *, until: float | None = None) -> dict[str, str] | None:
        _deadline(until)
        refs = self._state(root).get("refs", {}) if self._state(root) else None
        return None if refs is None else {name: commit for name, commit in refs.items() if name.startswith("refs/")}

    def new_commits(self, root: Path, tips: Sequence[str], known: Sequence[str], *, since: int, limit: int,
                    until: float | None = None) -> list[str] | None:
        _deadline(until)
        seen = {rev for name in known for rev in self._lineage(root, name)}
        times = self._state(root).get("times", {})
        fresh = (rev for name in tips for rev in self._lineage(root, name) if rev not in seen)
        return list(islice(dict.fromkeys(rev for rev in fresh if times.get(rev, since) >= since), limit))

    def commit_reachable(self, root: Path, revision: str, *, until: float | None = None) -> bool | None:
        _deadline(until)
        heads = [name for name in self._state(root).get("refs", {}) if name.startswith("refs/heads/")]
        return any(self.is_ancestor(root, revision, name) for name in (*heads, "HEAD"))

    def commit_message(self, root: Path, revision: str, *, until: float | None = None) -> str | None:
        _deadline(until)
        name = self.resolve_commit(root, revision)
        return self._state(root)["messages"].get(name, "") if name else None

    def commit_paths(self, root: Path, revision: str, *, until: float | None = None) -> list[str] | None:
        _deadline(until)
        tree = self._revision(root, revision)
        prior = self._revision(root, revision + "^") or {}
        return sorted(p for p in tree if tree[p] != prior.get(p)) if tree is not None else None

    def commit_removed_paths(self, root: Path, revision: str, *, until: float | None = None) -> list[str] | None:
        _deadline(until)
        tree = self._revision(root, revision)
        prior = self._revision(root, revision + "^") or {}
        return sorted(path for path in prior if path not in tree) if tree is not None else None

    def commit_time(self, root: Path, commit: str) -> int | None:
        value = self._state(root).get("times", {}).get(self.resolve_commit(root, commit))
        if value is not None and type(value) is not int:
            raise ValueError("Expected integer commit time")
        return value

    def resolve_commit(self, root: Path, spec: str) -> str | None:
        state = self._state(root)
        name = spec.rstrip("^")
        commit = state.get("refs", {}).get(name, name)
        for _ in range(len(spec) - len(name)):
            parents = state.get("parents", {}).get(commit, [])
            if not parents:
                return None
            commit = parents[0]
        return commit if commit in state.get("revisions", {}) or commit in state.get("parents", {}) else None

    def ref_heads(self, root: Path) -> dict[str, str] | None:
        return {name: commit for name, commit in self._state(root).get("refs", {}).items() if name.startswith("refs/heads/")}

    def refs_containing(self, root: Path, commit: str, refs: Sequence[str]) -> list[str]:
        names = sorted(self._state(root).get("refs", {}))
        return [n for n in names if n.startswith("refs/") and (not refs or any(_matches(n, s) for s in refs)) and self.is_ancestor(root, commit, n)][:1]

    def is_ancestor(self, root: Path, commit: str, descendant: str = "HEAD") -> bool:
        target = self.resolve_commit(root, commit)
        return target is not None and target in self._lineage(root, self.resolve_commit(root, descendant))

    def config_value(self, root: Path, key: str, *, flags: Sequence[str] = (), env: Mapping[str, str] | None = None) -> str | None:
        options = _FlagWords(flags)
        overrides = {}
        git_dir = work_tree = None
        for word in options:
            if word == "-C" or word.startswith("-C"):
                root = root / (options.value() if word == "-C" else word[2:])
            elif word.startswith("-c") or word.startswith("--config-env"):
                indirect = word.startswith("--config-env")
                item = options.value() if word in {"-c", "--config-env"} else word.removeprefix("--config-env=") if indirect else word[2:]
                name, separator, value = item.partition("=")
                overrides[name.lower()] = (env or {}).get(value) if indirect else value if separator else "true"
            elif word.partition("=")[0] in {"--namespace", "--git-dir", "--work-tree"}:
                option, separator, value = word.partition("=")
                value = value if separator else options.value()
                if option == "--git-dir":
                    git_dir = value
                elif option == "--work-tree":
                    work_tree = value
            elif word not in {"-p", "--paginate", "--bare", "--no-pager", "--no-optional-locks", "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs", "--literal-pathspecs", "--no-replace-objects"} and not word.startswith("--exec-path="):
                raise ValueError("Unsupported fixture config flag")
        if git_dir is not None:
            directory = root / git_dir
            root = directory.parent if directory.name == ".git" else directory
        elif work_tree is not None and self.toplevel(root) is None:
            root = root / work_tree
        entries = {name.lower(): value for name, value in self._state(root).get("config", {}).items()}
        return overrides.get(key.lower(), entries.get(key.lower()))

    def config_entries(self, root: Path, pattern: str, *, file: str | None = None) -> list[str] | None:
        state = self._state(root)
        entries = state.get("config") if file is None else state.get("config_files", {}).get(file)
        if entries is None:
            return None
        return [f"{k}\n{_text(v)}" for k, v in _map(entries).items() if re.search(pattern, k)] or None

    def grep_tracked(self, root: Path, needle: str) -> list[str]:
        return sorted(p for p, b in (self._scoped(root, self._revision(root, "HEAD")) or {}).items() if needle.encode() in b)

    def grep_index(self, root: Path, needle: str, index_file: Path | None = None) -> list[str]:
        index = self._scoped(root, self._listed_index(root, index_file))
        return sorted(p for p, b in index.items() if b"\0" not in b and needle.encode() in b)
