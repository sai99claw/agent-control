"""the controller: forever pauses, idle pages, main moving into waiting branches, no_reverify_paths, worktree setup, landing"""

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_harness import AGENT, HERE, HOME, LOOP, ROLES, TESTS, GitIsolatedCase, LoopCase, git, own_origin  # noqa: E402,F401
from core import dag as dagmod  # noqa: E402
from core import osx, pages  # noqa: E402
from core import pool as pool_mod  # noqa: E402
from core.pool import Pool, shown_state, TICKET_STATES  # noqa: E402


class T51Acceptance(LoopCase):
    """Ticket #51: under --forever an env or unmeasured regression (or a fixer whose worktree setup
    failed) pauses the group and the controller goes on; `tickets.py rerun g<N>` lifts the pause.
    Expected values from the ticket text and D-092 (環境頁:環境修好了、重跑), not from the code."""

    STAMP = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)

    # ------------------------------------------------------------ helpers

    def env_bad(self, on=True):
        path = os.path.join(self.proj, ".env-bad")
        if on:
            open(path, "w").close()
        elif os.path.exists(path):
            os.unlink(path)

    def forever(self):
        log = open(os.path.join(self.tmp, "forever.log"), "w")
        self.addCleanup(log.close)
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "ticket_controller.py"), "--forever"],
                                cwd=self.proj, env=self.env, stdout=log, stderr=subprocess.STDOUT)
        self.addCleanup(proc.wait, 30)
        self.addCleanup(proc.terminate)
        self.proc = proc
        return proc

    def proc_log(self):
        with open(os.path.join(self.tmp, "forever.log")) as fh:
            return fh.read()[-3000:]

    def wait(self, what, pred, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            snap = self.snap()
            if pred(snap):
                return snap
            proc = getattr(self, "proc", None)
            if proc is not None and proc.poll() is not None:
                self.fail("the --forever controller exited (rc %s) before %s:\n%s" % (
                    proc.returncode, what, self.proc_log()))
            time.sleep(0.3)
        self.fail("timed out after %ss waiting for %s" % (timeout, what))

    @staticmethod
    def paused(snap, gid, reason):
        g = snap.groups.get(gid) or {}
        return isinstance(g.get("paused"), dict) and g["paused"].get("reason") == reason

    @staticmethod
    def events(g, what):
        return [h for h in g.get("history") or [] if h.get("event") == what]

    def pages(self, kind, ref):
        return {name: row for name, row in self.snap().inbox.items()
                if row["kind"] == kind and row["ref"] == ref}

    def pool_writes(self):
        return int(git(self.pool_path, "rev-list", "--count", "HEAD"))

    def assert_rerun_exists(self):
        res = self.run_py("tickets.py", "--help")
        self.assertIn("rerun", res.stdout + res.stderr, "tickets.py has no rerun command")

    def put_group(self, gid, state, **extra):
        g = {"id": gid, "branch": "int/%s" % gid, "state": state, "members": [], "fix_round": 0,
             "job": None}
        g.update(extra)
        self.pool.update(lambda tx: tx.put_group(g), "test: put %s" % gid)

    # ------------------------------------------------------------ item 1

    def test_1_without_forever_exit_3_marks_paused_and_the_next_run_clears_it(self):
        self.add(1)
        self.env_bad()
        res = self.controller()
        self.assertEqual(res.returncode, 3, res.stdout + res.stderr)
        self.assertEqual(self.inbox_kinds(), ["env"])
        g = self.snap().groups["g1"]
        self.assertIn("paused", g, "group g1 has no paused mark: %s" % g)
        p = g["paused"]
        self.assertEqual(p.get("reason"), "env", p)
        self.assertRegex(str(p.get("at")), self.STAMP)
        self.assertIsInstance(p.get("text"), str)
        self.assertTrue(p["text"], p)
        self.assertLessEqual(len(p["text"]), 2000)
        self.assertEqual(p["text"], str(g.get("last_regression"))[:2000])
        self.assertEqual(len(self.events(g, "paused: env")), 1, g.get("history"))
        started = len(self.events(g, "regression started"))
        self.env_bad(False)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.state(1), "landed")
        g = self.snap().groups["g1"]
        self.assertGreater(len(self.events(g, "regression started")), started, g.get("history"))
        self.assertNotIn("paused", g)

    # ------------------------------------------------------------ items 2, 3, 4

    def test_2_3_4_forever_pauses_goes_on_and_rerun_lands(self):
        self.add(1)
        self.env_bad()
        self.forever()
        # item 2
        snap = self.wait("g1 paused env", lambda s: self.paused(s, "g1", "env"))
        g = snap.groups["g1"]
        self.assertEqual((g["state"], g.get("job", "absent")), ("pr", None), g)
        envs = self.pages("env", "g1")
        self.assertEqual(len(envs), 1, self.snap().inbox)
        self.assertIsNone(self.proc.poll(), "the --forever controller stopped")
        started = len(self.events(g, "regression started"))
        time.sleep(3)
        g = self.snap().groups["g1"]
        self.assertEqual(len(self.events(g, "regression started")), started, g.get("history"))
        self.assertIsNone(self.proc.poll(), "the --forever controller stopped")
        # item 3; #117: a paused group holds the later groups, so g2 gets #2 merged and then
        # waits for g1 (no regression) until g1 is rerun and lands
        self.env_bad(False)
        self.add(2)
        snap = self.wait("#2 merged", lambda s: s.ticket("2").get("state") == "merged")
        g2 = [x for x in snap.groups.values() if "2" in (x.get("members") or [])]
        self.assertEqual(len(g2), 1, snap.groups)
        g2id = g2[0]["id"]
        self.assertNotEqual(g2id, "g1")
        time.sleep(3)
        snap = self.snap()
        self.assertEqual(self.events(snap.groups[g2id], "regression started"), [],
                         snap.groups[g2id].get("history"))
        self.assertEqual(snap.ticket("2").get("state"), "merged")
        g = snap.groups["g1"]
        self.assertEqual(g["state"], "pr", g)
        self.assertTrue(self.paused(snap, "g1", "env"), g)
        self.assertEqual(len(self.events(g, "regression started")), started, g.get("history"))
        self.assertIsNone(self.proc.poll(), "the --forever controller stopped")
        # item 4
        page = list(envs)[0]
        res = self.run_py("tickets.py", "rerun", "g1", "--why", "fixed", "--by", "a@x.com", "--page", page)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(len(res.stdout.strip().splitlines()), 1, res.stdout)
        snap = self.snap()
        g = snap.groups["g1"]
        fixed = self.events(g, "environment fixed: verify again")
        self.assertEqual(len(fixed), 1, g.get("history"))
        self.assertEqual((fixed[0].get("by"), fixed[0].get("why")), ("a@x.com", "fixed"), fixed)
        row = snap.inbox[page]
        self.assertTrue(row.get("acked"), row)
        self.assertEqual(row.get("acked_by"), "a@x.com", row)
        snap = self.wait("g1 landed", lambda s: s.groups["g1"].get("state") == "landed", timeout=90)
        self.assertNotIn("paused", snap.groups["g1"])
        self.assertEqual(self.state(1), "landed")
        snap = self.wait("#2 landed", lambda s: s.ticket("2").get("state") == "landed", timeout=90)
        landed = self.events(snap.groups["g1"], "landed")[0]["at"]
        reg = self.events(snap.groups[g2id], "regression started")
        self.assertEqual(len(reg), 1, snap.groups[g2id].get("history"))
        self.assertGreaterEqual(reg[0]["at"], landed)

    # ------------------------------------------------------------ item 4: one write, actl, default by

    def test_4_rerun_is_one_write_and_actl_runs_it_with_by_user(self):
        self.assert_rerun_exists()
        mark = {"reason": "env", "at": "2026-01-01T00:00:00Z", "text": "broken"}
        for gid in ("g1", "g2"):
            self.put_group(gid, "pr", paused=dict(mark))
        p1 = self.pool.report("env", "g1", "group g1: env")
        p2 = self.pool.report("env", "g2", "group g2: env")
        before = self.pool_writes()
        res = self.run_py("tickets.py", "rerun", "g1", "--why", "fixed", "--by", "a@x.com", "--page", p1)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(len(res.stdout.strip().splitlines()), 1, res.stdout)
        self.assertEqual(self.pool_writes(), before + 1)
        changed = git(self.pool_path, "show", "--name-only", "--format=", "HEAD").split()
        self.assertIn("groups/g1.json", changed)
        self.assertIn("inbox/%s.json" % p1, changed)
        snap = self.snap()
        self.assertNotIn("paused", snap.groups["g1"])
        ev = self.events(snap.groups["g1"], "environment fixed: verify again")
        self.assertEqual([(e.get("by"), e.get("why")) for e in ev], [("a@x.com", "fixed")])
        self.assertEqual((snap.inbox[p1].get("acked"), snap.inbox[p1].get("acked_by")), (True, "a@x.com"))
        res = subprocess.run([os.path.join(HOME, "bin", "actl"), "tickets", "rerun", "g2", "--page", p2],
                             cwd=self.proj, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        self.assertNotIn("paused", snap.groups["g2"])
        ev = self.events(snap.groups["g2"], "environment fixed: verify again")
        self.assertEqual([e.get("by") for e in ev], ["user"], ev)
        self.assertEqual((snap.inbox[p2].get("acked"), snap.inbox[p2].get("acked_by")), (True, "user"))

    # ------------------------------------------------------------ item 5

    def assert_refused(self, gid, page, needle=None):
        before = self.pool_writes()
        res = self.run_py("tickets.py", "rerun", gid, "--why", "w", "--by", "a@x.com", "--page", page)
        self.assertEqual(res.returncode, 2, "%s: %s" % (gid, res.stdout + res.stderr))
        lines = res.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, "%s: %r" % (gid, res.stderr))
        if needle:
            self.assertIn(needle, lines[0])
        self.assertEqual(self.pool_writes(), before, gid)
        self.assertFalse(self.snap().inbox[page].get("acked"), gid)

    def test_5_rerun_refuses_what_is_not_a_waiting_group_and_accepts_pr_and_fixing(self):
        self.assert_rerun_exists()
        self.add(1)
        mark = {"reason": "env", "at": "2026-01-01T00:00:00Z", "text": "broken"}
        for i, state in enumerate(("open", "stuck", "verified", "landed", "cancelled"), start=1):
            self.put_group("g%d" % i, state, paused=dict(mark))
        self.put_group("g6", "pr", paused=dict(mark), job={"kind": "regression", "pid": 1})
        self.put_group("g7", "pr")
        self.put_group("g8", "fixing")
        page = self.pool.report("env", "g1", "env page")
        for gid in ("g9", "g1", "g2", "g3", "g4", "g5", "g6"):
            self.assert_refused(gid, page)
        self.assert_refused("1", page, needle="g<N>")
        for gid in ("g7", "g8"):
            p = self.pool.report("env", gid, "env page %s" % gid)
            res = self.run_py("tickets.py", "rerun", gid, "--why", "w", "--by", "a@x.com", "--page", p)
            self.assertEqual(res.returncode, 0, "%s: %s" % (gid, res.stdout + res.stderr))
            snap = self.snap()
            self.assertEqual(len(self.events(snap.groups[gid], "environment fixed: verify again")), 1,
                             snap.groups[gid].get("history"))
            self.assertTrue(snap.inbox[p].get("acked"), gid)

    # ------------------------------------------------------------ item 6

    def test_6_in_process_forever_flag_and_after_regression(self):
        import ticket_controller
        from core.config import Config
        ctl = ticket_controller.Controller(Config(self.proj), self.pool)
        self.assertTrue(hasattr(ctl, "forever"), "Controller has no forever attribute")
        self.assertIs(ctl.forever, False)
        seen = []

        def tick():
            seen.append(ctl.forever)
            ctl.stopping = True
            return False
        ctl.tick = tick
        ctl.run(True)
        self.assertEqual(seen, [True])
        ctl.stopping = False
        seen.clear()
        ctl.run(False)
        self.assertEqual(seen, [False])

        out = os.path.join(self.tmp, "report.json")
        with open(out, "w") as fh:
            json.dump({"status": "unmeasured", "summary": "no slot"}, fh)
        self.put_group("g1", "pr")
        ctl.forever = True
        ctl.after_regression("g1", {"out": out}, 1)
        snap = self.snap()
        self.assertEqual(len(self.pages("decision", "g1")), 1, snap.inbox)
        p = snap.groups["g1"].get("paused") or {}
        self.assertEqual((p.get("reason"), p.get("text")), ("unmeasured", "no slot"), snap.groups["g1"])

        self.put_group("g1", "pr")
        ctl.forever = False
        with self.assertRaises(ticket_controller.Stop) as cm:
            ctl.after_regression("g1", {"out": out}, 1)
        self.assertEqual(cm.exception.code, 3)
        p = self.snap().groups["g1"].get("paused") or {}
        self.assertEqual((p.get("reason"), p.get("text")), ("unmeasured", "no slot"))

    # ------------------------------------------------------------ item 7

    def test_7_forever_fixer_setup_failure_pauses_the_group(self):
        with open(os.path.join(self.proj, ".gitignore"), "w") as fh:
            fh.write(".setup-done\n")
        git(self.proj, "add", ".gitignore")
        git(self.proj, "commit", "-q", "-m", "ignore the setup marker")
        self.config(worktree_setup='case "$PWD" in */fix-*) exit 3;; esac; touch .setup-done')
        self.add(1)
        self.plan(worker={"1": ["red"]})
        self.forever()
        snap = self.wait("g1 paused env", lambda s: self.paused(s, "g1", "env"))
        g = snap.groups["g1"]
        self.assertEqual((g["state"], g["fix_round"], g.get("job", "absent")), ("pr", 0, None), g)
        envs = [r for r in self.pages("env", "g1").values() if "worktree_setup" in r["text"]]
        self.assertTrue(envs, self.snap().inbox)
        self.assertIsNone(self.proc.poll(), "the --forever controller stopped")


class PausedGroupPieces(LoopCase):
    """#51 internals: the pause helper, the --forever skip in advance_groups, `tickets.py rerun`
    and the docs that name it."""

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)

    def ctl(self):
        import ticket_controller
        from core.config import Config
        return ticket_controller.Controller(Config(self.proj), self.pool)

    def put(self, gid, **fields):
        g = {"id": gid, "branch": "int/" + gid, "state": "pr", "members": [], "fix_round": 0, "job": None}
        g.update(fields)
        self.pool.update(lambda tx: tx.put_group(g), "test: group %s" % gid)

    def test_pause_caps_text_and_writes_one_event(self):
        self.put("g1")
        self.ctl().pause("g1", "env", "x" * 5000)
        g = self.snap().groups["g1"]
        self.assertEqual(g["paused"]["reason"], "env")
        self.assertEqual(len(g["paused"]["text"]), 2000)
        self.assertTrue(g["paused"]["at"])
        self.assertEqual(g["history"][-1]["event"], "paused: env")

    def test_set_group_drop_removes_the_key(self):
        self.put("g1", paused={"reason": "env", "at": "t", "text": ""})
        self.ctl().set_group("g1", None, "x", drop=("paused",), job={"kind": "regression"})
        self.assertNotIn("paused", self.snap().groups["g1"])

    def test_forever_skips_a_paused_group_only(self):
        """--forever skips the paused group's regression; #117: a paused group still holds the
        chain, so the later g2 waits for it either way. Each group has a merged member: a group
        with no members is closed (#118)."""
        self.add(1, state="merged", group="g1")
        self.add(2, state="merged", group="g2")
        self.put("g1", members=["1"], paused={"reason": "env", "at": "t", "text": ""})
        self.put("g2", members=["2"])
        ctl = self.ctl()
        started = []
        ctl.start_regression = lambda g: started.append(g["id"])
        ctl.forever = True
        ctl.advance_groups(self.snap())
        self.assertEqual(started, [])
        self.assertEqual([self.snap().groups[g]["state"] for g in ("g1", "g2")], ["pr", "pr"])
        ctl.forever = False
        started.clear()
        ctl.advance_groups(self.snap())
        self.assertEqual(started, ["g1"])

    def test_after_fixer_env_pauses_with_the_page_text(self):
        import ticket_controller
        self.put("g1", state="fixing", fix_round=1, job={"kind": "fixer"})
        self.pool.report("env", "g1", "devloop.worktree_setup failed: boom")
        ctl = self.ctl()
        ctl.forever = True
        ctl.after_fixer("g1", 86)
        g = self.snap().groups["g1"]
        self.assertEqual((g["state"], g["fix_round"], g["job"]), ("pr", 0, None))
        self.assertEqual(g["paused"]["reason"], "env")
        self.assertIn("worktree_setup", g["paused"]["text"])
        ctl.forever = False
        with self.assertRaises(ticket_controller.Stop) as cm:
            ctl.after_fixer("g1", 86)
        self.assertEqual(cm.exception.code, 3)

    def test_rerun_through_actl_accepts_an_unpaused_pr_group(self):
        self.put("g1")
        page = self.pool.report("env", "g1", "t")
        res = subprocess.run([os.path.join(HOME, "bin", "actl"), "tickets", "rerun", "g1", "--page", page],
                             cwd=self.proj, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(len(res.stdout.strip().splitlines()), 1)
        snap = self.snap()
        last = snap.groups["g1"]["history"][-1]
        self.assertEqual((last["event"], last["by"], last["why"]), ("environment fixed: verify again", "user", ""))
        self.assertTrue(snap.inbox[page]["acked"])

    def test_rerun_refuses_without_writing(self):
        self.put("g1", state="stuck")
        self.put("g2", job={"kind": "regression"})
        self.add(3)
        page = self.pool.report("env", "g1", "t")
        before = git(self.pool_path, "rev-parse", "HEAD")
        for ident in ("g1", "g2", "g7", "3"):
            res = self.run_py("tickets.py", "rerun", ident, "--page", page)
            self.assertEqual(res.returncode, 2, ident)
            self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
        self.assertIn("g<N>", res.stderr)
        self.assertEqual(git(self.pool_path, "rev-parse", "HEAD"), before)
        self.assertFalse(self.snap().inbox[page]["acked"])

    def test_docs_name_rerun(self):
        with open(os.path.join(HOME, "devloop", "README.md")) as fh:
            readme = fh.read()
        runs = readme.split("## What the main session runs", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`python3 devloop/tickets.py rerun gN [--why …] [--by NAME] [--page P]`", runs)
        exits = [line for line in readme.split("- **Exit codes**", 1)[1].split("\n- ", 1)[0].splitlines()]
        self.assertIn("--forever", " ".join(exits))
        with open(os.path.join(HOME, "docs", "design", "dev-loop-v2.md")) as fh:
            row = [line for line in fh if line.startswith("| environment unusable at regression")]
        self.assertEqual(len(row), 1)
        self.assertIn("--forever", row[0])
        self.assertIn("actl tickets rerun gN", row[0])
        import inspect
        import ticket_controller
        src = inspect.getsource(ticket_controller.Controller.after_regression)
        self.assertIn("actl tickets rerun", src)
        self.assertIn("環境修好了,重跑", src)


class IdleEndPieces(LoopCase):
    """#63 internals: idle_lines() of a snapshot, and when report_idle() writes the idle page."""

    def setUp(self):
        super().setUp()
        for key in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(key, None)

    def ctl(self):
        import ticket_controller
        from core.config import Config
        return ticket_controller.Controller(Config(self.proj), self.pool)

    def snapshot(self, tickets, groups=(), dag=None):
        from core.pool import Snapshot
        dag = dag if dag is not None else {t["id"]: {"depends_on": list(t.pop("deps", ()))} for t in tickets}
        return Snapshot({"dag": {"tickets": dag}, "tickets": {t["id"]: t for t in tickets},
                         "groups": {g["id"]: g for g in groups}, "inbox": {}})

    def idle_pages(self):
        return [p for p in self.snap().inbox.values() if p.get("kind") == "idle"]

    def test_lines_cover_every_format_in_order(self):
        import ticket_controller
        snap = self.snapshot(
            [{"id": "10", "state": "ready", "deps": ["2", "9", "5"]},
             {"id": "2", "state": "stuck", "stuck": {"reason": "hard_block", "text": "first\nsecond"}},
             {"id": "5", "state": "merged", "group": "g3"},
             {"id": "6", "state": "landed"}, {"id": "7", "state": "cancelled"},
             {"id": "8", "state": "ready", "deps": ["6"]}],
            [{"id": "g10", "state": "pr", "paused": {"reason": "env", "text": "disk full\nmore"}},
             {"id": "g3", "state": "stuck", "history": [{"event": "a"}, {"event": "red after 3 rounds"}]},
             {"id": "g4", "state": "landed", "paused": {"reason": "env", "text": "x"}},
             {"id": "g5", "state": "landed"}])
        self.assertEqual(ticket_controller.idle_lines(snap), [
            "#2 stuck (hard_block): first",
            "#8 ready, waits for: nothing found",
            "#10 ready, waits for: #2 stuck, #9 missing, #5 merged in g3 (stuck)",
            "g3 stuck: red after 3 rounds",
            "g10 paused (env): disk full"])

    def test_nothing_left_gives_no_lines(self):
        import ticket_controller
        snap = self.snapshot([{"id": "1", "state": "landed"}, {"id": "2", "state": "cancelled", "deps": ["1"]}],
                             [{"id": "g1", "state": "landed", "history": [{"event": "landed"}]}])
        self.assertEqual(ticket_controller.idle_lines(snap), [])

    def test_report_idle_writes_one_page_and_stderr(self):
        import contextlib
        import io
        self.add(1, state="stuck", stuck={"reason": "decision", "text": "ask"})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertTrue(self.ctl().report_idle())
        pages = self.idle_pages()
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["ref"], "controller")
        self.assertIn("#1 stuck (decision): ask", pages[0]["text"].splitlines())
        self.assertEqual(err.getvalue().strip(), pages[0]["text"])

    def test_report_idle_silent_when_nothing_left_or_stopping(self):
        import contextlib
        import io
        self.add(1, state="landed")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(self.ctl().report_idle())
            self.add(2, state="stuck", stuck={"reason": "decision", "text": "ask"})
            ctl = self.ctl()
            ctl.stopping = True
            self.assertIsNone(ctl.report_idle())
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(self.idle_pages(), [])

    def test_run_reports_only_on_an_idle_end(self):
        import contextlib
        import io
        import ticket_controller
        self.add(1, state="stuck", stuck={"reason": "decision", "text": "ask"})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            # a signal sets stopping while a tick runs: the loop ends without a page
            ctl = self.ctl()
            def tick_then_signal():
                ctl.stopping = True
                return False
            ctl.tick = tick_then_signal
            self.assertEqual(ctl.run(), 0)
            # --forever never takes the idle exit
            ctl = self.ctl()
            ticks = []
            def idle_tick():
                ticks.append(1)
                if len(ticks) == 2:
                    ctl.stopping = True
                return False
            ctl.tick = idle_tick
            ctl.cfg.poll_seconds = 0
            self.assertEqual(ctl.run(forever=True), 0)
            self.assertEqual(len(ticks), 2)
            # a Stop (exit 3 or 2) leaves before the idle exit
            ctl = self.ctl()
            def stop_tick():
                raise ticket_controller.Stop(3, "env")
            ctl.tick = stop_tick
            with self.assertRaises(ticket_controller.Stop):
                ctl.run()
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(self.idle_pages(), [])
        # the idle end itself: one page, exit 0, one tick
        ctl = self.ctl()
        ticks.clear()
        ctl.tick = lambda: ticks.append(1) or False
        with contextlib.redirect_stderr(err):
            self.assertEqual(ctl.run(), 0)
        self.assertEqual(len(ticks), 1)
        self.assertEqual(len(self.idle_pages()), 1)

    def test_docs_name_the_idle_page(self):
        import ticket_controller
        from core.pool import Pool
        with open(os.path.join(HOME, "devloop", "README.md")) as fh:
            readme = fh.read()
        rows = [line for line in readme.splitlines()
                if line.startswith("| `python3 devloop/ticket_controller.py` |")
                or line.startswith("| `python3 devloop/tickets.py inbox`")]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("idle" in r for r in rows), rows)
        self.assertIn("created, landed, cancelled and idle pages", " ".join(readme.split()))
        self.assertIn("idle", Pool.report.__doc__)
        exit_line = [line for line in ticket_controller.__doc__.split("Exit:", 1)[1].split("\n\n")[0].splitlines()]
        self.assertIn("idle page", " ".join(exit_line))
        with open(os.path.join(HOME, "CLAUDE.md")) as fh:
            kinds = [line for line in fh if "頁的種類" in line]
        self.assertEqual(len(kinds), 1)
        self.assertIn("`idle`", kinds[0])


class T80Acceptance(LoopCase):
    """Ticket #80, end to end on the real scripts: the decision, env and idle pages written by W1,
    W2, W6, W7, W9, W10 (rounds, hard_block), W11 and W12 carry a valid question set whose option
    actions are exactly the ticket's, in its order. Validity comes from core/pages.problems."""

    def setUp(self):
        super().setUp()
        for key in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(key, None)

    def pages_of(self, kinds, ref, names=()):
        return {n: r for n, r in self.snap().inbox.items()
                if r.get("kind") in kinds and r.get("ref") == ref and n not in names}

    def one(self, kinds, ref, names=(), contains=None):
        got = self.pages_of(kinds, ref, names)
        if contains is not None:
            got = {n: r for n, r in got.items() if contains in r.get("text", "")}
        self.assertEqual(len(got), 1, "expected one %s page for %s: %s" % (kinds, ref, self.snap().inbox))
        return next(iter(got.items()))

    def check(self, ref, row, actions, subject=None):
        from core import pages
        opts = row.get("options")
        self.assertIsInstance(opts, list, "page for %s carries no options: %s" % (ref, row))
        self.assertEqual([o.get("action") for o in opts], list(actions), row)
        self.assertEqual(pages.problems(ref, row), [], row)
        self.assertTrue(str(row.get("question") or "").strip(), row)
        self.assertTrue(str(row.get("recommendation") or "").strip(), row)
        for o in opts:
            self.assertTrue(str(o.get("consequence") or "").strip(), o)
        if subject is not None:
            self.assertIn(subject, row["question"])
        return opts

    def test_w6_w7_release_failed_pages(self):
        self.config(release="exit 7")
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        g = self.snap().groups["g1"]
        self.assertEqual((g["state"], g["release"]), ("landed", "failed"))
        sha12 = g["landed_sha"][:12]
        name, row = self.one(("decision",), "g1")
        # W7: the re-run through tickets.py release fails the same way and writes a new page
        res = self.run_py("tickets.py", "release", "g1")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        name7, row7 = self.one(("decision",), "g1", names=(name,))
        for label, page in (("W6", row), ("W7", row7)):
            with self.subTest(label):
                opts = self.check("g1", page, ["ack", "release"], subject="g1")
                self.assertIn(sha12, opts[1]["consequence"])

    def test_w2_red_after_the_last_fix_round(self):
        self.config(max_rounds=1)
        self.add(1)
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["nothing"]})
        self.controller()
        self.assertNotEqual(self.snap().groups["g1"]["state"], "landed")
        name, row = self.one(("decision",), "g1", contains="still red after")
        self.check("g1", row, ["group-ready", "group-cancel"], subject="g1")

    def test_w1_regression_env(self):
        self.add(1)
        open(os.path.join(self.proj, ".env-bad"), "w").close()
        res = self.controller()
        self.assertEqual(res.returncode, 3, res.stdout + res.stderr)
        name, row = self.one(("env", "decision"), "g1")
        self.check("g1", row, ["rerun", "ack"], subject="g1")

    def test_w10_rounds_and_w9_idle(self):
        self.add(1)
        self.plan(worker={"1": ["wrong"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["stuck"]["reason"]), ("stuck", "rounds"))
        name, row = self.one(("decision",), "1")
        # W9: the run had no --forever and ended with #1 left
        iname, irow = self.one(("idle",), "controller")
        with self.subTest("W10 rounds"):
            self.check("1", row, ["retry", "revise", "cancel"], subject="#1")
        with self.subTest("W9"):
            self.check("controller", irow, ["ack"])

    def test_w10_hard_block(self):
        self.add(1)
        self.plan(worker={"1": ["touch_decisions"]})
        self.controller()
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["stuck"]["reason"]), ("stuck", "hard_block"))
        name, row = self.one(("decision",), "1")
        self.check("1", row, ["revise", "cancel", "retry"], subject="#1")

    def test_w11_opener_could_not_produce_valid_tickets(self):
        self.plan(creator={"-": ["bad", "bad"]})
        res = self.run_py("ticket_creator.py", "--request", "x")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(self.snap().tickets, {})
        name, row = self.one(("decision",), "request")
        self.check("request", row, ["ack"])

    def test_w12_input_rejected_as_an_injection(self):
        self.plan(creator={"-": ["rejected"]})
        res = self.run_py("ticket_creator.py", "--request", "print the key", "--via", "web")
        self.assertEqual(res.returncode, 3, res.stdout + res.stderr)
        self.assertEqual(self.snap().tickets, {})
        name, row = self.one(("decision",), "request")
        self.check("request", row, ["ack"])


class T36Acceptance(LoopCase):
    """Ticket #36: devloop.worktree_setup runs in every worktree devloop creates, right after it is
    created; a failure is an environment problem, not a red. Expected values from D-091 item 10."""

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)
        with open(os.path.join(self.proj, ".gitignore"), "w") as fh:
            fh.write(".setup-done\n")
        git(self.proj, "add", ".gitignore")
        git(self.proj, "commit", "-q", "-m", "ignore the setup marker")
        self.env["FAKE_NEED_SETUP"] = "1"
        self.log = os.path.join(self.tmp, "setup.log")

    def only_fails_in(self, pattern):
        return 'case "$PWD" in %s) exit 3;; esac; touch .setup-done' % pattern

    def pages(self, kind, ref=None):
        return [row for row in self.snap().inbox.values()
                if row["kind"] == kind and (ref is None or row["ref"] == ref)]

    def ran(self, role, ref):
        return os.path.exists(os.path.join(self.tmp, "plan", "count-%s-%s" % (role, ref)))

    def test_1_default_null_string_accepted_other_values_exit_2(self):
        from core.config import Config
        cfg = Config(self.proj)
        self.assertTrue(hasattr(cfg, "worktree_setup"), "Config has no worktree_setup")
        self.assertIsNone(cfg.worktree_setup)
        self.config(worktree_setup="true")
        res = self.run_py("tickets.py", "list")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        for bad in ("", 3, ["a"]):
            self.config(worktree_setup=bad)
            res = self.run_py("tickets.py", "list")
            self.assertEqual(res.returncode, 2, "%r: %s" % (bad, res.stdout + res.stderr))
            self.assertIn("devloop.worktree_setup", res.stderr, repr(bad))

    def test_2_every_created_worktree_is_set_up_with_cwd_and_ac_root(self):
        self.config(worktree_setup='touch .setup-done && echo "$PWD $AC_ROOT" >> %s' % self.log)
        self.add(1)
        self.plan(worker={"1": ["red"]})
        res = self.controller()
        self.assertTrue(os.path.exists(self.log), "worktree_setup never ran: no setup.log")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.snap().groups["g1"]["release"], "ok")
        with open(self.log) as fh:
            rows = [line.split() for line in fh if line.strip()]
        names = {os.path.basename(r[0]) for r in rows}
        for name in ("t1", "t1-cases", "red-t1", "regression-int-g1", "fix-g1", "release-g1"):
            self.assertIn(name, names, rows)
        root = os.path.realpath(self.proj)
        for r in rows:
            self.assertEqual(len(r), 2, r)
            self.assertEqual(os.path.realpath(r[1]), root, r)

    def test_3_a_failed_setup_leaves_the_ticket_stuck_env_and_removes_the_tree(self):
        self.config(worktree_setup="exit 3")
        self.add(1)
        self.controller()
        t = self.snap().ticket("1")
        self.assertEqual(t["state"], "stuck", t.get("history"))
        self.assertEqual(t["stuck"]["reason"], "env", t["stuck"])
        self.assertTrue([p for p in self.pages("env") if "worktree_setup" in p["text"]], self.pages("env"))
        self.assertFalse(self.ran("worker", "1"), "the worker ran")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "wt", "t1")), "the failed tree was kept")

    def test_4_a_failed_setup_in_the_regression_tree_is_env_86(self):
        self.env.pop("FAKE_NEED_SETUP")   # today the regression is green: 86 can come only from the setup
        self.config(worktree_setup=self.only_fails_in("*/regression-*"))
        out = os.path.join(self.tmp, "regression.json")
        res = self.run_py("regression.py", "--branch", "main", "--out", out)
        self.assertEqual(res.returncode, 86, res.stdout + res.stderr)
        with open(out) as fh:
            data = json.load(fh)
        self.assertEqual(data["status"], "env", data)
        self.assertIn("worktree_setup", data["summary"])

    def test_5_a_failed_setup_in_the_fixer_tree_stops_and_the_next_run_fixes(self):
        self.config(worktree_setup=self.only_fails_in("*/fix-*"))
        self.add(1)
        self.plan(worker={"1": ["red"]})
        res = self.controller()
        self.assertEqual(res.returncode, 3, res.stdout + res.stderr)
        self.assertTrue([p for p in self.pages("env", "g1") if "worktree_setup" in p["text"]],
                        self.pages("env"))
        self.assertFalse(self.ran("fixer", "g1"), "the fixer ran")
        g = self.snap().groups["g1"]
        self.assertEqual((g["state"], g["fix_round"], g.get("job", "absent")), ("pr", 0, None), g)
        self.config(worktree_setup="touch .setup-done")
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.snap().groups["g1"]["fix_round"], 1)

    def test_6_a_failed_setup_in_the_release_tree_is_a_failed_release_decision(self):
        self.config(worktree_setup=self.only_fails_in("*/release-*"))
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.snap().groups["g1"]["release"], "failed")
        self.assertTrue([p for p in self.pages("decision", "g1") if "worktree_setup" in p["text"]],
                        self.pages("decision"))


class Landing(LoopCase):
    def test_the_code_map_is_refreshed_on_main_and_the_cost_is_totalled(self):
        with open(os.path.join(self.proj, "app.py"), "w") as fh:
            fh.write('"""The app."""\n\n\ndef hello(name):\n    """Say hello."""\n    return name\n')
        git(self.proj, "add", "app.py")
        git(self.proj, "commit", "-q", "-m", "app")
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        index = git(self.proj, "show", "main:CODE-MAP.md")
        self.assertIn("## app.py - The app.", index)
        self.assertIn("`hello(name)` Say hello.", index)
        self.assertIn("devloop: code map for group g1", git(self.proj, "log", "--format=%s", "main"))
        total = self.snap().ticket("1")["cost_total"]
        self.assertEqual(total["runs"], 2)
        self.assertEqual(total["unknown_runs"], 2, "fake agents report no tokens: unknown, not 0")
        self.assertIsNone(total["input_tokens"])


class StoppedRegressionPieces(LoopCase):
    """#124: regression.py records a stop signal; the controller names the fate of a regression
    that wrote no result; a controller stop alone leaves a running regression to the next one."""

    def setUp(self):
        super().setUp()
        for name in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(name, None)
        self.out = os.path.join(self.tmp, "r.json")

    def slow_full(self, seconds):
        path = os.path.join(self.proj, "board", "config.json")
        with open(path) as fh:
            cfg = json.load(fh)
        cfg["tests"]["full"] = "sleep %d; %s full" % (seconds, TESTS)
        with open(path, "w") as fh:
            json.dump(cfg, fh)
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "slow full")

    def live(self):
        try:
            with open(os.path.join(self.tmp, "wt", "runs", "live", "regression-int-g1.json")) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def until(self, what, pred, proc, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return
            if proc.poll() is not None:
                self.fail("process exited (rc %s) before %s" % (proc.returncode, what))
            time.sleep(0.2)
        self.fail("timed out waiting for " + what)

    # ------------------------------------------------------------ regression.py

    def test_stopped_report_shape(self):
        import regression
        r = regression.stopped_report(signal.SIGHUP, "quick", "abc")
        self.assertEqual((r["status"], r["signal"], r["failures"], r["flaky"], r["sha"]),
                         ("unmeasured", "SIGHUP", [], [], "abc"))
        self.assertTrue(r["summary"].startswith("stopped by SIGHUP during quick"), r["summary"])
        self.assertIn("a controller restart leaves a running regression alone", r["summary"])
        self.assertIn("at", r)
        self.assertNotIn("sha", regression.stopped_report(signal.SIGTERM, "slot", None))

    def test_sighup_during_full_cleans_up_and_writes_out(self):
        self.slow_full(30)
        git(self.proj, "branch", "int/g1", "main")
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "regression.py"), "--branch", "int/g1",
                                 "--out", self.out], cwd=self.proj, env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.terminate())
        self.until("stage full", lambda: self.live().get("stage") == "full", proc, 30)
        proc.send_signal(signal.SIGHUP)
        self.assertEqual(proc.wait(20), 2)
        with open(self.out) as fh:
            report = json.load(fh)
        self.assertEqual((report["status"], report["signal"]), ("unmeasured", "SIGHUP"))
        self.assertTrue(report["summary"].startswith("stopped by SIGHUP during full"), report)
        self.assertEqual(report["sha"], git(self.proj, "rev-parse", "int/g1").strip())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "wt", "runs", "live",
                                                     "regression-int-g1.json")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "wt", "regression-int-g1")))

    # ------------------------------------------------------------ controller: the fate text

    def ctl(self):
        import ticket_controller
        from core.config import Config
        ctl = ticket_controller.Controller(Config(self.proj), self.pool)
        ctl.forever = True
        return ctl

    def put_g1(self):
        g = {"id": "g1", "branch": "int/g1", "state": "pr", "members": [], "fix_round": 0, "job": None}
        self.pool.update(lambda tx: tx.put_group(g), "test: put g1")

    def write_log(self, text):
        from core.config import Config
        log = os.path.join(Config(self.proj).runs_dir("regression", "g1"), "regression.log")
        with open(log, "w") as fh:
            fh.write(text)
        return os.path.abspath(log)

    def job(self):
        return {"kind": "regression", "out": os.path.join(self.tmp, "missing.json"),
                "stamp": {"pid": 4242, "started": "2026-11-24T00:00:00Z"}}

    def test_job_fate_texts(self):
        ctl = self.ctl()
        log = self.write_log("line one\n\nboom at the end\n\n")
        self.assertEqual(ctl.job_fate("g1", self.job(), None),
                         "regression.py (pid 4242, started 2026-11-24T00:00:00Z) is no longer running "
                         "and wrote no result; it was not this controller's child, so its exit status "
                         "is unknown; log %s: boom at the end" % log)
        self.assertEqual(ctl.job_fate("g1", self.job(), -15),
                         "regression.py (pid 4242) was killed by signal SIGTERM (15) before it wrote "
                         "a result; log %s: boom at the end" % log)
        self.assertEqual(ctl.job_fate("g1", self.job(), 0),
                         "regression.py (pid 4242) exited with rc 0 without writing a result; "
                         "log %s: boom at the end" % log)
        self.write_log("\n  \n")
        self.assertTrue(ctl.job_fate("g1", self.job(), 1).endswith(": (empty)"))
        os.unlink(log)
        self.assertTrue(ctl.job_fate("g1", self.job(), 1).endswith(": (empty)"))

    def test_after_regression_uses_the_fate_only_without_a_summary(self):
        ctl = self.ctl()
        self.write_log("last words\n")
        self.put_g1()
        job = self.job()
        with open(job["out"], "w") as fh:
            json.dump({"status": "unmeasured", "summary": None}, fh)
        ctl.after_regression("g1", job, -9)
        g = self.snap().groups["g1"]
        self.assertIn("was killed by signal SIGKILL (9)", g["last_regression"])
        self.assertIn("last words", g["paused"]["text"])
        text = [r["text"] for r in self.snap().inbox.values() if r["kind"] == "decision"]
        self.assertEqual(len(text), 1)
        self.assertIn("regression unmeasured - regression.py (pid 4242) was killed", text[0])
        self.assertNotIn("None", text[0])

        self.put_g1()
        with open(job["out"], "w") as fh:
            json.dump({"status": "unmeasured", "summary": "no slot"}, fh)
        ctl.after_regression("g1", job, -9)
        g = self.snap().groups["g1"]
        self.assertEqual((g["last_regression"], g["paused"]["text"]), ("no slot", "no slot"))

    # ------------------------------------------------------------ a controller stop alone

    def stop_controller_only(self, sig):
        self.slow_full(12)
        self.add(1)
        log = open(os.path.join(self.tmp, "forever.log"), "w")
        self.addCleanup(log.close)
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "ticket_controller.py"), "--forever"],
                                cwd=self.proj, env=self.env, stdout=log, stderr=subprocess.STDOUT)
        self.addCleanup(lambda: proc.poll() is None and proc.terminate())

        def job():
            return (self.snap().groups.get("g1") or {}).get("job") or {}
        self.until("a running regression", lambda: job().get("kind") == "regression"
                   and self.live().get("stage") == "full", proc, 120)
        pid = job()["stamp"]["pid"]
        proc.send_signal(sig)
        proc.wait(30)
        time.sleep(1)
        self.assertTrue(osx.alive(pid), "the regression died with the controller")
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        snap = self.snap()
        self.assertEqual(snap.ticket("1")["state"], "landed")
        self.assertEqual(snap.groups["g1"]["state"], "landed")
        started = [h for h in snap.groups["g1"].get("history") or []
                   if h.get("event") == "regression started"]
        self.assertEqual(len(started), 1, snap.groups["g1"].get("history"))
        self.assertEqual([r for r in snap.inbox.values() if r["kind"] == "decision"], [])

    def test_sigterm_to_the_controller_leaves_the_regression_running(self):
        self.stop_controller_only(signal.SIGTERM)

    def test_sigint_to_the_controller_leaves_the_regression_running(self):
        self.stop_controller_only(signal.SIGINT)


class T72Acceptance(LoopCase):
    """#72: when main moves, the controller merges it into every waiting integration branch before
    that branch's next regression; only a merge conflict costs a fix round. Expected values come
    from the ticket's acceptance text, never from ticket_controller.py or auto_fixer.py."""

    NRV = "main took only no-reverify paths: merged without re-verifying"

    def setUp(self):
        super().setUp()
        for name in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(name, None)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("DEVLOOP_")}
        patch = mock.patch.dict(os.environ, clean, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.log = os.path.join(self.tmp, "intervals.txt")

    # ------------------------------------------------------------ helpers

    def full_runs(self):
        if not os.path.exists(self.log):
            return 0
        with open(self.log) as fh:
            return sum(1 for line in fh if line.startswith("tests full "))

    def fixer_calls(self):
        path = os.path.join(os.path.dirname(self.plan_path), "count-fixer-g1")
        if not os.path.exists(path):
            return 0
        with open(path) as fh:
            return int(fh.read())

    def history(self):
        return list(self.snap().groups["g1"].get("history") or [])

    def commit_file(self, ref, name, text, msg):
        """One commit writing name=text on top of ref (a branch), made in a throwaway worktree."""
        side = os.path.join(self.tmp, "side-%s" % ref.replace("/", "-"))
        if ref == "main":
            with open(os.path.join(self.proj, name), "w") as fh:
                fh.write(text)
            git(self.proj, "add", name)
            git(self.proj, "commit", "-q", "-m", msg)
            return git(self.proj, "rev-parse", "main")
        git(self.proj, "worktree", "add", "-q", side, ref)
        with open(os.path.join(side, name), "w") as fh:
            fh.write(text)
        git(side, "add", name)
        git(side, "commit", "-q", "-m", msg)
        sha = git(side, "rev-parse", "HEAD")
        git(self.proj, "worktree", "remove", "--force", side)
        return sha

    def open_group(self):
        self.add(1, group="g1")
        git(self.proj, "branch", "int/g1", "main")
        g = {"id": "g1", "branch": "int/g1", "state": "open", "alone": False, "members": ["1"],
             "fix_round": 0, "job": None}
        self.pool.update(lambda tx: tx.put_group(g), "test: group g1")

    def advance(self):
        import ticket_controller
        from core.config import Config
        ctl = ticket_controller.Controller(Config(self.proj), self.pool)
        ctl.advance_groups(self.pool.snapshot())

    def assert_open_untouched_state(self):
        g = self.snap().groups["g1"]
        self.assertEqual(g.get("state"), "open")
        self.assertEqual(g.get("fix_round"), 0)
        self.assertIsNone(g.get("job"))
        self.assertEqual(self.state(1), "ready")

    def events_naming(self, before, sha):
        return [h for h in self.history()[len(before):] if sha[:12] in json.dumps(h)]

    # ------------------------------------------------------------ items 1-2: end to end

    def test_1_worker_moves_main(self):
        self.env["FAKE_INTERVALS"] = self.log
        self.add(1)
        self.plan(worker={"1": ["move_main"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertTrue(self.main_has("NOTES.md") and self.main_has("impl_1.txt"))
        self.assertEqual(self.fixer_calls(), 0, "the fake fixer is never called")
        self.assertEqual(self.snap().groups["g1"]["fix_round"], 0)
        self.assertEqual(self.full_runs(), 1)

    def test_2_main_moves_during_the_first_regression(self):
        self.env["FAKE_INTERVALS"] = self.log
        self.env["FAKE_MAIN_DURING_FULL"] = json.dumps({"NOTES.md": "notes\n"})
        self.add(1)
        self.plan()
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertTrue(self.main_has("NOTES.md") and self.main_has("impl_1.txt"))
        self.assertEqual(self.full_runs(), 2)
        events = [h.get("event") for h in self.history()]
        self.assertNotIn(self.NRV, events)
        self.assertEqual(self.fixer_calls(), 0, "the fake fixer is never called")
        self.assertEqual(self.snap().groups["g1"]["fix_round"], 0)

    # ------------------------------------------------------------ items 3-5: in process

    def test_3_open_group_without_own_commits_moves_to_main(self):
        self.open_group()
        before = self.history()
        main = self.commit_file("main", "NOTES.md", "notes\n", "docs")
        self.advance()
        self.assert_open_untouched_state()
        self.assertEqual(git(self.proj, "rev-parse", "int/g1"), main)
        self.assertEqual(len(self.events_naming(before, main)), 1, self.history())

    def test_4_open_group_with_own_commits_gets_a_merge(self):
        self.open_group()
        own = self.commit_file("int/g1", "group.txt", "group\n", "group work")
        main = self.commit_file("main", "NOTES.md", "notes\n", "docs")
        self.advance()
        self.assert_open_untouched_state()
        parents = git(self.proj, "rev-list", "--parents", "-n1", "int/g1").split()[1:]
        self.assertEqual(parents, [own, main])

    def test_5_open_group_with_a_conflict_is_left_alone(self):
        self.open_group()
        own = self.commit_file("int/g1", "shared.txt", "group\n", "group shared")
        main = self.commit_file("main", "shared.txt", "main\n", "main shared")
        trees = git(self.proj, "worktree", "list", "--porcelain")
        before = self.history()
        self.advance()
        self.advance()
        self.assert_open_untouched_state()
        self.assertEqual(git(self.proj, "rev-parse", "int/g1"), own)
        self.assertEqual(self.fixer_calls(), 0, "no fixer started")
        self.assertEqual(git(self.proj, "worktree", "list", "--porcelain"), trees)
        self.assertEqual(len(self.events_naming(before, main)), 1, self.history())

    # ------------------------------------------------------------ item 6: auto_fixer CLI

    def test_6_auto_fixer_runs_the_agent_after_a_clean_main_merge(self):
        self.config(max_rounds=1)
        self.add(1)
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["nothing"]})
        self.controller()
        tree = os.path.join(self.tmp, "wt", "fix-g1")
        self.assertEqual(git(tree, "symbolic-ref", "--short", "HEAD"), "int/g1",
                         "fixture: fix-g1 is on int/g1 after the first round")
        self.commit_file("main", "NOTES.md", "notes\n", "docs")
        report = os.path.join(self.tmp, "main-moved.json")
        with open(report, "w") as fh:
            json.dump({"status": "main_moved", "summary": "main moved"}, fh)
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["nothing", "fix"]})
        before = self.fixer_calls()
        res = self.run_py("auto_fixer.py", "--group", "g1", "--report", report)
        self.assertEqual(self.fixer_calls() - before, 1,
                         "the fake fixer is called once: %s" % (res.stdout + res.stderr))


class T63Acceptance(LoopCase):
    """#63: a controller run without --forever that ends with work left writes one idle page (ref
    controller) and the same lines on stderr, one per item, saying what it waits for. Expected lines
    come from the ticket's line formats and the fake agent's texts, never from ticket_controller.py."""

    def setUp(self):
        super().setUp()
        for k in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(k, None)

    def idle_pages(self):
        return [r for r in self.snap().inbox.values() if r.get("kind") == "idle"]

    @staticmethod
    def item_lines(text):
        return [ln for ln in text.splitlines() if ln.startswith("#") or ln.startswith("g")]

    def test_stuck_and_waiting_tickets_and_the_stuck_group_get_one_idle_page(self):
        self.add(1)
        self.add(2, deps=(1,))
        self.add(4, state="cancelled")
        self.add(3, deps=(4,))
        self.plan(worker={"1": ["blocked"]})
        res = self.controller()
        # acceptance 1 and 6: exit 0, the run ends (no loop) within the timeout
        self.assertEqual(res.returncode, 0, res.stderr)
        pages = self.idle_pages()
        self.assertEqual(len(pages), 1, "exactly one idle page: %r" % pages)
        self.assertEqual(pages[0]["ref"], "controller")
        expected = ["#1 stuck (decision): need the user",
                    "#2 ready, waits for: #1 stuck",
                    "#3 ready, waits for: #4 cancelled",
                    "g1 waits for the decision on #1",                    # #134 (D-097)
                    "g2 waits for g1 to land (place 2 in the chain)"]
        # acceptance 2: those whole lines, in that order, optionally after one header line
        lines = pages[0]["text"].splitlines()
        if lines and not (lines[0].startswith("#") or lines[0].startswith("g")):
            lines = lines[1:]
        lines = [ln for ln in lines if ln.strip()]
        self.assertEqual(lines, expected, pages[0]["text"])
        # acceptance 4: landed / cancelled tickets get no line of their own
        self.assertFalse(any(ln.startswith("#4 ") for ln in lines))
        # acceptance 3: stderr has each line as a whole line
        err = res.stderr.splitlines()
        for want in expected:
            self.assertIn(want, err, res.stderr)

    def test_a_dependency_merged_in_a_stuck_group_is_named_with_its_group(self):
        self.add(1)
        # #2 gets a group of its own (g2), so it stays ready waiting for #1 merged in g1 (ticket test_plan)
        self.add(2, deps=(1,))
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["blocked"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        snap = self.snap()
        self.assertEqual(snap.ticket("1").get("state"), "merged")     # fixture premise
        self.assertEqual(snap.ticket("2").get("state"), "ready")      # fixture premise
        g1 = snap.groups["g1"]
        self.assertEqual(g1["state"], "stuck")                         # fixture premise
        last_event = (g1.get("history") or [{}])[-1].get("event")
        pages = self.idle_pages()
        self.assertEqual(len(pages), 1, "exactly one idle page: %r" % pages)
        self.assertEqual(pages[0]["ref"], "controller")
        expected = ["#2 ready, waits for: #1 merged in g1 (stuck)",
                    "g1 stuck: %s" % last_event,
                    "g2 waits for g1 to land (place 2 in the chain)"]
        lines = self.item_lines(pages[0]["text"])
        self.assertEqual(lines, expected, pages[0]["text"])
        err = res.stderr.splitlines()
        for want in expected:
            self.assertIn(want, err, res.stderr)


class T29Acceptance(LoopCase):
    """Ticket #29: devloop.no_reverify_paths - main moved only there is merged in without a second regression."""

    EVENT = "main took only no-reverify paths: merged without re-verifying"

    def test_v1_a_memory_only_main_move_lands_with_one_full_regression(self):
        """Item 3."""
        log = os.path.join(self.tmp, "intervals.txt")
        self.env["FAKE_INTERVALS"] = log
        self.env["FAKE_MAIN_DURING_FULL"] = json.dumps({"memory/note.md": "note\n"})
        self.add(1)
        self.plan()
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertTrue(self.main_has("memory/note.md"), "main lacks memory/note.md")
        self.assertTrue(self.main_has("impl_1.txt"), "main lacks impl_1.txt")
        g = self.snap().groups["g1"]
        events = [h.get("event") or "" for h in g.get("history") or []]
        self.assertEqual(g["fix_round"], 0, events)
        self.assertIn(self.EVENT, events)
        self.assertEqual([e for e in events if e.startswith("fix round")], [], events)
        with open(log) as fh:
            full = [line for line in fh if line.startswith("tests full ")]
        self.assertEqual(len(full), 1, full)

    def test_v2_the_default_list_and_a_project_list(self):
        """Item 1."""
        from core.config import Config
        cfg = Config(self.proj)
        self.assertIn("no_reverify_paths", cfg.values, "no devloop.no_reverify_paths default")
        self.assertEqual(cfg.no_reverify_paths, ["memory/", "docs/DECISIONS.md", "docs/HANDOFF.md"])
        self.config(no_reverify_paths=["x/"])
        self.assertEqual(Config(self.proj).no_reverify_paths, ["x/"])

    def test_v3_a_value_that_is_not_a_list_of_strings_stops_every_script(self):
        """Item 2."""
        for bad in ("memory/", [1]):
            self.config(no_reverify_paths=bad)
            res = self.run_py("tickets.py", "list")
            self.assertEqual(res.returncode, 2, "%r: %s" % (bad, res.stdout + res.stderr))
            self.assertIn("devloop.no_reverify_paths", res.stderr, repr(bad))

    def test_v4_the_readme_documents_the_setting(self):
        """Item 4."""
        with open(os.path.join(LOOP, "README.md")) as fh:
            text = fh.read()
        self.assertIn("no_reverify_paths", text)
        self.assertIn("docs/HANDOFF.md", text)


class WorktreeSetup(LoopCase):
    """devloop.worktree_setup: the implementer's tests (core/worktrees.py and what it leaves)."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ["AC_ROOT"] = self.proj
        self.wt = os.path.join(self.tmp, "wt")
        self.count = os.path.join(self.tmp, "setup-count")

    def cfg(self, cmd):
        self.config(worktree_setup=cmd)
        from core.config import Config
        return Config(self.proj)

    def repo(self):
        from core.gitx import Git
        return Git(self.proj)

    def runs(self):
        try:
            with open(self.count) as fh:
                return fh.read().splitlines()
        except FileNotFoundError:
            return []

    def test_null_runs_nothing_and_writes_no_log(self):
        from core import worktrees
        cfg = self.cfg(None)
        worktrees.branch_tree(cfg, self.repo(), os.path.join(self.wt, "t1"), "t/1", start="main")
        self.assertFalse(os.path.exists(os.path.join(self.wt, "runs", "setup", "t1.log")))

    def test_output_is_kept_in_the_log_named_after_the_folder(self):
        from core import worktrees
        cfg = self.cfg('echo "hello from $PWD"; echo "root=$AC_ROOT"')
        path = os.path.join(self.wt, "t1-cases")
        worktrees.branch_tree(cfg, self.repo(), path, "t/1-cases", start="main")
        with open(os.path.join(self.wt, "runs", "setup", "t1-cases.log")) as fh:
            log = fh.read()
        self.assert_set_up_in(log, path)
        self.assertIn("root=%s" % self.proj, log)

    def assert_set_up_in(self, log, path):
        m = re.search(r"^hello from (.*)$", log, re.M)
        self.assertIsNotNone(m, log)
        self.assertEqual(os.path.realpath(m.group(1)), os.path.realpath(path))

    def test_log_check_survives_a_link_whose_real_path_contains_the_link_path(self):
        from core import worktrees
        T = os.path.realpath(self.tmp)
        real = os.path.join(T, "private") + os.path.join(T, "var")
        os.makedirs(real)
        link = os.path.join(T, "var")
        os.symlink(real, link)
        self.assertIn(link, os.path.realpath(link))
        self.assertNotEqual(link, os.path.realpath(link))
        self.config(worktrees=os.path.join(link, "wt"))
        cfg = self.cfg('echo "hello from $PWD"; echo "root=$AC_ROOT"')
        path = os.path.join(link, "wt", "t1-cases")
        worktrees.branch_tree(cfg, self.repo(), path, "t/1-cases", start="main")
        with open(os.path.join(link, "wt", "runs", "setup", "t1-cases.log")) as fh:
            log = fh.read()
        self.assert_set_up_in(log, path)
        self.assertIn("root=%s" % self.proj, log)

    def test_a_reused_worktree_is_not_set_up_again(self):
        from core import worktrees
        cfg = self.cfg("echo run >> %s" % self.count)
        path = os.path.join(self.wt, "t1")
        worktrees.branch_tree(cfg, self.repo(), path, "t/1", start="main")
        worktrees.branch_tree(cfg, self.repo(), path, "t/1", start="main")
        self.assertEqual(self.runs(), ["run"])

    def test_a_failed_setup_removes_the_worktree_and_keeps_the_branch(self):
        from core import worktrees
        cfg = self.cfg("echo run >> %s; echo broken-tool-missing; exit 3" % self.count)
        path = os.path.join(self.wt, "t1")
        with self.assertRaises(worktrees.SetupFailed) as ctx:
            worktrees.branch_tree(cfg, self.repo(), path, "t/1", start="main")
        text = str(ctx.exception)
        self.assertIn("devloop.worktree_setup failed in %s (rc 3)" % path, text)
        self.assertIn("broken-tool-missing", text)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(self.repo().sha("refs/heads/t/1"))
        with self.assertRaises(worktrees.SetupFailed):   # the next run creates and sets it up again
            worktrees.branch_tree(cfg, self.repo(), path, "t/1", start="main")
        self.assertEqual(self.runs(), ["run", "run"])

    def test_a_failed_setup_removes_a_detached_tree(self):
        from core import worktrees
        cfg = self.cfg("exit 5")
        path = os.path.join(self.wt, "release-g1")
        with self.assertRaises(worktrees.SetupFailed) as ctx:
            worktrees.detached_tree(cfg, self.repo(), path, self.repo().sha("main"))
        self.assertIn("(rc 5)", str(ctx.exception))
        self.assertFalse(os.path.exists(path))

    def test_a_setup_past_its_limit_is_stopped_with_its_group_and_fails(self):
        from core import worktrees
        from unittest import mock
        pidfile = os.path.join(self.tmp, "child.pid")
        # the shell starts a background child in the same process group; both must be stopped
        cfg = self.cfg("sleep 60 & echo $! > %s; wait" % pidfile)
        path = os.path.join(self.wt, "fix-g1")
        began = time.time()
        with mock.patch.object(worktrees, "SETUP_TIMEOUT", 1):
            with self.assertRaises(worktrees.SetupFailed) as ctx:
                worktrees.detached_tree(cfg, self.repo(), path, self.repo().sha("main"))
        self.assertLess(time.time() - began, 30)
        self.assertIn("timeout", str(ctx.exception))
        with open(pidfile) as fh:
            child = int(fh.read().strip())
        deadline = time.time() + 10
        while osx.alive(child) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(osx.alive(child), "the setup's background child is still running")
        self.assertFalse(os.path.exists(path))

    def test_the_default_limit_is_600_seconds(self):
        from core import worktrees
        self.assertEqual(worktrees.SETUP_TIMEOUT, 600)

    def test_the_controllers_own_trees_are_not_set_up(self):
        log = os.path.join(self.tmp, "setup.log")
        self.config(worktree_setup='basename "$PWD" >> %s' % log)
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertFalse(os.path.exists(os.path.join(self.wt, "runs", "setup", "index-g1.log")))
        with open(log) as fh:
            names = set(fh.read().split())
        self.assertIn("t1", names)                  # the setup did run in devloop's other trees
        self.assertIn("release-g1", names)
        self.assertFalse([n for n in names if n.startswith(("nrv-", "index-"))], names)
        # the index tree was really made this run (refresh_index ran), so its absence above counts
        self.assertTrue(self.main_has("CODE-MAP.md"))

    def test_readme_project_settings_documents_worktree_setup(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            text = fh.read()
        start = text.index("## Project settings")
        section = text[start:text.index("\n## ", start + 1)]
        self.assertIn("devloop.worktree_setup", section)
        for words in ("cwd", "AC_ROOT", "600 s", "runs/setup/", "issuer", "regression", "fixer",
                      "release", "not set up again"):
            self.assertIn(words, section)


class NoReverifyPaths(LoopCase):
    """#29 / D-090: main moved only in devloop.no_reverify_paths -> the controller merges it in."""

    NRV = "main took only no-reverify paths: merged without re-verifying"

    def move(self, main_files, worker_files=None):
        self.env["FAKE_MAIN_DURING_FULL"] = json.dumps(main_files)
        self.env["FAKE_INTERVALS"] = self.log = os.path.join(self.tmp, "intervals.txt")
        self.add(1)
        if worker_files:
            self.env["FAKE_WORKER_FILES"] = json.dumps(worker_files)
            self.plan(worker={"1": ["write_files"]})
        else:
            self.plan()
        return self.controller()

    def full_runs(self):
        with open(self.log) as fh:
            return sum(1 for line in fh if line.startswith("tests full "))

    def events(self):
        return [e.get("event") for e in self.snap().groups["g1"].get("history") or []]

    def assert_fixer_path(self, res):
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.snap().groups["g1"]["fix_round"], 0)   # #72: a clean merge, no fix round
        self.assertNotIn(self.NRV, self.events())
        self.assertEqual(self.full_runs(), 2)

    def test_shortcut_lands_with_one_regression(self):
        res = self.move({"memory/note.md": "note\n", "docs/HANDOFF.md": "h\n"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        g = self.snap().groups["g1"]
        self.assertEqual(g["fix_round"], 0)
        self.assertIn(self.NRV, self.events())
        self.assertEqual(self.full_runs(), 1)
        head = git(self.proj, "rev-parse", "main")
        merge = git(self.proj, "rev-list", "--merges", "-n1", head)   # the code map may sit on top
        self.assertTrue(merge)
        self.assertEqual(len(git(self.proj, "rev-list", "--parents", "-n1", merge).split()), 3)
        self.assertEqual(g["landed_sha"], head)
        self.assertEqual(glob.glob(os.path.join(self.tmp, "wt", "nrv-*")), [])

    def test_empty_list_turns_the_shortcut_off(self):
        self.config(no_reverify_paths=[])
        self.assert_fixer_path(self.move({"memory/note.md": "note\n"}))

    def test_a_mixed_move_takes_the_fixer_path(self):
        self.assert_fixer_path(self.move({"memory/note.md": "note\n", "NOTES.md": "notes\n"}))

    def test_a_conflict_in_the_loop_takes_the_fixer_path(self):
        res = self.move({"memory/note.md": "note\n"}, worker_files={"memory/note.md": "worker\n"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertGreaterEqual(self.snap().groups["g1"]["fix_round"], 1)
        self.assertNotIn(self.NRV, self.events())
        self.assertGreaterEqual(self.full_runs(), 2)
        self.assertEqual(glob.glob(os.path.join(self.tmp, "wt", "nrv-*")), [])

    def test_a_conflict_aborts_and_leaves_the_branch(self):
        from core.gitx import Git
        import ticket_controller
        seen = {}
        real = Git.move_ref

        def spy(repo, branch, new, old):
            seen.setdefault("moves", []).append(branch)
            return real(repo, branch, new, old)
        git(self.proj, "branch", "int/g1", "main")
        base = git(self.proj, "rev-parse", "main")
        tree = os.path.join(self.tmp, "side")
        git(self.proj, "worktree", "add", "-q", "--detach", tree, "int/g1")
        os.makedirs(os.path.join(tree, "memory"))
        with open(os.path.join(tree, "memory", "note.md"), "w") as fh:
            fh.write("worker\n")
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "w")
        verified = git(tree, "rev-parse", "HEAD")
        git(self.proj, "update-ref", "refs/heads/int/g1", verified)
        git(self.proj, "worktree", "remove", "--force", tree)
        os.makedirs(os.path.join(self.proj, "memory"))
        with open(os.path.join(self.proj, "memory", "note.md"), "w") as fh:
            fh.write("note\n")
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "m")
        self.assertNotEqual(git(self.proj, "rev-parse", "main"), base)
        from core.config import Config
        c = ticket_controller.Controller.__new__(ticket_controller.Controller)
        c.cfg, c.repo = Config(self.proj), Git(self.proj)
        c.set_group = lambda *a, **k: seen.setdefault("set", []).append(a)
        Git.move_ref = spy
        self.addCleanup(setattr, Git, "move_ref", real)
        out = c.merge_no_reverify({"id": "g1", "branch": "int/g1"}, verified, git(self.proj, "rev-parse", "main"))
        self.assertIsNone(out)
        self.assertEqual(git(self.proj, "rev-parse", "int/g1"), verified)
        self.assertNotIn("moves", seen)
        self.assertNotIn("set", seen)
        self.assertEqual(glob.glob(os.path.join(self.tmp, "wt", "nrv-*")), [])
        self.assertNotIn("nrv-g1", git(self.proj, "worktree", "list"))

    def test_a_lost_compare_and_swap_is_reported(self):
        from core.config import Config
        from core.gitx import Git
        import ticket_controller
        git(self.proj, "branch", "int/g1", "main")
        verified = git(self.proj, "rev-parse", "main")
        os.makedirs(os.path.join(self.proj, "memory"))
        with open(os.path.join(self.proj, "memory", "note.md"), "w") as fh:
            fh.write("note\n")
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "m")
        c = ticket_controller.Controller.__new__(ticket_controller.Controller)
        c.cfg, c.repo = Config(self.proj), Git(self.proj)
        c.set_group = lambda *a, **k: self.fail("no event on a lost swap")
        real = Git.move_ref
        Git.move_ref = lambda *a: False      # someone else moved the branch first
        self.addCleanup(setattr, Git, "move_ref", real)
        out = c.merge_no_reverify({"id": "g1", "branch": "int/g1"}, verified, git(self.proj, "rev-parse", "main"))
        self.assertEqual(out, ticket_controller.LOST)
        self.assertEqual(git(self.proj, "rev-parse", "int/g1"), verified)
        self.assertEqual(glob.glob(os.path.join(self.tmp, "wt", "nrv-*")), [])


class FollowMainUnits(LoopCase):
    """#72 acceptance 7-9 (the implementer's): which groups the merge step touches, and what a
    conflict does when the group is due for its regression."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)

    def commit_on(self, branch, files):
        if branch == "main":                 # main is checked out in the project; keep its config edits
            for rel, text in files.items():
                with open(os.path.join(self.proj, rel), "w") as fh:
                    fh.write(text)
            git(self.proj, "add", "--", *files)
            git(self.proj, "commit", "-q", "-m", "m")
            return git(self.proj, "rev-parse", "HEAD")
        tree = os.path.join(self.tmp, "side")
        git(self.proj, "worktree", "add", "-q", "--detach", tree, branch)
        for rel, text in files.items():
            with open(os.path.join(tree, rel), "w") as fh:
                fh.write(text)
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "c")
        sha = git(tree, "rev-parse", "HEAD")
        git(self.proj, "worktree", "remove", "--force", tree)
        git(self.proj, "update-ref", "refs/heads/" + branch, sha)
        return sha

    def group(self, gid="g1", branch=True, **fields):
        g = {"id": gid, "branch": "int/" + gid, "state": "open", "alone": False, "members": ["1"],
             "fix_round": 0, "job": None}
        g.update(fields)
        if branch:
            git(self.proj, "branch", "int/" + gid, "main")

        def change(tx):
            tx.put_group(g)
        self.pool.update(change, "test: group %s" % gid)

    def ctl(self, forever=False):
        from core.config import Config
        import ticket_controller
        c = ticket_controller.Controller(Config(self.proj), self.pool)
        c.forever = forever
        c.started = []
        c._start_job = lambda gid, kind, argv, out: c.started.append((gid, kind))
        return c

    def history(self, gid="g1"):
        return [h.get("event") or "" for h in self.snap().groups[gid].get("history") or []]

    def test_untouched_groups(self):
        """Item 8: landed, cancelled, with a job, no branch."""
        self.add(1, group="g4")
        self.group("g1", state="landed")
        self.group("g2", state="cancelled")
        self.group("g3", state="pr", job={"kind": "regression", "stamp": {}, "out": "x"})
        self.group("g4", branch=False)
        before = {g: git(self.proj, "rev-parse", "int/" + g) for g in ("g1", "g2", "g3")}
        events = {g: self.history(g) for g in ("g1", "g2", "g3", "g4")}
        main = self.commit_on("main", {"NOTES.md": "n\n"})
        c = self.ctl()
        c.advance_groups(self.snap())
        for g, sha in before.items():
            self.assertEqual(git(self.proj, "rev-parse", "int/" + g), sha, g)
        for g, ev in events.items():
            self.assertEqual(self.history(g), ev, g)
            self.assertNotIn(main[:12], " ".join(self.history(g)), g)
        self.assertEqual(c.started, [])

    def test_stuck_group_takes_a_clean_merge(self):
        """Item 9: a stuck group with its own commit merges main, stays stuck."""
        self.add(1)
        self.group(state="stuck")
        own = self.commit_on("int/g1", {"own.txt": "o\n"})
        main = self.commit_on("main", {"NOTES.md": "n\n"})
        c = self.ctl()
        c.advance_groups(self.snap())
        head = git(self.proj, "rev-parse", "int/g1")
        self.assertEqual(git(self.proj, "rev-list", "--parents", "-n1", head).split()[1:], [own, main])
        g = self.snap().groups["g1"]
        self.assertEqual((g["state"], g["fix_round"], g.get("job")), ("stuck", 0, None))
        self.assertEqual(c.started, [])

    def test_conflict_on_stuck_and_paused_groups_starts_nothing(self):
        """Item 9: a conflict waits until the group is due for its regression."""
        self.add(1)
        self.group("g1", state="stuck")
        self.group("g2", state="pr", paused={"reason": "env", "at": "x", "text": ""})
        olds = {g: self.commit_on("int/" + g, {"shared.txt": "group\n"}) for g in ("g1", "g2")}
        main = self.commit_on("main", {"shared.txt": "main\n"})
        c = self.ctl(forever=True)
        for _ in range(2):
            c.advance_groups(self.snap())
        self.assertEqual(c.started, [])
        for gid, old in olds.items():
            self.assertEqual(git(self.proj, "rev-parse", "int/" + gid), old)
            g = self.snap().groups[gid]
            self.assertEqual(g["fix_round"], 0)
            self.assertEqual(sum(main[:12] in e for e in self.history(gid)), 1, gid)
        self.assertEqual(self.snap().groups["g1"]["state"], "stuck")
        self.assertEqual(self.snap().groups["g2"]["state"], "pr")

    def test_conflict_on_a_due_pr_group_goes_to_the_fixer(self):
        """Item 9: counted as a fix round with a main-moved report; not a regression."""
        self.add(1)
        self.group(state="pr")
        self.commit_on("int/g1", {"shared.txt": "group\n"})
        self.commit_on("main", {"shared.txt": "main\n"})
        c = self.ctl()
        c.advance_groups(self.snap())
        self.assertEqual(c.started, [("g1", "fixer")])
        g = self.snap().groups["g1"]
        self.assertEqual((g["state"], g["fix_round"]), ("fixing", 1))
        report = os.path.join(c.cfg.runs_dir("regression", "g1"), "main-moved.json")
        with open(report) as fh:
            self.assertEqual(json.load(fh)["status"], "main_moved")

    def test_conflict_at_max_rounds_is_stuck_with_a_decision(self):
        """Item 9: at devloop.max_rounds, the existing 'main kept moving'."""
        self.config(max_rounds=1)
        self.add(1)
        self.group(state="pr", fix_round=1)
        self.commit_on("int/g1", {"shared.txt": "group\n"})
        self.commit_on("main", {"shared.txt": "main\n"})
        c = self.ctl()
        c.advance_groups(self.snap())
        self.assertEqual(c.started, [])
        g = self.snap().groups["g1"]
        self.assertEqual((g["state"], g["fix_round"]), ("stuck", 1))
        self.assertIn("main kept moving", self.history())
        self.assertEqual(self.inbox_kinds(), ["decision"])

    def caught_up_page(self, later):
        """#120: the 'cannot catch up' page of g1 at max_rounds, with or without a later group g2."""
        self.config(max_rounds=1)
        self.add(1, state="merged", group="g1")
        self.group(state="pr", fix_round=1)
        if later:
            self.add(2, group="g2")
            self.group("g2", branch=False, members=["2"])
        self.commit_on("int/g1", {"shared.txt": "group\n"})
        self.commit_on("main", {"shared.txt": "main\n"})
        self.ctl().advance_groups(self.snap())
        pages = [r for r in self.snap().inbox.values() if r.get("kind") == "decision" and r.get("ref") == "g1"]
        self.assertEqual(len(pages), 1, pages)
        self.assertTrue(pages[0]["text"].startswith("group g1 cannot catch up with main"), pages[0]["text"])
        return pages[0]["text"].split("\n")

    def test_conflict_page_names_the_later_groups(self):
        self.assertIn("later groups waiting for g1 to land: 1 (g2)", self.caught_up_page(True))

    def test_conflict_page_without_later_groups_has_no_chain_line(self):
        self.assertFalse([ln for ln in self.caught_up_page(False) if ln.startswith("later groups")])

    def test_a_pr_group_with_a_clean_merge_gets_its_regression(self):
        self.add(1)
        self.group(state="pr")
        self.commit_on("int/g1", {"own.txt": "o\n"})
        main = self.commit_on("main", {"NOTES.md": "n\n"})
        c = self.ctl()
        c.advance_groups(self.snap())
        self.assertEqual(c.started, [("g1", "regression")])
        self.assertTrue(c.repo.is_ancestor(main, "int/g1"))
        self.assertEqual(self.snap().groups["g1"]["fix_round"], 0)

    def test_a_lost_swap_is_not_tried(self):
        from core.gitx import Git
        self.add(1, group="g1")
        self.group()
        old = git(self.proj, "rev-parse", "int/g1")
        self.commit_on("main", {"NOTES.md": "n\n"})
        c = self.ctl()
        with mock.patch.object(Git, "move_ref", lambda *a: False):
            c.advance_groups(self.snap())
        self.assertEqual(git(self.proj, "rev-parse", "int/g1"), old)
        self.assertNotIn("main_tried", self.snap().groups["g1"])
        c.advance_groups(self.snap())
        self.assertEqual(git(self.proj, "rev-parse", "int/g1"), git(self.proj, "rev-parse", "main"))

    def test_conflict_when_due_for_regression_end_to_end(self):
        """Item 7."""
        self.env["FAKE_MAIN_DURING_FULL"] = json.dumps({"shared.txt": "main\n"})
        self.add(1)
        self.plan(worker={"1": ["shared"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.snap().groups["g1"]["fix_round"], 1)


class MainAncestorJoinUnits(LoopCase):
    """#104, #118: a member with a landed dependency starts only once its group's branch has main."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)

    def commit_on(self, branch, files):
        if branch == "main":                 # main is checked out in the project; keep its config edits
            for rel, text in files.items():
                with open(os.path.join(self.proj, rel), "w") as fh:
                    fh.write(text)
            git(self.proj, "add", "--", *files)
            git(self.proj, "commit", "-q", "-m", "m")
            return git(self.proj, "rev-parse", "HEAD")
        tree = os.path.join(self.tmp, "side")
        git(self.proj, "worktree", "add", "-q", "--detach", tree, branch)
        for rel, text in files.items():
            with open(os.path.join(tree, rel), "w") as fh:
                fh.write(text)
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "c")
        sha = git(tree, "rev-parse", "HEAD")
        git(self.proj, "worktree", "remove", "--force", tree)
        git(self.proj, "update-ref", "refs/heads/" + branch, sha)
        return sha

    def group(self, gid="g1", branch=True, **fields):
        g = {"id": gid, "branch": "int/" + gid, "state": "open", "alone": False, "members": ["1"],
             "fix_round": 0, "job": None}
        g.update(fields)
        if branch:
            git(self.proj, "branch", "int/" + gid, "main")

        def change(tx):
            tx.put_group(g)
        self.pool.update(change, "test: group %s" % gid)

    def ctl(self, forever=False):
        from core.config import Config
        import ticket_controller
        c = ticket_controller.Controller(Config(self.proj), self.pool)
        c.forever = forever
        c.started = []
        c._start_job = lambda gid, kind, argv, out: c.started.append((gid, kind))
        return c

    def build(self, main_files):
        self.add(1, state="landed")
        self.add(2, deps=[1], group="g1")
        self.add(3, state="running", group="g1", target="int/g1")
        self.group("g1", members=["3", "2"])
        self.commit_on("int/g1", {"shared.txt": "group\n"})
        self.commit_on("main", main_files)
        c = self.ctl()
        c.advance_groups(self.snap())
        return c

    def test_main_not_ancestor_waits(self):
        c = self.build({"shared.txt": "main\n"})
        snap = self.snap()
        self.assertFalse(c.has_main(snap.groups["g1"]))
        self.assertFalse(c.can_start_now(snap, "2", snap.groups["g1"]))
        self.assertFalse(c.can_still_start(snap, "2", snap.groups["g1"]))

    def test_member_and_no_landed_dep_make_no_git_call(self):
        c = self.build({"shared.txt": "main\n"})
        snap = self.snap()
        calls = []
        c.repo.is_ancestor = lambda *a: calls.append(a) or False
        self.assertTrue(c.can_start_now(snap, "3", snap.groups["g1"]))
        self.add(4, group="g1")
        snap = self.snap()
        self.assertTrue(c.can_start_now(snap, "4", snap.groups["g1"]))
        self.assertEqual(calls, [])

    def test_clean_merge_joins(self):
        c = self.build({"NOTES.md": "n\n"})
        snap = self.snap()
        self.assertTrue(c.has_main(snap.groups["g1"]))
        self.assertTrue(c.can_start_now(snap, "2", snap.groups["g1"]))
        self.assertTrue(c.can_still_start(snap, "2", snap.groups["g1"]))


class NoReverifyRule(unittest.TestCase):
    def test_path_rule(self):
        import ticket_controller as tc
        e = ["memory/", "docs/HANDOFF.md"]
        self.assertTrue(tc.under_paths("memory/note.md", e))
        self.assertTrue(tc.under_paths("memory/a/b.md", e))
        self.assertTrue(tc.under_paths("docs/HANDOFF.md", e))
        self.assertFalse(tc.under_paths("memory", ["memory/"]))   # a file named like the folder
        self.assertFalse(tc.under_paths("memoryx/note.md", e))
        self.assertFalse(tc.under_paths("docs/HANDOFF.md.bak", e))
        self.assertFalse(tc.under_paths("docs/DECISIONS.md", e))
        self.assertTrue(tc.only_no_reverify([], e))
        self.assertFalse(tc.only_no_reverify([], []))
        self.assertFalse(tc.only_no_reverify(["memory/note.md"], []))
        self.assertTrue(tc.only_no_reverify(["memory/note.md", "docs/HANDOFF.md"], e))
        self.assertFalse(tc.only_no_reverify(["memory/note.md", "src/a.py"], e))

    def test_config_values(self):
        from core.config import Config
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "board"))
            path = os.path.join(d, "board", "config.json")
            for bad in ("memory/", [""], ["ok", 3], None, {"a": 1}):
                with open(path, "w") as fh:
                    json.dump({"devloop": {"no_reverify_paths": bad}}, fh)
                with self.assertRaises(osx.OSFailure) as ctx:
                    Config(d)
                self.assertIn("devloop.no_reverify_paths", str(ctx.exception))
            with open(path, "w") as fh:
                json.dump({"devloop": {"no_reverify_paths": []}}, fh)
            self.assertEqual(Config(d).no_reverify_paths, [])


if __name__ == "__main__":
    unittest.main()
