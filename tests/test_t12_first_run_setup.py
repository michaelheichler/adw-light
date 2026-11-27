import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from t12_wheelhouse import make_wheelhouse

TREE = Path(__file__).resolve().parents[1]
ENTRY = TREE / "adw_light.py"
SETUP = TREE / "engine" / "setup_env.py"
SCRUBBED_ENV = ("CLAUDE_PLUGIN_ROOT", "ADW_STATE_ROOT", "ADW_CODEX_HOOK", "ADW_PYTHON", "ADW_ALLOW_PROTECTED_EDIT")
FAKE_PYTHON = '#!/bin/sh\ncase "$*" in *version_info*) echo "{major} {minor}"; exit 0;; esac\nexit 1\n'
VENV_PYTHON = """#!/bin/sh
case "$*" in
  *version_info*) echo "3 12"; exit 0;;
  "-m venv --clear "*) mkdir -p "$4/bin"; cp "{inner}" "$4/bin/python"; exit 0;;
esac
exit 1
"""
LEFTOVER_PIP = """#!/bin/sh
staging=$(cd "$(dirname "$0")/.." && pwd)
target="${staging%.staging-*}"
mkdir -p "$target" "$target.old-$PPID/leftover"
exit 0
"""
LOGGED_MAIN = """import sys
sys.path.insert(0, sys.argv[1])
from lib.first_run import main
code = main(sys.argv[2:])
print("after-out", flush=True)
print("after-err", file=sys.stderr, flush=True)
sys.exit(code)
"""
LOCK_TAKER = """#!/bin/sh
printf '1' > "{lock}"
case "$*" in *version_info*) echo "3 10"; exit 0;; esac
exit 1
"""


def make_fake_python(folder, name, version):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(FAKE_PYTHON.format(major=version[0], minor=version[1]), encoding="utf-8")
    path.chmod(0o755)
    return path


def make_script(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def make_project(root, files):
    for relative in files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    return root


def offline_env(home, wheelhouse, **extra):
    env = {key: value for key, value in os.environ.items()
           if key not in SCRUBBED_ENV and not key.startswith("PIP_")}
    return {**env, "HOME": str(home), "ADW_STATE_ROOT": str(home / "state"), "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": str(wheelhouse), "PIP_NO_CACHE_DIR": "1", "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1", **extra}


def run_setup(env, *projects, languages=()):
    arguments = [part for project in projects for part in ("--project", str(project))]
    arguments += [part for language in languages for part in ("--language", language)]
    return subprocess.run([sys.executable, "-I", "-S", "-B", str(SETUP), *arguments], capture_output=True,
                          text=True, check=False, env=env, timeout=600, stdin=subprocess.DEVNULL)


def session_start(env, project):
    payload = {"session_id": "t12-setup", "cwd": str(project), "hook_event_name": "SessionStart",
               "source": "startup"}
    return subprocess.run([sys.executable, "-ISB", str(ENTRY), "SessionStart", "--host", "claude",
                           "--state-dir", env["ADW_STATE_ROOT"]], input=json.dumps(payload),
                          capture_output=True, text=True, check=False, cwd=str(project), env=env, timeout=60)


def install_dir(home):
    return home / ".adw-lite" / "install"


def setup_record(home):
    return json.loads((install_dir(home) / "setup.json").read_text(encoding="utf-8"))


def setup_rows(home):
    ledger = home / "state" / "ledger" / "ledger.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    rows = (json.loads(line) for line in lines if line)
    return [row for row in rows if row.get("hook") == "first_run_setup"]


def downloads(home):
    log = home / ".adw-lite" / "cache" / "grammars" / "downloads.log"
    return log.read_text(encoding="utf-8").split() if log.exists() else []


def wait_for_setup_row(home, seconds=300):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not setup_rows(home):
        time.sleep(0.05)
    return setup_rows(home)


class Scratch(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.home = self.scratch / "home"
        self.home.mkdir()
        self.project = self.scratch / "project"


class GrammarSelectionTests(Scratch):
    def test_a_python_and_go_project_fetches_only_the_go_grammar(self):
        make_project(self.project, ["a.py", "main.go"])
        env = offline_env(self.home, make_wheelhouse(self.scratch), ADW_PYTHON=sys.executable)
        result = run_setup(env, self.project)
        record = setup_record(self.home)
        self.assertEqual((result.returncode, record["status"], record["build"], record["requested"],
                          record["fetched"], record["grammars"], downloads(self.home)),
                         (0, "ready", "wheel", ["go"], ["go"], ["go"], ["go"]))

    def test_a_ready_setup_writes_one_ledger_row_with_its_outcome(self):
        make_project(self.project, ["a.py", "main.go"])
        run_setup(offline_env(self.home, make_wheelhouse(self.scratch), ADW_PYTHON=sys.executable), self.project)
        rows = setup_rows(self.home)
        self.assertEqual([(row["event"], row["status"], row["build"], row["requested"], row["fetched"])
                          for row in rows], [("setup", "ready", "wheel", ["go"], ["go"])])

    def test_vendored_and_hidden_folders_add_no_grammar(self):
        make_project(self.project, ["main.go", "node_modules/a.js", "vendor/b.rb", ".hidden/c.java",
                                    "build/d.kt", "dist/e.php", "target/f.rs", "venv/g.swift"])
        run_setup(offline_env(self.home, make_wheelhouse(self.scratch), ADW_PYTHON=sys.executable), self.project)
        self.assertEqual(setup_record(self.home)["requested"], ["go"])

    def test_a_new_language_in_a_ready_setup_fetches_only_that_grammar(self):
        make_project(self.project, ["a.py", "main.go"])
        env = offline_env(self.home, make_wheelhouse(self.scratch), ADW_PYTHON=sys.executable)
        run_setup(env, self.project)
        make_project(self.project, ["lib.rs"])
        run_setup(env, self.project)
        record = setup_record(self.home)
        self.assertEqual((record["requested"], record["fetched"], record["grammars"], downloads(self.home)),
                         (["rust"], ["rust"], ["go", "rust"], ["go", "rust"]))


class NamedLanguageTests(Scratch):
    def test_a_named_language_without_a_project_fetches_its_grammar(self):
        env = offline_env(self.home, make_wheelhouse(self.scratch), ADW_PYTHON=sys.executable)
        result = run_setup(env, languages=("go",))
        record = setup_record(self.home)
        self.assertEqual((result.returncode, record["status"], record["requested"], record["fetched"],
                          record["grammars"], downloads(self.home)), (0, "ready", ["go"], ["go"], ["go"], ["go"]))

    def test_a_named_language_without_a_grammar_is_never_requested(self):
        make_project(self.project, ["main.go"])
        env = offline_env(self.home, make_wheelhouse(self.scratch), ADW_PYTHON=sys.executable)
        run_setup(env, self.project, languages=("cobol", "rust"))
        record = setup_record(self.home)
        self.assertEqual((record["requested"], downloads(self.home)), (["go", "rust"], ["go", "rust"]))


class ParserBuildTests(Scratch):
    def test_a_parser_with_only_a_source_release_is_built_from_source(self):
        make_project(self.project, ["main.go"])
        env = offline_env(self.home, make_wheelhouse(self.scratch, core="sdist"), ADW_PYTHON=sys.executable)
        result = run_setup(env, self.project)
        record = setup_record(self.home)
        self.assertEqual((result.returncode, record["status"], record["build"], record["grammars"]),
                         (0, "ready", "source", ["go"]))

    def test_a_parser_with_no_release_at_all_fails_and_leaves_no_environment(self):
        make_project(self.project, ["main.go"])
        env = offline_env(self.home, make_wheelhouse(self.scratch, core="none"), ADW_PYTHON=sys.executable)
        result = run_setup(env, self.project)
        self.assertEqual((result.returncode, setup_record(self.home)["status"], setup_record(self.home)["reason"],
                          (install_dir(self.home) / "venv").exists()), (1, "failed", "install_failed", False))


class InterpreterChoiceTests(Scratch):
    def test_the_newest_python_on_path_is_chosen(self):
        fakes = self.scratch / "bin"
        make_fake_python(fakes, "python3.19", (3, 19))
        newest = make_fake_python(fakes, "python3.20", (3, 20))
        env = offline_env(self.home, make_wheelhouse(self.scratch), PATH=f"{fakes}:/usr/bin:/bin")
        result = run_setup(env, make_project(self.project, ["main.go"]))
        record = setup_record(self.home)
        self.assertEqual((result.returncode, record["reason"], record["version"], record["python"]),
                         (1, "install_failed", "3.20", str(newest)))

    def test_a_python_override_below_the_floor_reports_no_python(self):
        old = make_fake_python(self.scratch / "bin", "python3.10", (3, 10))
        env = offline_env(self.home, make_wheelhouse(self.scratch), ADW_PYTHON=str(old))
        result = run_setup(env, make_project(self.project, ["main.go"]))
        self.assertEqual((result.returncode, setup_record(self.home)["status"], setup_record(self.home)["reason"],
                          [row["status"] for row in setup_rows(self.home)]), (1, "failed", "no_python", ["failed"]))


class SetupLockTests(Scratch):
    def old_python_env(self):
        old = make_fake_python(self.scratch / "bin", "python3.10", (3, 10))
        return offline_env(self.home, make_wheelhouse(self.scratch), ADW_PYTHON=str(old))

    def place_lock(self, age_seconds, owner="1\n"):
        lock = install_dir(self.home) / "setup.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text(owner, encoding="utf-8")
        stamp = time.time() - age_seconds
        os.utime(lock, (stamp, stamp))

    def setup_outcome(self, env):
        result = run_setup(env, make_project(self.project, ["main.go"]))
        record = install_dir(self.home) / "setup.json"
        return result.returncode, json.loads(record.read_text(encoding="utf-8")).get("reason") if record.exists() else None

    def test_a_running_setup_makes_a_second_setup_exit_without_a_record(self):
        self.place_lock(0)
        result = run_setup(self.old_python_env(), make_project(self.project, ["main.go"]))
        self.assertEqual((result.returncode, (install_dir(self.home) / "setup.json").exists()), (0, False))

    def test_setup_with_a_log_appends_its_own_and_every_later_stdout_and_stderr_line_to_that_log(self):
        self.place_lock(0, owner="busy\n")
        log = self.scratch / "setup.log"
        log.write_text("earlier run\n", encoding="utf-8")
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", LOGGED_MAIN, str(SETUP.parent), "--log", str(log)],
                                capture_output=True, text=True, check=False, timeout=60,
                                env=offline_env(self.home, self.scratch), stdin=subprocess.DEVNULL)
        lines = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual((result.returncode, result.stdout, result.stderr, lines[0], lines[2:], len(lines)),
                         (0, "", "", "earlier run", ["after-out", "after-err"], 4))

    def test_a_lock_older_than_an_hour_no_longer_stops_setup(self):
        self.place_lock(7200)
        result = run_setup(self.old_python_env(), make_project(self.project, ["main.go"]))
        self.assertEqual((result.returncode, setup_record(self.home)["reason"]), (1, "no_python"))

    def test_a_fresh_lock_whose_owner_has_exited_no_longer_stops_setup(self):
        self.place_lock(0, owner=f"{dead_pid()}\n")
        self.assertEqual(self.setup_outcome(self.old_python_env()), (1, "no_python"))

    def test_a_fresh_empty_lock_stops_setup(self):
        self.place_lock(0, owner="")
        self.assertEqual(self.setup_outcome(self.old_python_env()), (0, None))

    def test_a_fresh_lock_with_no_process_number_stops_setup(self):
        self.place_lock(0, owner="busy\n")
        self.assertEqual(self.setup_outcome(self.old_python_env()), (0, None))

    def test_an_empty_lock_older_than_a_minute_no_longer_stops_setup(self):
        self.place_lock(120, owner="")
        self.assertEqual(self.setup_outcome(self.old_python_env()), (1, "no_python"))

    def test_a_lock_with_no_process_number_older_than_a_minute_no_longer_stops_setup(self):
        self.place_lock(120, owner="busy\n")
        self.assertEqual(self.setup_outcome(self.old_python_env()), (1, "no_python"))

    def test_a_setup_keeps_a_lock_that_another_process_took_over_during_the_run(self):
        lock = install_dir(self.home) / "setup.lock"
        taker = make_script(self.scratch / "bin" / "python3.10", LOCK_TAKER.format(lock=lock))
        env = offline_env(self.home, make_wheelhouse(self.scratch), ADW_PYTHON=str(taker))
        outcome = self.setup_outcome(env)
        self.assertEqual((outcome, lock.read_text(encoding="utf-8")), ((1, "no_python"), "1"))


class SetupErrorTests(Scratch):
    def failing_swap_env(self):
        inner = make_script(self.scratch / "bin" / "leftover-pip", LEFTOVER_PIP)
        python = make_script(self.scratch / "bin" / "python3.12", VENV_PYTHON.format(inner=inner))
        return offline_env(self.home, make_wheelhouse(self.scratch), CLAUDE_PLUGIN_ROOT=str(TREE),
                           ADW_PYTHON=str(python))

    def test_an_error_during_setup_saves_a_failed_record_and_leaves_no_staging_or_lock(self):
        result = run_setup(self.failing_swap_env(), make_project(self.project, ["main.go"]))
        record = setup_record(self.home)
        leftovers = sorted(path.name for path in install_dir(self.home).iterdir()
                           if path.name.startswith("venv.staging-") or path.name == "setup.lock")
        self.assertEqual((result.returncode, record["status"], record["reason"], leftovers,
                          [(row["status"], row["reason"]) for row in setup_rows(self.home)]),
                         (1, "failed", "setup_error", [], [("failed", "setup_error")]))

    def test_a_session_start_within_an_hour_of_a_setup_error_starts_no_setup(self):
        env = self.failing_swap_env()
        run_setup(env, make_project(self.project, ["main.go"]))
        result = session_start(env, self.project)
        self.assertEqual((result.returncode, (install_dir(self.home) / "setup.log").exists()), (0, False))


class SessionStartTests(Scratch):
    def plugin_env(self, wheelhouse, **extra):
        return offline_env(self.home, wheelhouse, CLAUDE_PLUGIN_ROOT=str(TREE), **extra)

    def test_a_plugin_session_start_runs_setup_in_the_background(self):
        make_project(self.project, ["a.py", "main.go"])
        result = session_start(self.plugin_env(make_wheelhouse(self.scratch), ADW_PYTHON=sys.executable),
                               self.project)
        rows = wait_for_setup_row(self.home)
        self.assertEqual((result.returncode, type(json.loads(result.stdout)),
                          [(row["status"], row["requested"]) for row in rows]), (0, dict, [("ready", ["go"])]))

    def test_a_session_start_outside_the_plugin_layer_starts_no_setup(self):
        make_project(self.project, ["main.go"])
        env = offline_env(self.home, make_wheelhouse(self.scratch), ADW_PYTHON=sys.executable)
        result = session_start(env, self.project)
        self.assertEqual((result.returncode, install_dir(self.home).exists()), (0, False))

    def test_a_session_start_with_every_grammar_present_starts_no_setup(self):
        make_project(self.project, ["a.py", "main.go"])
        env = self.plugin_env(make_wheelhouse(self.scratch), ADW_PYTHON=sys.executable)
        run_setup(env, self.project)
        result = session_start(env, self.project)
        self.assertEqual((result.returncode, (install_dir(self.home) / "setup.log").exists()), (0, False))

    def test_a_session_start_within_an_hour_of_a_failed_setup_starts_no_setup(self):
        old = make_fake_python(self.scratch / "bin", "python3.10", (3, 10))
        env = self.plugin_env(make_wheelhouse(self.scratch), ADW_PYTHON=str(old))
        run_setup(env, make_project(self.project, ["main.go"]))
        result = session_start(env, self.project)
        self.assertEqual((result.returncode, (install_dir(self.home) / "setup.log").exists()), (0, False))


class MalformedSetupRecordTests(Scratch):
    @classmethod
    def setUpClass(cls):
        shared = tempfile.TemporaryDirectory()
        cls.addClassCleanup(shared.cleanup)
        root = Path(shared.name).resolve()
        ready_home = root / "ready-home"
        ready_home.mkdir()
        cls.wheelhouse = make_wheelhouse(root)
        run_setup(offline_env(ready_home, cls.wheelhouse, ADW_PYTHON=sys.executable),
                  make_project(root / "project", ["a.py", "main.go"]))
        cls.ready = setup_record(ready_home)
        cls.venv = install_dir(ready_home) / "venv"

    def ready_setup(self):
        make_project(self.project, ["a.py", "main.go"])
        install_dir(self.home).mkdir(parents=True)
        (install_dir(self.home) / "venv").symlink_to(self.venv, target_is_directory=True)
        env = offline_env(self.home, self.wheelhouse, CLAUDE_PLUGIN_ROOT=str(TREE),
                          ADW_PYTHON=str(self.scratch / "missing-python"))
        return self.ready, env

    def seed(self, record):
        (install_dir(self.home) / "setup.json").write_text(json.dumps(record), encoding="utf-8")

    def assert_treated_as_missing(self, damage):
        ready, env = self.ready_setup()
        self.seed(damage(ready))
        result = session_start(env, self.project)
        rows = wait_for_setup_row(self.home, seconds=60)
        self.assertEqual((result.returncode, type(json.loads(result.stdout)),
                          [(row["status"], row["reason"]) for row in rows]), (0, dict, [("failed", "no_python")]))

    def test_a_ready_record_with_a_number_for_grammars_reruns_setup(self):
        """Build fixtures with factory functions."""
        self.assert_treated_as_missing(lambda ready: {**ready, "grammars": 5})

    def test_a_ready_record_with_a_number_for_requested_reruns_setup(self):
        """Build fixtures with factory functions."""
        self.assert_treated_as_missing(lambda ready: {**ready, "requested": 5})

    def test_a_ready_record_with_a_bool_timestamp_reruns_setup(self):
        """Build fixtures with factory functions."""
        self.assert_treated_as_missing(lambda ready: {**ready, "ts": True})

    def test_a_ready_record_with_a_text_timestamp_reruns_setup(self):
        """Build fixtures with factory functions."""
        self.assert_treated_as_missing(lambda ready: {**ready, "ts": "yesterday"})

    def test_a_ready_record_with_a_list_for_pins_reruns_setup(self):
        """Build fixtures with factory functions."""
        self.assert_treated_as_missing(lambda ready: {**ready, "pins": [1]})

    def test_a_ready_record_with_a_number_for_version_reruns_setup(self):
        """Build fixtures with factory functions."""
        self.assert_treated_as_missing(lambda ready: {**ready, "version": 3.12})

    def test_a_ready_record_without_a_version_reruns_setup(self):
        """Build fixtures with factory functions."""
        self.assert_treated_as_missing(lambda ready: {key: value for key, value in ready.items() if key != "version"})

    def test_a_record_that_is_a_json_list_reruns_setup(self):
        """Build fixtures with factory functions."""
        self.assert_treated_as_missing(lambda ready: [ready])

    def test_a_record_that_is_a_json_string_reruns_setup(self):
        """Build fixtures with factory functions."""
        self.assert_treated_as_missing(lambda ready: "ready")

    def test_a_well_formed_ready_record_starts_no_setup(self):
        """Build fixtures with factory functions."""
        ready, env = self.ready_setup()
        self.seed(ready)
        result = session_start(env, self.project)
        self.assertEqual((result.returncode, type(json.loads(result.stdout)),
                          (install_dir(self.home) / "setup.log").exists()), (0, dict, False))


class HookInterpreterTests(Scratch):
    def resolve(self):
        env = {key: value for key, value in os.environ.items() if key not in SCRUBBED_ENV}
        return subprocess.run(["/bin/sh", str(TREE / "hooks" / "resolve-python.sh"), "3.11"], capture_output=True,
                              text=True, check=False, env={**env, "HOME": str(self.home)}, timeout=60)

    def test_hooks_prefer_the_setup_environment_python(self):
        venv_python = install_dir(self.home) / "venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(sys.executable)
        result = self.resolve()
        self.assertEqual((result.returncode, result.stdout.strip()), (0, str(venv_python)))

    def test_hooks_fall_back_when_the_setup_environment_python_does_not_run(self):
        broken = install_dir(self.home) / "venv" / "bin" / "python"
        broken.parent.mkdir(parents=True)
        broken.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        broken.chmod(0o755)
        result = self.resolve()
        self.assertEqual((result.returncode, result.stdout.strip() == str(broken)), (0, False))


if __name__ == "__main__":
    unittest.main()
