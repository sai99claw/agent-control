"""#125: `actl dag` lets the main session change a ready ticket's dependencies and group (D-096).

Expected values come from the ticket text, not from the implementation."""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_harness import HOME, LOOP, LoopCase, git  # noqa: E402,F401
from core import dag as dagmod  # noqa: E402
from core import pool as pool_mod  # noqa: E402

STAMP = "2026-11-23T00:00:00Z"
SCRIPT = os.path.join(LOOP, "dag_edit.py")


class T125Acceptance(LoopCase):
    """Acceptance items 3-7 of #125, each case on a fresh pool with groups written raw."""

    # ------------------------------------------------------------ helpers

    def fixture(self, groups, tickets):
        """groups: {gid: (state, [members])}; tickets: {id: dict(group=, state=, deps=, **fields)}."""
        def change(tx):
            for gid, (state, members) in groups.items():
                tx.put_group({"id": gid, "branch": "int/" + gid, "state": state, "members": list(members),
                              "fix_round": 0, "job": None, "created": STAMP})
        if groups:
            self.pool.update(change, "test: groups")
        for ident, spec in tickets.items():
            spec = dict(spec)
            deps = spec.pop("deps", ())
            spec.setdefault("state", "ready")
            gid = spec.setdefault("group", None)
            if gid:
                spec.setdefault("target", "int/" + gid)
            self.add(ident, deps=deps, **spec)

    def dag(self, *args):
        self.assertTrue(os.path.isfile(SCRIPT), "devloop/dag_edit.py does not exist")
        return self.run_py("dag_edit.py", *args, timeout=120)

    def head(self):
        return git(self.pool_path, "rev-parse", "HEAD")

    def writes(self):
        return int(git(self.pool_path, "rev-list", "--count", "HEAD"))

    def clean(self):
        return git(self.pool_path, "status", "--porcelain") == ""

    def ok(self, *args):
        before = self.writes()
        res = self.dag(*args)
        self.assertEqual(res.returncode, 0, "%s: %s" % (args, res.stdout + res.stderr))
        self.assertEqual(self.writes(), before + 1, "%s: not one pool write" % (args,))
        self.assertTrue(self.clean(), args)
        return res

    def last_line(self, res):
        lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines, res.stdout)
        return lines[-1]

    def refused(self, args, *needles):
        head = self.head()
        res = self.dag(*args)
        self.assertEqual(res.returncode, 2, "%s: %s" % (args, res.stdout + res.stderr))
        lines = [ln for ln in res.stderr.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, "%s: %r" % (args, res.stderr))
        for n in needles:
            self.assertIn(n, lines[0], args)
        self.assertEqual(self.head(), head, "%s: pool written" % (args,))
        self.assertTrue(self.clean(), "%s: working copy not clean" % (args,))
        return lines[0]

    @staticmethod
    def events(obj, what):
        return [h for h in obj.get("history") or [] if h.get("event") == what]

    def by_why(self, obj, what):
        return [(h.get("by"), h.get("why")) for h in self.events(obj, what)]

    # ------------------------------------------------------------ item 6 (a), (b): dep add / drop

    def test_a_dep_add_then_b_drop(self):
        self.fixture({"g1": ("open", ["1"]), "g2": ("open", ["2"])},
                     {"1": dict(group="g1"), "2": dict(group="g2")})
        res = self.ok("dep", "1", "--add", "2", "--reason", "r")
        self.assertEqual(self.last_line(res), "chain: g2, g1")
        snap = self.snap()
        self.assertEqual(snap.dag["1"]["depends_on"], ["2"])
        last = snap.tickets["1"]["history"][-1]
        self.assertEqual((last.get("event"), last.get("by"), last.get("why")),
                         ("dag: depends on #2", "main-session", "r"))
        for gid in ("g1", "g2"):
            self.assertEqual(self.by_why(snap.groups[gid], "dag: #1 depends on #2"), [("main-session", "r")], gid)

        res = self.ok("dep", "1", "--drop", "2", "--reason", "r", "--by", "alice")
        self.assertEqual(self.last_line(res), "chain: g1, g2")
        snap = self.snap()
        self.assertEqual(snap.dag["1"]["depends_on"], [])
        last = snap.tickets["1"]["history"][-1]
        self.assertEqual((last.get("event"), last.get("by"), last.get("why")),
                         ("dag: no longer depends on #2", "alice", "r"))
        for gid in ("g1", "g2"):
            self.assertEqual(self.by_why(snap.groups[gid], "dag: #1 no longer depends on #2"), [("alice", "r")], gid)

    def test_dep_events_once_in_a_shared_group_and_none_in_a_landed_one(self):
        self.fixture({"g1": ("open", ["1", "3"]), "g2": ("landed", ["2"])},
                     {"1": dict(group="g1"), "2": dict(group="g2", state="landed"), "3": dict(group="g1")})
        g2_before = list(self.snap().groups["g2"].get("history") or [])
        self.ok("dep", "1", "--add", "3", "--reason", "why3")
        snap = self.snap()
        self.assertEqual(self.by_why(snap.groups["g1"], "dag: #1 depends on #3"), [("main-session", "why3")])
        res = self.ok("dep", "1", "--add", "2", "--reason", "why2")
        self.assertEqual(self.last_line(res), "chain: g1")
        snap = self.snap()
        self.assertEqual(snap.dag["1"]["depends_on"], ["3", "2"])
        self.assertEqual(self.by_why(snap.groups["g1"], "dag: #1 depends on #2"), [("main-session", "why2")])
        self.assertEqual(snap.groups["g2"].get("history") or [], g2_before, "a landed group got an event")

    # ------------------------------------------------------------ item 6 (c), (d): move

    def test_c_move_the_last_member_cancels_the_old_group(self):
        self.fixture({"g1": ("open", ["1"]), "g2": ("open", ["2"])},
                     {"1": dict(group="g1"), "2": dict(group="g2")})
        res = self.ok("move", "1", "--to", "g2", "--reason", "r")
        self.assertEqual(self.last_line(res), "chain: g2")
        snap = self.snap()
        g1, g2, t1 = snap.groups["g1"], snap.groups["g2"], snap.tickets["1"]
        self.assertEqual(g2["members"], ["2", "1"])
        self.assertEqual(t1["group"], "g2")
        self.assertIsNone(t1.get("target"))
        self.assertEqual(g1["members"], [])
        self.assertEqual(g1["state"], "cancelled")
        self.assertEqual(g1["history"][-1].get("event"), "cancelled: its last member #1 moved to g2")
        self.assertEqual(self.by_why(t1, "dag: moved from g1 to g2"), [("main-session", "r")])
        self.assertEqual(self.by_why(g1, "dag: member #1 moved to g2"), [("main-session", "r")])
        self.assertEqual(self.by_why(g2, "dag: member #1 moved in from g1"), [("main-session", "r")])

    def test_d_move_to_new(self):
        self.fixture({"g1": ("open", ["1", "3"]), "g2": ("open", ["2"])},
                     {"1": dict(group="g1"), "2": dict(group="g2"), "3": dict(group="g1")})
        res = self.ok("move", "1", "--to", "new", "--reason", "r", "--by", "bob")
        self.assertEqual(self.last_line(res), "chain: g1, g2, g3")
        snap = self.snap()
        self.assertIn("g3", snap.groups)
        g3 = snap.groups["g3"]
        self.assertEqual(g3["members"], ["1"])
        self.assertEqual(g3["state"], "open")
        first = g3["history"][0]
        self.assertEqual((first.get("event"), first.get("by"), first.get("why")),
                         ("dag: member #1 moved in from g1", "bob", "r"))
        self.assertEqual(snap.groups["g1"]["members"], ["3"])
        self.assertEqual(snap.groups["g1"]["state"], "open")
        self.assertEqual(snap.tickets["1"]["group"], "g3")
        self.assertIsNone(snap.tickets["1"].get("target"))
        self.assertEqual(self.by_why(snap.tickets["1"], "dag: moved from g1 to g3"), [("bob", "r")])
        self.assertEqual(self.by_why(snap.groups["g1"], "dag: member #1 moved to g3"), [("bob", "r")])

    def test_move_a_ticket_with_no_group(self):
        self.fixture({"g2": ("open", ["2"])}, {"1": dict(), "2": dict(group="g2")})
        self.ok("move", "1", "--to", "g2", "--reason", "r")
        snap = self.snap()
        self.assertEqual(snap.groups["g2"]["members"], ["2", "1"])
        self.assertEqual(snap.tickets["1"]["group"], "g2")
        self.assertEqual(self.by_why(snap.tickets["1"], "dag: moved from no group to g2"), [("main-session", "r")])
        self.assertEqual(self.by_why(snap.groups["g2"], "dag: member #1 moved in from no group"),
                         [("main-session", "r")])

    # ------------------------------------------------------------ item 5 / 6 (e): refusals

    def two_open(self, **t1):
        self.fixture({"g1": ("open", ["1"]), "g2": ("open", ["2"])},
                     {"1": dict(group="g1", **t1), "2": dict(group="g2")})

    def test_e_reason_missing_or_blank(self):
        self.two_open()
        self.refused(("dep", "1", "--add", "2"), "--reason")
        self.refused(("dep", "1", "--add", "2", "--reason", "  "), "--reason")
        self.refused(("move", "1", "--to", "g2", "--reason", ""), "--reason")
        self.refused(("dep", "9", "--add", "2"), "--reason")   # first in the order

    def test_e_n_unknown(self):
        self.two_open()
        self.refused(("dep", "9", "--add", "2", "--reason", "r"), "#9 does not exist")
        self.refused(("move", "9", "--to", "g2", "--reason", "r"), "#9 does not exist")
        self.ok("dep", "1", "--add", "2", "--reason", "r")

    def test_e_n_not_ready(self):
        self.two_open()
        for state in ("running", "stuck", "cancelled"):
            self.pool.update(lambda tx, s=state: tx.put_ticket(dict(tx.ticket("1"), state=s)), "test: " + state)
            self.refused(("dep", "1", "--add", "2", "--reason", "r"), "#1 is %s" % state)
            self.refused(("move", "1", "--to", "g2", "--reason", "r"), "#1 is %s" % state)
        for state in ("merged", "landed"):
            self.pool.update(lambda tx, s=state: tx.put_ticket(dict(tx.ticket("1"), state=s)), "test: " + state)
            line = self.refused(("dep", "1", "--add", "2", "--reason", "r"), "#1", "(%s)" % state)
            self.assertIn(pool_mod.frozen_reason("1", state), line)
        self.refused(("dep", "1", "--add", "9", "--reason", "r"), "#1 is feature complete")   # before M unknown

    def test_e_m_unknown_self_and_already(self):
        self.fixture({"g1": ("open", ["1", "3"]), "g2": ("open", ["2"])},
                     {"1": dict(group="g1", deps=["3"]), "2": dict(group="g2"), "3": dict(group="g1")})
        self.refused(("dep", "1", "--add", "9", "--reason", "r"), "#9 does not exist")
        self.refused(("dep", "1", "--drop", "9", "--reason", "r"), "#9 does not exist")
        self.refused(("dep", "1", "--add", "1", "--reason", "r"), "cannot depend on itself")
        self.refused(("dep", "1", "--add", "3", "--reason", "r"), "already depends on #3")
        self.ok("dep", "1", "--add", "2", "--reason", "r")

    def test_e_add_cancelled(self):
        self.fixture({"g1": ("open", ["1"])}, {"1": dict(group="g1"), "2": dict(state="cancelled"),
                                               "3": dict(state="landed")})
        self.refused(("dep", "1", "--add", "2", "--reason", "r"), "#2 is cancelled")
        self.ok("dep", "1", "--add", "3", "--reason", "r")

    def test_e_drop_absent(self):
        self.two_open()
        self.refused(("dep", "1", "--drop", "2", "--reason", "r"), "does not depend on #2")

    def test_e_drop_mentioned(self):
        self.two_open(deps=["2"])
        fields = [("objective", "after #2 lands"), ("acceptance", ["a", "like #2."]),
                  ("test_plan", "see #2"), ("outline", "uses #2's code"), ("notes", "#2")]
        for field, value in fields:
            self.pool.update(lambda tx, f=field, v=value: tx.put_ticket(dict(tx.ticket("1"), **{f: v})),
                             "test: " + field)
            self.refused(("dep", "1", "--drop", "2", "--reason", "r"), "mentions #2", "`actl new --revise 1`")
            self.pool.update(lambda tx, f=field: tx.put_ticket(dict(tx.ticket("1"), **{f: "#20 and #21"})),
                             "test: clear " + field)
        self.ok("dep", "1", "--drop", "2", "--reason", "r")   # '#2' followed by a digit is not a mention

    def test_e_move_bad_group(self):
        self.two_open()
        self.refused(("move", "1", "--to", "g9", "--reason", "r"), "group g9 does not exist")
        self.refused(("move", "1", "--to", "g1", "--reason", "r"), "already in g1")
        for state in ("pr", "fixing", "verified", "stuck", "landed", "cancelled"):
            self.pool.update(lambda tx, s=state: tx.put_group(dict(tx.data.groups["g2"], state=s)), "test: " + state)
            self.refused(("move", "1", "--to", "g2", "--reason", "r"), "group g2 is %s" % state)
        self.pool.update(lambda tx: tx.put_group(dict(tx.data.groups["g2"], state="open")), "test: open")
        self.ok("move", "1", "--to", "g2", "--reason", "r")

    def test_e_ticket_cycle(self):
        self.fixture({"g1": ("open", ["1", "2"])},
                     {"1": dict(group="g1"), "2": dict(group="g1", deps=["1"])})
        sentence = [p for p in dagmod.problems({"1": {"depends_on": ["2"]}, "2": {"depends_on": ["1"]}})
                    if p.startswith("cycle: ")]
        self.assertTrue(sentence)
        self.refused(("dep", "1", "--add", "2", "--reason", "r"), sentence[0])

    def test_e_ticket_cycle_comes_before_group_cycle(self):
        self.fixture({"g1": ("open", ["1"]), "g2": ("open", ["2"])},
                     {"1": dict(group="g1"), "2": dict(group="g2", deps=["1"])})
        line = self.refused(("dep", "1", "--add", "2", "--reason", "r"), "cycle: #")
        self.assertNotIn("the group order would be a cycle", line)

    def test_e_group_cycle(self):
        self.fixture({"g1": ("open", ["1", "4"]), "g2": ("open", ["2", "3"])},
                     {"1": dict(group="g1"), "2": dict(group="g2"), "3": dict(group="g2", deps=["1"]),
                      "4": dict(group="g1")})
        self.refused(("dep", "4", "--add", "2", "--reason", "r"),
                     "the group order would be a cycle: g1 -> g2 -> g1")
        self.ok("dep", "1", "--add", "4", "--reason", "r")

    # ------------------------------------------------------------ item 7: bin/actl

    def test_actl_dag_runs_the_script_and_help_lists_it(self):
        self.two_open()
        actl = os.path.join(HOME, "bin", "actl")
        res = subprocess.run([actl, "--help"], cwd=self.proj, env=self.env, capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue([ln for ln in res.stdout.splitlines() if ln.startswith("  dag dep|move")], res.stdout)
        before = self.writes()
        res = subprocess.run([actl, "dag", "dep", "1", "--add", "2", "--reason", "r"], cwd=self.proj,
                             env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.last_line(res), "chain: g2, g1")
        self.assertEqual(self.writes(), before + 1)
        snap = self.snap()
        self.assertEqual(snap.dag["1"]["depends_on"], ["2"])
        last = snap.tickets["1"]["history"][-1]
        self.assertEqual((last.get("event"), last.get("by"), last.get("why")),
                         ("dag: depends on #2", "main-session", "r"))
        head = self.head()
        res = subprocess.run([actl, "dag", "dep", "1", "--add", "2", "--reason", "r"], cwd=self.proj,
                             env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("already depends on #2", res.stderr)
        self.assertEqual(self.head(), head)


class T141Acceptance(LoopCase):
    """#141 (D-099): `actl dag after gA --add|--drop gB` puts a group after another. Items 4-9;
    expected values come from the ticket text, not from the implementation."""

    # ------------------------------------------------------------ helpers (copied from T125Acceptance)

    def fixture(self, groups, tickets):
        """groups: {gid: (state, [members]) or (state, [members], job)}; tickets: {id: dict(group=, state=, deps=)}."""
        def change(tx):
            for gid, spec in groups.items():
                state, members = spec[0], spec[1]
                job = spec[2] if len(spec) > 2 else None
                tx.put_group({"id": gid, "branch": "int/" + gid, "state": state, "members": list(members),
                              "fix_round": 0, "job": job, "created": STAMP})
        if groups:
            self.pool.update(change, "test: groups")
        for ident, spec in tickets.items():
            spec = dict(spec)
            deps = spec.pop("deps", ())
            spec.setdefault("state", "ready")
            gid = spec.setdefault("group", None)
            if gid:
                spec.setdefault("target", "int/" + gid)
            self.add(ident, deps=deps, **spec)

    def three(self, g1=("open",), t1=None, deps3=()):
        """Item 4's fixture: g1, g2, g3 with #1, #2, #3; g1's (state[, job]) and #1's fields overridable."""
        groups = {"g1": (g1[0], ["1"]) + tuple(g1[1:]), "g2": ("open", ["2"]), "g3": ("open", ["3"])}
        self.fixture(groups, {"1": dict(group="g1", **(t1 or {})), "2": dict(group="g2"),
                              "3": dict(group="g3", deps=list(deps3))})

    def set_after(self, edges):
        """dag.json group_after, written after every self.add()."""
        self.pool.update(lambda tx: tx.put_dag(dict(tx.data.raw["dag"], group_after=edges)), "test: group_after")

    def set_group(self, gid, **fields):
        self.pool.update(lambda tx: tx.put_group(dict(tx.data.groups[gid], **fields)), "test: %s %s" % (gid, fields))

    def dag(self, *args):
        self.assertTrue(os.path.isfile(SCRIPT), "devloop/dag_edit.py does not exist")
        return self.run_py("dag_edit.py", *args, timeout=120)

    def dagjson(self):
        return self.snap().raw["dag"]

    def head(self):
        return git(self.pool_path, "rev-parse", "HEAD")

    def writes(self):
        return int(git(self.pool_path, "rev-list", "--count", "HEAD"))

    def clean(self):
        return git(self.pool_path, "status", "--porcelain") == ""

    def ok(self, *args):
        before = self.writes()
        res = self.dag(*args)
        self.assertEqual(res.returncode, 0, "%s: %s" % (args, res.stdout + res.stderr))
        self.assertEqual(self.writes(), before + 1, "%s: not one pool write" % (args,))
        self.assertTrue(self.clean(), args)
        return res

    def first_line(self, res):
        lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines, res.stdout)
        return lines[0]

    def last_line(self, res):
        lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines, res.stdout)
        return lines[-1]

    def refused(self, args, sentence):
        """Item 6: exit 2, exactly one stderr line 'dag: <sentence>', pool HEAD unchanged, clean."""
        head = self.head()
        res = self.dag(*args)
        self.assertEqual(res.returncode, 2, "%s: %s" % (args, res.stdout + res.stderr))
        lines = [ln for ln in res.stderr.splitlines() if ln.strip()]
        self.assertEqual(lines, ["dag: " + sentence], args)
        self.assertEqual(self.head(), head, "%s: pool written" % (args,))
        self.assertTrue(self.clean(), "%s: working copy not clean" % (args,))
        return lines[0]

    @staticmethod
    def events(obj, what):
        return [h for h in obj.get("history") or [] if h.get("event") == what]

    def by_why(self, obj, what):
        return [(h.get("by"), h.get("why")) for h in self.events(obj, what)]

    # ------------------------------------------------------------ item 4: fixture walk (also items 1-3)

    def test_item4_add_then_drop(self):
        self.three()
        tickets_before = self.snap().raw["dag"].get("tickets")
        ticket_files = {i: dict(t) for i, t in self.snap().tickets.items()}
        res = self.ok("after", "g1", "--add", "g3", "--reason", "r")
        self.assertEqual(self.first_line(res), "g1 after g3")
        self.assertEqual(self.last_line(res), "chain: g3, g1, g2")
        snap = self.snap()
        self.assertEqual(snap.raw["dag"].get("group_after"), {"g1": ["g3"]})
        self.assertEqual(snap.raw["dag"].get("tickets"), tickets_before)
        self.assertEqual({i: dict(t) for i, t in snap.tickets.items()}, ticket_files, "a ticket file changed")
        for gid in ("g1", "g3"):
            self.assertEqual(self.by_why(snap.groups[gid], "dag: g1 after g3"), [("main-session", "r")], gid)
        self.assertEqual(self.events(snap.groups["g2"], "dag: g1 after g3"), [])

        res = self.ok("after", "g1", "--drop", "g3", "--reason", "r2", "--by", "alice")
        self.assertEqual(self.first_line(res), "g1 no longer after g3")
        self.assertEqual(self.last_line(res), "chain: g1, g2, g3")
        snap = self.snap()
        self.assertNotIn("g1", snap.raw["dag"].get("group_after") or {})
        self.assertEqual(snap.raw["dag"].get("tickets"), tickets_before)
        for gid in ("g1", "g3"):
            self.assertEqual(self.by_why(snap.groups[gid], "dag: g1 no longer after g3"), [("alice", "r2")], gid)
        self.assertEqual(self.events(snap.groups["g2"], "dag: g1 no longer after g3"), [])

    def test_item2_add_appends_and_drop_keeps_other_entries(self):
        self.three()
        self.ok("after", "g1", "--add", "g3", "--reason", "r")
        self.ok("after", "g1", "--add", "g2", "--reason", "r")
        self.assertEqual(self.dagjson().get("group_after"), {"g1": ["g3", "g2"]})
        self.ok("after", "g1", "--drop", "g3", "--reason", "r")
        self.assertEqual(self.dagjson().get("group_after"), {"g1": ["g2"]})

    # ------------------------------------------------------------ item 5: merged groups can be reordered

    def test_item5_pr_with_a_merged_member(self):
        self.three(g1=("pr",), t1=dict(state="merged"))
        res = self.ok("after", "g1", "--add", "g3", "--reason", "r")
        self.assertEqual(self.last_line(res), "chain: g3, g1, g2")

    def test_item5_fixing_with_a_fixer_job(self):
        self.three(g1=("fixing", {"kind": "fixer", "stamp": "x"}), t1=dict(state="merged"))
        res = self.ok("after", "g1", "--add", "g3", "--reason", "r")
        self.assertEqual(self.last_line(res), "chain: g3, g1, g2")

    def test_item5_stuck(self):
        self.three(g1=("stuck",), t1=dict(state="merged"))
        res = self.ok("after", "g1", "--add", "g3", "--reason", "r")
        self.assertEqual(self.last_line(res), "chain: g3, g1, g2")

    # ------------------------------------------------------------ item 6: refusals, one per sentence

    def test_item6a_reason_missing_or_blank(self):
        self.three()
        s = "--reason is required and must not be blank"
        self.refused(("after", "g1", "--add", "g3"), s)
        self.refused(("after", "g1", "--add", "g3", "--reason", "  "), s)
        self.refused(("after", "g1", "--drop", "g3", "--reason", ""), s)
        self.refused(("after", "g9", "--add", "g9", "--reason", " "), s)   # first in the order
        self.ok("after", "g1", "--add", "g3", "--reason", "r")

    def test_item6b_group_does_not_exist(self):
        self.three()
        self.refused(("after", "g9", "--add", "g3", "--reason", "r"), "group g9 does not exist")
        self.refused(("after", "g9", "--add", "g8", "--reason", "r"), "group g9 does not exist")
        self.refused(("after", "g1", "--add", "g9", "--reason", "r"), "group g9 does not exist")
        self.refused(("after", "g1", "--drop", "g9", "--reason", "r"), "group g9 does not exist")
        self.ok("after", "g1", "--add", "g3", "--reason", "r")

    def test_item6c_after_itself(self):
        self.three()
        self.refused(("after", "g1", "--add", "g1", "--reason", "r"), "g1 cannot be after itself")
        self.refused(("after", "g1", "--drop", "g1", "--reason", "r"), "g1 cannot be after itself")
        self.ok("after", "g1", "--add", "g3", "--reason", "r")

    def test_item6d_regression_running(self):
        self.three(g1=("pr", {"kind": "regression", "stamp": "x"}), t1=dict(state="merged"))
        self.set_after({"g1": ["g2"]})
        s = "group g1 has a regression running: its order no longer changes"
        self.refused(("after", "g1", "--add", "g3", "--reason", "r"), s)
        self.refused(("after", "g1", "--drop", "g2", "--reason", "r"), s)
        self.refused(("after", "g1", "--drop", "g3", "--reason", "r"), s)    # before (f)
        self.set_group("g1", state="verified")
        self.refused(("after", "g1", "--add", "g3", "--reason", "r"), s)     # job checked before the state
        self.set_group("g1", state="pr", job=None)
        self.ok("after", "g1", "--add", "g3", "--reason", "r")

    def test_item6d_verified_landed_cancelled(self):
        self.three()
        self.set_after({"g1": ["g2"]})
        for state in ("verified", "landed", "cancelled"):
            self.set_group("g1", state=state)
            s = "group g1 is %s: its order no longer changes" % state
            self.refused(("after", "g1", "--add", "g3", "--reason", "r"), s)
            self.refused(("after", "g1", "--drop", "g2", "--reason", "r"), s)
            self.refused(("after", "g1", "--drop", "g3", "--reason", "r"), s)   # before (f)
        self.set_group("g1", state="open")
        self.ok("after", "g1", "--add", "g3", "--reason", "r")

    def test_item6e_already_set(self):
        self.three()
        self.set_after({"g1": ["g3"]})
        self.refused(("after", "g1", "--add", "g3", "--reason", "r"), "g1 is already set after g3")
        self.assertEqual(self.dagjson().get("group_after"), {"g1": ["g3"]})
        self.ok("after", "g1", "--add", "g2", "--reason", "r")

    def test_item6e_b_landed_or_cancelled(self):
        self.three()
        for state in ("landed", "cancelled"):
            self.set_group("g3", state=state)
            self.refused(("after", "g1", "--add", "g3", "--reason", "r"), "group g3 is %s: g1 cannot wait for it" % state)
        self.ok("after", "g1", "--add", "g2", "--reason", "r")

    def test_item6f_drop_not_set(self):
        self.three()
        self.set_after({"g1": ["g2"]})
        self.refused(("after", "g1", "--drop", "g3", "--reason", "r"), "g1 is not set after g3")
        self.ok("after", "g1", "--drop", "g2", "--reason", "r")

    def test_item6_drop_an_edge_to_a_landed_group_is_accepted(self):
        self.three()
        self.set_group("g3", state="landed")
        self.set_after({"g1": ["g3"]})
        res = self.ok("after", "g1", "--drop", "g3", "--reason", "gone", "--by", "bob")
        self.assertEqual(self.first_line(res), "g1 no longer after g3")
        self.assertEqual(self.last_line(res), "chain: g1, g2")
        snap = self.snap()
        self.assertNotIn("g1", snap.raw["dag"].get("group_after") or {})
        for gid in ("g1", "g3"):
            self.assertEqual(self.by_why(snap.groups[gid], "dag: g1 no longer after g3"), [("bob", "gone")], gid)

    # ------------------------------------------------------------ item 7: cycles

    def test_item7i_cycle_through_a_ticket_dependency(self):
        self.three(deps3=["1"])
        self.refused(("after", "g1", "--add", "g3", "--reason", "r"),
                     "the group order would be a cycle: g1 -> g3 -> g1")
        self.assertNotIn("g1", self.dagjson().get("group_after") or {})
        self.ok("after", "g1", "--add", "g2", "--reason", "r")

    def test_item7ii_cycle_through_another_edge(self):
        self.three()
        self.ok("after", "g3", "--add", "g1", "--reason", "r")
        self.refused(("after", "g1", "--add", "g3", "--reason", "r"),
                     "the group order would be a cycle: g1 -> g3 -> g1")
        self.assertEqual(self.dagjson().get("group_after"), {"g3": ["g1"]})

    # ------------------------------------------------------------ item 8: docs

    def test_item8_actl_help(self):
        self.three()
        res = subprocess.run([os.path.join(HOME, "bin", "actl"), "--help"], cwd=self.proj, env=self.env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue([ln for ln in res.stdout.splitlines() if ln.startswith("  dag dep|move|after")], res.stdout)

    def test_item8_readme(self):
        with open(os.path.join(HOME, "devloop", "README.md")) as fh:
            readme = fh.read()
        start = "| `actl dag dep N --add M` / `--drop M` / `actl dag move N --to gK|new`"
        lines = readme.splitlines()
        rows = [i for i, ln in enumerate(lines) if ln.startswith(start)]
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("`actl dag after gA --add gB`", lines[rows[0]])
        self.assertTrue(lines[rows[0] + 1].startswith("| `python3 devloop/board.py serve"), lines[rows[0] + 1])
        paras = readme.split("\n\n")
        i = next(i for i, p in enumerate(paras) if p.startswith("Groups form one chain"))
        self.assertTrue(paras[i + 1].startswith("Only the main session changes the DAG by hand"), paras[i + 1][:80])
        self.assertIn("actl dag after", paras[i + 1])
        self.assertIn("D-099", paras[i + 1])

    def test_item8_claude_md(self):
        with open(os.path.join(HOME, "CLAUDE.md")) as fh:
            lines = fh.read().splitlines()
        move = [i for i, ln in enumerate(lines) if '`actl dag move N --to gK|new --reason "…"`' in ln]
        self.assertEqual(len(move), 1, move)
        self.assertEqual(lines[move[0] + 1],
                         '  把整組排到另一組後面是 `actl dag after gA --add gB --reason "…"`(票已合入的組也可以)。')

    # ------------------------------------------------------------ item 9: bin/actl

    def test_item9_actl_dag_after(self):
        self.three()
        before = self.writes()
        res = subprocess.run([os.path.join(HOME, "bin", "actl"), "dag", "after", "g1", "--add", "g3", "--reason", "r"],
                             cwd=self.proj, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.last_line(res), "chain: g3, g1, g2")
        self.assertEqual(self.writes(), before + 1)


if __name__ == "__main__":
    unittest.main()
