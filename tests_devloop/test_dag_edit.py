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


if __name__ == "__main__":
    unittest.main()
