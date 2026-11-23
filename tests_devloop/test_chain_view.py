"""issue #6 part 4 (#120): where a group stands in the chain, on the idle page, a stuck group's page and the
board's DAG tab. Expected values come from the ticket text, never from the implementation."""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_harness import LoopCase  # noqa: E402


class T120Acceptance(LoopCase):
    NS = "{http://www.w3.org/2000/svg}"
    NOW = 1793000000

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(k)

    # ------------------------------------------------------------ helpers
    def pages(self, kind, ref=None):
        return [r for r in self.snap().inbox.values()
                if r.get("kind") == kind and (ref is None or r.get("ref") == ref)]

    def decision_page(self, start):
        found = [p for p in self.pages("decision", "g1") if (p.get("text") or "").startswith(start)]
        self.assertEqual(len(found), 1, "one decision page for g1 starting %r: %r"
                         % (start, self.pages("decision")))
        return found[0]

    # ------------------------------------------------------------ acceptance 1
    def test_item1_idle_page_names_the_wait_and_the_place(self):
        self.add(1)
        self.add(2, deps=[1])
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["blocked"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        pages = self.pages("idle")
        self.assertEqual(len(pages), 1, "exactly one idle page: %r" % pages)
        lines = [ln for ln in pages[0]["text"].split("\n") if ln.startswith("#") or ln.startswith("g")]
        expected = ["#2 ready, waits for: #1 merged in g1 (stuck)",
                    "g1 stuck: fixer needs a decision",
                    "g2 waits for g1 to land (place 2 in the chain)"]
        self.assertEqual(lines, expected, pages[0]["text"])
        err = res.stderr.split("\n")
        self.assertIn("g2 waits for g1 to land (place 2 in the chain)", err, res.stderr)

    # ------------------------------------------------------------ acceptance 2
    def test_item2a_still_red_page_counts_the_groups_behind(self):
        self.config(max_rounds=1)
        self.add(1)
        self.add(2)
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["nothing"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        page = self.decision_page("group g1 is still red after 1 fix rounds")
        self.assertIn("later groups waiting for g1 to land: 1 (g2)", page["text"].split("\n"), page["text"])

    def test_item2b_fixer_decision_page_counts_the_groups_behind(self):
        self.add(1)
        self.add(2)
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["blocked"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        page = self.decision_page("group g1: the fixer needs your decision")
        self.assertIn("later groups waiting for g1 to land: 1 (g2)", page["text"].split("\n"), page["text"])

    # ------------------------------------------------------------ acceptance 3
    def lanes(self):
        import board
        import xml.etree.ElementTree as ET
        from core.config import Config
        page = board.render(Config(self.proj), self.snap(), query={"tab": "dag"}, now=self.NOW)
        m = re.search(r'<svg id="dag".*?</svg>', page, re.S)
        self.assertIsNotNone(m, "no <svg id=\"dag\"> on the DAG tab")
        root = ET.fromstring(m.group(0))
        return {el.get("data-group"): el for el in root.iter(self.NS + "g")
                if "lane" in (el.get("class") or "").split()}

    def title(self, lane):
        heads = [el for el in lane.iter(self.NS + "text") if "lh" in (el.get("class") or "").split()]
        self.assertEqual(len(heads), 1, "one <text class=\"lh\"> per lane")
        return "".join(heads[0].itertext())

    def test_item3_dag_lanes_carry_place_and_wait(self):
        self.add(1, state="merged", group="g21")
        self.add(7, state="merged", group="g21")
        self.add(3, state="ready", group="g22")
        self.add(4, state="ready", group="g22")
        self.add(8, state="ready", group="g22")
        self.pool.update(lambda tx: (
            tx.put_group({"id": "g21", "state": "pr", "branch": "int/g21", "members": ["1", "7"]}),
            tx.put_group({"id": "g22", "state": "open", "branch": "int/g22", "members": ["3", "4", "8"]})),
            "test: groups")
        lanes = self.lanes()
        self.assertIn("g21", lanes)
        self.assertIn("g22", lanes)
        g21, g22 = lanes["g21"], lanes["g22"]
        self.assertEqual(g21.get("data-place"), "1")
        self.assertIsNone(g21.get("data-waits"))
        self.assertTrue(self.title(g21).endswith(" · 2 張 · 鏈上第 1 個"), self.title(g21))
        self.assertEqual(g22.get("data-place"), "2")
        self.assertEqual(g22.get("data-waits"), "g21")
        self.assertTrue(self.title(g22).endswith(" · 3 張 · 鏈上第 2 個 · 等 g21 落地"), self.title(g22))

    def test_item3_landed_and_cancelled_lanes_get_neither(self):
        self.add(1, state="landed", group="g1")
        self.add(2, state="cancelled", group="g2")
        self.add(3, state="ready", group="g3")
        self.pool.update(lambda tx: (
            tx.put_group({"id": "g1", "state": "landed", "branch": "int/g1", "members": ["1"]}),
            tx.put_group({"id": "g2", "state": "cancelled", "branch": "int/g2", "members": ["2"]}),
            tx.put_group({"id": "g3", "state": "open", "branch": "int/g3", "members": ["3"]})),
            "test: groups")
        import board
        import xml.etree.ElementTree as ET
        from core.config import Config
        page = board.render(Config(self.proj), self.snap(), query={"tab": "dag", "done": "1"}, now=self.NOW)
        root = ET.fromstring(re.search(r'<svg id="dag".*?</svg>', page, re.S).group(0))
        lanes = {el.get("data-group"): el for el in root.iter(self.NS + "g")
                 if "lane" in (el.get("class") or "").split()}
        # positive control on the same setup: the open group is first in the chain
        self.assertEqual(lanes["g3"].get("data-place"), "1")
        self.assertIsNone(lanes["g3"].get("data-waits"))
        for gid in ("g1", "g2"):
            if gid in lanes:
                self.assertIsNone(lanes[gid].get("data-place"), gid)
                self.assertIsNone(lanes[gid].get("data-waits"), gid)
                self.assertNotIn("鏈上第", self.title(lanes[gid]), gid)


if __name__ == "__main__":
    unittest.main()
