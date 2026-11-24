"""#117: groups form one chain - a group's regression waits until every earlier group has landed."""

import importlib
import importlib.util
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_harness import HOME, LOOP, LoopCase  # noqa: E402,F401
from core.pool import Snapshot  # noqa: E402


def events(snap, gid):
    return [(h.get("at"), h.get("event")) for h in snap.groups[gid].get("history") or []]


def started(snap, gid):
    return [at for at, ev in events(snap, gid) if ev == "regression started"]


def landed_at(snap, gid):
    rows = [at for at, ev in events(snap, gid) if ev == "landed"]
    return rows[-1] if rows else None


class ChainHelpers(unittest.TestCase):
    """Acceptance 1: core.chain order / holds / waits_for / behind on the ticket's fixture."""

    def chain(self):
        self.assertIsNotNone(importlib.util.find_spec("core.chain"), "devloop/core/chain.py is missing")
        mod = importlib.import_module("core.chain")
        for name in ("order", "holds", "waits_for", "behind"):
            self.assertTrue(callable(getattr(mod, name, None)), "core.chain.%s is missing" % name)
        return mod

    def fixture(self):
        tickets = {}
        for ident, state in (("1", "landed"), ("2", "stuck"), ("3", "merged"), ("4", "cancelled"),
                             ("5", "merged"), ("6", "stuck"), ("10", "ready")):
            gid = "g5" if ident == "6" else "g" + ident
            tickets[ident] = {"id": ident, "state": state, "group": gid}
        groups = {}
        for gid, state, members in (("g1", "landed", ["1"]), ("g2", "stuck", ["2"]), ("g3", "pr", ["3"]),
                                    ("g4", "cancelled", ["4"]), ("g5", "stuck", ["5", "6"]),
                                    ("g10", "open", ["10"])):
            groups[gid] = {"id": gid, "state": state, "members": members}
        return Snapshot({"dag": {"tickets": {}}, "tickets": tickets, "groups": groups, "inbox": {},
                         "lessons": {}, "memory_state": {}})

    def test_order(self):
        chain = self.chain()
        self.assertEqual(chain.order(self.fixture()), ["g2", "g3", "g5", "g10"])

    def test_holds(self):
        chain, snap = self.chain(), self.fixture()
        expected = {"g1": False, "g2": False, "g3": True, "g5": True, "g10": True}
        self.assertEqual({gid: bool(chain.holds(snap, gid)) for gid in expected}, expected)

    def test_waits_for(self):
        chain, snap = self.chain(), self.fixture()
        expected = {"g1": None, "g2": None, "g3": None, "g5": "g3", "g10": "g5"}
        self.assertEqual({gid: chain.waits_for(snap, gid) for gid in expected}, expected)

    def test_behind(self):
        chain, snap = self.chain(), self.fixture()
        expected = {"g2": [], "g3": ["g5", "g10"], "g5": ["g10"], "g10": []}
        self.assertEqual({gid: list(chain.behind(snap, gid)) for gid in expected}, expected)

    def test_a_member_merged_in_another_group_does_not_count(self):
        """'a merged member' needs the ticket's group to be gid: g5's #5 moved to g7 leaves g5
        with no merged member, so g5 holds no one and g10 waits for g3."""
        chain, snap = self.chain(), self.fixture()
        snap.tickets["5"]["group"] = "g7"
        self.assertFalse(chain.holds(snap, "g5"))
        self.assertEqual(chain.waits_for(snap, "g10"), "g3")
        self.assertEqual(list(chain.behind(snap, "g5")), [])


class T117Acceptance(LoopCase):
    """Acceptance 3, 4, 5 (end to end through the controller) and 9 (README)."""

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(k)

    def run_ok(self, timeout=240):
        res = self.controller(timeout=timeout)
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-2000:])
        return res

    def test_two_groups_reaching_pr_together_run_one_regression_each(self):
        """Acceptance 3."""
        self.add(1)
        self.add(2)
        self.run_ok()
        snap = self.snap()
        self.assertEqual((self.state(1), self.state(2)), ("landed", "landed"))
        self.assertEqual(len(started(snap, "g1")), 1, events(snap, "g1"))
        self.assertEqual(len(started(snap, "g2")), 1, events(snap, "g2"))
        g1_landed = landed_at(snap, "g1")
        self.assertIsNotNone(g1_landed, events(snap, "g1"))
        self.assertGreaterEqual(started(snap, "g2")[0], g1_landed, events(snap, "g2"))
        sha = snap.groups["g1"].get("landed_sha") or ""
        self.assertTrue(sha, snap.groups["g1"])
        moved = "main moved to %s: merged it into int/g2" % sha[:12]
        evs = [ev for _, ev in events(snap, "g2")]
        self.assertIn(moved, evs, evs)
        self.assertLess(evs.index(moved), evs.index("regression started"), evs)

    def test_a_later_group_done_first_waits_for_the_earlier_one(self):
        """Acceptance 4."""
        self.add(1)
        self.add(2)
        self.plan(worker={"1": ["slow"]})
        self.run_ok()
        snap = self.snap()
        self.assertEqual((self.state(1), self.state(2)), ("landed", "landed"))
        self.assertEqual(len(started(snap, "g2")), 1, events(snap, "g2"))
        g1_landed = landed_at(snap, "g1")
        self.assertIsNotNone(g1_landed, events(snap, "g1"))
        self.assertGreaterEqual(started(snap, "g2")[0], g1_landed, events(snap, "g2"))

    def test_a_stuck_group_with_a_merged_member_holds_later_groups_and_the_run_ends(self):
        """Acceptance 5."""
        self.add(1)
        self.add(2)
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["blocked"]})
        self.run_ok(timeout=60)
        snap = self.snap()
        self.assertEqual(snap.groups["g1"].get("state"), "stuck", events(snap, "g1"))
        self.assertEqual(snap.groups["g2"].get("state"), "pr", events(snap, "g2"))
        self.assertEqual(started(snap, "g2"), [], events(snap, "g2"))
        self.assertEqual(self.state(2), "merged")
        idle = [r for r in snap.inbox.values() if r.get("kind") == "idle"]
        self.assertEqual(len(idle), 1, self.inbox_kinds())

    def test_readme_says_groups_form_one_chain(self):
        """Acceptance 9."""
        with open(os.path.join(HOME, "devloop", "README.md")) as fh:
            text = fh.read()
        lines = text.splitlines()
        anchor = "group complete (= one PR, a local mark) -> regression.py"
        start = next((i for i, ln in enumerate(lines) if ln.startswith(anchor)), None)
        self.assertIsNotNone(start, "flow block line missing")
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("```")), len(lines))
        flow = " ".join(" ".join(lines[start + 1:end]).split())
        self.assertIn("one chain", flow, "the flow block after the group-complete line does not name the chain")
        rest = " ".join(" ".join(lines[end:]).split())
        self.assertIn("one chain", rest, "the grouping text does not say 'one chain'")
        self.assertIn("every earlier group is landed or cancelled", rest)
        paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", "\n".join(lines[end:]))]
        self.assertTrue(any("stuck" in p and "holds no one" in p and "merged" in p for p in paras),
                        "no paragraph says a stuck group with no merged ticket holds no one")


if __name__ == "__main__":
    unittest.main()


class BehindLine(unittest.TestCase):
    """#120: the stuck-group page line from core.chain.behind (the implementer's)."""

    def snap(self, groups):
        tickets = {"1": {"id": "1", "state": "merged", "group": "g1"}}
        return Snapshot({"dag": {"tickets": {}}, "tickets": tickets,
                         "groups": {gid: {"id": gid, "state": st, "members": ["1"] if gid == "g1" else []}
                                    for gid, st in groups},
                         "inbox": {}, "lessons": {}, "memory_state": {}})

    def test_names_every_later_group_in_chain_order(self):
        from core import chain
        snap = self.snap([("g1", "stuck"), ("g12", "open"), ("g3", "pr"), ("g2", "landed")])
        self.assertEqual(chain.behind_line(snap, "g1"), "later groups waiting for g1 to land: 2 (g3, g12)")
        self.assertEqual(chain.with_behind("head", snap, "g1"),
                         "head\nlater groups waiting for g1 to land: 2 (g3, g12)")

    def test_nothing_behind_is_empty(self):
        from core import chain
        snap = self.snap([("g1", "stuck"), ("g0", "landed")])
        self.assertEqual(chain.behind_line(snap, "g1"), "")
        self.assertEqual(chain.with_behind("head", snap, "g1"), "head")


class DagOrderUnits(unittest.TestCase):
    """#122: core.chain order / sequence / cycle edge cases and the call sites (the implementer's)."""

    def snap(self, groups, tickets=(), deps=None):
        """groups: (gid, state, created or None); tickets: (id, group, state)."""
        gs = {}
        for gid, state, created in groups:
            gs[gid] = {"id": gid, "state": state, "members": []}
            if created is not None:
                gs[gid]["created"] = created
        ts = {i: {"id": i, "state": st, "group": g} for i, g, st in tickets}
        dag = {i: {"depends_on": d} for i, d in (deps or {}).items()}
        return Snapshot({"dag": {"tickets": dag}, "tickets": ts, "groups": gs,
                         "inbox": {}, "lessons": {}, "memory_state": {}})

    def test_natural_id_order_when_nothing_else_differs(self):
        from core import chain
        snap = self.snap([(g, "open", None) for g in ("gx", "g10", "g63", "g62a", "g2", "g62")])
        self.assertEqual(chain.order(snap), ["g2", "g10", "g62", "g62a", "g63", "gx"])

    def test_a_missing_created_sorts_first(self):
        from core import chain
        snap = self.snap([("g1", "open", "2026-11-20T00:00:01Z"), ("g2", "open", None)])
        self.assertEqual(chain.order(snap), ["g2", "g1"])

    def test_edges_of_landed_or_cancelled_tickets_and_groups_are_ignored(self):
        from core import chain
        groups = [("g1", "open", "a"), ("g2", "open", "b")]
        for t1, t2, want in (("ready", "ready", ["g2", "g1"]), ("ready", "landed", ["g1", "g2"]),
                             ("cancelled", "ready", ["g1", "g2"]), ("ready", "cancelled", ["g1", "g2"])):
            snap = self.snap(groups, [("1", "g1", t1), ("2", "g2", t2)], {"1": ["2"]})
            self.assertEqual(chain.order(snap), want, (t1, t2))
        snap = self.snap([("g1", "open", "a"), ("g2", "cancelled", "b"), ("g3", "open", "c")],
                         [("1", "g1", "ready"), ("2", "g2", "merged")], {"1": ["2"]})
        self.assertEqual(chain.order(snap), ["g1", "g3"])
        self.assertEqual(chain.sequence(snap), ["g2", "g1", "g3"])

    def test_prerequisites_are_pulled_ahead_recursively(self):
        from core import chain
        snap = self.snap([("g1", "open", "a"), ("g2", "open", "b"), ("g3", "open", "c"), ("g4", "open", "d")],
                         [("1", "g1", "ready"), ("3", "g3", "ready"), ("4", "g4", "merged")],
                         {"1": ["3"], "3": ["4"]})
        self.assertEqual(chain.order(snap), ["g4", "g3", "g1", "g2"])
        self.assertEqual(chain.waits_for(snap, "g2"), "g1")
        self.assertIsNone(chain.cycle(snap))

    def test_cycle_starts_at_the_smallest_key_on_one(self):
        from core import chain
        snap = self.snap([("g1", "open", "a"), ("g2", "open", "b"), ("g3", "open", "c")],
                         [("1", "g1", "ready"), ("2", "g2", "ready"), ("3", "g3", "ready")],
                         {"1": ["2"], "2": ["3"], "3": ["1"]})
        self.assertEqual(chain.cycle(snap), ["g1", "g2", "g3", "g1"])
        self.assertEqual(sorted(chain.order(snap)), ["g1", "g2", "g3"])
        snap = self.snap([("g1", "open", "a"), ("g2", "open", "b"), ("g3", "open", "c")],
                         [("1", "g1", "ready"), ("2", "g2", "ready"), ("3", "g3", "ready"), ("4", "g3", "ready")],
                         {"1": ["2"], "3": ["2"], "2": ["4"]})
        self.assertEqual(chain.cycle(snap), ["g2", "g3", "g2"])

    def test_the_opener_refuses_a_cycle_among_new_groups(self):
        import ticket_creator
        snap = self.snap([])
        result = {"tickets": [{"key": k} for k in ("new-1", "new-2", "new-3")],
                  "dag": {"new-1": {"depends_on": ["new-2"], "group": "new-g1"},
                          "new-2": {"depends_on": ["new-3"], "group": "new-g2"},
                          "new-3": {"depends_on": [], "group": "new-g1"}}}
        self.assertEqual(ticket_creator.group_problems(result, snap),
                         ["new-3: group new-g1 makes the group order a cycle: new-g1 -> new-g2 -> new-g1"])
        self.assertEqual(snap.groups, {}, "the check runs on a copy")

    def test_a_cycle_already_in_the_pool_refuses_nothing_new(self):
        import ticket_creator
        snap = self.snap([("g1", "open", "a"), ("g2", "open", "b")],
                         [("1", "g1", "ready"), ("2", "g2", "ready"), ("3", "g2", "ready")],
                         {"1": ["2"], "3": ["1"]})
        result = {"tickets": [{"key": "new-1"}], "dag": {"new-1": {"depends_on": ["1"], "group": "g1"}}}
        self.assertEqual(ticket_creator.group_problems(result, snap), [])

    def test_tickets_routes_every_non_number_to_the_group_path(self):
        import tickets
        self.assertEqual([tickets._is_ticket_id(i) for i in ("12", "g12", "g62a", "x")],
                         [True, False, False, False])

    def test_no_code_sorts_groups_by_the_digits_of_the_id(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "devloop")
        hits = []
        for base, _, files in os.walk(root):
            for name in files:
                if name.endswith(".py"):
                    with open(os.path.join(base, name)) as fh:
                        for n, line in enumerate(fh, 1):
                            if re.search(r"int\([^)]*\[1:\]\)|_gnum|group_order", line) \
                                    and "next_group_id" not in line and 'k[1:]) for k in self.groups' not in line:
                                hits.append("%s:%d" % (name, n))
        self.assertEqual(hits, [])

    def test_texts_name_the_dag_order(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "devloop", "prompts", "creator", "02-rules.md")) as fh:
            rules = " ".join(fh.read().split("# Rules for the DAG", 1)[1].split())
        self.assertNotIn("Never a group that comes before", rules)
        self.assertIn("cycle", rules)
        with open(os.path.join(root, "devloop", "README.md")) as fh:
            readme = " ".join(fh.read().split())
        for old in ("group-number order", "chain order: existing groups by number"):
            self.assertNotIn(old, readme)
        self.assertIn("makes the group order a cycle", readme)
