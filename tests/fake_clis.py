import json
import os
import shlex
import shutil
import sys
from pathlib import Path

CLI_NAMES = ("claude", "codex", "ssh", "git")
SCRIPTED_NAMES = ("claude", "codex", "ssh")
CALL_LOG = "fake-cli-calls.jsonl"
FAILURE_PLAN = "fake-cli-failures.json"
GITHUB_PREFIXES = ("https://github.com/", "git@github.com:", "ssh://git@github.com/")
REMOTE_HOME = "remote home"
FAKE_GIT_SCRIPT = """#!/bin/sh
case " $* " in
  *" remote get-url "*) exec {real} "$@" ;;
esac
exec {real} {redirects} "$@"
"""
FAKE_CLI_SCRIPT = """import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
GITHUB = Path(@GITHUB@)
REAL_GIT = @REAL_GIT@
REMOTE_HOME = HOME / @REMOTE_HOME@
NAME = Path(sys.argv[0]).name
CALL = [NAME, *sys.argv[1:]]
VALUED = ("--scope", "--ref")
REMOTE_ENV = ("PATH", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE", "GIT_CONFIG_NOSYSTEM", "ADW_PYTHON")
SSH_VALUED = ("-o", "-p", "-i", "-l", "-F", "-J")


def load(path, empty):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else empty


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")


def refuse(reason):
    print("fake " + NAME + ": " + reason, file=sys.stderr)
    sys.exit(2)


def parsed(args):
    words, options, rest = [], {}, iter(args)
    for word in rest:
        if word in VALUED:
            options[word] = next(rest, "")
        elif word.startswith("--"):
            options[word] = True
        else:
            words.append(word)
    return words, options


def manifest_name(folder):
    manifest = Path(folder) / ".claude-plugin" / "marketplace.json"
    if not manifest.is_file():
        refuse("no marketplace manifest in " + str(folder))
    return json.loads(manifest.read_text(encoding="utf-8"))["name"]


def tagged_tree(slug, tag, markets_root):
    bare = GITHUB / (slug + ".git")
    if not tag or not bare.is_dir():
        refuse("no GitHub repository " + slug + " at " + repr(tag))
    archive = subprocess.run([REAL_GIT, "--git-dir", str(bare), "archive", "refs/tags/" + tag],
                             capture_output=True, check=False)
    if archive.returncode:
        refuse("no tag " + tag + " in " + slug)
    unpacked = markets_root / (".unpack-" + str(os.getpid()))
    shutil.rmtree(unpacked, ignore_errors=True)
    unpacked.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(unpacked)
    return unpacked


def added(markets, source, tag, markets_root):
    if tag is None and Path(source).is_absolute():
        name = manifest_name(source)
        entry = {"name": name, "source": "directory", "path": source}
    else:
        slug, _, ref = source.partition("#")
        tree = tagged_tree(slug, tag if tag is not None else ref, markets_root)
        name = manifest_name(tree)
        target = markets_root / name
        shutil.rmtree(target, ignore_errors=True)
        tree.rename(target)
        entry = {"name": name, "source": "github", "repo": slug, "ref": tag if tag is not None else ref,
                 "path": str(target)}
    if name in markets:
        refuse("marketplace " + name + " is already added")
    return {**markets, name: entry}


def removed(markets, name):
    entry = markets.get(name) or refuse("no marketplace " + name)
    if entry.get("source") == "github":
        shutil.rmtree(entry["path"], ignore_errors=True)
    return {key: value for key, value in markets.items() if key != name}


def cached_copy(root, plugin_id, markets):
    plugin, _, market = plugin_id.partition("@")
    if market not in markets:
        refuse("unknown marketplace " + market)
    source = Path(markets[market]["path"])
    version = json.loads((source / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version", "unknown")
    target = root / "plugins" / "cache" / market / plugin / version
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    return str(target)


def known(table, key):
    if key not in table:
        refuse("nothing named " + key)
    return {name: value for name, value in table.items() if name != key}


def claude(args):
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    markets_file, installs_file = root / "plugins" / "known_marketplaces.json", root / "plugins" / "installed_plugins.json"
    markets, installs = load(markets_file, {}), load(installs_file, {})
    match parsed(args)[0]:
        case ["plugin", "marketplace", "list"]:
            print(json.dumps(list(markets.values())))
        case ["plugin", "marketplace", "add", source]:
            save(markets_file, added(markets, source, None, root / "plugins" / "marketplaces"))
        case ["plugin", "marketplace", "remove", name]:
            save(markets_file, removed(markets, name))
        case ["plugin", "install", plugin_id]:
            save(installs_file, {**installs, plugin_id: cached_copy(root, plugin_id, markets)})
        case ["plugin", "uninstall", plugin_id]:
            save(installs_file, known(installs, plugin_id))
        case ["plugin", "list"]:
            print(json.dumps([{"id": plugin_id, "scope": "user", "enabled": True, "installPath": path}
                              for plugin_id, path in installs.items()]))
        case _:
            refuse("unknown command")


def codex(args):
    root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    markets_file, installs_file = root / "fake-codex-marketplaces.json", root / "fake-codex-plugins.json"
    markets, installs = load(markets_file, {}), load(installs_file, {})
    words, options = parsed(args)
    match words:
        case ["plugin", "marketplace", "list"]:
            print(json.dumps({"marketplaces": list(markets.values())}))
        case ["plugin", "marketplace", "add", source]:
            table = added(markets, source, options.get("--ref"), root / "plugins" / "marketplaces")
            save(markets_file, table)
            print(json.dumps(table[[name for name in table if name not in markets][0]]))
        case ["plugin", "marketplace", "remove", name]:
            save(markets_file, removed(markets, name))
        case ["plugin", "add", plugin_id]:
            path = cached_copy(root, plugin_id, markets)
            save(installs_file, {**installs, plugin_id: path})
            print(json.dumps({"installedPath": path}))
        case ["plugin", "remove", plugin_id]:
            save(installs_file, known(installs, plugin_id))
        case _:
            refuse("unknown command")


def ssh(args):
    rest = list(args)
    while rest and rest[0].startswith("-"):
        if rest.pop(0) in SSH_VALUED and rest:
            rest.pop(0)
    if len(rest) < 2:
        refuse("expected a host and a command")
    (REMOTE_HOME / "tmp").mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key in REMOTE_ENV or key.startswith("PIP_")}
    env.update(HOME=str(REMOTE_HOME), TMPDIR=str(REMOTE_HOME / "tmp"))
    sys.exit(subprocess.run(["/bin/sh", "-c", " ".join(rest[1:])], env=env, cwd=str(REMOTE_HOME), check=False).returncode)


with open(HOME / @LOG@, "a", encoding="utf-8") as log:
    log.write(json.dumps(CALL) + "\\n")
if any(CALL[:len(rule)] == rule for rule in load(HOME / @PLAN@, [])):
    refuse("planned failure")
{"claude": claude, "codex": codex, "ssh": ssh}[NAME](sys.argv[1:])
"""


class RealCliReachable(AssertionError):
    pass


def real_git(fake_bin):
    search = os.pathsep.join(part for part in os.environ.get("PATH", os.defpath).split(os.pathsep)
                             if part and Path(part).resolve() != fake_bin.resolve())
    found = shutil.which("git", path=search)
    if found is None:
        raise RealCliReachable("No git outside the fake folder backs the stand-in GitHub.")
    return found


class FakeClis:
    def __init__(self, root, github=None):
        self.root = Path(root)
        self.bin = self.root / "bin"
        self.log = self.root / CALL_LOG
        self.plan = self.root / FAILURE_PLAN
        self.github = Path(github) if github else self.root / "github"
        self.remote_home = self.root / REMOTE_HOME
        self.bin.mkdir(parents=True)
        self.github.mkdir(parents=True, exist_ok=True)
        git = real_git(self.bin)
        tokens = {"@GITHUB@": str(self.github), "@REAL_GIT@": git, "@REMOTE_HOME@": REMOTE_HOME, "@LOG@": CALL_LOG,
                  "@PLAN@": FAILURE_PLAN}
        script = "#!" + sys.executable + " -I\n" + FAKE_CLI_SCRIPT
        for token, value in tokens.items():
            script = script.replace(token, repr(value))
        for name in SCRIPTED_NAMES:
            self.install(name, script)
        redirects = " ".join("-c " + shlex.quote(f"url.file://{self.github}/.insteadOf={prefix}")
                             for prefix in GITHUB_PREFIXES)
        self.install("git", FAKE_GIT_SCRIPT.format(real=shlex.quote(git), redirects=redirects))

    def install(self, name, text):
        (self.bin / name).write_text(text, encoding="utf-8")
        (self.bin / name).chmod(0o755)

    def search_path(self, *first):
        return os.pathsep.join([*map(str, first), str(self.bin), os.environ.get("PATH", os.defpath)])

    def require_on(self, env):
        resolved = {name: shutil.which(name, path=env.get("PATH", "")) for name in CLI_NAMES}
        escaped = {name: path for name, path in resolved.items()
                   if path is None or Path(path).resolve() != (self.bin / name).resolve()}
        if escaped:
            raise RealCliReachable(f"The subprocess PATH resolves {escaped} instead of the fakes in {self.bin}.")
        return env

    def fail_on(self, *prefix):
        planned = json.loads(self.plan.read_text(encoding="utf-8")) if self.plan.exists() else []
        self.plan.write_text(json.dumps([*planned, list(prefix)]), encoding="utf-8")

    def forget_calls(self):
        self.log.unlink(missing_ok=True)

    def calls(self, name):
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []
        return [row for row in rows if row[0] == name]

    def github_repo(self, slug):
        return self.github / f"{slug}.git"

    def remote_config(self, layer):
        return self.remote_home / {"claude": ".claude", "codex": ".codex"}[layer]
