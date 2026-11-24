"""#118 (issue #6 part 2): a ticket's group is written when the ticket is opened; the controller
only follows it. Expected values come from the ticket text, never from the code."""

import os
import re
import subprocess
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_harness import HOME, LOOP, LoopCase, git  # noqa: E402,F401

STAMP = "2026-11-23T00:00:00Z"
NS = "{http://www.w3.org/2000/svg}"


def events(obj):
    return [h.get("event") for h in (obj or {}).get("history") or []]


class T118Acceptance(LoopCase):

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(k)

    # ------------------------------------------------------------ helpers

    def groups(self, *specs):
        """Opening-shaped groups, written raw: specs are (gid, members)."""
        def change(tx):
            for gid, members in specs:
                tx.put_group({"id": gid, "branch": "int/" + gid, "state": "open", "members": list(members),
                              "fix_round": 0, "job": None, "created": STAMP, "history": []})
        self.pool.update(change, "test: groups")

    def run_ctl(self, timeout=240):
        try:
            res = self.controller(timeout=timeout)
        except subprocess.TimeoutExpired:
            snap = self.snap()
            self.fail("the controller was still running after %ss; groups %s, tickets %s" % (
                timeout, {g: (v.get("state"), v.get("members")) for g, v in snap.groups.items()},
                {i: (t.get("state"), t.get("group")) for i, t in snap.tickets.items()}))
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-2000:])
        return res

    def dispatched(self, ident):
        t = self.snap().ticket(str(ident))
        return [h for h in t.get("history") or [] if h.get("event") == "dispatched"]

    def group_event_at(self, gid, what):
        rows = [h.get("at") for h in self.snap().groups[gid].get("history") or [] if h.get("event") == what]
        self.assertTrue(rows, "%s has no %r event: %s" % (gid, what, events(self.snap().groups[gid])))
        return rows[-1]

    def has_branch(self, name):
        return subprocess.run(["git", "rev-parse", "--verify", "--quiet", "refs/heads/" + name],
                              cwd=self.proj, capture_output=True).returncode == 0

    def open_tickets(self):
        res = self.run_py("ticket_creator.py", "--request", "x")
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-2000:])

    # ------------------------------------------------------------ acceptance 1

    def test_opening_makes_one_new_group_for_its_tickets(self):
        self.open_tickets()
        snap = self.snap()
        self.assertEqual(set(snap.groups), {"g1"})
        g = snap.groups["g1"]
        self.assertEqual(set(g), {"id", "branch", "state", "members", "fix_round", "job", "created", "history"}, g)
        self.assertEqual({k: g[k] for k in ("id", "branch", "state", "members", "fix_round", "job")},
                         {"id": "g1", "branch": "int/g1", "state": "open", "members": ["1", "2"],
                          "fix_round": 0, "job": None})
        self.assertTrue(g["created"], g)
        self.assertEqual(events(g), ["opened by the opener for #1, #2"])
        for ident in ("1", "2"):
            t = snap.ticket(ident)
            self.assertEqual(t.get("group"), "g1", t)
            self.assertIsNone(t.get("target"), t)
        self.assertFalse(self.has_branch("int/g1"), "the opening created int/g1")

    # ------------------------------------------------------------ acceptance 2

    def test_opening_keeps_only_depends_on_in_the_dag(self):
        self.add(9, state="landed")

        def change(tx):
            dag = tx.data.dag
            dag["9"] = {"depends_on": [], "verify": "alone", "why_alone": "x"}
            tx.put_dag({"tickets": dag})
        self.pool.update(change, "test: old node")
        self.open_tickets()
        dag = self.snap().dag
        for ident, node in dag.items():
            self.assertNotIn("verify", node, (ident, node))
            self.assertNotIn("why_alone", node, (ident, node))
        self.assertEqual(dag.get("10"), {"depends_on": []})
        self.assertEqual(dag.get("11"), {"depends_on": ["10"]})

    # ------------------------------------------------------------ acceptance 3

    def test_dispatch_follows_the_tickets_group(self):
        self.add(1, group="g1")
        self.add(2, group="g2")
        self.add(3, group="g1")
        self.groups(("g1", ["1", "3"]), ("g2", ["2"]))
        self.run_ctl()
        snap = self.snap()
        self.assertEqual([self.state(i) for i in (1, 2, 3)], ["landed"] * 3)
        self.assertEqual(snap.groups["g1"].get("members"), ["1", "3"])
        self.assertEqual(snap.groups["g2"].get("members"), ["2"])
        for ident, gid in (("1", "g1"), ("2", "g2"), ("3", "g1")):
            rows = self.dispatched(ident)
            self.assertTrue(rows, "#%s has no dispatched event" % ident)
            self.assertEqual({h.get("target") for h in rows}, {"int/" + gid}, (ident, rows))
        self.assertEqual(set(snap.groups), {"g1", "g2"})

    # ------------------------------------------------------------ acceptance 4

    def test_a_dependency_landed_in_an_earlier_group(self):
        self.add(1, group="g1")
        self.add(2, deps=[1], group="g2")
        self.groups(("g1", ["1"]), ("g2", ["2"]))
        self.run_ctl()
        snap = self.snap()
        self.assertEqual((self.state(1), self.state(2)), ("landed", "landed"))
        rows = self.dispatched(2)
        self.assertTrue(rows, "#2 has no dispatched event")
        self.assertGreaterEqual(rows[0]["at"], self.group_event_at("g1", "landed"))
        landed_sha, base = snap.groups["g1"].get("landed_sha"), snap.groups["g2"].get("base")
        self.assertTrue(landed_sha and base, (snap.groups["g1"], snap.groups["g2"]))
        res = subprocess.run(["git", "merge-base", "--is-ancestor", landed_sha, base], cwd=self.proj)
        self.assertEqual(res.returncode, 0, "g2's base does not contain g1's landed sha")

    # ------------------------------------------------------------ acceptance 5

    def test_tickets_without_a_group_are_adopted_one_group_each(self):
        self.add(1)
        self.add(2)
        self.add(3, deps=[1])
        self.run_ctl()
        snap = self.snap()
        self.assertEqual([self.state(i) for i in (1, 2, 3)], ["landed"] * 3)
        self.assertEqual({gid: g.get("members") for gid, g in snap.groups.items()},
                         {"g1": ["1"], "g2": ["2"], "g3": ["3"]})

    # ------------------------------------------------------------ acceptance 6

    def test_a_member_that_cannot_start_leaves_and_the_group_goes_on(self):
        self.add(1, group="g1")
        self.add(2, deps=[1], group="g1")
        self.groups(("g1", ["1", "2"]))
        self.plan(worker={"1": ["blocked"]})
        self.run_ctl(timeout=30)
        snap = self.snap()
        self.assertEqual(self.state(1), "stuck")
        t2 = snap.ticket("2")
        self.assertEqual(t2.get("state"), "ready")
        self.assertIsNone(t2.get("group"), t2)
        self.assertIsNone(t2.get("target"), t2)
        g1 = snap.groups["g1"]
        self.assertEqual(g1.get("state"), "stuck")
        self.assertEqual(g1.get("members"), ["1"])
        self.assertIn("member #2 left: it cannot start in g1", events(g1))
        self.assertEqual(set(snap.groups), {"g1"})

    # ------------------------------------------------------------ acceptance 8

    def test_a_later_group_waiting_on_a_stuck_earlier_group_stays_open(self):
        self.add(1, group="g1")
        self.add(2, deps=[1], group="g2")
        self.groups(("g1", ["1"]), ("g2", ["2"]))
        self.plan(worker={"1": ["red"]}, fixer={"g1": ["blocked"]})
        self.run_ctl(timeout=60)
        snap = self.snap()
        self.assertEqual(snap.groups["g1"].get("state"), "stuck")
        g2 = snap.groups["g2"]
        self.assertEqual((g2.get("state"), g2.get("members")), ("open", ["2"]))
        t2 = snap.ticket("2")
        self.assertEqual((t2.get("state"), t2.get("group")), ("ready", "g2"))
        self.assertEqual(self.inbox_kinds().count("idle"), 1, self.inbox_kinds())

    # ------------------------------------------------------------ acceptance 10

    def test_a_config_with_max_group_size_is_accepted(self):
        from core import config as configmod
        self.assertNotIn("max_group_size", repr(getattr(configmod, "DEFAULTS", {})))
        self.config(max_group_size=0)
        res = self.run_py("tickets.py", "list")
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-2000:])

    # ------------------------------------------------------------ acceptance 11

    def test_the_dag_field_verify_is_gone(self):
        from core import dag as dagmod
        self.assertFalse(hasattr(dagmod, "VERIFY"), "core/dag.py still has VERIFY")
        self.assertFalse(hasattr(dagmod, "verify"), "core/dag.py still has verify()")
        self.assertEqual(dagmod.problems({"1": {"verify": "sometimes"}, "2": {"verify": "alone"}}), [])
        for name in ("ticket_controller.py", "board.py"):
            with open(os.path.join(LOOP, name)) as fh:
                src = fh.read()
            self.assertIsNone(re.search(r"""dagmod\.verify|["']verify["']|why_alone""", src),
                              "%s still reads verify" % name)
        import ticket_creator
        text = ticket_creator.chain()
        self.assertNotIn('"verify"', text)
        self.assertNotIn("why_alone", text)

    def test_the_ticket_table_has_no_verify_column(self):
        import board
        from core.config import Config
        self.add(1)
        page = board.render(Config(self.proj), self.snap(), query={"tab": "tickets"})
        start = page.find("<h2>票</h2>")
        self.assertGreaterEqual(start, 0)
        start = page.find("<table", start)
        row = page[page.find("<tr", start):page.find("</tr>", start)]
        heads = re.findall(r"<th[^>]*>(.*?)</th>", row, re.S)
        self.assertEqual(heads, ["票", "狀態", "輪", "整合分支", "相依", "驗證者", "覆核", "token", "快取讀",
                                 "快取寫", "分鐘", "估 token", "說明", "標題"])

    # ------------------------------------------------------------ acceptance 12

    def test_a_lane_counts_its_members_without_a_cap(self):
        import xml.etree.ElementTree as ET
        import board
        from core.config import Config
        self.add(1, state="merged", group="g21")
        self.add(7, state="merged", group="g21")
        self.pool.update(lambda tx: tx.put_group({"id": "g21", "state": "pr", "branch": "int/g21",
                                                  "members": ["1", "7"]}), "test: g21")
        page = board.render(Config(self.proj), self.snap(), query={"tab": "dag"},
                            now=1795435200)
        m = re.search(r'<svg id="dag".*?</svg>', page, re.S)
        self.assertIsNotNone(m, "no <svg id=\"dag\"> on the DAG tab")
        root = ET.fromstring(m.group(0))
        lanes = [el for el in root.iter(NS + "g") if "lane" in (el.get("class") or "").split()
                 and el.get("data-group") == "g21"]
        self.assertEqual(len(lanes), 1, "one lane for g21")
        lane = lanes[0]
        self.assertEqual(lane.get("data-members"), "2")
        heads = [el for el in lane.iter(NS + "text") if "lh" in (el.get("class") or "").split()]
        self.assertEqual(len(heads), 1)
        title = "".join(heads[0].itertext())
        self.assertTrue(title.endswith(" · 2 張 · 鏈上第 1 個"), title)

    # ------------------------------------------------------------ acceptance 14

    def test_readme_tells_what_happens_to_existing_pools(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            text = fh.read()
        self.assertIn("a group of its own at the end of the chain", text)
        self.assertNotIn("max_group_size", text)


class T134Acceptance(LoopCase):
    """#134 (D-097): an open group with a stuck member waits for the decision on it unless nothing
    needs it. Expected values come from D-097 and the ticket text, never from the code."""

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

    def advance(self):
        c = self.ctl()
        self.assertTrue(hasattr(c, "advance_groups"), "Controller has no advance_groups")
        c.advance_groups(self.snap())
        return self.snap()

    @staticmethod
    def item_lines(text):
        return [ln for ln in text.splitlines() if ln.startswith("#") or ln.startswith("g")]

    # ------------------------------------------------------------ acceptance 1

    def test_a_dependent_member_holds_the_group_for_the_decision(self):
        self.add(1, state="stuck", group="g1", target="int/g1")
        self.add(2, deps=[1], group="g1", target="int/g1")
        self.group("g1", ["1", "2"])
        snap = self.advance()
        g1 = snap.groups["g1"]
        self.assertEqual((g1.get("state"), g1.get("members")), ("open", ["1", "2"]), events(g1))
        t2 = snap.ticket("2")
        self.assertEqual((t2.get("group"), t2.get("target")), ("g1", "int/g1"), t2)
        self.assertFalse([e for e in events(g1) if e and e.startswith("member #2 left")], events(g1))

    # ------------------------------------------------------------ acceptance 2

    def test_a_dependent_in_a_later_group_holds_the_group_and_stays_in_its_own(self):
        self.add(1, state="stuck", group="g1", target="int/g1")
        self.add(2, state="merged", group="g1", target="int/g1")
        self.add(3, deps=[1], group="g2", target="int/g2")
        self.group("g1", ["1", "2"])
        self.group("g2", ["3"])
        snap = self.advance()
        g1, g2 = snap.groups["g1"], snap.groups["g2"]
        self.assertEqual((g1.get("state"), g1.get("members")), ("open", ["1", "2"]), events(g1))
        self.assertEqual(g2.get("state"), "open", events(g2))
        self.assertIn("3", g2.get("members"), events(g2))

    # ------------------------------------------------------------ acceptance 3

    def test_a_stuck_member_nothing_needs_leaves_and_the_group_goes_on(self):
        self.add(1, state="stuck", group="g1", target="int/g1")
        self.add(2, state="merged", group="g1", target="int/g1")
        self.group("g1", ["1", "2"])
        snap = self.advance()
        t1 = snap.ticket("1")
        self.assertIsNone(t1.get("group"), t1)
        self.assertIsNone(t1.get("target"), t1)
        g1 = snap.groups["g1"]
        self.assertEqual((g1.get("members"), g1.get("state")), (["2"], "pr"), events(g1))
        self.assertIn("member #1 left: stuck, nothing depends on it and the rest of g1 is merged (D-097)",
                      events(g1))

    # ------------------------------------------------------------ acceptance 4

    def test_an_unfinished_member_that_cannot_start_holds_the_group(self):
        self.add(1, state="stuck", group="g1", target="int/g1")
        self.add(2, deps=[9], group="g1", target="int/g1")
        self.group("g1", ["1", "2"])
        snap = self.advance()
        g1 = snap.groups["g1"]
        self.assertEqual((g1.get("state"), g1.get("members")), ("open", ["1", "2"]), events(g1))
        self.assertEqual(snap.ticket("2").get("group"), "g1")

    # ------------------------------------------------------------ acceptance 5

    def test_a_stuck_member_alone_holds_the_group(self):
        self.add(1, state="stuck", group="g1", target="int/g1")
        self.group("g1", ["1"])
        snap = self.advance()
        g1 = snap.groups["g1"]
        self.assertEqual((g1.get("state"), g1.get("members")), ("open", ["1"]), events(g1))
        self.assertEqual(snap.ticket("1").get("group"), "g1")

    # ------------------------------------------------------------ acceptance 6

    def test_a_run_whose_group_waits_for_a_decision_ends_with_one_idle_page(self):
        self.add(1)
        self.plan(worker={"1": ["blocked"]})
        res = self.controller(timeout=60)
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-2000:])
        self.assertEqual(self.state(1), "stuck")
        idle = [r for r in self.snap().inbox.values() if r.get("kind") == "idle"]
        self.assertEqual(len(idle), 1, self.inbox_kinds())
        self.assertEqual(self.item_lines(idle[0]["text"]),
                         ["#1 stuck (decision): need the user", "g1 waits for the decision on #1"],
                         idle[0]["text"])

    # ------------------------------------------------------------ acceptance 7

    def test_a_stuck_ticket_made_ready_lands_in_the_group_it_held(self):
        self.add(1)
        self.plan(worker={"1": ["blocked", "ok"]})
        res = self.controller(timeout=60)
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-2000:])
        self.assertEqual(self.state(1), "stuck")
        res = self.run_py("tickets.py", "ready", "1")
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-2000:])
        t1 = self.snap().ticket("1")
        self.assertEqual((t1.get("group"), t1.get("target")), ("g1", "int/g1"), t1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-2000:])
        snap = self.snap()
        self.assertEqual(self.state(1), "landed", snap.ticket("1").get("history"))
        self.assertEqual(snap.ticket("1").get("group"), "g1")
        self.assertEqual(set(snap.groups), {"g1"})
