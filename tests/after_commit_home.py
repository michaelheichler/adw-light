import os
from itertools import count
from pathlib import Path

from after_run_home import CLAUDE, AfterRunCase, Project, project_files
from fake_repo_home import BASE, Repo, repo_state
from lib.git_port import FakeGitView

CLEAN_MESSAGE = "Add notes.\n"
FLAGGED_MESSAGE = "We utilize this tool for the build.\n"
CLEAN_TEXT = "Short note.\n"
FLAGGED_TEXT = "We utilize this tool for the build.\n"
NOTES = "notes.md"


class ResolvingGitView(FakeGitView):
    def toplevel(self, directory, **options):
        return super().toplevel(Path(os.path.realpath(directory)), **options)


class Commit:
    def __init__(self, name, tree, parent, message, time=None):
        self.name, self.tree, self.parent, self.message, self.time = name, tree, parent, message, time


def merged(tree, files):
    return {path: content for path, content in {**tree, **(files or {})}.items() if content is not None}


class AfterCommitCase(AfterRunCase):
    projects = {}
    links = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for link, target in cls.links.items():
            (cls.root / link).symlink_to(cls.root / target)
        cls.numbers = count()

    def history_of(self, name):
        return self.__dict__.setdefault("_histories", {}).setdefault(name, [])

    def tip(self, name):
        commits = self.history_of(name)
        return commits[-1] if commits else Commit(BASE, dict(self.projects[name].files), None, "")

    def make_commit(self, name, files=None, message=CLEAN_MESSAGE, amend=False, time=None):
        tip = self.tip(name)
        parent = tip.parent if amend else tip.name
        base = self.tip_tree_before(name) if amend else tip.tree
        self.write_files(name, files)
        commit = Commit(f"{name}-commit-{next(self.numbers)}", merged(base, files), parent, message, time)
        if amend:
            self.history_of(name).pop()
        self.history_of(name).append(commit)
        return commit.name

    def write_files(self, name, files):
        for relative, content in (files or {}).items():
            if content is None:
                self.remove(name, relative)
            else:
                self.change(name, relative, content)

    def sides_of(self, name):
        return self.__dict__.setdefault("_sides", {}).setdefault(name, {})

    def pointers_of(self, name):
        return self.__dict__.setdefault("_pointers", {}).setdefault(name, {})

    def branch_commit(self, name, ref, files=None, message=CLEAN_MESSAGE, time=None):
        chain = self.sides_of(name).setdefault(ref, [])
        parent = chain[-1] if chain else self.tip(name)
        commit = Commit(f"{name}-side-{next(self.numbers)}", merged(parent.tree, files), parent.name, message, time)
        chain.append(commit)
        return commit.name

    def point(self, name, refs):
        self.pointers_of(name).update(refs)

    def moving(self, name, refs):
        return lambda: self.point(name, refs)

    def dropping(self, name, count=1):
        return lambda: [self.history_of(name).pop() for _ in range(count)]

    def reveal(self, name):
        self.__dict__.setdefault("_revealed", set()).add(name)

    def tip_tree_before(self, name):
        commits = self.history_of(name)
        return commits[-2].tree if len(commits) > 1 else dict(self.projects[name].files)

    def committing(self, name, files=None, message=CLEAN_MESSAGE, amend=False, times=1):
        return lambda: [self.make_commit(name, files, message, amend) for _ in range(times)]

    def repository(self, name, project):
        state = {**repo_state(Repo(project.files, branches=project.branches)), "disk": self.disk(name)}
        commits = self.history_of(name)
        sides = self.sides_of(name)
        if not commits and not sides and not self.pointers_of(name):
            return state
        everything = [*commits, *(commit for chain in sides.values() for commit in chain)]
        revisions = {**state["revisions"], **{commit.name: commit.tree for commit in everything}}
        parents = {**state["parents"], **{commit.name: [commit.parent] for commit in everything}}
        tip = commits[-1].name if commits else BASE
        refs = {**state["refs"], "HEAD": tip, "main": tip, "refs/heads/main": tip,
                **{ref: chain[-1].name for ref, chain in sides.items()}, **self.pointers_of(name)}
        messages = {commit.name: commit.message for commit in everything}
        times = {commit.name: commit.time for commit in everything if commit.time is not None}
        index = dict(revisions[refs["HEAD"]])
        return {**state, "revisions": revisions, "parents": parents, "refs": refs, "messages": messages,
                "times": times, "index": index}

    def view_data(self):
        shown = self.__dict__.get("_revealed", set())
        return {"roots": {str(self.folder(name)): self.repository(name, project)
                          for name, project in self.projects.items() if project.git and (project.listed or name in shown)}}

    def view(self):
        return ResolvingGitView(self.view_data())

    def run_and_stop(self, name, command, effect, host=CLAUDE, cwd=None):
        self.start(name, host)
        decision, verdict, _ = self.pair(name, command, effect, host=host)
        return decision, verdict, self.stop(name, host)


def commit_project(extra=None):
    return Project(project_files({NOTES: "Old.\n", **(extra or {})}))
