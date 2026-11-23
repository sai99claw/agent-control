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
