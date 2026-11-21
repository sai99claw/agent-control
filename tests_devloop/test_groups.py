"""integration groups: size, membership, ready / cancel of a group, rulings on a group"""

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


class T33Acceptance(LoopCase):
    """#33: devloop.max_group_size caps how many tickets one integration branch takes."""

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def groups(self):
        return {g["id"]: g["members"] for g in self.snap().groups.values()}

    def test_max_group_size_defaults_to_10_and_takes_a_setting(self):
        self.assertEqual(getattr(self.cfg(), "max_group_size", None), 10)
        self.config(max_group_size=3)
        self.assertEqual(getattr(self.cfg(), "max_group_size", None), 3)

    def test_bad_max_group_size_stops_tickets_list(self):
        for bad in (0, -1, 2.5, "10", True, None):
            with self.subTest(bad=bad):
                self.config(max_group_size=bad)
                res = self.run_py("tickets.py", "list")
                self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
                self.assertIn("devloop.max_group_size", res.stderr)

    def test_three_together_tickets_with_size_2_take_two_groups(self):
        self.config(max_group_size=2)
        self.add(1)
        self.add(2)
        self.add(3)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        for i in (1, 2, 3):
            self.assertEqual(self.state(i), "landed", i)
        self.assertEqual(self.groups(), {"g1": ["1", "2"], "g2": ["3"]})

    def test_size_1_dependent_ticket_opens_its_group_after_g1_lands(self):
        self.config(max_group_size=1)
        self.add(1)
        self.add(2, deps=[1])
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.state(2), "landed")
        self.assertEqual(self.groups(), {"g1": ["1"], "g2": ["2"]})
        groups = self.snap().groups
        landed, base = groups["g1"].get("landed_sha"), groups["g2"].get("base")
        self.assertTrue(landed and base, groups)
        res = subprocess.run(["git", "merge-base", "--is-ancestor", landed, base], cwd=self.proj,
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, "g2 was not opened from a main holding g1's landing")


class T75Acceptance(LoopCase):
    """#75: a ticket that is not a member never joins an open group that already holds
    devloop.max_group_size members, whichever path chose the group. Expected values come from
    the cap rule in the ticket's acceptance items, not from the code."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key, None)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key, None)
        self.config(max_group_size=2)

    def full_g1(self, first_state="running", deps3=()):
        """g1 open with members ['1', '2']; ticket 3 ready, `group` g1, not a member."""
        import ticket_controller
        from core.config import Config
        ctl = ticket_controller.Controller(Config(self.proj), self.pool)
        self.add(1, group="g1", state=first_state)
        self.add(2, group="g1", state="running")
        self.add(3, deps=deps3, group="g1")
        gid = ctl.new_group(False)
        self.assertEqual(gid, "g1")

        def change(tx):
            g = tx.data.groups["g1"]
            g["members"] = ["1", "2"]
            tx.put_group(g)
        self.pool.update(change, "test: fill g1")
        snap = self.snap()
        self.assertEqual(snap.groups["g1"]["members"], ["1", "2"])
        self.assertEqual(snap.groups["g1"]["state"], "open")
        self.assertEqual(snap.ticket("3")["state"], "ready")
        return ctl, ticket_controller

    def test_group_for_skips_a_full_group_named_by_the_ticket(self):
        ctl, _ = self.full_g1()
        got = ctl.group_for(self.snap(), "3")
        self.assertIsNotNone(got, "ticket 3 has no dependencies: it must get a group")
        self.assertNotEqual(got, "g1", "g1 already holds max_group_size members")
        self.assertEqual(self.snap().groups["g1"]["members"], ["1", "2"])

    def test_group_for_waits_when_the_dependency_is_merged_in_a_full_group(self):
        ctl, _ = self.full_g1(first_state="merged", deps3=[1])
        before = set(self.snap().groups)
        got = ctl.group_for(self.snap(), "3")
        self.assertIsNone(got, "ticket 3 cannot join full g1 and its dependency is not landed")
        self.assertEqual(set(self.snap().groups), before, "group_for opened a group")
        self.assertEqual(self.snap().groups["g1"]["members"], ["1", "2"])

    def test_start_issuer_refuses_a_full_group(self):
        ctl, tc = self.full_g1()
        calls = []

        def spawn(*a, **k):
            calls.append((a, k))
            return mock.Mock()
        with mock.patch.object(tc.osx, "spawn", spawn):
            ctl.start_issuer("3", "g1")
        snap = self.snap()
        self.assertEqual(snap.ticket("3")["state"], "ready")
        self.assertEqual(snap.groups["g1"]["members"], ["1", "2"])
        self.assertEqual(calls, [], "spawn was called for a ticket refused by a full group")
        # positive control on the same setup: with room in g1, ticket 3 is dispatched to it

        def free(tx):
            g = tx.data.groups["g1"]
            g["members"] = ["1"]
            tx.put_group(g)
        self.pool.update(free, "test: room in g1")
        with mock.patch.object(tc.osx, "spawn", spawn):
            ctl.start_issuer("3", "g1")
        snap = self.snap()
        self.assertEqual(snap.ticket("3")["state"], "running")
        self.assertEqual(snap.groups["g1"]["members"], ["1", "3"])
        self.assertEqual(len(calls), 1)


class GroupSizeUnits(LoopCase):
    """devloop.max_group_size: joinable() and group_for() (implementer's unit tests)."""

    def ctl(self, size):
        from unittest import mock
        import ticket_controller
        c = ticket_controller.Controller.__new__(ticket_controller.Controller)
        c.cfg = mock.Mock(max_group_size=size)
        return c

    def snap(self, members):
        from unittest import mock
        return mock.Mock(dag={}, tickets={"9": {}}, groups={"g1": {"id": "g1", "members": members}})

    def test_full_group_not_joinable(self):
        from unittest import mock
        with mock.patch("ticket_controller.dagmod.verify", return_value="together"), \
                mock.patch("ticket_controller.dagmod.deps", return_value=[]):
            c = self.ctl(2)
            self.assertFalse(c.joinable(self.snap(["1", "2"]), "9", {"id": "g1", "members": ["1", "2"]}))
            self.assertTrue(c.joinable(self.snap(["1"]), "9", {"id": "g1", "members": ["1"]}))
            self.assertTrue(c.joinable(self.snap(["1", "9"]), "9", {"id": "g1", "members": ["1", "9"]}))

    def test_member_of_an_overfull_group_goes_back_to_it(self):
        """A group filled before the cap existed keeps its members: a ready member returns to it."""
        from unittest import mock
        members = ["1", "2", "3"]
        snap = mock.Mock(dag={}, tickets={"3": {"group": "g1"}},
                         groups={"g1": {"id": "g1", "state": "open", "members": members}})
        with mock.patch("ticket_controller.dagmod.verify", return_value="together"), \
                mock.patch("ticket_controller.dagmod.deps", return_value=[]):
            self.assertEqual(self.ctl(2).group_for(snap, "3"), "g1")

    def test_loop_with_cap_2_keeps_every_group_at_two(self):
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)
        self.config(max_group_size=2, max_parallel=4, max_rounds=4)
        for i in range(1, 9):
            self.add(i, deps=[1] if i == 5 else [2] if i == 6 else [])
        self.plan(worker={"4": ["nothing", "nothing", "ok"], "7": ["slow"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        groups = self.pool.snapshot().groups
        self.assertTrue(groups)
        for g in groups.values():
            self.assertLessEqual(len(g["members"]), 2, g)
        self.assertEqual(sum(len(g["members"]) for g in groups.values()), 8)


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

    def layout(self, third_state):
        self.add(1, state="landed")
        self.add(2, deps=[1])
        self.add(3, state=third_state, group="g1", target="int/g1")
        git(self.proj, "branch", "int/g1", "main")
        g = {"id": "g1", "branch": "int/g1", "state": "open", "alone": False, "members": ["3"],
             "fix_round": 0, "job": None}
        self.pool.update(lambda tx: tx.put_group(g), "test: group g1")
        self.commit_on("int/g1", {"shared.txt": "group\n"})
        self.commit_on("main", {"shared.txt": "main\n"})
        c = self.ctl()
        c.advance_groups(self.snap())
        return c

    def test_landed_dep_does_not_join_a_group_lacking_main(self):
        """Item 4."""
        c = self.layout("running")
        snap = self.snap()
        g1 = snap.groups["g1"]
        # the layout is the one the ticket describes: main's merge conflicted, g1 still open
        self.assertEqual(g1["state"], "open")
        self.assertTrue(g1.get("main_tried"), g1)
        self.assertFalse(c.repo.is_ancestor("main", "refs/heads/int/g1"))
        self.assertIs(c.joinable(snap, "2", g1), False)

    def test_group_for_picks_no_group_lacking_main(self):
        """Item 4 (group_for half): joins none, opens none."""
        c = self.layout("running")
        snap = self.snap()
        self.assertEqual(snap.groups["g1"]["state"], "open")
        self.assertFalse(c.repo.is_ancestor("main", "refs/heads/int/g1"))
        self.assertIsNone(c.group_for(snap, "2"))
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


if __name__ == "__main__":
    unittest.main()
