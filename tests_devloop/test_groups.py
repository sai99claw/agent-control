"""integration groups: membership, ready / cancel of a group, rulings on a group"""

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


class T34Acceptance(LoopCase):
    """#34: tickets.py ready g<N> --note stores the user's ruling on the group for later fixers."""

    HEADER = "## The user's ruling for this group"
    SENTENCE = "This ruling is the only thing that allows you to go beyond the tickets' text"

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)

    def make_stuck(self):
        self.add(1)
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["blocked", "blocked", "fix"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.snap().groups["g1"]["state"], "stuck")
        self.assertEqual(self.state(1), "merged")

    def fixer_prompt(self):
        path = os.path.join(self.tmp, "wt", "runs", "fix", "g1", "r1", "prompt.md")
        self.assertTrue(os.path.exists(path), "no fixer prompt at %s" % path)
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def check_noted(self, res, text, earlier, t0, t1):
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        g = self.snap().groups["g1"]
        self.assertEqual(g["state"], "pr", g)
        self.assertEqual(g["fix_round"], 0, g)
        self.assertIsNone(g["job"], g)
        notes = g.get("notes")
        self.assertIsInstance(notes, list, g)
        self.assertEqual(len(notes), len(earlier) + 1, notes)
        self.assertEqual(notes[:-1], earlier, "earlier notes were not kept in order")
        last = notes[-1]
        self.assertEqual(set(last), {"text", "by", "at"}, last)
        self.assertEqual(last["text"], text)
        self.assertEqual(last["by"], "user")
        # osx.now() format: %Y-%m-%dT%H:%M:%SZ in UTC, between the times taken around the call
        self.assertRegex(last["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertTrue(t0 <= last["at"] <= t1, (t0, last["at"], t1))
        ev = g["history"][-1]
        self.assertEqual(ev.get("event"), "ready again by the user", ev)
        self.assertEqual(ev.get("by"), "user", ev)
        self.assertEqual(ev.get("note"), text, ev)
        return notes

    def test_item1_and_4_note_stored_and_reaches_fixer(self):
        self.make_stuck()
        t0 = osx.now()
        res = self.run_py("tickets.py", "ready", "g1", "--note", "keep the newer output")
        t1 = osx.now()
        notes = self.check_noted(res, "keep the newer output", [], t0, t1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.snap().groups["g1"]["state"], "stuck")
        prompt = self.fixer_prompt()
        self.assertIn(self.HEADER, prompt.splitlines())
        self.assertIn("keep the newer output", prompt)
        self.assertIn(self.SENTENCE, prompt)
        t0 = osx.now()
        res = self.run_py("tickets.py", "ready", "g1", "--note", "drop case 2")
        t1 = osx.now()
        notes = self.check_noted(res, "drop case 2", notes, t0, t1)
        self.assertEqual([n["text"] for n in notes], ["keep the newer output", "drop case 2"])
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        prompt = self.fixer_prompt()
        self.assertIn(self.HEADER, prompt.splitlines())
        self.assertIn("drop case 2", prompt)
        self.assertIn(self.SENTENCE, prompt)
        self.assertNotIn("keep the newer output", prompt)
        notes = self.snap().groups["g1"].get("notes")
        self.assertEqual([n["text"] for n in notes], ["keep the newer output", "drop case 2"])

    def test_item2_actl_tickets_ready_group_note(self):
        self.make_stuck()
        t0 = osx.now()
        res = subprocess.run([os.path.join(HOME, "bin", "actl"), "tickets", "ready", "g1", "--note",
                              "keep the newer output"], cwd=self.proj, env=self.env,
                             capture_output=True, text=True, timeout=120)
        t1 = osx.now()
        self.check_noted(res, "keep the newer output", [], t0, t1)

    def test_item3_note_with_ticket_id_refused(self):
        self.add(1)
        self.plan(worker={"1": ["blocked"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "stuck")
        before = self.snap().ticket("1")
        res = self.run_py("tickets.py", "ready", "1", "--note", "x")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertTrue(any("--note" in ln and "g<N>" in ln for ln in res.stderr.splitlines()), res.stderr)
        self.assertEqual(self.snap().ticket("1"), before)


class GroupFollowUnits(LoopCase):
    """#118 (the implementer's): the start condition, can-still-start, adoption order, the open
    group's in-update decision, and a group with no members left."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)

    def group(self, gid, members, state="open", branch=False, **fields):
        g = {"id": gid, "branch": "int/" + gid, "state": state, "members": list(members),
             "fix_round": 0, "job": None, "created": "2026-11-23T00:00:00Z", "history": []}
        g.update(fields)
        if branch:
            git(self.proj, "branch", "int/" + gid, "main")
        self.pool.update(lambda tx: tx.put_group(g), "test: group %s" % gid)

    def ctl(self):
        from core.config import Config
        import ticket_controller
        c = ticket_controller.Controller(Config(self.proj), self.pool)
        c.forever = False
        c.started, c.issued = [], []
        c._start_job = lambda gid, kind, argv, out: c.started.append((gid, kind))
        c.start_issuer = lambda ident, gid: c.issued.append((ident, gid))
        return c

    def commit_main(self, rel, text):
        with open(os.path.join(self.proj, rel), "w") as fh:
            fh.write(text)
        git(self.proj, "add", "--", rel)
        git(self.proj, "commit", "-q", "-m", "m")
        return git(self.proj, "rev-parse", "HEAD")

    def events(self, gid):
        return [h.get("event") for h in self.snap().groups[gid].get("history") or []]

    def test_landed_dependency_waits_for_a_branch_lacking_main(self):
        self.add(1, state="landed")
        self.add(2, deps=[1], group="g1")
        self.group("g1", ["2"], branch=True)
        self.commit_main("NOTES.md", "n\n")
        c = self.ctl()
        snap = self.snap()
        self.assertFalse(c.can_start_now(snap, "2", snap.groups["g1"]))
        self.assertFalse(c.can_still_start(snap, "2", snap.groups["g1"]))
        c.dispatch(snap)
        self.assertEqual(c.issued, [])
        git(self.proj, "update-ref", "refs/heads/int/g1", "main")
        snap = self.snap()
        self.assertTrue(c.can_start_now(snap, "2", snap.groups["g1"]))
        c.dispatch(snap)
        self.assertEqual(c.issued, [("2", "g1")])

    def test_missing_branch_is_made_from_main_right_before_the_start(self):
        self.add(1, state="landed")
        self.add(2, deps=[1], group="g1")
        self.group("g1", ["2"])
        main = self.commit_main("NOTES.md", "n\n")
        c = self.ctl()
        snap = self.snap()
        self.assertTrue(c.can_start_now(snap, "2", snap.groups["g1"]))
        c.dispatch(snap)
        self.assertEqual(c.issued, [("2", "g1")])
        self.assertEqual(git(self.proj, "rev-parse", "int/g1"), main)
        self.assertEqual(self.snap().groups["g1"].get("base"), main)

    def test_a_ticket_is_started_only_in_its_own_open_group(self):
        self.add(1, group="g1")
        self.add(2, group="g2")
        self.group("g1", ["1"], state="stuck")
        self.group("g2", ["2"])
        c = self.ctl()
        c.adopt = lambda snap: []
        c.dispatch(self.snap())
        self.assertEqual(c.issued, [("2", "g2")])

    def test_can_still_start(self):
        self.add(1, group="g1")                       # ready in an earlier group
        self.add(2, state="running", group="g1")
        self.add(3, state="merged", group="g3")       # merged in g3, which #13 pulls ahead of g2 (#122)
        self.add(4, state="stuck", group="g1")
        self.add(5, state="merged", group="g9")       # merged in a cancelled earlier group
        self.add(6, state="ready")                    # ready without a group
        self.group("g1", ["1", "2", "4"])
        self.group("g3", ["3"])
        self.group("g9", ["5"], state="cancelled")
        for ident, dep in ((11, 1), (12, 2), (13, 3), (14, 4), (16, 6)):
            self.add(ident, deps=[dep], group="g2")
        self.add(15, deps=[5], group="g10")
        self.group("g2", ["11", "12", "13", "14", "16"])
        self.group("g10", ["15"])
        c = self.ctl()
        snap = self.snap()
        got = {i: c.can_still_start(snap, i, snap.groups[snap.tickets[i]["group"]])
               for i in ("11", "12", "13", "14", "15", "16")}
        self.assertEqual(got, {"11": True, "12": True, "13": True, "14": True, "15": False, "16": False})

    def test_adoption_counts_a_group_made_earlier_in_the_same_write(self):
        self.add(1)
        self.add(2, deps=[3])
        self.add(3, deps=[1])
        self.add(4, deps=[5])                          # #5 does not exist
        c = self.ctl()
        self.assertEqual(c.adopt(self.snap()), ["1", "3"])
        snap = self.snap()
        self.assertEqual({g: snap.groups[g]["members"] for g in snap.groups}, {"g1": ["1"], "g2": ["3"]})
        self.assertEqual(snap.groups["g2"]["history"][0]["event"], "opened for #3, which had no open group")
        self.assertIsNone(snap.tickets["2"].get("group"))
        self.assertIsNone(snap.tickets["4"].get("group"))
        self.assertEqual(c.adopt(self.snap()), ["2"])
        self.assertEqual(self.snap().tickets["2"]["group"], "g3")

    def test_adoption_leaves_a_group_that_is_not_open(self):
        self.add(1, group="g1", target="int/g1")
        self.group("g1", ["1"], state="pr")
        c = self.ctl()
        self.assertEqual(c.adopt(self.snap()), [])
        snap = self.snap()
        self.assertEqual(snap.groups["g1"]["members"], ["1"])
        self.assertNotIn("g2", snap.groups)
        self.assertEqual(snap.tickets["1"]["group"], "g1")

    def test_a_member_added_before_the_update_keeps_the_group_open(self):
        self.add(1, state="stuck", group="g1")
        self.group("g1", ["1"])
        c = self.ctl()
        snap = self.snap()
        self.assertFalse(c.open_stays(snap, snap.groups["g1"]))

        def opener(tx):                                 # the opener writes between snapshot and update
            g = tx.data.groups["g1"]
            g["members"].append("2")
            tx.put_group(g)
            tx.put_ticket({"id": "2", "state": "ready", "group": "g1"})
        self.pool.update(opener, "test: a member joins")
        self.assertIsNone(c.close_open("g1"))
        self.assertEqual(self.snap().groups["g1"]["state"], "open")

    def test_an_open_group_goes_on_after_its_ready_members_leave(self):
        self.add(1, state="merged", group="g1")
        self.add(2, deps=[9], group="g1", target="int/g1")
        self.group("g1", ["1", "2"])
        c = self.ctl()
        c.advance_groups(self.snap())
        snap = self.snap()
        self.assertEqual((snap.groups["g1"]["state"], snap.groups["g1"]["members"]), ("pr", ["1"]))
        self.assertEqual((snap.tickets["2"]["group"], snap.tickets["2"]["target"]), (None, None))
        self.assertEqual(self.events("g1")[-2:], ["member #2 left: it cannot start in g1",
                                                  "all tickets merged or stuck: PR ready"])

    def test_an_open_group_whose_members_all_leave_is_cancelled(self):
        self.add(2, deps=[9], group="g1")
        self.group("g1", ["2"])
        c = self.ctl()
        c.advance_groups(self.snap())
        self.assertEqual(self.snap().groups["g1"]["state"], "cancelled")
        self.assertEqual(self.events("g1")[-1], "no tickets left: group closed")

    def test_a_group_with_no_members_left_is_cancelled(self):
        for state in ("stuck", "pr", "fixing"):
            with self.subTest(state=state):
                gid = "g" + str(len(self.snap().groups) + 1)
                self.group(gid, [], state=state, branch=True)
                self.ctl().advance_groups(self.snap())
                g = self.snap().groups[gid]
                self.assertEqual(g["state"], "cancelled")
                self.assertEqual(g["history"][-1]["event"], "no tickets left: group closed")

    def test_a_group_with_a_job_and_no_members_is_kept(self):
        self.group("g1", [], state="pr", branch=True, job={"kind": "regression", "stamp": {}, "out": "x"})
        self.ctl().advance_groups(self.snap())
        self.assertEqual(self.snap().groups["g1"]["state"], "pr")

    def test_tick_counts_an_open_group_only_when_a_member_can_start_now(self):
        self.add(1, state="merged", group="g0")
        self.add(2, deps=[1], group="g1")
        self.group("g0", ["1"], state="stuck", branch=True)
        self.group("g1", ["2"])
        c = self.ctl()
        c.finish_jobs = c.advance_groups = lambda snap: None
        self.assertFalse(c.tick())                     # #2 waits for #1 merged in another group
        self.add(3, group="g1")
        self.pool.update(lambda tx: tx.data.groups["g1"]["members"].append("3")
                         or tx.put_group(tx.data.groups["g1"]), "test: #3 joins")
        c.dispatch = lambda snap: 0
        self.assertTrue(c.tick())                      # #3 has no dependency: it can start now


class StuckHoldsUnits(LoopCase):
    """#134 (the implementer's): core.chain.stuck_holds (D-097) and the idle line it gives."""

    group = GroupFollowUnits.group

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)

    def holds(self, ident="1", gid="g1"):
        from core import chain
        return chain.stuck_holds(self.snap(), ident, gid)

    def test_a_stuck_ticket_alone_does_not_hold(self):
        self.add(1, state="stuck", group="g1")
        self.group("g1", ["1"])
        self.assertFalse(self.holds())

    def test_beside_only_cancelled_or_merged_members_it_does_not_hold(self):
        self.add(1, state="stuck", group="g1")
        self.add(2, state="cancelled", group="g1")
        self.group("g1", ["1", "2"])
        self.assertFalse(self.holds())
        self.add(3, state="merged", group="g1")
        self.pool.update(lambda tx: tx.data.groups["g1"]["members"].append("3")
                         or tx.put_group(tx.data.groups["g1"]), "test: #3 joins")
        self.assertFalse(self.holds())

    def test_an_unfinished_member_beside_it_holds(self):
        for state in ("ready", "running", "stuck"):
            with self.subTest(state=state):
                self.add(1, state="stuck", group="g1")
                self.add(2, state=state, group="g1")
                self.group("g1", ["1", "2"])
                self.assertTrue(self.holds())

    def test_a_direct_dependent_holds_unless_it_is_cancelled(self):
        self.add(1, state="stuck", group="g1")
        self.add(2, state="merged", group="g1")
        self.group("g1", ["1", "2"])
        self.add(3, deps=[1], group="g2")
        self.assertTrue(self.holds())
        self.add(3, deps=[1], state="cancelled", group="g2")
        self.assertFalse(self.holds())
        self.add(3, deps=[2], group="g2")             # depends on another member only
        self.assertFalse(self.holds())

    def test_the_idle_line_names_every_stuck_member_that_holds(self):
        import ticket_controller
        self.add(1, state="stuck", group="g1")
        self.add(2, state="stuck", group="g1")
        self.group("g1", ["1", "2"])
        self.assertIn("g1 waits for the decision on #1, #2", ticket_controller.idle_lines(self.snap()))


class T104Acceptance(LoopCase):
    """#104 acceptance 4-5: a ready ticket with a landed dependency does not join an open group
    whose branch lacks main. Helpers copied from FollowMainUnits (not subclassed)."""

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
        if branch == "main":
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

    def ctl(self):
        from core.config import Config
        import ticket_controller
        c = ticket_controller.Controller(Config(self.proj), self.pool)
        c.forever = False
        c.started = []
        c._start_job = lambda gid, kind, argv, out: c.started.append((gid, kind))
        return c

    def layout(self, third_state, main_file="shared.txt"):
        self.add(1, state="landed")
        self.add(2, deps=[1], group="g1")
        self.add(3, state=third_state, group="g1", target="int/g1")
        git(self.proj, "branch", "int/g1", "main")
        g = {"id": "g1", "branch": "int/g1", "state": "open", "alone": False, "members": ["3", "2"],
             "fix_round": 0, "job": None}
        self.pool.update(lambda tx: tx.put_group(g), "test: group g1")
        self.commit_on("int/g1", {"shared.txt": "group\n"})
        self.commit_on("main", {main_file: "main\n"})
        c = self.ctl()
        c.issued = []
        c.start_issuer = lambda ident, gid: c.issued.append((ident, gid))
        c.advance_groups(self.snap())
        return c

    def test_landed_dep_does_not_join_a_group_lacking_main(self):
        """Item 4 (#118: #2 is a member of g1 and is not started while int/g1 lacks main)."""
        c = self.layout("running")
        snap = self.snap()
        g1 = snap.groups["g1"]
        # the layout is the one the ticket describes: main's merge conflicted, g1 still open
        self.assertEqual(g1["state"], "open")
        self.assertTrue(g1.get("main_tried"), g1)
        self.assertFalse(c.repo.is_ancestor("main", "refs/heads/int/g1"))
        self.assertIs(c.can_start_now(snap, "2", g1), False)
        c.dispatch(snap)
        self.assertEqual(c.issued, [])

    def test_group_for_picks_no_group_lacking_main(self):
        """Item 4 (#118: after a clean main commit int/g1 has main and #2 is started in g1)."""
        c = self.layout("running", main_file="NOTES.md")
        snap = self.snap()
        self.assertEqual(snap.groups["g1"]["state"], "open")
        self.assertTrue(c.repo.is_ancestor("main", "refs/heads/int/g1"))
        c.dispatch(snap)
        self.assertEqual(c.issued, [("2", "g1")])
        self.assertEqual(sorted(self.snap().groups), ["g1"])

    def test_merged_member_lets_the_group_move_to_pr(self):
        """Item 5."""
        c = self.layout("merged")
        self.assertEqual(self.snap().groups["g1"]["state"], "pr")


class T13Acceptance(LoopCase):
    """tickets.py ready g<N>: a stuck group goes back to the regression (#13)."""

    def setUp(self):
        super().setUp()
        # when this suite runs under a devloop agent, its DEVLOOP_TICKET must not reach the fake
        # fixer: the fake agent keys its plan on DEVLOOP_TICKET before DEVLOOP_GROUP
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)

    def make_stuck(self):
        self.add(1)
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["blocked", "fix"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.snap().groups["g1"]["state"], "stuck")
        self.assertEqual(self.state(1), "merged")

    def test_ready_group_goes_back_to_pr_and_lands(self):
        self.make_stuck()
        before = self.snap().ticket("1")
        res = self.run_py("tickets.py", "ready", "g1", "--why", "main is fixed")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        g = self.snap().groups["g1"]
        self.assertEqual(g["state"], "pr")
        self.assertEqual(g["fix_round"], 0)
        self.assertIsNone(g["job"])
        last = g["history"][-1]
        self.assertEqual(last.get("event"), "ready again by the user", last)
        self.assertEqual(last.get("by"), "user", last)
        self.assertEqual(last.get("why"), "main is fixed", last)
        self.assertEqual(self.snap().ticket("1"), before)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.snap().groups["g1"]["state"], "landed")
        self.assertFalse(self.main_has("red.txt"))

    def test_actl_tickets_ready_group(self):
        self.make_stuck()
        res = subprocess.run([os.path.join(HOME, "bin", "actl"), "tickets", "ready", "g1"], cwd=self.proj,
                             env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        g = self.snap().groups["g1"]
        self.assertEqual(g["state"], "pr")
        self.assertEqual(g["fix_round"], 0)

    def put_group(self, state, ticket_state="merged", branch=True):
        self.add(1)

        def change(tx):
            t = tx.ticket("1")
            t.update(state=ticket_state, group="g1")
            tx.put_ticket(t)
            tx.put_group({"id": "g1", "branch": "int/g1", "state": state, "members": ["1"],
                          "fix_round": 1, "job": None, "history": []})
        self.pool.update(change, "test: group g1")
        if branch:
            git(self.proj, "branch", "int/g1", "main")

    def refused(self, gid, text):
        before = self.snap().groups.get(gid)
        res = self.run_py("tickets.py", "ready", gid)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertTrue(any(text in ln for ln in res.stderr.splitlines()), res.stderr)
        self.assertEqual(self.snap().groups.get(gid), before)

    def test_refuses_pr_group(self):
        self.put_group("pr")
        self.refused("g1", "not stuck")

    def test_refuses_landed_group(self):
        self.put_group("landed")
        self.refused("g1", "not stuck")

    def test_refuses_missing_branch(self):
        self.put_group("stuck", branch=False)
        self.refused("g1", "no longer exists")

    def test_refuses_no_merged_member(self):
        self.put_group("stuck", ticket_state="stuck")
        self.refused("g1", "no merged ticket")

    def test_refuses_unknown_group(self):
        self.refused("g9", "no group g9")

    def test_docs(self):
        with open(os.path.join(HOME, "devloop", "README.md")) as fh:
            readme = fh.read().splitlines()
        self.assertTrue(any(ln.startswith("| `python3 devloop/tickets.py ready gN`") for ln in readme))
        with open(os.path.join(HOME, "devloop", "tickets.py")) as fh:
            doc = fh.read().split('"""')[1]
        self.assertIn("tickets.py ready g", doc)


class CancelGroupUnit(T13Acceptance):
    """cmd_cancel_group: ready and merged members are cancelled, others stay, branch kept."""

    def test_cancels_merged_member_and_pages(self):
        self.put_group("stuck")
        res = self.run_py("tickets.py", "cancel", "g1", "--why", "dropped")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        self.assertEqual(snap.groups["g1"]["state"], "cancelled")
        self.assertEqual(snap.tickets["1"]["state"], "cancelled")
        pages = [r for r in snap.inbox.values() if r.get("kind") == "cancelled"]
        self.assertEqual(len(pages), 1)
        self.assertIn("#1", pages[0]["text"])

    def test_running_member_refused(self):
        self.put_group("open", ticket_state="running")
        res = self.run_py("tickets.py", "cancel", "g1")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("#1 is running", res.stderr)
        self.assertEqual(self.snap().groups["g1"]["state"], "open")


class T25Acceptance(LoopCase):
    """#25: tickets.py cancel g<N> cancels a stuck or open group by hand."""

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)

    def put_group(self, state, member_states, branch=True):
        for m in member_states:
            self.add(m)

        def change(tx):
            for m, st in member_states.items():
                t = tx.ticket(m)
                t.update(state=st, group="g1")
                tx.put_ticket(t)
            tx.put_group({"id": "g1", "branch": "int/g1", "state": state, "members": list(member_states),
                          "fix_round": 0, "job": None, "history": []})
        self.pool.update(change, "test: group g1")
        if branch:
            git(self.proj, "branch", "int/g1", "main")

    def actl(self, *args):
        return subprocess.run([os.path.join(HOME, "bin", "actl")] + list(args), cwd=self.proj,
                              env=self.env, capture_output=True, text=True, timeout=120)

    def test_item1_stuck_group_is_cancelled(self):
        self.put_group("stuck", {"1": "merged", "2": "stuck", "3": "cancelled"})
        sha = git(self.proj, "rev-parse", "int/g1")
        before = self.snap()
        hist1 = len(before.ticket("1").get("history") or [])
        t2, t3 = before.ticket("2"), before.ticket("3")
        pages = set(before.inbox)
        res = self.run_py("tickets.py", "cancel", "g1", "--why", "dropped")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        g = snap.groups["g1"]
        self.assertEqual(g["state"], "cancelled", g)
        last = g["history"][-1]
        self.assertEqual(last.get("by"), "user", last)
        self.assertEqual(last.get("why"), "dropped", last)
        self.assertEqual(snap.ticket("1")["state"], "cancelled")
        self.assertEqual(len(snap.ticket("1").get("history") or []), hist1 + 1, snap.ticket("1"))
        self.assertEqual(snap.ticket("2")["state"], "stuck")
        self.assertEqual(snap.ticket("3")["state"], "cancelled")
        self.assertEqual(snap.ticket("2"), t2)
        self.assertEqual(snap.ticket("3"), t3)
        self.assertEqual(git(self.proj, "rev-parse", "int/g1"), sha)
        new = [snap.inbox[k] for k in set(snap.inbox) - pages]
        self.assertEqual(len(new), 1, new)
        self.assertEqual(new[0]["kind"], "cancelled", new[0])
        self.assertEqual(new[0]["ref"], "g1", new[0])
        self.assertIn("#1", new[0]["text"], new[0])

    def test_item2_open_group_is_cancelled(self):
        self.put_group("open", {"1": "ready"})
        res = self.run_py("tickets.py", "cancel", "g1", "--why", "x")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.snap().groups["g1"]["state"], "cancelled")
        self.assertEqual(self.state(1), "cancelled")

    def test_item3_actl_tickets_cancel_group(self):
        self.put_group("open", {"1": "ready"})
        res = self.actl("tickets", "cancel", "g1", "--why", "x")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.snap().groups["g1"]["state"], "cancelled")
        self.assertEqual(self.state(1), "cancelled")

    def refused(self, gid, text):
        before = self.snap()
        res = self.run_py("tickets.py", "cancel", gid, "--why", "x")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
        self.assertIn(text, res.stderr)
        after = self.snap()
        self.assertEqual(after.groups, before.groups)
        self.assertEqual(after.tickets, before.tickets)
        self.assertEqual(after.inbox, before.inbox)

    def test_item4_refuses_landed_group(self):
        self.put_group("landed", {"1": "landed"})
        self.refused("g1", "group g1 is landed")

    def test_item4_refuses_pr_group(self):
        self.put_group("pr", {"1": "merged"})
        self.refused("g1", "group g1 is pr")

    def test_item4_refuses_cancelled_group(self):
        self.put_group("cancelled", {"1": "cancelled"})
        self.refused("g1", "group g1 is cancelled")

    def test_item4_refuses_open_group_with_running_member(self):
        self.put_group("open", {"1": "running"})
        self.refused("g1", "#1 is running")

    def test_item4_refuses_unknown_group(self):
        self.put_group("open", {"1": "ready"})
        self.refused("g9", "no group g9")

    def test_item5_docs(self):
        with open(os.path.join(HOME, "devloop", "tickets.py")) as fh:
            doc = fh.read().split('"""')[1]
        self.assertIn("tickets.py cancel g", doc)
        with open(os.path.join(HOME, "devloop", "README.md")) as fh:
            readme = fh.read().splitlines()
        rows = [i for i, ln in enumerate(readme) if ln.startswith("| `python3 devloop/tickets.py cancel gN")]
        self.assertEqual(len(rows), 1, "no `cancel gN` row in README")
        release = [i for i, ln in enumerate(readme) if ln.startswith("| `python3 devloop/tickets.py release gN")]
        self.assertTrue(release, "no `release gN` row in README")
        self.assertGreater(rows[0], release[0])
        inbox = [ln for ln in readme if ln.startswith("| `python3 devloop/tickets.py inbox")]
        self.assertEqual(len(inbox), 1)
        self.assertIn("cancelled", inbox[0])


class AllCancelledHelper(unittest.TestCase):
    """Controller.all_cancelled: true only for a non-empty group of cancelled members."""

    def test_all_cancelled(self):
        import ticket_controller
        from types import SimpleNamespace
        snap = SimpleNamespace(tickets={"1": {"state": "cancelled"}, "2": {"state": "cancelled"},
                                        "3": {"state": "stuck"}})
        f = ticket_controller.Controller.all_cancelled
        self.assertTrue(f(snap, {"members": ["1", "2"]}))
        self.assertFalse(f(snap, {"members": ["1", "3"]}))
        self.assertFalse(f(snap, {"members": []}))
        self.assertFalse(f(snap, {"members": ["9"]}))


class T24Acceptance(LoopCase):
    """#24: the controller closes a group whose tickets are all cancelled."""

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)
        self.plan()

    def put_group(self, state, members=("1",), member_states=None, **fields):
        member_states = member_states or {m: "cancelled" for m in members}
        for m in members:
            self.add(m)

        def change(tx):
            for m in members:
                t = tx.ticket(m)
                t.update(state=member_states[m], group="g1")
                tx.put_ticket(t)
            g = {"id": "g1", "branch": "int/g1", "state": state, "members": list(members),
                 "fix_round": 0, "job": None, "history": []}
            g.update(fields)
            tx.put_group(g)
        self.pool.update(change, "test: group g1")

    def group(self):
        return self.snap().groups["g1"]

    def main_sha(self):
        return git(self.proj, "rev-parse", "main")

    def regression_dir(self):
        return os.path.join(self.tmp, "wt", "runs", "regression", "g1")

    def released_lines(self):
        if not os.path.exists(self.released):
            return []
        with open(self.released) as fh:
            return fh.read().splitlines()

    def run_once(self):
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res

    def assert_closed(self):
        g = self.group()
        self.assertEqual(g["state"], "cancelled", g)
        self.assertTrue(g.get("history"), g)
        self.assertIn("cancelled", g["history"][-1].get("event", ""), g["history"][-1])

    def test_item1_open_group_all_cancelled_is_closed(self):
        self.put_group("open")
        git(self.proj, "branch", "int/g1", "main")
        self.run_once()
        self.assert_closed()
        self.assertFalse(os.path.exists(self.regression_dir()), "a regression ran for g1")
        pages = [r for r in self.snap().inbox.values() if r.get("ref") == "g1"]
        self.assertEqual(pages, [])

    def test_item2_stuck_group_all_cancelled_is_closed(self):
        self.put_group("stuck", members=("1", "2"))
        git(self.proj, "branch", "int/g1", "main")
        self.run_once()
        self.assert_closed()

    def test_item3_pr_group_all_cancelled_is_closed_not_landed(self):
        self.put_group("pr")
        git(self.proj, "branch", "int/g1", "main")
        main = self.main_sha()
        self.run_once()
        self.assertEqual(self.group()["state"], "cancelled", self.group())
        self.assertFalse(os.path.exists(self.regression_dir()), "a regression ran for g1")
        self.assertEqual(self.main_sha(), main)
        self.assertEqual(self.released_lines(), [])

    def test_item4_verified_group_all_cancelled_is_closed_not_landed(self):
        git(self.proj, "branch", "int/g1", "main")
        wt = os.path.join(self.tmp, "g1-wt")
        git(self.proj, "worktree", "add", "-q", wt, "int/g1")
        with open(os.path.join(wt, "extra.txt"), "w") as fh:
            fh.write("extra\n")
        git(wt, "add", "extra.txt")
        git(wt, "commit", "-q", "-m", "extra")
        tip = git(wt, "rev-parse", "HEAD")
        git(self.proj, "worktree", "remove", "--force", wt)
        main = self.main_sha()
        self.assertNotEqual(tip, main)
        self.put_group("verified", verified_sha=tip)
        self.run_once()
        self.assertEqual(self.group()["state"], "cancelled", self.group())
        self.assertEqual(self.main_sha(), main)
        res = subprocess.run(["git", "merge-base", "--is-ancestor", tip, "main"], cwd=self.proj,
                             capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "main contains int/g1's commit")
        self.assertFalse(self.main_has("extra.txt"))
        self.assertEqual(self.released_lines(), [])

    def test_item5_list_hides_cancelled_group_unless_all(self):
        self.put_group("cancelled")
        res = self.run_py("tickets.py", "list")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse([ln for ln in res.stdout.splitlines() if ln.split()[:1] == ["g1"]], res.stdout)
        res = self.run_py("tickets.py", "list", "--all")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue([ln for ln in res.stdout.splitlines() if ln.split()[:1] == ["g1"]], res.stdout)


class NoGroupPlacementUnits(LoopCase):
    """D-097 3 internals: a ticket that has a group field but no open group is not adopted."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)

    def ctl(self):
        from core.config import Config
        import ticket_controller
        c = ticket_controller.Controller(Config(self.proj), self.pool)
        c.forever = False
        c.started, c.issued = [], []
        c._start_job = lambda gid, kind, argv, out: c.started.append((gid, kind))
        c.start_issuer = lambda ident, gid: c.issued.append((ident, gid))
        return c

    def test_ticket_that_left_a_group_is_not_adopted_and_has_an_idle_line(self):
        self.add(1, group=None)
        self.assertEqual(self.ctl().adopt(self.snap()), [])
        snap = self.snap()
        self.assertNotIn("g1", snap.groups)
        import ticket_controller
        self.assertEqual(ticket_controller.idle_lines(snap), [
            "#1 ready, has no open group: waits for the main session to place it (actl dag move 1 --to gK|new)"])

    def test_ticket_without_a_group_field_is_still_adopted(self):
        self.add(1)
        self.assertEqual(self.ctl().adopt(self.snap()), ["1"])


class T86Acceptance(LoopCase):
    """#86: a ticket that leaves a group is no longer that group's member; landing, releasing and
    cancelling the group count only tickets whose own group is that group."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        self.plan()

    def setup_pool(self, tickets, groups):
        """tickets: {id: {state, group, ...}}; groups: {gid: {state, members, ...}}."""
        for ident in tickets:
            self.add(ident)

        def change(tx):
            for ident, fields in tickets.items():
                t = tx.ticket(ident)
                t.update(fields)
                tx.put_ticket(t)
            for gid, fields in groups.items():
                g = {"id": gid, "branch": "int/" + gid, "fix_round": 0, "job": None, "history": []}
                g.update(fields)
                tx.put_group(g)
        self.pool.update(change, "test: #86 fixture")

    def left_group_fixture(self):
        self.setup_pool({"1": {"state": "merged", "group": "g1"},
                         "2": {"state": "stuck", "group": "g1"}},
                        {"g1": {"state": "pr", "members": ["1", "2"]}})

    def assert_left(self, ticket_event):
        snap = self.snap()
        t2, g1 = snap.ticket("2"), snap.groups["g1"]
        self.assertEqual(t2.get("state"), "ready", t2)
        self.assertIsNone(t2.get("group"), t2)
        self.assertEqual(g1.get("members"), ["1"], g1)
        self.assertTrue(g1.get("history"), g1)
        self.assertEqual(g1["history"][-1].get("event"), "member #2 left: back to ready", g1["history"])
        self.assertIn(ticket_event, [h.get("event") for h in t2.get("history") or []], t2.get("history"))

    def test_item1_tickets_ready_removes_the_ticket_from_its_closed_group(self):
        self.left_group_fixture()
        res = self.run_py("tickets.py", "ready", "2", "--why", "x")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assert_left("set ready by main")

    def test_item2_user_retry_removes_the_ticket_from_its_closed_group(self):
        self.left_group_fixture()
        res = self.run_py("ticket_creator.py", "--revise", "2", "--from", "user", "--decision", "retry")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assert_left("user decision: ready")

    def released_lines(self):
        if not os.path.exists(self.released):
            return []
        with open(self.released) as fh:
            return fh.read().splitlines()

    def test_item3_landing_counts_only_members_whose_group_is_this_group(self):
        git(self.proj, "branch", "int/g1", "main")
        git(self.proj, "branch", "int/g2", "main")
        wt = os.path.join(self.tmp, "g1-wt")
        git(self.proj, "worktree", "add", "-q", wt, "int/g1")
        with open(os.path.join(wt, "g1.txt"), "w") as fh:
            fh.write("g1\n")
        git(wt, "add", "g1.txt")
        git(wt, "commit", "-q", "-m", "g1 work")
        tip = git(wt, "rev-parse", "HEAD")
        git(self.proj, "worktree", "remove", "--force", wt)
        self.assertNotEqual(tip, git(self.proj, "rev-parse", "main"))
        self.setup_pool({"1": {"state": "merged", "group": "g1"},
                         "2": {"state": "merged", "group": "g2"}},
                        {"g1": {"state": "verified", "verified_sha": tip, "members": ["1", "2"]},
                         "g2": {"state": "stuck", "members": ["2"]}})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        self.assertEqual(snap.groups["g1"].get("state"), "landed", snap.groups["g1"])
        t1, t2 = snap.ticket("1"), snap.ticket("2")
        self.assertEqual(t1.get("state"), "landed", t1)
        self.assertTrue(t1.get("landed_sha"), t1)
        self.assertEqual(t2.get("state"), "merged", t2)
        self.assertEqual(t2.get("group"), "g2", t2)
        self.assertFalse(t2.get("landed_sha"), t2)
        self.assertEqual(self.released_lines(), ["g1 1"])
        pages = [r for r in snap.inbox.values() if r.get("kind") == "landed" and r.get("ref") == "g1"]
        self.assertEqual(len(pages), 1, pages)
        text = pages[0].get("text") or ""
        self.assertRegex(text, r"#1\b", text)
        self.assertNotRegex(text, r"#2\b", text)

    def cancel_fixture(self, t2):
        git(self.proj, "branch", "int/g1", "main")
        groups = {"g1": {"state": "stuck", "members": ["1", "2"]}}
        if t2.get("group") == "g2":
            git(self.proj, "branch", "int/g2", "main")
            groups["g2"] = {"state": "open", "members": ["2"]}
        self.setup_pool({"1": {"state": "stuck", "group": "g1"}, "2": t2}, groups)

    def test_item4_cancel_leaves_a_ticket_that_left_the_group(self):
        self.cancel_fixture({"state": "ready", "group": None})
        res = self.run_py("tickets.py", "cancel", "g1", "--why", "x")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        self.assertEqual(snap.groups["g1"].get("state"), "cancelled", snap.groups["g1"])
        self.assertEqual(snap.ticket("2").get("state"), "ready", snap.ticket("2"))
        self.assertIn("cancelled tickets: none", res.stdout)
        pages = [r for r in snap.inbox.values() if r.get("kind") == "cancelled" and r.get("ref") == "g1"]
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("cancelled tickets: none", pages[0].get("text") or "", pages[0])

    def test_item4_cancel_ignores_a_running_ticket_of_another_group(self):
        self.cancel_fixture({"state": "running", "group": "g2"})
        res = self.run_py("tickets.py", "cancel", "g1", "--why", "x")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        self.assertEqual(snap.groups["g1"].get("state"), "cancelled", snap.groups["g1"])
        self.assertEqual(snap.ticket("2").get("state"), "running", snap.ticket("2"))


class MemberLeavesUnit(LoopCase):
    """core.pool.leave_group: a ticket back to ready leaves a group that is not open."""

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(k)
        self.plan()

    def leave(self, gstate):
        self.add("1")

        def prep(tx):
            t = tx.ticket("1")
            t.update(group="g1", target="int/g1")
            tx.put_ticket(t)
            tx.put_group({"id": "g1", "branch": "int/g1", "state": gstate, "members": ["1", "2"],
                          "history": []})
        self.pool.update(prep, "test: prep")

        def go(tx):
            t = tx.ticket("1")
            pool_mod.leave_group(tx, t)
            tx.put_ticket(t)
        self.pool.update(go, "test: leave")
        return self.snap()

    def test_stuck_group_is_left(self):
        s = self.leave("stuck")
        self.assertIsNone(s.tickets["1"]["group"])
        self.assertIsNone(s.tickets["1"]["target"])
        self.assertEqual(s.groups["g1"]["members"], ["2"])
        self.assertEqual(s.groups["g1"]["history"][-1]["event"], "member #1 left: back to ready")

    def test_open_group_is_kept(self):
        s = self.leave("open")
        self.assertEqual(s.tickets["1"]["group"], "g1")
        self.assertEqual(s.groups["g1"]["members"], ["1", "2"])
        self.assertEqual(s.groups["g1"]["history"], [])


class DispatchOrderUnits(LoopCase):
    """Start order when ready tickets outnumber free slots: chain place, then DAG order."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)

    def group(self, gid, members, state="open", **fields):
        g = {"id": gid, "branch": "int/" + gid, "state": state, "members": list(members),
             "fix_round": 0, "job": None, "created": "2026-11-23T00:00:00Z", "history": []}
        g.update(fields)
        self.pool.update(lambda tx: tx.put_group(g), "test: group %s" % gid)

    def ctl(self):
        from core.config import Config
        import ticket_controller
        c = ticket_controller.Controller(Config(self.proj), self.pool)
        c.forever = False
        c.started, c.issued = [], []
        c._start_job = lambda gid, kind, argv, out: c.started.append((gid, kind))
        c.start_issuer = lambda ident, gid: c.issued.append((ident, gid))
        return c

    def edge(self):
        self.pool.update(lambda tx: tx.put_dag(dict(tx.data.raw["dag"], group_after={"g1": ["g2"]})), "test: edge")

    def test_tickets_outside_the_chain_rank_last_and_raise_nothing(self):
        self.config(max_parallel=2)
        self.add(1, group=None)
        self.add(3, group="g7")
        self.add(2, group="g1")
        self.group("g1", ["2"])
        c = self.ctl()
        c.dispatch(self.snap())
        self.assertEqual(c.issued, [("2", "g1")])

    def test_a_blocked_ticket_of_an_earlier_group_holds_no_slot(self):
        self.config(max_parallel=2)
        self.add(5, state="running", group="g2")
        self.add(3, deps=["5"], group="g2")
        self.add(1, group="g1")
        self.group("g2", ["5", "3"])
        self.group("g1", ["1"])
        self.edge()
        c = self.ctl()
        c.dispatch(self.snap())
        self.assertEqual(c.issued, [("1", "g1")])

    def test_start_order_is_documented_in_step_3(self):
        import ticket_controller
        doc = ticket_controller.__doc__
        step = doc[doc.index("3. dispatch"):doc.index("4. memory")]
        self.assertIn("chain.order", step)


class T135Acceptance(LoopCase):
    """#135 (verifier): a ready ticket that has a group field but no open group gets no new group
    from the controller; the idle page says it waits for the main session (D-097 3)."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)

    def group(self, gid, members, state="open", branch=False, **fields):
        g = {"id": gid, "branch": "int/" + gid, "state": state, "members": list(members),
             "fix_round": 0, "job": None, "created": "2026-11-23T00:00:00Z", "history": []}
        g.update(fields)
        if branch:
            git(self.proj, "branch", "int/" + gid, "main")
        self.pool.update(lambda tx: tx.put_group(g), "test: group %s" % gid)

    def ctl(self):
        from core.config import Config
        import ticket_controller
        c = ticket_controller.Controller(Config(self.proj), self.pool)
        c.forever = False
        c.started, c.issued = [], []
        c._start_job = lambda gid, kind, argv, out: c.started.append((gid, kind))
        c.start_issuer = lambda ident, gid: c.issued.append((ident, gid))
        return c

    def test_a_ticket_with_group_none_is_not_adopted_or_started(self):
        self.add(1, group=None)
        self.assertIn("group", self.snap().tickets["1"])                 # fixture premise
        c = self.ctl()
        self.assertEqual(c.adopt(self.snap()), [])
        snap = self.snap()
        self.assertEqual(snap.groups, {}, snap.groups)
        self.assertIsNone(snap.tickets["1"]["group"])
        c.dispatch(snap)
        self.assertEqual((c.issued, c.started), ([], []))
        snap = self.snap()
        self.assertEqual(snap.groups, {}, snap.groups)
        self.assertIsNone(snap.tickets["1"]["group"])
        self.assertEqual(snap.tickets["1"]["state"], "ready")

    def test_a_ticket_whose_group_is_in_pr_is_not_adopted(self):
        self.add(1, group="g1", target="int/g1")
        self.group("g1", ["1"], state="pr")
        c = self.ctl()
        self.assertEqual(c.adopt(self.snap()), [])
        snap = self.snap()
        self.assertEqual(snap.tickets["1"]["group"], "g1")
        self.assertEqual(sorted(snap.groups), ["g1"], snap.groups)
        self.assertEqual(snap.groups["g1"]["members"], ["1"])

    def test_run_without_forever_leaves_it_ready_with_one_idle_page(self):
        for key in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(key, None)
        self.add(1, group=None)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        self.assertEqual(snap.ticket("1").get("state"), "ready", snap.ticket("1"))
        pages = [r for r in snap.inbox.values() if r.get("kind") == "idle"]
        self.assertEqual(len(pages), 1, "exactly one idle page: %r" % pages)
        lines = [ln for ln in pages[0]["text"].splitlines() if ln.startswith("#") or ln.startswith("g")]
        self.assertEqual(lines, ["#1 ready, has no open group: waits for the main session to place it "
                                 "(actl dag move 1 --to gK|new)"], pages[0]["text"])


class DagEditUnits(LoopCase):
    """#125 (the implementer's): core/dagedit.py checks and writes, and the docs and test map
    (acceptance 8 and 9)."""

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(k)

    def groups(self, layout):
        """layout: {gid: [members]}; members are ready tickets of that open group."""
        for gid, members in layout.items():
            for ident in members:
                self.add(ident, group=gid)

        def change(tx):
            for gid, members in layout.items():
                tx.put_group({"id": gid, "branch": "int/" + gid, "state": "open", "members": list(members),
                              "fix_round": 0, "job": None, "created": "2026-11-23T00:00:00Z"})
        self.pool.update(change, "test: groups")

    def edit(self, n, why="r", **change):
        from core import dagedit
        out = []
        head = git(self.pool_path, "rev-parse", "HEAD")
        self.pool.update(lambda tx: out.append(dagedit.apply(tx, n, "me", why, **change)), "test: edit")
        if out[-1]:
            self.assertEqual(git(self.pool_path, "rev-parse", "HEAD"), head)
            self.assertEqual(git(self.pool_path, "status", "--porcelain"), "")
        return out[-1]

    def set_field(self, ident, **fields):
        def change(tx):
            t = tx.ticket(ident)
            t.update(fields)
            tx.put_ticket(t)
        self.pool.update(change, "test: set")

    def test_add_and_drop_write_dag_and_events(self):
        from core import chain
        self.groups({"g1": ["1"], "g2": ["2"]})
        self.assertIsNone(self.edit("1", add="2"))
        s = self.snap()
        self.assertEqual(s.dag["1"]["depends_on"], ["2"])
        self.assertEqual(chain.order(s), ["g2", "g1"])
        last = s.tickets["1"]["history"][-1]
        self.assertEqual((last["event"], last["by"], last["why"]), ("dag: depends on #2", "me", "r"))
        for gid in ("g1", "g2"):
            self.assertEqual(s.groups[gid]["history"][-1]["event"], "dag: #1 depends on #2")
        self.assertIsNone(self.edit("1", drop="2"))
        s = self.snap()
        self.assertEqual(s.dag["1"]["depends_on"], [])
        self.assertEqual(s.groups["g2"]["history"][-1]["event"], "dag: #1 no longer depends on #2")

    def test_same_group_gets_one_event(self):
        self.groups({"g1": ["1", "2"]})
        self.assertIsNone(self.edit("1", add="2"))
        events = [h["event"] for h in self.snap().groups["g1"].get("history") or []]
        self.assertEqual(events, ["dag: #1 depends on #2"])

    def test_move_to_existing_cancels_empty_old_group(self):
        self.groups({"g1": ["1"], "g2": ["2"]})
        self.assertIsNone(self.edit("1", to="g2"))
        s = self.snap()
        self.assertEqual(s.groups["g2"]["members"], ["2", "1"])
        self.assertEqual(s.tickets["1"]["group"], "g2")
        self.assertIsNone(s.tickets["1"]["target"])
        self.assertEqual(s.groups["g1"]["state"], "cancelled")
        self.assertEqual(s.groups["g1"]["members"], [])
        self.assertEqual([h["event"] for h in s.groups["g1"]["history"]],
                         ["dag: member #1 moved to g2", "cancelled: its last member #1 moved to g2"])
        self.assertEqual(s.groups["g2"]["history"][-1]["event"], "dag: member #1 moved in from g1")
        self.assertEqual(s.tickets["1"]["history"][-1]["event"], "dag: moved from g1 to g2")

    def test_move_to_new_and_from_no_group(self):
        self.groups({"g1": ["1", "3"], "g2": ["2"]})
        self.add("4")
        self.assertIsNone(self.edit("1", to="new"))
        self.assertIsNone(self.edit("4", to="new"))
        s = self.snap()
        self.assertEqual(s.groups["g3"]["members"], ["1"])
        first = s.groups["g3"]["history"][0]
        self.assertEqual((first["event"], first["by"], first["why"]), ("dag: member #1 moved in from g1", "me", "r"))
        self.assertEqual(s.groups["g1"]["members"], ["3"])
        self.assertEqual(s.groups["g1"]["state"], "open")
        self.assertEqual(s.groups["g4"]["history"][0]["event"], "dag: member #4 moved in from no group")
        self.assertEqual(s.tickets["4"]["history"][-1]["event"], "dag: moved from no group to g4")

    def test_refusals_in_order(self):
        self.groups({"g1": ["1", "4"], "g2": ["2", "3"]})
        self.add("5", state="cancelled")
        self.add("6", state="running")
        self.add("7", state="merged")
        self.assertIn("--reason", self.edit("1", why=" ", add="9"))
        self.assertIn("#9 does not exist", self.edit("9", add="2"))
        self.assertIn("#6 is running", self.edit("6", add="2"))
        bad = self.edit("7", add="2")
        self.assertIn("#7", bad)
        self.assertIn("(merged)", bad)
        self.assertIn("#9 does not exist", self.edit("1", add="9"))
        self.assertIn("cannot depend on itself", self.edit("1", add="1"))
        self.assertIn("#5 is cancelled", self.edit("1", add="5"))
        self.assertIn("does not depend on #2", self.edit("1", drop="2"))
        self.assertIsNone(self.edit("1", add="2"))
        self.assertIn("already depends on #2", self.edit("1", add="2"))
        self.set_field("1", acceptance=["x", "waits for #2."])
        bad = self.edit("1", drop="2")
        self.assertIn("mentions #2", bad)
        self.assertIn("`actl new --revise 1`", bad)
        self.set_field("1", acceptance=["#22 only"])
        self.assertIsNone(self.edit("1", drop="2"))
        self.assertIn("group g9 does not exist", self.edit("1", to="g9"))
        self.assertIn("already in g1", self.edit("1", to="g1"))

        def stuck(tx):
            tx.put_group({"id": "g8", "state": "stuck", "members": []})
        self.pool.update(stuck, "test: g8")
        self.assertIn("group g8 is stuck", self.edit("1", to="g8"))

    def test_cycles_refused(self):
        self.groups({"g1": ["1", "4"], "g2": ["2", "3"]})
        self.assertIsNone(self.edit("3", add="1"))
        self.assertEqual(self.edit("4", add="2"), "the group order would be a cycle: g1 -> g2 -> g1")
        self.assertTrue(self.edit("1", add="3").startswith("cycle: "))

    def test_move_that_makes_a_group_cycle_is_refused(self):
        self.groups({"g1": ["1"], "g2": ["2"], "g3": ["3"]})
        self.assertIsNone(self.edit("2", add="1"))   # g2 waits for g1
        self.assertIsNone(self.edit("3", add="2"))   # g3 waits for g2
        bad = self.edit("1", to="g3")                 # g3 would wait for g2, which waits for g3
        self.assertTrue(bad.startswith("the group order would be a cycle: "), bad)

    def test_script_and_actl(self):
        self.groups({"g1": ["1"], "g2": ["2"]})
        res = self.run_py("dag_edit.py", "dep", "1", "--add", "2")
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stderr.strip().count("\n"), 0, res.stderr)
        self.assertIn("--reason", res.stderr)
        res = subprocess.run([os.path.join(HOME, "bin", "actl"), "dag", "dep", "1", "--add", "2", "--reason", "r"],
                             cwd=self.proj, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip().splitlines()[-1], "chain: g2, g1")
        self.assertEqual(self.snap().tickets["1"]["history"][-1]["by"], "main-session")

    def test_item8_docs(self):
        with open(os.path.join(HOME, "devloop", "README.md")) as fh:
            readme = fh.read()
        rows = readme.split("## What the main session runs", 1)[1].split("\n## ", 1)[0].splitlines()
        rerun = next(i for i, r in enumerate(rows) if r.startswith("| `python3 devloop/tickets.py rerun gN"))
        self.assertTrue(rows[rerun + 1].startswith(
            "| `actl dag dep N --add M` / `--drop M` / `actl dag move N --to gK|new`"), rows[rerun + 1])
        self.assertTrue(rows[rerun + 2].startswith("| `python3 devloop/board.py serve"), rows[rerun + 2])
        paras = readme.split("\n\n")
        i = next(i for i, p in enumerate(paras) if p.startswith("Groups form one chain"))
        self.assertIn("actl dag dep", paras[i + 1])
        self.assertIn("D-096", paras[i + 1])
        with open(os.path.join(HOME, "CLAUDE.md")) as fh:
            lines = fh.read().splitlines()
        self.assertEqual(sum("頁的種類" in l for l in lines), 1)
        self.assertEqual(sum("不輪詢" in l for l in lines), 1)
        text = "\n".join(lines).split("## 怎麼做事", 1)[1].split("\n## ", 1)[0]
        self.assertIn('`actl dag dep Y --add X --reason "…"`', text)
        self.assertIn('`actl dag move N --to gK|new --reason "…"`', text)
        self.assertIn("D-096", text)

    def test_item9_test_map(self):
        with open(os.path.join(HOME, "board", "config.json")) as fh:
            rows = json.load(fh)["tests"]["unittest"]["map"]
        chained = [r for r in rows if "test_chain" in r["ids"]]
        self.assertTrue(any(r["match"] == "devloop/*" for r in chained))
        self.assertTrue(any(r["match"] == "tests_devloop/*" for r in chained))
        for r in chained:
            self.assertIn("test_dag_edit", r["ids"], r["match"])

class ChainGateUnits(LoopCase):
    """#117 acceptance 2, 6, 7, 8 (the implementer's): what the controller starts for a group in
    pr or fixing while an earlier group holds the chain, and what tick() counts as work."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)

    def group(self, gid, state, members, merged=(), **fields):
        """A group with its own branch off main; members in `merged` are merged tickets of it."""
        git(self.proj, "branch", "int/" + gid, "main")
        for ident in members:
            self.add(ident, state="merged" if ident in merged else "stuck", group=gid)
        g = {"id": gid, "branch": "int/" + gid, "state": state, "alone": False,
             "members": list(members), "fix_round": 0, "job": None}
        g.update(fields)

        def change(tx):
            tx.put_group(g)
        self.pool.update(change, "test: group %s" % gid)

    def ctl(self, forever=False):
        from core.config import Config
        import ticket_controller
        c = ticket_controller.Controller(Config(self.proj), self.pool)
        c.forever = forever
        c.started, c.landed = [], []
        c._start_job = lambda gid, kind, argv, out: c.started.append((gid, kind))
        c.land = lambda g: c.landed.append(g["id"])
        return c

    def history(self, gid):
        return [h.get("event") or "" for h in self.snap().groups[gid].get("history") or []]

    def commit_main(self, rel, text):
        with open(os.path.join(self.proj, rel), "w") as fh:
            fh.write(text)
        git(self.proj, "add", "--", rel)
        git(self.proj, "commit", "-q", "-m", "m")
        return git(self.proj, "rev-parse", "HEAD")

    def test_a_later_pr_group_waits_for_an_earlier_pr_group(self):
        self.group("g1", "pr", ["1"], merged=["1"])
        self.group("g2", "pr", ["2"], merged=["2"])
        c = self.ctl()
        c.advance_groups(self.snap())
        self.assertEqual(c.started, [("g1", "regression")])

    def test_a_waiting_group_still_follows_main(self):
        self.group("g1", "pr", ["1"], merged=["1"], job={"kind": "regression", "stamp": {}, "out": "x"})
        self.group("g2", "pr", ["2"], merged=["2"])
        main = self.commit_main("NOTES.md", "n\n")
        c = self.ctl()
        c.advance_groups(self.snap())
        self.assertEqual(c.started, [])
        self.assertEqual(git(self.proj, "rev-parse", "int/g2"), main)
        self.assertIn("main moved to %s: fast-forwarded int/g2 to it" % main[:12], self.history("g2"))

    def test_no_main_moved_fix_round_while_waiting(self):
        self.group("g1", "fixing", ["1"], merged=["1"])
        self.group("g2", "pr", ["2"], merged=["2"])
        tree = os.path.join(self.tmp, "side")
        git(self.proj, "worktree", "add", "-q", tree, "int/g2")
        with open(os.path.join(tree, "shared.txt"), "w") as fh:
            fh.write("group\n")
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "c")
        git(self.proj, "worktree", "remove", "--force", tree)
        self.commit_main("shared.txt", "main\n")
        c = self.ctl()
        c._start_job = lambda gid, kind, argv, out: c.started.append((gid, kind)) if gid == "g2" else None
        c.advance_groups(self.snap())
        self.assertEqual(c.started, [])
        g = self.snap().groups["g2"]
        self.assertEqual((g["state"], g["fix_round"]), ("pr", 0))
        self.assertTrue(any("conflicts with int/g2" in e for e in self.history("g2")))

    def test_no_retried_fix_round_while_waiting(self):
        report = os.path.join(self.tmp, "fix-report.json")
        with open(report, "w") as fh:
            fh.write("{}")
        self.group("g1", "pr", ["1"], merged=["1"], job={"kind": "regression", "stamp": {}, "out": "x"})
        self.group("g2", "fixing", ["2"], merged=["2"], fix_retry=True, fix_report=report, fix_round=1)
        c = self.ctl()
        c.advance_groups(self.snap())
        self.assertEqual(c.started, [])
        self.assertEqual(self.snap().groups["g2"]["fix_round"], 1)

    def test_landing_a_verified_group_is_not_gated(self):
        self.group("g1", "pr", ["1"], merged=["1"], job={"kind": "regression", "stamp": {}, "out": "x"})
        self.group("g2", "verified", ["2"], merged=["2"])
        c = self.ctl()
        c.advance_groups(self.snap())
        self.assertEqual(c.landed, ["g2"])

    def test_stuck_without_merged_member_and_cancelled_hold_no_one(self):
        self.group("g1", "stuck", ["1"])
        self.group("g2", "cancelled", ["2"])
        self.group("g3", "pr", ["3"], merged=["3"])
        c = self.ctl()
        c.advance_groups(self.snap())
        self.assertEqual(c.started, [("g3", "regression")])

    def test_stuck_with_merged_member_holds(self):
        self.group("g1", "stuck", ["1", "2"], merged=["1"])
        self.group("g3", "pr", ["3"], merged=["3"])
        c = self.ctl()
        c.advance_groups(self.snap())
        self.assertEqual(c.started, [])

    def paused_holds(self, forever):
        self.group("g1", "pr", ["1"], merged=["1"], paused={"reason": "env", "at": "x", "text": ""})
        self.group("g2", "pr", ["2"], merged=["2"])
        c = self.ctl(forever=forever)
        c._start_job = lambda gid, kind, argv, out: c.started.append((gid, kind)) if gid == "g2" else None
        c.advance_groups(self.snap())
        self.assertEqual(c.started, [])

    def test_a_paused_group_holds_under_forever(self):
        self.paused_holds(True)

    def test_a_paused_group_holds_without_forever(self):
        self.paused_holds(False)

    def busy(self):
        c = self.ctl()
        c.finish_jobs = c.advance_groups = lambda snap: None
        return c.tick()

    def test_tick_does_not_count_a_waiting_group(self):
        self.group("g1", "stuck", ["1", "2"], merged=["1"])
        self.group("g2", "pr", ["2"], merged=["2"])
        self.group("g3", "fixing", ["3"], merged=["3"])
        self.assertFalse(self.busy())

    def test_tick_counts_the_first_pr_group_and_groups_with_a_job(self):
        self.group("g1", "pr", ["1"], merged=["1"])
        self.assertTrue(self.busy())

    def test_tick_counts_a_waiting_group_with_a_job(self):
        self.group("g1", "stuck", ["1"], merged=["1"])
        self.group("g2", "fixing", ["2"], merged=["2"], job={"kind": "fixer", "stamp": {}, "out": "x"})
        self.assertTrue(self.busy())

    def test_tick_counts_an_open_group_behind_a_holder(self):
        """#118 acceptance 9: an open group counts only while a member runs or can start now."""
        self.group("g1", "stuck", ["1"], merged=["1"])
        self.group("g2", "open", ["2"], merged=["2"])
        self.assertFalse(self.busy())

    def test_tick_counts_a_verified_group_behind_a_holder(self):
        self.group("g1", "stuck", ["1"], merged=["1"])
        self.group("g2", "verified", ["2"], merged=["2"])
        self.assertTrue(self.busy())


if __name__ == "__main__":
    unittest.main()
