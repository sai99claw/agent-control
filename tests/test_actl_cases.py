"""#159 acceptance cases written by the verifier (D-071 C1-C3): `bin/actl` root resolution,
event.py with no project root, and the land.sh dogfood gate rule.

Every expected path comes from this module's own tempdirs (realpath), never from what
actl prints. The per-test A copy (`<home>/my-ac-home`) is built here, not by the harness
helper, so these cases do not lean on the implementation they are checking: AC_HOME is
always passed explicitly, and the copy is not a git tree.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, SCRIPTS, TIMEOUT, Sandbox, write_executable  # noqa: E402

# The A whose scripts are under test (the mutated copy's parent in mutation runs).
A_HOME = os.path.dirname(os.path.abspath(SCRIPTS))

# Writes its own $0 into the marker: which gate.sh land ran is then a file on disk.
GATE_NAMES_ITSELF = """#!/bin/sh
echo "$0" > "$AC_TEST_LOG"
exit 0
"""
GATE_WT = """#!/bin/sh
echo "WT-GATE" > "$AC_TEST_LOG"
exit 0
"""
GATE_HOME = """#!/bin/sh
echo "HOME-GATE $0" > "$AC_TEST_LOG"
exit 0
"""


def listing(path):
    """Sorted recursive listing; None when the directory is not there."""
    if not os.path.isdir(path):
        return None
    rows = []
    for here, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(dirs + files):
            rows.append(os.path.relpath(os.path.join(here, name), path))
    return rows


def read_bytes(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()


class ActlBase(Sandbox):
    """Sandbox plus a per-test copy of A and a bare environment for actl."""

    def copy_of_a(self):
        """`<home>/my-ac-home`: A's scripts/, bin/, board/board.py + config.json, templates/."""
        where = os.path.join(self.home, "my-ac-home")
        if os.path.isdir(where):
            return where
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(SCRIPTS, os.path.join(where, "scripts"), ignore=ignore)
        if os.path.isdir(os.path.join(A_HOME, "bin")):
            shutil.copytree(os.path.join(A_HOME, "bin"), os.path.join(where, "bin"),
                            ignore=ignore)
        os.makedirs(os.path.join(where, "board"))
        for name in ("board.py", "config.json"):
            src = os.path.join(A_HOME, "board", name)
            if not os.path.exists(src):
                src = os.path.join(ROOT, "board", name)
            shutil.copy(src, os.path.join(where, "board", name))
        shutil.copytree(os.path.join(ROOT, "templates"), os.path.join(where, "templates"))
        return where

    def bare_env(self, **extra):
        """Sandbox env with no project root of any kind: no AC_ROOT / AC_HOME /
        AC_CODE_ROOT / CLAUDE_PROJECT_DIR. The case adds back the one it is testing."""
        env = self.env()
        for key in ("AC_ROOT", "AC_HOME", "AC_CODE_ROOT", "AC_CONTROL_DIR",
                    "CLAUDE_PROJECT_DIR"):
            env.pop(key, None)
        env.update(extra)
        return env

    def actl(self, *args, cwd, env=None):
        return subprocess.run(["sh", os.path.join(A_HOME, "bin", "actl"), *args],
                              cwd=cwd, env=env or self.bare_env(),
                              capture_output=True, text=True, timeout=TIMEOUT)

    def version_lines(self, done):
        self.assertEqual(done.returncode, 0,
                         "actl version must be rc 0\n" + done.stdout + done.stderr)
        lines = {}
        for line in done.stdout.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                lines[key] = value
        for key in ("AC_HOME", "ac_sha", "AC_ROOT", "AC_CODE_ROOT"):
            self.assertIn(key, lines, "actl version has no %s= line:\n%s" % (key, done.stdout))
        return lines

    def put_ticket(self, ident, subject, where=None):
        self.make_ticket(ident, subject=subject)
        if where:
            shutil.move(os.path.join(self.repo, "tickets", "%s.json" % ident),
                        os.path.join(where, "tickets", "%s.json" % ident))

    def other_project(self, subject):
        """A second project: a copy of the sandbox (own .git) whose only ticket is #82."""
        other = os.path.join(self.home, "other-project")
        shutil.copytree(self.repo, other, symlinks=True)
        for name in os.listdir(os.path.join(other, "tickets")):
            os.remove(os.path.join(other, "tickets", name))
        self.put_ticket(82, subject, where=other)
        return other


class RootResolution(ActlBase):
    """A1: V1 arms through `sh $AC_HOME/bin/actl version` plus one real command."""

    SUBJECT = "verifier-159 sandbox ticket subject"
    OTHER = "verifier-159 OTHER project ticket subject"

    def test_a1a_cwd_in_a_ticket_worktree_resolves_the_main_sandbox_and_the_worktree_code(self):
        self.put_ticket(81, self.SUBJECT)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "ticket 81")
        # worktree_dir default: <main repo>/../<name>-wt
        wt = os.path.join(self.home, "repo-wt", "t81")
        self.git("worktree", "add", "-q", "-b", "t81", wt, "main")

        lines = self.version_lines(self.actl("version", cwd=wt))
        self.assertEqual(lines["AC_ROOT"], os.path.realpath(self.repo),
                         "a ticket worktree must resolve to its main repo")
        self.assertEqual(lines["AC_CODE_ROOT"], os.path.realpath(wt),
                         "the code root is the tree the cwd is in")

        done = self.actl("ticket", "list", "--open", cwd=wt)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(self.SUBJECT, done.stdout)

    def test_a1b_outside_any_project_refuses_and_touches_nothing(self):
        lonely = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, lonely, True)
        probe = subprocess.run(["git", "-C", lonely, "rev-parse", "--git-dir"],
                               capture_output=True, text=True, env=self.bare_env())
        self.assertNotEqual(probe.returncode, 0,
                            "fixture: %s is inside a git tree, the case cannot run" % lonely)
        a_tickets = os.path.join(A_HOME, "tickets")
        before_tmp, before_a = listing(lonely), listing(a_tickets)

        done = self.actl("ticket", "list", "--open", cwd=lonely)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("not inside a project", done.stderr)
        self.assertIn("-C", done.stderr)
        self.assertNotIn("#", done.stdout, "no ticket may be listed from anywhere")
        self.assertEqual(listing(lonely), before_tmp, "the tmp dir changed")
        self.assertEqual(listing(a_tickets), before_a, "A's own tickets/ changed")

        # The ticket text: `actl version` works outside any project (rc 0, AC_ROOT=(none)).
        lines = self.version_lines(self.actl("version", cwd=lonely))
        self.assertEqual(lines["AC_ROOT"], "(none)")
        self.assertEqual(lines["AC_CODE_ROOT"], "(none)")

    def test_a1c_dash_c_wins_over_the_cwd(self):
        self.put_ticket(81, self.SUBJECT)
        other = self.other_project(self.OTHER)

        lines = self.version_lines(self.actl("-C", other, "version", cwd=self.repo))
        self.assertEqual(lines["AC_ROOT"], os.path.realpath(other))

        done = self.actl("-C", other, "ticket", "list", "--open", cwd=self.repo)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(self.OTHER, done.stdout)
        self.assertNotIn(self.SUBJECT, done.stdout, "the cwd's project was listed")

    def test_a1d_claude_project_dir_wins_over_the_cwd(self):
        self.put_ticket(81, self.SUBJECT)
        other = self.other_project(self.OTHER)
        env = self.bare_env(CLAUDE_PROJECT_DIR=other)

        lines = self.version_lines(self.actl("version", cwd=self.repo, env=env))
        self.assertEqual(lines["AC_ROOT"], os.path.realpath(other))

        done = self.actl("ticket", "list", "--open", cwd=self.repo, env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(self.OTHER, done.stdout)
        self.assertNotIn(self.SUBJECT, done.stdout, "the cwd's project was listed")

    def test_a1e_an_unknown_command_is_rc_2_with_the_table(self):
        done = self.actl("nonsense", cwd=self.repo)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for word in ("land", "gate", "ticket", "version"):
            self.assertIn(word, done.stderr, "the command table is not on stderr")


class EventWithNoRoot(ActlBase):
    """A2 behaviour arm: event.py never falls back to where it lives."""

    def test_a2_event_emit_outside_any_project_is_rc_2_and_writes_nowhere(self):
        home = self.copy_of_a()   # has board/config.json, the old __file__ walk would find it
        lonely = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, lonely, True)
        a_events = os.path.join(home, "board", "events.jsonl")
        before = read_bytes(a_events)

        done = subprocess.run(
            ["python3", os.path.join(home, "scripts", "event.py"),
             "emit", "ticket.attempt.start", "--ticket", "1"],
            cwd=lonely, env=self.bare_env(AC_HOME=home),
            capture_output=True, text=True, timeout=TIMEOUT)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("not inside a project", done.stderr)
        self.assertEqual(read_bytes(a_events), before, "A's board/events.jsonl changed")
        self.assertEqual(listing(lonely), [], "something was written under the tmp dir")


class DogfoodGateRule(ActlBase):
    """A3 (C3): land.sh runs the worktree's gate only when the project is A itself."""

    def ticket_branch(self, ident, files=None):
        self.make_ticket(ident)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "open #%s" % ident)
        self.git("push", "-q", "origin", "main")
        wt = os.path.join(self.home, "wt", "t%s-x" % ident)
        self.git("worktree", "add", "-q", "-b", "t%s-x" % ident, wt, "main")
        for rel, text in (files or {"src/change.txt": "change\n"}).items():
            path = self.write(rel, text, where=wt)
            if rel.endswith(".sh"):
                os.chmod(path, 0o755)
        self.git("add", "-A", cwd=wt)
        self.git("commit", "-q", "-m", "t%s work" % ident, cwd=wt)
        return wt

    def land(self, home, branch):
        return subprocess.run(
            ["sh", os.path.join(home, "scripts", "land.sh"), branch],
            cwd=self.repo, env=self.env(AC_HOME=home, AC_ROOT=self.repo),
            capture_output=True, text=True, timeout=TIMEOUT)

    def marker(self):
        self.assertTrue(os.path.exists(self.log), "no gate ran at all (no marker file)")
        with open(self.log, encoding="utf-8") as handle:
            return handle.read().strip()

    def test_a3a_a_plain_project_lands_with_ac_homes_gate(self):
        home = self.copy_of_a()
        write_executable(os.path.join(home, "scripts", "gate.sh"), GATE_NAMES_ITSELF)
        wt = self.ticket_branch(1)

        done = self.land(home, "t1-x")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(os.path.realpath(self.marker()),
                         os.path.realpath(os.path.join(home, "scripts", "gate.sh")),
                         "the gate that ran is not $AC_HOME/scripts/gate.sh")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "scripts", "gate.sh")),
                         "the sandbox project carries a scripts/gate.sh")
        self.assertFalse(os.path.exists(os.path.join(wt, "scripts", "gate.sh")),
                         "the sandbox worktree carries a scripts/gate.sh")

    def test_a3b_when_the_project_is_ac_home_the_worktrees_gate_runs(self):
        home = self.copy_of_a()
        shutil.copytree(os.path.join(home, "scripts"), os.path.join(self.repo, "scripts"),
                        dirs_exist_ok=True)
        if os.path.isdir(os.path.join(home, "bin")):
            shutil.copytree(os.path.join(home, "bin"), os.path.join(self.repo, "bin"),
                            dirs_exist_ok=True)
        shutil.copy(os.path.join(home, "board", "board.py"),
                    os.path.join(self.repo, "board", "board.py"))
        write_executable(os.path.join(self.repo, "scripts", "gate.sh"), GATE_HOME)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the project is A itself")
        self.git("push", "-q", "origin", "main")
        self.ticket_branch(2, {"scripts/gate.sh": GATE_WT})

        done = self.land(self.repo, "t2-x")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.marker(), "WT-GATE",
                         "dogfood: the ticket worktree's gate.sh must be the one that ran")


if __name__ == "__main__":
    unittest.main()
