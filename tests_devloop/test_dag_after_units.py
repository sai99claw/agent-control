"""#141 (the implementer's): core.dagedit.after_check / after_apply on in-memory data (D-099)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "devloop"))

from core import chain, dagedit  # noqa: E402
from core.pool import Snapshot  # noqa: E402


def data(states, deps=None, after=None, jobs=None):
    """states: {gid: state}; group gN holds ticket N; deps: {ticket: [ids]}; after: group_after."""
    groups, tickets, dag = {}, {}, {"tickets": {}}
    for gid, state in states.items():
        n = gid[1:]
        groups[gid] = {"id": gid, "state": state, "members": [n], "job": (jobs or {}).get(gid),
                       "created": "2026-11-23T00:00:00Z"}
        tickets[n] = {"id": n, "state": "ready", "group": gid}
        dag["tickets"][n] = {"depends_on": list((deps or {}).get(n, []))}
    if after is not None:
        dag["group_after"] = after
    return Snapshot({"dag": dag, "tickets": tickets, "groups": groups})


OPEN3 = {"g1": "open", "g2": "open", "g3": "open"}


class AfterCheckUnits(unittest.TestCase):

    def test_refusals_in_order(self):
        d = data(OPEN3)
        self.assertEqual(dagedit.after_check(d, "g1", "g3", " ", True), "--reason is required and must not be blank")
        self.assertEqual(dagedit.after_check(d, "g9", "g8", "r", True), "group g9 does not exist")
        self.assertEqual(dagedit.after_check(d, "g1", "g8", "r", True), "group g8 does not exist")
        self.assertEqual(dagedit.after_check(d, "g1", "g1", "r", True), "g1 cannot be after itself")
        self.assertEqual(dagedit.after_check(d, "g1", "g3", "r", False), "g1 is not set after g3")
        self.assertIsNone(dagedit.after_check(d, "g1", "g3", "r", True))

    def test_job_and_state(self):
        d = data({"g1": "verified", "g2": "open"}, jobs={"g1": {"kind": "regression", "stamp": "x"}})
        self.assertEqual(dagedit.after_check(d, "g1", "g2", "r", True),
                         "group g1 has a regression running: its order no longer changes")
        d = data({"g1": "verified", "g2": "open"})
        self.assertEqual(dagedit.after_check(d, "g1", "g2", "r", True), "group g1 is verified: its order no longer changes")
        for state in ("pr", "fixing", "stuck"):
            d = data({"g1": state, "g2": "open"}, jobs={"g1": {"kind": "fixer", "stamp": "x"}})
            self.assertIsNone(dagedit.after_check(d, "g1", "g2", "r", True), state)

    def test_add_refusals_and_drop_of_landed(self):
        d = data({"g1": "open", "g2": "landed"}, after={"g1": ["g2"]})
        self.assertEqual(dagedit.after_check(d, "g1", "g2", "r", True), "g1 is already set after g2")
        self.assertIsNone(dagedit.after_check(d, "g1", "g2", "r", False))
        d = data({"g1": "open", "g2": "cancelled"})
        self.assertEqual(dagedit.after_check(d, "g1", "g2", "r", True), "group g2 is cancelled: g1 cannot wait for it")

    def test_cycle_through_dep_and_check_writes_nothing(self):
        d = data(OPEN3, deps={"3": ["1"]})
        before = repr(d.raw)
        self.assertEqual(dagedit.after_check(d, "g1", "g3", "r", True),
                         "the group order would be a cycle: g1 -> g3 -> g1")
        self.assertEqual(repr(d.raw), before)


class AfterApplyUnits(unittest.TestCase):

    def test_add_then_drop(self):
        tx = dagedit.Dry(data(OPEN3))
        self.assertIsNone(dagedit.after_apply(tx, "g1", "me", "r", add="g3"))
        self.assertEqual(tx.data.raw["dag"]["group_after"], {"g1": ["g3"]})
        self.assertEqual(chain.order(tx.data), ["g3", "g1", "g2"])
        for gid in ("g1", "g3"):
            self.assertEqual([(h["event"], h["by"], h["why"]) for h in tx.data.groups[gid]["history"]],
                             [("dag: g1 after g3", "me", "r")])
        self.assertNotIn("history", tx.data.groups["g2"])
        self.assertIsNone(dagedit.after_apply(tx, "g1", "al", "r2", drop="g3"))
        self.assertEqual(tx.data.raw["dag"]["group_after"], {})
        self.assertEqual(tx.data.groups["g3"]["history"][-1]["event"], "dag: g1 no longer after g3")
        self.assertEqual(chain.order(tx.data), ["g1", "g2", "g3"])

    def test_refusal_writes_nothing(self):
        tx = dagedit.Dry(data(OPEN3))
        before = repr(tx.data.raw)
        self.assertEqual(dagedit.after_apply(tx, "g1", "me", "r", drop="g3"), "g1 is not set after g3")
        self.assertEqual(repr(tx.data.raw), before)


if __name__ == "__main__":
    unittest.main()
