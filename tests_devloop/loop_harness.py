"""End-to-end tests of the loop: a throwaway project, a throwaway pool, fake agents and fake
tests behind the real test interface. Run: python3 -m unittest discover tests_devloop"""

import calendar
import glob
import html
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.dirname(HERE)
LOOP = os.path.join(HOME, "devloop")
sys.path.insert(0, LOOP)

from core import dag as dagmod  # noqa: E402
from core import osx, pages  # noqa: E402
from core import pool as pool_mod  # noqa: E402
from core.pool import Pool, shown_state, TICKET_STATES  # noqa: E402

AGENT = ["python3", os.path.join(HERE, "fake_agent.py")]
ROLES = ("creator", "worker", "worker_simple", "verifier", "reviewer", "fixer", "consolidator",
         "consolidator_peer")
TESTS = "python3 %s" % os.path.join(HERE, "fake_tests.py")


def git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class GitIsolatedCase(unittest.TestCase):
    """Runs every git command of the test against an empty global config, no system config and a fixed identity."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.mkdtemp(prefix="git-config-")
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = os.path.join(tmp, "gitconfig")
        open(cfg, "w").close()
        patch = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": cfg, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        patch.start()
        self.addCleanup(patch.stop)


class LoopCase(GitIsolatedCase):
    release = None
    loop_extra = {}

    def config(self, **devloop):
        path = os.path.join(self.proj, "board", "config.json")
        with open(path) as fh:
            cfg = json.load(fh)
        cfg["devloop"].update(devloop)
        with open(path, "w") as fh:
            json.dump(cfg, fh)

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="loop-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, "board"))
        self.pool_path = os.path.join(self.tmp, "pool")
        self.released = os.path.join(self.tmp, "released.txt")
        loop = {"pool": self.pool_path, "worktrees": os.path.join(self.tmp, "wt"), "remote": None,
                "max_parallel": 2, "poll_seconds": 0.2, "issuer_timeout_seconds": 120,
                "agent_timeout_seconds": 60, "pool_remote": None,
                "release": self.release or "echo $DEVLOOP_GROUP $DEVLOOP_TICKETS >> %s" % self.released,
                "agents": {r: AGENT for r in ROLES}}
        loop.update(self.loop_extra)
        config = {"config_version": 2,
                  "tests": {"preflight": TESTS + " preflight", "run": TESTS + " run", "full": TESTS + " full"},
                  "hard_block": {"paths": ["docs/DECISIONS.md"], "dirs": [], "patterns": []},
                  "devloop": loop}
        with open(os.path.join(self.proj, "board", "config.json"), "w") as fh:
            json.dump(config, fh)
        with open(os.path.join(self.proj, "README.md"), "w") as fh:
            fh.write("demo\n")
        git(self.proj, "init", "-q", "-b", "main")
        git(self.proj, "config", "user.name", "t")
        git(self.proj, "config", "user.email", "t@t")
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "init")
        self.pool = Pool(self.pool_path, remote=None).init()
        self.plan_path = os.path.join(self.tmp, "plan", "plan.json")
        os.makedirs(os.path.dirname(self.plan_path))
        # a test must never reach a real model: a `claude` that fails loudly comes first on PATH
        guard = os.path.join(self.tmp, "guard-bin")
        os.makedirs(guard)
        with open(os.path.join(guard, "claude"), "w") as fh:
            fh.write("#!/bin/sh\necho 'real claude called from a test' >&2\nexit 97\n")
        os.chmod(os.path.join(guard, "claude"), 0o755)
        self.env = dict(os.environ, AC_ROOT=self.proj, FAKE_PLAN=self.plan_path,
                        PATH=guard + os.pathsep + os.environ.get("PATH", ""),
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t")
        # a test's loop has its own slots: when the suite itself runs under a machine slot (an
        # agent's run_tests.py), the inherited DEVLOOP_SLOT must not switch the test loop's slots off
        self.env.pop("DEVLOOP_SLOT", None)

    # ------------------------------------------------------------ helpers

    def plan(self, **roles):
        with open(self.plan_path, "w") as fh:
            json.dump(roles, fh)

    def add(self, ident, deps=(), needs_verifier=True, complexity="simple", **extra):
        def change(tx):
            t = {"id": str(ident), "title": "ticket %s" % ident, "objective": "o", "acceptance": ["a"],
                 "needs_verifier": needs_verifier, "complexity": complexity, "state": "ready",
                 "round": 0, "feedback": [], "cases": None}
            t.update(extra)
            tx.put_ticket(t)
            dag = tx.data.dag
            dag[str(ident)] = {"depends_on": [str(d) for d in deps]}
            tx.put_dag({"tickets": dag})
        self.pool.update(change, "test: add #%s" % ident)

    def run_py(self, script, *args, timeout=240):
        return subprocess.run(["python3", os.path.join(LOOP, script)] + list(args), cwd=self.proj,
                              env=self.env, capture_output=True, text=True, timeout=timeout)

    def controller(self, timeout=240):
        res = self.run_py("ticket_controller.py", timeout=timeout)
        return res

    def snap(self):
        return self.pool.snapshot()

    def pool_memory(self, rel=None):
        """Memory texts in the pool's memory/ folder on disk: {rel: text}, or one text (None if
        missing). rel is the key form, "memory/role/worker.md"."""
        out = {}
        base = os.path.join(self.pool_path, "memory")
        for folder, _, names in os.walk(base):
            for n in names:
                if n.endswith(".md"):
                    full = os.path.join(folder, n)
                    with open(full) as fh:
                        out["memory/" + os.path.relpath(full, base).replace(os.sep, "/")] = fh.read()
        return out if rel is None else out.get(rel)

    def state(self, ident):
        return self.snap().ticket(str(ident)).get("state")

    def inbox_kinds(self):
        return sorted(row["kind"] for row in self.snap().inbox.values())

    def main_has(self, name):
        return git(self.proj, "ls-tree", "--name-only", "main", name) == name


def own_origin(port, headers):
    """A served board's POST headers as a browser on the board sends them: its own Origin (#76),
    unless the case passes its own Origin or Referer."""
    h = dict(headers or {})
    if not any(k.lower() in ("origin", "referer") for k in h):
        h["Origin"] = "http://127.0.0.1:%d" % port
    return h
