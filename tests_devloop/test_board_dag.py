"""the board's DAG tab (#42, #82-#85) and the BoardLiveTab helpers its classes inherit (#66-#68)"""

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


class T85Acceptance(LoopCase):
    """#85: the DAG tab's top lane 待開 lists running openers and the opener's unanswered pages.
    Expected strings come from the ticket text, never from the implementation. BoardLiveTab is defined
    later in this module, so its helpers are called through it at run time instead of inherited (inheriting
    would also re-run its cases)."""

    NS = "{http://www.w3.org/2000/svg}"
    S_TEXT = "2026-11-06T11:45:00Z"
    S = calendar.timegm((2026, 11, 6, 11, 45, 0, 0, 0, 0))

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(k)

    def alive(self, started):
        return BoardLiveTab.alive(self, started)

    def dead(self, started, pid=999999):
        return BoardLiveTab.dead(self, started, pid)

    def cfg(self):
        return BoardLiveTab.cfg(self)

    def put(self, name, **rec):
        return BoardLiveTab.put(self, name, **rec)

    @staticmethod
    def stamp_text(epoch):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    def opener(self, name, started=None, title="Board DAG tab", revise=None, stamp=None):
        stamp = stamp or self.alive(self.S_TEXT if started is None else started)
        self.put(name, kind="opener", stamp=stamp, title=title, revise=revise, via="main", model="m", effort=None)
        return stamp

    def write_page(self, name, **row):
        row.setdefault("acked", False)
        self.pool.update(lambda tx: tx.write("inbox/%s.json" % name, row), "test: page " + name)

    # ------------------------------------------------------------ svg readers
    def root(self, now=None):
        import board
        page = board.render(self.cfg(), self.snap(), query={"tab": "dag"}, now=self.S + 860 if now is None else now)
        return self.svg_of(page)

    def svg_of(self, page):
        import xml.etree.ElementTree as ET
        m = re.search(r'<svg id="dag".*?</svg>', page, re.S)
        self.assertIsNotNone(m, "no <svg id=\"dag\"> on the DAG tab")
        try:
            return ET.fromstring(m.group(0))
        except ET.ParseError as exc:
            self.fail("the DAG svg does not parse: %s" % exc)

    @staticmethod
    def cls(el):
        return (el.get("class") or "").split()

    def with_class(self, root, name, tag=None):
        return [el for el in root.iter() if name in self.cls(el) and (tag is None or el.tag == self.NS + tag)]

    def opening(self, root):
        found = self.with_class(root, "opening", "g")
        self.assertEqual(len(found), 1, "one <g class=\"opening\"> in the svg")
        return found[0]

    def text_of(self, item):
        return "".join("".join(t.itertext()) for t in item.iter(self.NS + "text"))

    def items(self, root, kind):
        return [el for el in self.with_class(self.opening(root), "open-item", "g") if el.get("data-kind") == kind]

    def opener_texts(self, root):
        return [(el.get("data-name"), self.text_of(el)) for el in self.items(root, "opener")]

    def page_items(self, root):
        """[(data-page, text, href of the <a> wrapping it)]."""
        out = []
        for a in self.opening(root).iter(self.NS + "a"):
            for g in a:
                if g.tag == self.NS + "g" and "open-item" in self.cls(g) and g.get("data-kind") == "page":
                    out.append((g.get("data-page"), self.text_of(g), a.get("href")))
        unwrapped = [el for el in self.items(root, "page") if el.get("data-page") not in [p for p, _, _ in out]]
        self.assertEqual(unwrapped, [], "every page item is wrapped in an <a>")
        return out

    # ------------------------------------------------------------ item 1
    def test_item1_running_opener_lane_first_with_its_item(self):
        self.add(1)
        self.add(2, state="merged", group="g9")
        self.pool.update(lambda tx: tx.put_group({"id": "g9", "state": "open", "branch": "int/g9",
                                                  "members": ["2"]}), "test: g9")
        self.opener("opener-1")
        root = self.root()
        first = [el for el in root if el.tag == self.NS + "g"]
        self.assertTrue(first, "the svg has child <g> elements")
        first = first[0]
        self.assertIn("opening", self.cls(first), "the first child <g> of the svg is the 待開 lane")
        self.assertEqual(first.get("data-lane"), "待開")
        bands = [el for el in first.iter(self.NS + "rect") if "band" in self.cls(el)]
        self.assertTrue(bands, "the 待開 lane has a <rect class=\"band\">")
        lane_bands = [r for lane in self.with_class(root, "lane", "g") for r in lane.iter(self.NS + "rect")
                      if "band" in self.cls(r)]
        self.assertTrue(lane_bands, "the fixture draws group lanes")
        for r in lane_bands:
            self.assertLess(float(bands[0].get("y")), float(r.get("y")))
        heads = ["".join(t.itertext()) for t in first.iter(self.NS + "text") if "lh" in self.cls(t)]
        self.assertEqual(heads, ["待開"])
        self.assertEqual(self.opener_texts(root), [("opener-1", "開題:Board DAG tab · 14 分")])

    # ------------------------------------------------------------ item 2
    def test_item2_revise_untitled_clipped_and_order(self):
        import board
        self.add(1)
        self.opener("op-a", started="2026-11-06T11:40:00Z", title=None, revise="5")
        self.opener("op-b", started="2026-11-06T11:42:00Z", title=None)
        self.opener("op-c", started="2026-11-06T11:44:00Z", title="x" * 50)
        root = self.root()
        now = self.S + 860
        self.assertEqual(self.opener_texts(root), [
            ("op-a", "改票 #5 · " + board.live_elapsed(now - board.pool_seconds("2026-11-06T11:40:00Z"))),
            ("op-b", "開題 · " + board.live_elapsed(now - board.pool_seconds("2026-11-06T11:42:00Z"))),
            ("op-c", "開題:" + "x" * 36 + "… · " + board.live_elapsed(now - board.pool_seconds("2026-11-06T11:44:00Z"))),
        ])

    def test_item2_waiting_opener_appends_its_state_and_ended_is_not_listed(self):
        self.add(1)
        op = self.opener("op-wait", title="Waiting one")
        self.put("agent-w", kind="agent", stamp=self.alive("2026-11-06T11:46:00Z"), parent=op["pid"], role="creator",
                 ticket=None, group=None, round=None, run_dir="/r/w", log=None, command="claude", model="m",
                 effort=None, state="slot", since="2026-11-06T11:46:00Z", until=None)
        self.opener("op-gone", stamp=self.dead(osx.now()), title="Gone one")
        root = self.root()
        self.assertEqual(self.opener_texts(root), [
            ("op-wait", "開題:Waiting one · 14 分 · 等機器名額:第 1 個(共 1 個在等)")])

    # ------------------------------------------------------------ items 3 and 4
    def test_item3_and_4_opener_pages_listed_newest_first_others_not(self):
        import board
        self.add(1)
        self.add(5)
        self.pool.update(lambda tx: tx.put_group({"id": "g21", "state": "fixing", "branch": "int/g21",
                                                  "members": []}), "test: g21")
        # listed
        self.write_page("pa", kind="decision", ref="request", text="the opener needs your decision\nmore",
                        question="Which tab gets the lane?", at="2026-11-06T11:00:00Z")
        self.write_page("pb", kind="decision", ref="5", at="2026-11-06T11:10:00Z",
                        text="#5 needs your decision (raised by the worker): which file?")
        self.write_page("pc", kind="decision", ref="web", at="2026-11-06T11:20:00Z",
                        text="input from the web was NOT acted on - it looked like a prompt injection: x")
        self.write_page("pd", kind="decision", ref="request", at="2026-11-06T11:30:00Z", question="",
                        text="the opener could not produce valid tickets:\n- bad")
        # not listed
        self.write_page("n1", kind="decision", ref="request", text="old", question="q?", acked=True,
                        at="2026-11-06T11:31:00Z")
        for i, kind in enumerate(("env", "created", "landed", "idle", "cancelled")):
            self.write_page("k%d" % i, kind=kind, ref="request", text="input from the web x",
                            at="2026-11-06T11:3%d:00Z" % (2 + i))
        self.write_page("o1", kind="decision", ref="g21", at="2026-11-06T11:40:00Z",
                        text="group g21 is still red after 3 fix rounds: ...")
        self.write_page("o2", kind="decision", ref="5", at="2026-11-06T11:41:00Z", text="#5 is stuck (rounds): ...")
        self.write_page("o3", kind="decision", ref="g21", at="2026-11-06T11:42:00Z",
                        text="group g21: input from the web was NOT acted on - it looked like a prompt injection: y")
        inbox = self.snap().inbox
        for name in ("pa", "pb", "pc", "pd"):
            self.assertIn(name, inbox, "the fixture's page names are the inbox keys")
        root = self.root()
        self.assertEqual(self.page_items(root), [
            ("pd", "要你判:the opener could not produce valid tick…", "?tab=decide"),
            ("pc", "要你判:" + "input from the web was NOT acted on - it looked like a prompt injection: x"[:39] + "…",
             "?tab=decide"),
            ("pb", "要你判:" + "#5 needs your decision (raised by the worker): which file?"[:39] + "…", "?tab=decide"),
            ("pa", "要你判:Which tab gets the lane?", "?tab=decide"),
        ])
        self.assertEqual(board._clip("the opener could not produce valid tickets:", 40),
                         "the opener could not produce valid tickets:"[:39] + "…")

    # ------------------------------------------------------------ item 5
    def lanes_and_nodes(self, root):
        return ({el.get("data-group"): el for el in self.with_class(root, "lane", "g")},
                {el.get("data-id"): el for el in self.with_class(root, "node", "g")})

    def shape(self, el):
        """Every element below `el` in order: (tag, attributes without y, text, y or None)."""
        return [(d.tag, sorted((k, v) for k, v in d.attrib.items() if k != "y"), d.text, d.get("y"))
                for d in el.iter()]

    def test_item5_nothing_to_list_then_lanes_only_move_down(self):
        self.add(1, state="merged", group="g9")
        self.add(2, deps=[1])
        self.add(3, group="g9")
        self.pool.update(lambda tx: tx.put_group({"id": "g9", "state": "open", "branch": "int/g9",
                                                  "members": ["1", "3"]}), "test: g9")
        self.write_page("other", kind="decision", ref="g9", at="2026-11-06T11:00:00Z",
                        text="group g9 is still red after 3 fix rounds: ...")
        self.write_page("acked", kind="decision", ref="request", at="2026-11-06T11:01:00Z", text="q", acked=True)
        self.opener("op-gone", stamp=self.dead(osx.now()))
        before = self.root()
        self.assertEqual(self.with_class(before, "opening"), [], "nothing to list: no 'opening' element")
        lanes0, nodes0 = self.lanes_and_nodes(before)
        self.assertEqual(set(nodes0), {"1", "2", "3"})
        self.opener("op-live")
        after = self.root()
        self.opening(after)
        for el in self.with_class(after, "opening") + self.with_class(after, "open-item"):
            self.assertFalse({"lane", "node"} & set(self.cls(el)), "an opening/open-item element is no lane or node")
        lanes1, nodes1 = self.lanes_and_nodes(after)
        self.assertEqual(set(lanes1), set(lanes0))
        self.assertEqual(set(nodes1), set(nodes0))
        deltas = set()
        for key, a, b in ([("lane " + k, lanes0[k], lanes1[k]) for k in lanes0]
                          + [("node " + k, nodes0[k], nodes1[k]) for k in nodes0]):
            s0, s1 = self.shape(a), self.shape(b)
            self.assertEqual([x[:3] for x in s0], [x[:3] for x in s1], "%s keeps its markup" % key)
            for (_, _, _, y0), (_, _, _, y1) in zip(s0, s1):
                self.assertEqual(y0 is None, y1 is None, key)
                if y0 is not None:
                    deltas.add(round(float(y1) - float(y0), 6))
        self.assertEqual(len(deltas), 1, "every lane and node moves down by the same amount: %s" % deltas)
        self.assertGreater(deltas.pop(), 0)
        height = float(after.get("height"))
        for el in after.iter():
            if el.tag == self.NS + "rect":
                self.assertLessEqual(float(el.get("y")) + float(el.get("height")), height, "svg height holds rects")
            elif el.tag == self.NS + "text":
                self.assertLessEqual(float(el.get("y")), height, "svg height holds texts")

    # ------------------------------------------------------------ item 6
    def test_item6_served_board_end_to_end(self):
        import http.client
        import socket
        lock = os.path.join(self.cfg().worktree_root, "controller.lock")
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        with open(lock, "w") as fh:
            fh.write(json.dumps(osx.stamp()))
        self.pool.update(lambda tx: (
            tx.put_group({"id": "g21", "state": "pr", "branch": "int/g21", "members": ["1"]}),
            tx.put_group({"id": "g22", "state": "open", "branch": "int/g22", "members": ["3"]})), "test: groups")
        self.add(1, state="merged", group="g21")
        self.add(2, deps=[1])
        self.add(3, group="g22")
        started = int(time.time()) - 860
        self.put("regression-int-g21", kind="regression", stamp=self.alive(self.stamp_text(started)), group="g21",
                 branch="int/g21", sha="a" * 40, exclusive=False, stage="full", since=self.stamp_text(started))
        self.opener("opener-web", started=self.stamp_text(int(time.time()) - 60))
        key = os.path.join(self.tmp, "key")
        with open(key, "w") as fh:
            fh.write("k1\n")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "board.py"), "serve", "--port", str(port),
                                 "--key-file", key], cwd=self.proj, env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)

        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request("GET", path, headers={"X-Board-Key": "k1"})
                resp = conn.getresponse()
                return resp.status, resp.read().decode("utf-8")
            finally:
                conn.close()
        for _ in range(100):
            try:
                get("/")
                break
            except OSError:
                time.sleep(0.1)
        status, page = get("/?tab=dag")
        self.assertLess(time.time() - started, 899, "the GET came too late for the 14 分 oracle")
        self.assertEqual(status, 200)
        root = self.svg_of(page)
        lanes = {el.get("data-group"): el for el in self.with_class(root, "lane", "g")}
        nodes = {el.get("data-id"): el for el in self.with_class(root, "node", "g")}
        # (a)
        self.assertIn("g21", lanes)
        lh = ["".join(t.itertext()) for t in lanes["g21"].iter(self.NS + "text") if "lh" in self.cls(t)]
        self.assertEqual(len(lh), 1)
        self.assertIn("regression full", lh[0])
        self.assertIn("14 分", lh[0])
        # (b)
        self.assertIn("2", nodes)
        waits2 = ["".join(t.itertext()) for t in nodes["2"].iter(self.NS + "text") if "wait" in self.cls(t)]
        self.assertEqual(waits2, ["等 g21 落地 — regression 跑中"])
        self.assertTrue([el for el in root.iter(self.NS + "path") if "lanewait" in self.cls(el)
                         and el.get("data-from") == "lane:g21" and el.get("data-to") == "2"])
        # (c)
        texts = [self.text_of(el) for el in self.items(root, "opener")]
        self.assertTrue(any(t.startswith("開題:Board DAG tab") for t in texts), texts)
        # (d)
        for ident, n in nodes.items():
            if n.get("data-state") == "ready":
                w = ["".join(t.itertext()) for t in n.iter(self.NS + "text") if "wait" in self.cls(t)]
                self.assertTrue(w and w[0].strip(), "#%s has a non-empty wait line" % ident)
        self.assertTrue([el for el in root.iter() if el.get("data-to") == "2"
                         and set(self.cls(el)) & {"wait", "chain", "lanewait"}])
        # (e)
        self.assertIn("g22", lanes)
        self.assertIn("3", [el.get("data-id") for el in self.with_class(lanes["g22"], "node", "g")])
        st = ["".join(t.itertext()) for t in nodes["3"].iter(self.NS + "text") if "st" in self.cls(t)]
        self.assertEqual(st, ["組內待做"])


class BoardLiveTab(LoopCase):
    """#66's helpers: live_clock, live_elapsed, the log tail and step, and the rows live_rows builds."""

    NOW = calendar.timegm((2026, 11, 6, 12, 0, 0, 0, 0, 0))

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(k)

    def alive(self, started):
        p = subprocess.Popen(["sleep", "60"])
        self.addCleanup(p.wait)
        self.addCleanup(p.terminate)
        return {"pid": p.pid, "host": osx.host(), "boot": osx.boot_id(), "started": started}

    def dead(self, started, pid=999999):
        """A stamp of a gone process; `started` recent, or the next inflight.put prunes the record."""
        return {"pid": pid, "host": osx.host(), "boot": "other-boot", "started": started}

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def put(self, name, **rec):
        from core import inflight
        rec.setdefault("result", None)
        inflight.put(self.cfg(), name, rec)

    def rows(self):
        import board
        return board.live_rows(self.cfg(), self.snap())

    def log(self, name, lines):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write("".join(line + "\n" for line in lines))
        return path

    def test_live_clock_and_elapsed(self):
        import board
        self.assertEqual(board.live_clock(self.NOW), time.strftime("%m-%d %H:%M:%S", time.localtime(self.NOW)))
        for seconds, text in ((0, "0 分"), (59, "0 分"), (61, "1 分"), (3599, "59 分"), (3600, "1 小時 0 分"),
                              (5400, "1 小時 30 分"), (-5, "0 分"), (90061, "25 小時 1 分")):
            self.assertEqual(board.live_elapsed(seconds), text, seconds)

    def test_tail_keeps_the_last_twenty_lines_and_step_reads_the_last_mark(self):
        import board
        path = self.log("a.log", ["line %d" % i for i in range(1, 31)])
        self.assertEqual(board.live_tail(path).splitlines(), ["line %d" % i for i in range(11, 31)])
        self.assertEqual(board.live_tail(os.path.join(self.tmp, "missing.log")), "")
        self.assertEqual(board.live_tail(None), "")
        path = self.log("r.log", ["devloop-stage: build", "x", "devloop-stage:  upload  ", " devloop-stage: no"])
        self.assertEqual(board.live_release_step(path), "upload")
        self.assertIsNone(board.live_release_step(self.log("n.log", ["plain"])))

    def test_opener_takes_its_newest_agent_and_the_others_have_no_row(self):
        op = self.alive("2026-11-06T10:00:00Z")
        self.put("opener-1", kind="opener", stamp=op, title="a title", revise=None, via="main", model="m0", effort=None)
        for name, started, model in (("agent-a", "2026-11-06T10:01:00Z", "old"), ("agent-b", "2026-11-06T10:02:00Z", "new")):
            self.put(name, kind="agent", stamp=self.alive(started), parent=op["pid"], role="creator", ticket=None,
                     group=None, round=None, run_dir="/r/" + name, log=None, command="claude", model=model,
                     effort="high", state="slot", since=started, until=None)
        rows = self.rows()
        self.assertEqual([(r["kind"], r["state"]) for r in rows], [("opener", "slot")])
        self.assertEqual(rows[0]["what"], "開題:a title")
        self.assertEqual(rows[0]["model"], "claude · new · high")
        self.assertEqual(rows[0]["run_dir"], "/r/agent-b")

    def test_issuer_without_agent_regression_fixer_release_and_order(self):
        self.add(1, title="first")
        self.pool.update(lambda tx: tx.put_group({"id": "g2", "branch": "int/g2", "state": "fixing", "members": [],
                                                  "fix_round": 2}), "test: group")
        iss = self.alive("2026-11-06T09:00:00Z")
        self.put("issuer-1", kind="issuer", stamp=iss, ticket="1", target="int/g1", round=2)
        self.put("regression-int-g2", kind="regression", stamp=self.alive("2026-11-06T08:00:00Z"), group="g2",
                 branch="int/g2", sha="a" * 40, exclusive=False, stage="machine", since="2026-11-06T08:00:00Z")
        self.put("regression-int-g9", kind="regression", stamp=self.alive("2026-11-06T08:00:00Z"), group="g9",
                 branch="int/g9", sha="b" * 40, exclusive=False, stage="full", since="2026-11-06T08:00:00Z")
        fx = self.alive("2026-11-06T11:00:00Z")
        self.put("fixer-g2", kind="fixer", stamp=fx, group="g2", round=2, max_rounds=3)
        self.put("agent-f", kind="agent", stamp=self.alive("2026-11-06T11:01:00Z"), parent=fx["pid"], role="fixer",
                 ticket=None, group="g2", round=2, run_dir="/r/f", log=None, command="claude", model="m",
                 effort=None, state="quota", since="x", until="2026-11-06T13:00:00Z")
        log = self.log("rel.log", ["start", "devloop-stage: tagging"])
        self.put("release-g2", kind="release", stamp=self.alive("2026-11-06T11:30:00Z"), group="g2", sha="c" * 40,
                 log=log)
        self.put("opener-dead", kind="opener", stamp=self.dead(osx.now()), title=None, revise=7,
                 via="main", model=None, effort=None)
        handed = self.log("result.json", ["{}"])
        self.put("opener-done", kind="opener", stamp=self.dead(osx.now()), title="t", revise=None,
                 via="main", model=None, effort=None, result=handed)
        import board
        rows = self.rows()
        got = [(r["name"], r["kind"], r["state"], r["what"], r["text"]) for r in rows]
        self.assertEqual(got, [
            ("regression-int-g2", "regression", "slot", "g2 修第 2 輪後", "等整台機器"),
            ("regression-int-g9", "regression", "running", "g9 未知", "full"),
            ("issuer-1", "issuer", "running", "#1 first 第 2 輪", "沒有 agent 在跑"),
            ("fixer-g2", "fixer", "quota", "g2 第 2/3 輪",
             "額度用完,到 " + board.live_clock(board.pool_seconds("2026-11-06T13:00:00Z"))),
            ("release-g2", "release", "running", "g2 " + "c" * 12, "tagging"),
            ("opener-dead", "opener", "ended", "改票 #7", "結束了,沒有交件"),
        ])

    def test_render_escapes_marks_slots_and_creates_nothing(self):
        import board
        self.config(machine_slots=2)
        live = os.path.join(self.tmp, "wt", "runs", "live")
        slots_dir = os.path.join(self.tmp, "wt", "slots")
        page = board.render(self.cfg(), self.snap(), query={"tab": "live"}, now=self.NOW)
        self.assertFalse(os.path.exists(live))
        self.assertFalse(os.path.exists(slots_dir))
        self.assertIn('<a class="refresh" href="?tab=live">重新整理</a>', page)
        self.assertIn('<table class="live">', page)
        self.put("opener-x", kind="opener", stamp=self.alive("2026-11-06T11:00:00Z"), title="<b>x</b>",
                 revise=None, via="web", model="m", effort="low")
        page = board.render(self.cfg(), self.snap(), query={"tab": "live"}, now=self.NOW)
        self.assertIn("開題:&lt;b&gt;x&lt;/b&gt;", page)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn('data-slots="0/2"', page)
        self.assertIn('<td class="slots">名額 0/2</td>', page)
        self.assertIn('<td class="model">m · low</td>', page)
        self.assertIn('data-started="%d"' % (self.NOW - 3600), page)
        self.assertIn('<td class="elapsed">1 小時 0 分</td>', page)
        self.assertFalse(os.path.exists(slots_dir))


class LiveQueueUnits(BoardLiveTab):
    """#67's helpers: live_queue numbering, live_ticket_rows and live_decision_rows."""

    def test_queue_numbers_by_since_and_names_the_exclusive_regression(self):
        import board
        for name, since in (("agent-b", "2026-11-06T10:00:05Z"), ("agent-c", "2026-11-06T10:00:00Z"),
                            ("agent-a", "2026-11-06T10:00:00Z")):
            self.put(name, kind="agent", stamp=self.alive(since), parent=None, role="worker", ticket=None, group="g1",
                     round=1, run_dir=None, log=None, command="c", model=None, effort=None, state="slot",
                     since=since, until=None)
        self.put("regression-m", kind="regression", stamp=self.alive("2026-11-06T09:00:00Z"), group="g2",
                 branch="b", sha="a" * 40, exclusive=False, stage="machine", since="2026-11-06T09:00:00Z")
        got = {r["name"]: r["attrs"].get("data-queue") for r in self.rows()}
        self.assertEqual(got, {"agent-a": "1/3", "agent-c": "2/3", "agent-b": "3/3", "regression-m": None})
        self.put("regression-x", kind="regression", stamp=self.alive("2026-11-06T09:30:00Z"), group="g7",
                 branch="b", sha="a" * 40, exclusive=True, stage="full", since="2026-11-06T09:30:00Z")
        rows = [r for r in self.rows() if "data-queue" in r["attrs"]]
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertEqual(r["attrs"]["data-yield"], "g7")
            self.assertTrue(r["text"].endswith(",讓位給 g7 的獨占 regression"), r["text"])
        self.assertTrue(board.live_clock)

    def test_ticket_rows_name_unsatisfied_dependencies(self):
        import board
        self.add(1, state="landed")
        self.add(2, state="merged", group="g1")
        self.add(3, state="merged", group="g2")
        self.add(4, deps=[1, 2])
        self.add(5, deps=[3, 9])
        self.pool.update(lambda tx: (tx.put_group({"id": "g1", "branch": "int/g1", "state": "open", "members": ["2"]}),
                                     tx.put_group({"id": "g2", "branch": "int/g2", "state": "pr", "members": ["3"]})),
                         "test: groups")
        rows = board.live_ticket_rows(self.snap())
        self.assertEqual([(r["name"], r["state"], r["text"], r["what"]) for r in rows], [
            ("ticket-4", "ready", "待派", "#4 ticket 4"),
            ("ticket-5", "deps", "等 #3(%s)、#9(不存在) 落地" % board.STATE_TEXT["merged"], "#5 ticket 5")])
        self.assertTrue(all(r["rec"] == {} and r["started"] is None for r in rows))

    def test_decision_rows_follow_the_cards(self):
        import board
        snap = self.snap()
        rows = board.live_decision_rows(snap, self.NOW)
        cards = board.waiting_cards(snap, self.NOW)
        self.assertEqual([r["text"] for r in rows], [c["want"] for c in cards])
        self.assertEqual([r["attrs"]["data-subject"] for r in rows], [str(c["subject"]) for c in cards])


    def test_dep_satisfied_and_controller_alive(self):
        import board
        self.add(1, state="landed")
        self.add(2, state="merged", group="g1")
        self.add(3, state="merged", group="g2")
        self.add(4, state="merged")
        self.add(5, state="running")
        self.pool.update(lambda tx: (tx.put_group({"id": "g1", "branch": "int/g1", "state": "open", "members": ["2"]}),
                                     tx.put_group({"id": "g2", "branch": "int/g2", "state": "pr", "members": ["3"]})),
                         "test: groups")
        snap = self.snap()
        self.assertEqual([board.dep_satisfied(snap, d) for d in ("1", "2", "3", "4", "5", "99")],
                         [True, True, False, False, False, False])
        cfg = self.cfg()
        lock = os.path.join(cfg.worktree_root, "controller.lock")
        self.assertFalse(board.controller_alive(cfg))
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        for content, alive in (("not json", False), ("[1]", False), (json.dumps(self.dead(osx.now())), False),
                               (json.dumps(osx.stamp()), True)):
            with open(lock, "w") as fh:
                fh.write(content)
            self.assertEqual(board.controller_alive(cfg), alive, content)


class LiveIdleReasonUnits(BoardLiveTab):
    """#68's helper: live_idle_reason picks the first reason that holds."""

    def lock(self, content):
        path = os.path.join(self.cfg().worktree_root, "controller.lock")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)

    def reason(self):
        import board
        return board.live_idle_reason(self.cfg(), self.snap(), self.NOW)

    def test_empty_pool_is_none(self):
        self.assertEqual(self.reason(), ("none", "沒有在跑的,也沒有待做的事。"))

    def test_ready_ticket_needs_a_live_controller(self):
        self.add(1)
        self.assertEqual(self.reason()[0], "controller")
        self.lock("not json")
        self.assertEqual(self.reason()[0], "controller")
        self.lock(json.dumps(self.dead("2026-11-06T10:00:00Z")))
        self.assertEqual(self.reason()[0], "controller")
        self.lock(json.dumps(osx.stamp()))
        self.assertEqual(self.reason()[0], "dispatch")

    def test_deps_names_first_ticket_and_dependency(self):
        self.lock(json.dumps(osx.stamp()))
        self.add(1, state="merged", group="g1")
        self.add(2, deps=[1])
        self.pool.update(lambda tx: tx.put_group({"id": "g1", "branch": "int/g1", "state": "pr", "members": ["1"]}),
                         "test: group")
        self.assertEqual(self.reason(), ("deps", "沒有可派的票;下一張 #2 在等 #1 落地。"))


# a model key left out of an agent record (T83Acceptance)
T83_MISSING = object()


class T83Acceptance(BoardLiveTab):
    """#83: a DAG node of a ticket with a live agent, issuer or revising opener shows one line per run.
    Expected strings come from the ticket and the fixture below, never from the code."""

    NS = "{http://www.w3.org/2000/svg}"
    S = BoardLiveTab.NOW - 7200
    UNTIL = "2026-11-06T13:00:00Z"

    @staticmethod
    def pool_stamp(epoch):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    def fixture(self, state="running", second=False):
        self.add(5, state=state, group="g3")
        members = ["5"]
        if second:
            self.add(6, state="ready", group="g3")
            members.append("6")
        self.pool.update(lambda tx: tx.put_group({"id": "g3", "state": "open", "branch": "int/g3",
                                                  "members": members}), "test: g3")

    def agent(self, name, role="verifier", started=None, ticket="5", round=2, model="m-v", state="running",
              parent=None, since=None, until=None, **extra):
        started = self.S if started is None else started
        stamp = self.alive(self.pool_stamp(started))
        rec = dict(kind="agent", stamp=stamp, parent=parent, role=role, ticket=ticket, group="g3", round=round,
                   run_dir="/r/" + name, log=None, command="claude", effort=None, state=state,
                   since=self.pool_stamp(started if since is None else since), until=until)
        if model is not T83_MISSING:
            rec["model"] = model
        rec.update(extra)
        self.put(name, **rec)
        return stamp

    def page(self, now=None):
        import board
        return board.render(self.cfg(), self.snap(), query={"tab": "dag"}, now=self.S + 860 if now is None else now)

    def svg(self, page):
        import xml.etree.ElementTree as ET
        m = re.search(r'<svg id="dag".*?</svg>', page, re.S)
        self.assertIsNotNone(m, "no <svg id=\"dag\"> on the DAG tab")
        try:
            return ET.fromstring(m.group(0))
        except ET.ParseError as exc:
            self.fail("the DAG svg does not parse: %s" % exc)

    def nodes(self, root):
        return {el.get("data-id"): el for el in root.iter(self.NS + "g")
                if "node" in (el.get("class") or "").split()}

    def node(self, page, ident="5"):
        nodes = self.nodes(self.svg(page))
        self.assertIn(ident, nodes, "no node <g> for #%s" % ident)
        return nodes[ident]

    def live(self, node):
        """The node's direct <text class="live"> children, in document order."""
        return [el for el in node if el.tag == self.NS + "text" and "live" in (el.get("class") or "").split()]

    def lines(self, node):
        return ["".join(el.itertext()) for el in self.live(node)]

    # ---- item 1
    def test_agent_line(self):
        self.fixture()
        self.agent("agent-v")
        lines = self.live(self.node(self.page()))
        self.assertEqual(len(lines), 1, "one live line for one live agent")
        self.assertEqual(lines[0].get("data-role"), "verifier")
        self.assertEqual("".join(lines[0].itertext()), "驗證者寫案例 第 2/3 輪 · m-v · 14 分")
        self.assertIsNone(lines[0].get("data-wait"))

    # ---- item 2
    def test_role_texts(self):
        for role, text in (("worker", "worker"), ("worker_simple", "worker"), ("verifier", "驗證者寫案例"),
                           ("reviewer", "覆核"), ("tester", "tester")):
            with self.subTest(role=role):
                self.fixture()
                self.agent("agent-x", role=role, model="m")
                lines = self.live(self.node(self.page()))
                self.assertEqual([(el.get("data-role"), "".join(el.itertext())) for el in lines],
                                 [(role, "%s 第 2/3 輪 · m · 14 分" % text)])

    def test_two_agents_oldest_first(self):
        self.fixture()
        self.agent("agent-z-worker", role="worker", started=self.S, model="m-w")
        self.agent("agent-a-verifier", role="verifier", started=self.S + 60, model="m-v")
        lines = self.live(self.node(self.page()))
        self.assertEqual([(el.get("data-role"), "".join(el.itertext())) for el in lines],
                         [("worker", "worker 第 2/3 輪 · m-w · 14 分"),
                          ("verifier", "驗證者寫案例 第 2/3 輪 · m-v · 13 分")])

    def test_model_empty_or_missing_and_round_none(self):
        for model, rnd, text in (("", 2, "驗證者寫案例 第 2/3 輪 · 14 分"),
                                 (T83_MISSING, 2, "驗證者寫案例 第 2/3 輪 · 14 分"),
                                 (None, 2, "驗證者寫案例 第 2/3 輪 · 14 分"),
                                 ("m-v", None, "驗證者寫案例 · m-v · 14 分"),
                                 ("", None, "驗證者寫案例 · 14 分")):
            with self.subTest(model=model, round=rnd):
                self.fixture()
                self.agent("agent-v", model=model, round=rnd)
                self.assertEqual(self.lines(self.node(self.page())), [text])

    # ---- item 3
    def test_revising_opener(self):
        for state in ("running", "stuck"):
            with self.subTest(state=state):
                self.fixture(state=state)
                self.put("opener-r", kind="opener", stamp=self.alive(self.pool_stamp(self.S)), title=None,
                         revise="5", via="main", model="m-o", effort=None)
                lines = self.live(self.node(self.page()))
                self.assertEqual([(el.get("data-role"), "".join(el.itertext())) for el in lines],
                                 [("opener", "改題中 · m-o · 14 分")])

    def test_revising_opener_uses_its_agent_model(self):
        for state in ("running", "stuck"):
            with self.subTest(state=state):
                self.fixture(state=state)
                op = self.alive(self.pool_stamp(self.S))
                self.put("opener-r", kind="opener", stamp=op, title=None, revise="5", via="main", model="m-o",
                         effort=None)
                self.agent("agent-c", role="creator", started=self.S + 10, ticket=None, round=None, model="m-c",
                           parent=op["pid"])
                lines = self.live(self.node(self.page()))
                self.assertEqual([(el.get("data-role"), "".join(el.itertext())) for el in lines],
                                 [("opener", "改題中 · m-c · 14 分")])

    # ---- item 4
    def test_issuer_without_agent(self):
        self.fixture()
        self.put("issuer-5", kind="issuer", stamp=self.alive(self.pool_stamp(self.S)), ticket="5", target="int/g3",
                 round=2)
        lines = self.live(self.node(self.page()))
        self.assertEqual([(el.get("data-role"), "".join(el.itertext())) for el in lines],
                         [("issuer", "issuer 第 2/3 輪 · 沒有 agent 在跑 · 14 分")])

    def test_issuer_with_agent_shows_only_the_agent(self):
        self.fixture()
        iss = self.alive(self.pool_stamp(self.S))
        self.put("issuer-5", kind="issuer", stamp=iss, ticket="5", target="int/g3", round=2)
        self.agent("agent-w", role="worker", started=self.S + 60, model="m-w", parent=iss["pid"])
        lines = self.live(self.node(self.page()))
        self.assertEqual([(el.get("data-role"), "".join(el.itertext())) for el in lines],
                         [("worker", "worker 第 2/3 輪 · m-w · 13 分")])

    # ---- item 5
    def test_slot_waits_numbered_oldest_since_first(self):
        self.fixture()
        self.agent("agent-w", role="worker", started=self.S, model="m-w", state="slot", since=self.S + 200)
        self.agent("agent-v", role="verifier", started=self.S + 60, model="m-v", state="slot", since=self.S + 100)
        lines = self.live(self.node(self.page()))
        self.assertEqual([("".join(el.itertext()), el.get("data-wait"), el.get("data-queue")) for el in lines], [
            ("worker 第 2/3 輪 · m-w · 14 分 · 等機器名額:第 2 個(共 2 個在等)", "slot", "2/2"),
            ("驗證者寫案例 第 2/3 輪 · m-v · 13 分 · 等機器名額:第 1 個(共 2 個在等)", "slot", "1/2")])

    def test_slot_wait_gives_way_to_exclusive_regression(self):
        self.fixture()
        self.put("regression-int-g7", kind="regression", stamp=self.alive(self.pool_stamp(self.S - 600)),
                 group="g7", branch="int/g7", sha="a" * 40, exclusive=True, stage="full",
                 since=self.pool_stamp(self.S - 600))
        self.agent("agent-v", state="slot", since=self.S + 100)
        lines = self.live(self.node(self.page()))
        self.assertEqual([("".join(el.itertext()), el.get("data-wait"), el.get("data-queue")) for el in lines], [
            ("驗證者寫案例 第 2/3 輪 · m-v · 14 分 · 等機器名額:第 1 個(共 1 個在等),讓位給 g7 的獨占 regression",
             "slot", "1/1")])

    def test_quota_wait(self):
        import board
        self.fixture()
        self.agent("agent-v", state="quota", since=self.S + 100, until=self.UNTIL)
        lines = self.live(self.node(self.page()))
        self.assertEqual([("".join(el.itertext()), el.get("data-wait")) for el in lines], [
            ("驗證者寫案例 第 2/3 輪 · m-v · 14 分 · 額度用完,到 "
             + board.live_clock(board.pool_seconds(self.UNTIL)), "quota")])

    # ---- item 6
    def test_ended_other_tickets_and_ticketless_agents_add_no_line(self):
        self.fixture()
        self.agent("agent-v")
        self.put("agent-ended", kind="agent", stamp=self.dead(osx.now()), parent=None, role="worker", ticket="5",
                 group="g3", round=1, run_dir="/r/e", log=None, command="claude", model="m-e", effort=None,
                 state="running", since=osx.now(), until=None)
        self.put("issuer-ended", kind="issuer", stamp=self.dead(osx.now()), ticket="5", target="int/g3", round=1)
        self.put("opener-ended", kind="opener", stamp=self.dead(osx.now()), title=None, revise="5", via="main",
                 model="m-e", effort=None)
        self.agent("agent-other", role="worker", ticket="9", model="m-9")
        self.put("issuer-9", kind="issuer", stamp=self.alive(self.pool_stamp(self.S)), ticket="9",
                 target="int/g3", round=1)
        self.put("opener-9", kind="opener", stamp=self.alive(self.pool_stamp(self.S)), title=None, revise="9",
                 via="main", model="m-9", effort=None)
        fx = self.alive(self.pool_stamp(self.S))
        self.put("fixer-g3", kind="fixer", stamp=fx, group="g3", round=1, max_rounds=3)
        self.agent("agent-fix", role="fixer", ticket=None, round=1, model="m-f", parent=fx["pid"])
        self.agent("agent-loose", role="fixer", ticket=None, round=1, model="m-l")
        import board
        states = {r["name"]: r["state"] for r in board.live_rows(self.cfg(), self.snap())}
        for name in ("agent-ended", "issuer-ended", "opener-ended"):
            self.assertEqual(states.get(name), "ended", name)
        self.assertEqual(self.lines(self.node(self.page())), ["驗證者寫案例 第 2/3 輪 · m-v · 14 分"])

    # ---- item 7
    def test_geometry(self):
        self.fixture(second=True)
        self.agent("agent-1", role="worker", started=self.S, model="m-w")
        self.agent("agent-2", role="verifier", started=self.S + 60, model="m-v")
        self.agent("agent-3", role="reviewer", started=self.S + 120, model="m-r")
        root = self.svg(self.page())
        nodes = self.nodes(root)
        self.assertEqual(sorted(nodes), ["5", "6"])
        self.assertEqual(len(self.live(nodes["5"])), 3, "one live line per live agent")
        self.assertEqual(nodes["5"].get("data-layer"), nodes["6"].get("data-layer"))
        rects = {}
        for ident, node in nodes.items():
            rs = [el for el in node if el.tag == self.NS + "rect"]
            self.assertEqual(len(rs), 1, "one <rect> in node #%s" % ident)
            r = rs[0]
            x, y, w, h = (float(r.get(k)) for k in ("x", "y", "width", "height"))
            rects[ident] = (x, y, w, h)
            for t in node.iter(self.NS + "text"):
                ty = float(t.get("y"))
                self.assertTrue(y <= ty <= y + h, "#%s text %r at y=%s outside rect %s..%s"
                                % (ident, "".join(t.itertext()), ty, y, y + h))
        (x5, y5, w5, h5), (x6, y6, w6, h6) = rects["5"], rects["6"]
        overlap = x5 < x6 + w6 and x6 < x5 + w5 and y5 < y6 + h6 and y6 < y5 + h5
        self.assertFalse(overlap, "node rects overlap: %s %s" % (rects["5"], rects["6"]))
        self.assertEqual(x5, x6, "#5 and #6 in one column")
        self.assertGreaterEqual(y6, y5 + h5, "#6 sits below #5")
        width, height = float(root.get("width")), float(root.get("height"))
        for ident, (x, y, w, h) in rects.items():
            self.assertTrue(x + w <= width and y + h <= height, "svg %sx%s does not contain #%s rect %s"
                            % (width, height, ident, rects[ident]))

    # ---- item 1 on a served board
    def test_served_board_agent_line(self):
        import http.client
        import socket
        self.fixture()
        started = int(time.time()) - 860
        self.agent("agent-v", started=started)
        key = os.path.join(self.tmp, "key")
        with open(key, "w") as fh:
            fh.write("k1\n")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "board.py"), "serve", "--port", str(port),
                                 "--key-file", key], cwd=self.proj, env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)

        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request("GET", path, headers={"X-Board-Key": "k1"})
                resp = conn.getresponse()
                return resp.status, resp.read().decode("utf-8")
            finally:
                conn.close()
        for _ in range(100):
            try:
                get("/")
                break
            except OSError:
                time.sleep(0.1)
        status, page = get("/?tab=dag")
        self.assertLess(time.time() - started, 899, "the GET came too late for the 14 分 oracle")
        self.assertEqual(status, 200)
        lines = self.live(self.node(page))
        self.assertEqual([(el.get("data-role"), "".join(el.itertext())) for el in lines],
                         [("verifier", "驗證者寫案例 第 2/3 輪 · m-v · 14 分")])


# T83Acceptance borrows BoardLiveTab's helpers only; its inherited cases are not #83's.
for _name in [n for n in dir(BoardLiveTab) if n.startswith("test_")]:
    if _name not in T83Acceptance.__dict__:
        setattr(T83Acceptance, _name, None)
del _name


class T84Acceptance(BoardLiveTab):
    """#84: every ready DAG node says what it waits for; the dependency it waits on is highlighted up to
    the moving or stuck source. Expected strings come from the ticket and fixture F, never from the code."""

    NS = "{http://www.w3.org/2000/svg}"
    WAIT_KINDS = ("deps", "controller", "parallel", "next")

    # BoardLiveTab's own cases are not #84's acceptance: inherit only its helpers, not its tests.
    test_live_clock_and_elapsed = None
    test_tail_keeps_the_last_twenty_lines_and_step_reads_the_last_mark = None
    test_opener_takes_its_newest_agent_and_the_others_have_no_row = None
    test_issuer_without_agent_regression_fixer_release_and_order = None
    test_render_escapes_marks_slots_and_creates_nothing = None

    # ------------------------------------------------------------ fixture F
    def lock(self, content):
        path = os.path.join(self.cfg().worktree_root, "controller.lock")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)

    def fixture_f(self, lock=True):
        if lock:
            self.lock(json.dumps(osx.stamp()))
        self.pool.update(lambda tx: (
            tx.put_group({"id": "g21", "state": "pr", "branch": "int/g21", "members": ["1", "7"]}),
            tx.put_group({"id": "g22", "state": "open", "branch": "int/g22", "members": ["3", "4", "8"]})),
            "test: groups")
        self.put("regression-int-g21", kind="regression", stamp=self.alive("2026-11-06T11:00:00Z"), group="g21",
                 branch="int/g21", sha="a" * 40, exclusive=False, stage="full", since="2026-11-06T11:00:00Z")
        self.add(1, state="merged", group="g21")
        self.add(2, deps=[1])
        self.add(3, state="running", group="g22")
        self.add(4, group="g22")
        self.add(5, deps=[3])
        self.add(6, deps=[5])
        self.add(7)
        self.add(8, state="stuck", group="g22")
        self.add(9, deps=[8])
        self.add(10, state="cancelled")
        self.add(11, deps=[10])
        self.add(12, deps=["99"])

    # ------------------------------------------------------------ svg readers
    def root(self):
        import board
        import xml.etree.ElementTree as ET
        page = board.render(self.cfg(), self.snap(), query={"tab": "dag"}, now=self.NOW)
        m = re.search(r'<svg id="dag".*?</svg>', page, re.S)
        self.assertIsNotNone(m, "no <svg id=\"dag\"> on the DAG tab")
        try:
            return ET.fromstring(m.group(0))
        except ET.ParseError as exc:
            self.fail("the DAG svg does not parse: %s" % exc)

    @staticmethod
    def cls(el):
        return (el.get("class") or "").split()

    def nodes(self, root):
        return {el.get("data-id"): el for el in root.iter(self.NS + "g") if "node" in self.cls(el)}

    def waits(self, node):
        return [el for el in node.iter(self.NS + "text") if "wait" in self.cls(el)]

    def wait_text(self, node):
        lines = self.waits(node)
        self.assertEqual(len(lines), 1, "#%s: one <text class=\"wait\"> line, got %d"
                         % (node.get("data-id"), len(lines)))
        return "".join(lines[0].itertext())

    def st(self, node):
        found = [el for el in node.iter(self.NS + "text") if "st" in self.cls(el)]
        self.assertEqual(len(found), 1, "#%s: one <text class=\"st\">" % node.get("data-id"))
        return "".join(found[0].itertext())

    def edges(self, root):
        return {(el.get("data-from"), el.get("data-to")): el for el in root.iter(self.NS + "path")
                if "edge" in self.cls(el)}

    def lanewaits(self, root):
        return [el for el in root.iter() if "lanewait" in self.cls(el)]

    def lane_of(self, root, ident):
        for lane in root.iter(self.NS + "g"):
            if "lane" in self.cls(lane):
                if any(n.get("data-id") == ident and "node" in self.cls(n) for n in lane.iter(self.NS + "g")):
                    return lane.get("data-group")
        self.fail("#%s is in no lane" % ident)

    def expect_waits(self, root, expected):
        nodes = self.nodes(root)
        for ident, (kind, text, on) in expected.items():
            with self.subTest(ticket=ident):
                self.assertIn(ident, nodes, "no node for #%s" % ident)
                n = nodes[ident]
                self.assertEqual(n.get("data-state"), "ready")
                self.assertEqual(self.wait_text(n), text)
                self.assertEqual(n.get("data-wait"), kind)
                if on is not None:
                    self.assertEqual(n.get("data-wait-on"), on)

    def invariant(self, root, exempt=()):
        """Item 8 on one render. exempt: the (d, R) pairs of drawn dependencies whose walk reaches no moving or
        stuck source (only missing tickets); the caller names them from its fixture and checks them itself."""
        nodes = self.nodes(root)
        ready = [i for i, n in nodes.items() if n.get("data-state") == "ready"]
        self.assertTrue(ready, "the render has ready nodes")
        edges = self.edges(root)
        lanes = self.lanewaits(root)
        for ident in ready:
            n = nodes[ident]
            lines = self.waits(n)
            self.assertTrue(lines and "".join(lines[0].itertext()).strip(), "#%s: empty or no wait line" % ident)
            self.assertIn(n.get("data-wait"), self.WAIT_KINDS, "#%s: data-wait" % ident)
            if n.get("data-wait") != "deps":
                continue
            on = (n.get("data-wait-on") or "").split()
            drawn = [d for d in on if (d, ident) in edges and (d, ident) not in exempt]
            lane = [el for el in lanes if el.get("data-to") == ident]
            if not drawn and not lane:
                continue
            marked = [el for el in root.iter() if el.get("data-to") == ident
                      and set(self.cls(el)) & {"wait", "chain", "lanewait"}]
            self.assertTrue(marked, "#%s waits on drawn %s / lane %s but nothing to it is highlighted"
                            % (ident, drawn, lane))

    F_WAITS = {
        "2": ("deps", "等 g21 落地 — regression 跑中", "1"),
        "5": ("deps", "等 #3(做中)", "3"),
        "6": ("deps", "等 #5(待派)", "5"),
        "9": ("deps", "等你判 #8", "8"),
        "11": ("deps", "#10 已取消,不會落地", "10"),
        "12": ("deps", "#99 不存在", "99"),
    }

    # ---- items 1, 2, 4 (F)
    def test_f_every_ready_node_has_its_wait(self):
        self.fixture_f()
        root = self.root()
        expected = dict(self.F_WAITS)
        expected.update({"4": ("next", "下一輪派出", None), "7": ("next", "下一輪派出", None)})
        self.expect_waits(root, expected)
        self.invariant(root)

    # ---- item 4 (controller variants)
    def test_f_without_controller_lock(self):
        self.fixture_f(lock=False)
        root = self.root()
        expected = dict(self.F_WAITS)
        expected.update({"4": ("controller", "controller 沒在跑", None),
                         "7": ("controller", "controller 沒在跑", None)})
        self.expect_waits(root, expected)
        self.invariant(root)

    def test_bad_or_dead_controller_lock_is_not_alive(self):
        self.fixture_f(lock=False)
        for label, content in (("not json", "not json"), ("not an object", "[1, 2]"),
                               ("dead stamp", json.dumps(self.dead("2026-11-06T10:00:00Z")))):
            with self.subTest(lock=label):
                self.lock(content)
                self.expect_waits(self.root(), {"4": ("controller", "controller 沒在跑", None),
                                                "7": ("controller", "controller 沒在跑", None)})
        self.lock(json.dumps(osx.stamp()))
        self.expect_waits(self.root(), {"4": ("next", "下一輪派出", None)})

    def test_f_with_max_parallel_one(self):
        self.config(max_parallel=1)
        self.fixture_f()
        root = self.root()
        expected = dict(self.F_WAITS)
        expected.update({"4": ("parallel", "等空位(同時最多 1 張在做)", None),
                         "7": ("parallel", "等空位(同時最多 1 張在做)", None)})
        self.expect_waits(root, expected)
        self.invariant(root)

    # ---- item 2: join, collapse, numeric order of data-wait-on
    def test_parts_join_collapse_and_numeric_order(self):
        self.fixture_f()
        self.add(13, state="merged", group="g21")
        self.add(14, deps=[1, 13])
        self.add(15, deps=[3, 5])
        self.add(16, deps=[10, 9])
        root = self.root()
        self.expect_waits(root, {"14": ("deps", "等 g21 落地 — regression 跑中", "1 13"),
                                 "15": ("deps", "等 #3(做中)、等 #5(待派)", "3 5")})
        n = self.nodes(root)["16"]
        self.assertEqual(n.get("data-wait"), "deps")
        self.assertEqual(n.get("data-wait-on"), "9 10")
        self.assertEqual(sorted(self.wait_text(n).split("、")), sorted(["等 #9(待派)", "#10 已取消,不會落地"]))
        self.invariant(root)

    # ---- item 3: <G now>
    def test_group_now_texts(self):
        self.lock(json.dumps(osx.stamp()))
        groups = (("g31", "pr"), ("g32", "pr"), ("g33", "fixing"), ("g34", "verified"), ("g35", "stuck"),
                  ("g36", "verified"), ("g37", "pr"))
        self.pool.update(lambda tx: [tx.put_group({"id": g, "state": s, "branch": "int/" + g, "members": []})
                                     for g, s in groups], "test: groups")
        started = "2026-11-06T11:00:00Z"
        for g, stage in (("g31", "machine"), ("g32", "slot")):
            self.put("regression-int-" + g, kind="regression", stamp=self.alive(started), group=g,
                     branch="int/" + g, sha="b" * 40, exclusive=False, stage=stage, since=started)
        self.put("fixer-g33", kind="fixer", stamp=self.alive(started), group="g33", round=2, max_rounds=3)
        self.put("release-g34", kind="release", stamp=self.alive(started), group="g34", sha="c" * 40,
                 log=self.log("rel.log", ["devloop-stage: tagging"]))
        self.put("regression-int-g37", kind="regression", stamp=self.dead(osx.now()), group="g37",
                 branch="int/g37", sha="d" * 40, exclusive=False, stage="full", since=started)
        for k, (g, _) in enumerate(groups):
            self.add(31 + k, state="merged", group=g)
            self.add(41 + k, deps=[31 + k])
        self.add(38, state="merged", group="g31")
        self.add(48, deps=[31, 38])
        root = self.root()
        self.expect_waits(root, {
            "41": ("deps", "等 g31 落地 — regression 等機器名額", "31"),
            "42": ("deps", "等 g32 落地 — regression 等機器名額", "32"),
            "43": ("deps", "等 g33 落地 — 修補第 2/3 輪", "33"),
            "44": ("deps", "等 g34 落地 — 發版中", "34"),
            "45": ("deps", "等 g35 落地 — 卡住", "35"),
            "46": ("deps", "等 g36 落地 — 已驗,待落地", "36"),
            "47": ("deps", "等 g37 落地 — PR(待驗)", "37"),
            "48": ("deps", "等 g31 落地 — regression 等機器名額", "31 38"),
        })
        # item 7 on the same render: one lane line per (G, R), chain only for the stuck group
        got = sorted((el.get("data-from"), el.get("data-to"), "chain" in self.cls(el), "edge" in self.cls(el))
                     for el in self.lanewaits(root))
        want = sorted([("lane:g3%d" % (k + 1), str(41 + k), g == "g35", False) for k, (g, _) in enumerate(groups)]
                      + [("lane:g31", "48", False, False)])
        self.assertEqual(got, want)
        edges = self.edges(root)
        for k in range(7):
            self.assertNotIn("wait", self.cls(edges[(str(31 + k), str(41 + k))]), "merged-in-not-open edge")
        self.invariant(root)

    # ---- item 5
    def test_group_member_and_ungrouped_state_texts(self):
        self.fixture_f()
        root = self.root()
        nodes = self.nodes(root)
        self.assertEqual(self.lane_of(root, "4"), "g22")
        self.assertEqual(self.st(nodes["4"]), "組內待做")
        self.assertEqual(nodes["4"].get("data-member"), "g22")
        self.assertEqual(self.lane_of(root, "7"), "")
        self.assertIsNone(nodes["7"].get("data-member"))
        self.assertEqual(self.st(nodes["7"]), "待派")
        for ident in ("2", "5", "6", "7", "9", "11", "12"):
            with self.subTest(ticket=ident):
                self.assertEqual(self.st(nodes[ident]), "待派")
                self.assertTrue(self.wait_text(nodes[ident]).strip())

    # ---- item 6
    def test_f_edge_highlights(self):
        self.fixture_f()
        edges = self.edges(self.root())
        for pair in (("3", "5"), ("5", "6")):
            self.assertIn(pair, edges)
            self.assertIn("wait", self.cls(edges[pair]), pair)
        for pair in (("8", "9"), ("10", "11")):
            self.assertIn(pair, edges)
            self.assertIn("chain", self.cls(edges[pair]), pair)
            self.assertNotIn("wait", self.cls(edges[pair]), pair)
        self.assertIn(("1", "2"), edges)
        self.assertFalse({"wait", "chain"} & set(self.cls(edges[("1", "2")])))

    def test_edge_sources(self):
        self.lock(json.dumps(osx.stamp()))
        self.pool.update(lambda tx: (
            tx.put_group({"id": "g21", "state": "pr", "branch": "int/g21", "members": ["1"]}),
            tx.put_group({"id": "g5", "state": "stuck", "branch": "int/g5", "members": ["5"]})), "test: groups")
        self.add(20)
        self.add(21, deps=[20])
        self.add(1, state="merged", group="g21")
        self.add(2, deps=[1])
        self.add(22, deps=[2])
        self.add(12, deps=["99"])
        self.add(23, deps=[12])
        self.add(5, state="merged", group="g5")
        self.add(6, deps=[5])
        self.add(24, deps=[6])
        root = self.root()
        edges = self.edges(root)
        self.assertIn("wait", self.cls(edges[("20", "21")]), "a ready ticket with nothing unsatisfied moves")
        self.assertIn("wait", self.cls(edges[("2", "22")]), "merged in a pr group moves through its lane line")
        self.assertNotIn("wait", self.cls(edges[("1", "2")]))
        self.assertFalse({"wait", "chain"} & set(self.cls(edges[("12", "23")])), "a missing source is neither")
        for pair in (("5", "6"), ("6", "24")):
            self.assertIn("chain", self.cls(edges[pair]), pair)
            self.assertNotIn("wait", self.cls(edges[pair]), pair)
        self.expect_waits(root, {"21": ("deps", "等 #20(待派)", "20"), "22": ("deps", "等 #2(待派)", "2")})
        got = sorted((el.get("data-from"), el.get("data-to"), "chain" in self.cls(el)) for el in self.lanewaits(root))
        self.assertEqual(got, [("lane:g21", "2", False), ("lane:g5", "6", True)])
        # item 8's exemption: 12->23 reaches only missing #99, so it stays unhighlighted and the wait lines say it all
        self.expect_waits(root, {"23": ("deps", "等 #12(待派)", "12"), "12": ("deps", "#99 不存在", "99")})
        self.assertFalse([el for el in self.lanewaits(root) if el.get("data-to") == "23"], "#23 has no lane line")
        self.invariant(root, exempt={("12", "23")})

    # ---- item 7 (F)
    def test_f_one_lane_line(self):
        self.fixture_f()
        lines = self.lanewaits(self.root())
        self.assertEqual(len(lines), 1, "exactly one lane line in F")
        el = lines[0]
        self.assertEqual(el.tag, self.NS + "path")
        self.assertEqual((el.get("data-from"), el.get("data-to")), ("lane:g21", "2"))
        self.assertNotIn("edge", self.cls(el))
        self.assertNotIn("chain", self.cls(el))

    # ---- item 9
    def test_legend_wait_entry_and_colour(self):
        import board
        self.fixture_f()
        page = board.render(self.cfg(), self.snap(), query={"tab": "dag"}, now=self.NOW)
        m = re.search(r'<div class="legend">(.*?)</div>', page, re.S)
        self.assertIsNotNone(m, "no legend")
        legend = m.group(1)
        for text in list(board.STATE_TEXT.values()) + ["被卡住", "卡住的源頭"]:
            self.assertIn(">%s</span>" % text, legend, "legend lost %s" % text)
        entry = [s for s in re.findall(r"<span[^>]*>.*?</span>", legend, re.S) if "等待路徑" in s]
        self.assertEqual(len(entry), 1, "one legend entry 等待路徑")
        style = board.DAG_STYLE
        blocks = [b for b in re.findall(r"\{([^{}]*--chain:[^{}]*)\}", style)]
        self.assertEqual(len(blocks), 2, "light and dark variable blocks with --chain")
        light, dark = [dict(re.findall(r"(--[\w-]+):([^;}]+)", b)) for b in blocks]
        known = {"--chain"} | {k for k in light if k.startswith("--s-")}
        new = sorted(set(light) - known)
        self.assertTrue(new, "a new colour variable next to --chain")
        ok = []
        for var in new:
            if var not in dark:
                continue
            if any(light[var].strip() == light[k].strip() for k in known):
                continue
            if any(dark[var].strip() == dark[k].strip() for k in known if k in dark):
                continue
            span = entry[0]
            classes = re.findall(r'class="([^"]*)"', span)
            inline = "var(%s)" % var in span
            styled = False
            for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style):
                if "var(%s)" % var not in body or ".legend" not in sel:
                    continue
                if any(c and ("." + c) in sel for cs in classes for c in cs.split()):
                    styled = True
            if inline or styled:
                ok.append(var)
        self.assertTrue(ok, "the 等待路徑 entry is drawn in a new variable defined light and dark, distinct from "
                            "--chain and the state colours (new: %s)" % new)


class T42Acceptance(LoopCase):
    """#42: the DAG tab draws the ticket DAG and highlights the chains stuck behind a blocker.
    Expected values come from the ticket and the fixture below, never from the code."""

    NS = "{http://www.w3.org/2000/svg}"

    def fixture(self):
        self.add(1, state="stuck", group="g1")
        self.add(2, deps=[1])
        self.add(3, deps=[2])
        self.add(4, state="cancelled")
        self.add(5, deps=[4])
        self.add(6, state="landed", group="g1")
        self.add(7, deps=[6])
        self.add(8, state="merged", group="g2")
        self.add(9, deps=[8])
        self.add(10, deps=[6], state="running", group="g3")

        def groups(tx):
            tx.put_group({"id": "g1", "state": "landed", "members": ["1", "6"], "branch": "int/g1"})
            tx.put_group({"id": "g2", "state": "stuck", "members": ["8"], "branch": "int/g2"})
            tx.put_group({"id": "g3", "state": "open", "members": ["10"], "branch": "int/g3"})
        self.pool.update(groups, "test: groups")

    def page(self, done=False):
        import board
        from core.config import Config
        query = {"tab": "dag"}
        if done:
            query["done"] = "1"
        return board.render(Config(self.proj), self.snap(), query=query)

    def tab_body(self, page):
        import board
        start = page.index('<div id="tab-dag" class="tab">')
        end = page.index(board.PAGE_SCRIPT, start)
        return page[start:end]

    def svg(self, page):
        import re
        import xml.etree.ElementTree as ET
        prefix = '<svg id="dag" xmlns="http://www.w3.org/2000/svg"'
        self.assertEqual(page.count(prefix), 1, "one <svg id=\"dag\" xmlns=...> expected")
        m = re.search(r'<svg id="dag".*?</svg>', page, re.S)
        self.assertIsNotNone(m)
        try:
            return ET.fromstring(m.group(0))
        except ET.ParseError as exc:
            self.fail("the DAG svg does not parse: %s" % exc)

    @staticmethod
    def classes(el):
        return (el.get("class") or "").split()

    def nodes(self, root):
        return {el.get("data-id"): el for el in root.iter(self.NS + "g") if "node" in self.classes(el)}

    def edges(self, root):
        return [el for el in root.iter() if "edge" in self.classes(el)]

    def lanes(self, root):
        return [el for el in root.iter(self.NS + "g") if "lane" in self.classes(el)]

    # ---- item 1
    def test_tab_has_one_parsable_svg(self):
        self.fixture()
        root = self.svg(self.page())
        self.assertEqual(root.tag, self.NS + "svg")
        self.assertEqual(root.get("id"), "dag")

    # ---- item 2
    def test_shown_tickets_and_node_markup(self):
        self.fixture()
        import board
        snap = self.snap()
        for done, want in ((False, {"1", "2", "3", "4", "5", "7", "8", "9", "10"}),
                           (True, {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10"})):
            with self.subTest(done=done):
                root = self.svg(self.page(done))
                count = sum(1 for el in root.iter(self.NS + "g") if "node" in self.classes(el))
                nodes = self.nodes(root)
                self.assertEqual(count, len(want))
                self.assertEqual(set(nodes), want)
                parent = {c: p for p in root.iter() for c in p}
                for ident, node in nodes.items():
                    t = snap.tickets[ident]
                    self.assertIn("s-" + t["state"], self.classes(node), ident)
                    self.assertEqual(node.get("data-state"), t["state"], ident)
                    self.assertEqual(node.get("data-group"), t.get("group") or "", ident)
                    self.assertIsNotNone(node.get("data-layer"), ident)
                    titles = list(node.iter(self.NS + "title"))
                    self.assertTrue(titles, ident)
                    self.assertTrue(("".join(titles[0].itertext())).startswith("#%s ticket %s" % (ident, ident)),
                                    ident)
                    up = parent.get(node)
                    self.assertIsNotNone(up, ident)
                    self.assertEqual(up.tag, self.NS + "a", ident)
                    self.assertEqual(up.get("href"), "?ticket=%s" % ident, ident)

    def test_ticket_outside_dag_is_shown(self):
        def change(tx):
            tx.put_ticket({"id": "11", "title": "ticket 11", "objective": "o", "acceptance": ["a"],
                           "state": "ready", "round": 0, "feedback": [], "cases": None})
        self.pool.update(change, "test: #11 outside dag")
        root = self.svg(self.page())
        self.assertEqual(set(self.nodes(root)), {"11"})
        self.assertEqual(self.nodes(root)["11"].get("data-layer"), "0")

    # ---- item 3
    def test_layers(self):
        self.fixture()
        for done, want in ((False, {"1": 0, "2": 1, "3": 2, "4": 0, "5": 1, "7": 0, "8": 0, "9": 1, "10": 0}),
                           (True, {"1": 0, "2": 1, "3": 2, "4": 0, "5": 1, "6": 0, "7": 1, "8": 0, "9": 1,
                                   "10": 1})):
            with self.subTest(done=done):
                nodes = self.nodes(self.svg(self.page(done)))
                self.assertEqual({i: n.get("data-layer") for i, n in nodes.items()},
                                 {i: str(v) for i, v in want.items()})

    # ---- item 4 (and the chain part of item 6)
    def test_edges_and_chains(self):
        self.fixture()
        for done, want, chain in (
                (False, {("1", "2"), ("2", "3"), ("4", "5"), ("8", "9")},
                 {("1", "2"), ("2", "3"), ("4", "5"), ("8", "9")}),
                (True, {("1", "2"), ("2", "3"), ("4", "5"), ("8", "9"), ("6", "7"), ("6", "10")},
                 {("1", "2"), ("2", "3"), ("4", "5"), ("8", "9")})):
            with self.subTest(done=done):
                edges = self.edges(self.svg(self.page(done)))
                pairs = [(el.get("data-from"), el.get("data-to")) for el in edges]
                self.assertEqual(sorted(pairs), sorted(want))
                self.assertEqual({(el.get("data-from"), el.get("data-to")) for el in edges
                                  if "chain" in self.classes(el)}, chain)

    # ---- item 5
    def test_lanes(self):
        import board
        self.fixture()
        snap = self.snap()
        for done in (False, True):
            with self.subTest(done=done):
                root = self.svg(self.page(done))
                lanes = self.lanes(root)
                self.assertEqual([el.get("data-group") for el in lanes], ["g1", "g2", "g3", ""])
                for lane in lanes:
                    gid = lane.get("data-group")
                    texts = ["".join(t.itertext()) for t in lane.iter(self.NS + "text")]
                    if gid:
                        label = board.group_state_text(snap.groups[gid])
                        self.assertTrue(any(gid in s and label in s for s in texts), (gid, label, texts))
                    else:
                        self.assertTrue(any("未分組" in s for s in texts), texts)
                labels = {el.get("data-group"): " ".join("".join(t.itertext()) for t in el.iter(self.NS + "text"))
                          for el in lanes}
                self.assertIn("已落地", labels["g1"])
                self.assertIn("卡住", labels["g2"])
                self.assertIn("收票中", labels["g3"])
                for ident, node in self.nodes(root).items():
                    lane = [el for el in lanes if el.get("data-group") == node.get("data-group")]
                    self.assertEqual(len(lane), 1, ident)
                    self.assertIn(node, list(lane[0].iter()), ident)
                members = {el.get("data-group"): sorted(n.get("data-id") for n in el.iter(self.NS + "g")
                                                        if "node" in self.classes(n)) for el in lanes}
                want = {"g1": ["1", "6"] if done else ["1"], "g2": ["8"], "g3": ["10"],
                        "": sorted(["2", "3", "4", "5", "7", "9"])}
                self.assertEqual(members, want)

    def test_no_ungrouped_lane_when_all_grouped(self):
        self.add(1, group="g1", state="merged")
        self.pool.update(lambda tx: tx.put_group({"id": "g1", "state": "open", "members": ["1"],
                                                  "branch": "int/g1"}), "test: g1")
        lanes = self.lanes(self.svg(self.page()))
        self.assertEqual([el.get("data-group") for el in lanes], ["g1"])

    # ---- item 6
    def test_blocked_and_blockers(self):
        self.fixture()
        for done in (False, True):
            with self.subTest(done=done):
                nodes = self.nodes(self.svg(self.page(done)))
                blocked = {i: n.get("data-blocked-by") for i, n in nodes.items() if "blocked" in self.classes(n)}
                self.assertEqual(blocked, {"2": "1", "3": "1", "5": "4", "9": "8"})
                self.assertEqual({i for i, n in nodes.items() if "blocker" in self.classes(n)}, {"1", "4", "8"})

    def test_blocked_by_lists_every_blocker_sorted(self):
        self.add(10, state="stuck")
        self.add(9, state="cancelled")
        self.add(3, deps=[10, 9])
        nodes = self.nodes(self.svg(self.page()))
        self.assertEqual(nodes["3"].get("data-blocked-by"), "9 10")

    # ---- item 7
    def test_legend(self):
        import board
        self.add(1)
        body = self.tab_body(self.page())
        for label in sorted(set(board.STATE_TEXT.values())) + ["被卡住", "卡住的源頭"]:
            self.assertIn(label, body, label)

    # ---- item 8
    def test_done_toggle_link(self):
        import html
        import re
        self.fixture()
        for done, href, text in ((False, "?tab=dag&done=1", "顯示已落地與已取消"),
                                 (True, "?tab=dag", "收起已落地與已取消")):
            with self.subTest(done=done):
                body = self.tab_body(self.page(done))
                links = [(html.unescape(m.group(1)), re.sub(r"<[^>]*>", "", m.group(2)).strip())
                         for m in re.finditer(r'<a\b[^>]*?\bhref=["\']([^"\']*)["\'][^>]*>(.*?)</a>', body, re.S)]
                self.assertIn((href, text), links)

    # ---- item 9
    def test_nothing_to_show(self):
        body = self.tab_body(self.page())
        self.assertIn("沒有要顯示的票", body)
        self.assertNotIn("<svg", body)


class T82Acceptance(BoardLiveTab):
    """#82: each integration-branch lane title on the DAG tab is its group's live status line.
    Expected strings come from the ticket and the fixture below, never from the code."""

    NS = "{http://www.w3.org/2000/svg}"
    S = BoardLiveTab.NOW - 7200

    @staticmethod
    def pool_stamp(epoch):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    @staticmethod
    def hhmm(epoch):
        return time.strftime("%H:%M", time.localtime(epoch))

    def fixture(self, last_regression="3 failing:\n- a\n- b\n- c"):
        self.add(1, state="merged", group="g21")
        self.add(2, state="merged", group="g21")
        self.group(last_regression)

    def group(self, last_regression):
        g = {"id": "g21", "state": "pr", "branch": "int/g21", "members": ["1", "2"]}
        if last_regression is not None:
            g["last_regression"] = last_regression
        self.pool.update(lambda tx: tx.put_group(g), "test: g21")

    def regression(self, stage="full", started=None, group="g21", name=None):
        started = self.S if started is None else started
        self.put(name or "regression-int-" + group, kind="regression", stamp=self.alive(self.pool_stamp(started)),
                 group=group, branch="int/" + group, sha="a" * 40, exclusive=False, stage=stage,
                 since=self.pool_stamp(started))

    def page(self, now=None, done=False):
        import board
        query = {"tab": "dag"}
        if done:
            query["done"] = "1"
        return board.render(self.cfg(), self.snap(), query=query, now=self.S + 860 if now is None else now)

    def svg(self, page):
        import xml.etree.ElementTree as ET
        m = re.search(r'<svg id="dag".*?</svg>', page, re.S)
        self.assertIsNotNone(m, "no <svg id=\"dag\"> on the DAG tab")
        try:
            return ET.fromstring(m.group(0))
        except ET.ParseError as exc:
            self.fail("the DAG svg does not parse: %s" % exc)

    def lane(self, page, gid="g21"):
        root = self.svg(page)
        lanes = [el for el in root.iter(self.NS + "g") if "lane" in (el.get("class") or "").split()
                 and el.get("data-group") == gid]
        self.assertEqual(len(lanes), 1, "one lane for %s" % gid)
        return lanes[0]

    def lh(self, lane):
        heads = [el for el in lane.iter(self.NS + "text") if "lh" in (el.get("class") or "").split()]
        self.assertEqual(len(heads), 1, "one <text class=\"lh\"> per lane")
        return heads[0]

    def title(self, lane):
        return "".join(self.lh(lane).itertext())

    # ---- items 1 and 2
    def test_regression_running_title_and_attrs(self):
        self.fixture()
        self.regression("full")
        lane = self.lane(self.page())
        self.assertEqual(self.title(lane), "g21 · PR(待驗) · regression full · %s 起 · 14 分 · 上次 regression:紅 3 條 · 2/10 張"
                         % self.hhmm(self.S))
        self.assertEqual(lane.get("data-now"), "regression")
        self.assertEqual(lane.get("data-red"), "3")
        self.assertEqual(lane.get("data-members"), "2/10")

    def test_members_use_max_group_size(self):
        self.config(max_group_size=4)
        self.fixture()
        lane = self.lane(self.page())
        self.assertEqual(lane.get("data-members"), "2/4")
        self.assertTrue(self.title(lane).endswith(" · 2/4 張"), self.title(lane))

    def test_first_running_kind_in_live_rows_order_is_data_now(self):
        self.fixture()
        fx = self.alive(self.pool_stamp(self.S - 600))
        self.put("fixer-g21", kind="fixer", stamp=fx, group="g21", round=2, max_rounds=3)
        self.regression("full")
        import board
        kinds = [r["kind"] for r in board.live_rows(self.cfg(), self.snap())]
        self.assertEqual(kinds, ["fixer", "regression"])
        self.assertEqual(self.lane(self.page()).get("data-now"), "fixer")

    # ---- item 3
    def test_regression_stage_texts(self):
        self.fixture()
        for stage, text in (("preflight", "regression preflight"), ("machine", "regression 等整台機器"),
                            ("slot", "regression 等機器名額:第 1 個(共 1 個在等)")):
            with self.subTest(stage=stage):
                self.regression(stage)
                self.assertEqual(self.title(self.lane(self.page())),
                                 "g21 · PR(待驗) · %s · %s 起 · 14 分 · 上次 regression:紅 3 條 · 2/10 張"
                                 % (text, self.hhmm(self.S)))

    def test_elapsed_counts_from_the_stamp(self):
        self.fixture()
        self.regression("full")
        title = self.title(self.lane(self.page(now=self.S + 5400)))
        self.assertIn(" · regression full · %s 起 · 1 小時 30 分 · " % self.hhmm(self.S), title)

    # ---- item 4
    def test_fixer_without_agent(self):
        self.fixture()
        self.put("fixer-g21", kind="fixer", stamp=self.alive(self.pool_stamp(self.S)), group="g21", round=2,
                 max_rounds=3)
        lane = self.lane(self.page())
        self.assertEqual(self.title(lane), "g21 · PR(待驗) · 修補第 2/3 輪 · 沒有 agent 在跑 · %s 起 · 14 分 · "
                         "上次 regression:紅 3 條 · 2/10 張" % self.hhmm(self.S))
        self.assertEqual(lane.get("data-now"), "fixer")

    def test_fixer_with_agent_in_quota(self):
        import board
        self.fixture()
        fx = self.alive(self.pool_stamp(self.S))
        self.put("fixer-g21", kind="fixer", stamp=fx, group="g21", round=2, max_rounds=3)
        until = "2026-11-06T13:00:00Z"
        self.put("agent-f", kind="agent", stamp=self.alive(self.pool_stamp(self.S + 60)), parent=fx["pid"],
                 role="fixer", ticket=None, group="g21", round=2, run_dir="/r/f", log=None, command="claude",
                 model="m", effort=None, state="quota", since=self.pool_stamp(self.S + 60), until=until)
        lane = self.lane(self.page())
        self.assertEqual(self.title(lane), "g21 · PR(待驗) · 修補第 2/3 輪 · 額度用完,到 %s · %s 起 · 14 分 · "
                         "上次 regression:紅 3 條 · 2/10 張"
                         % (board.live_clock(board.pool_seconds(until)), self.hhmm(self.S)))
        self.assertEqual(lane.get("data-now"), "fixer")

    # ---- item 5
    def test_release_step_and_without_step(self):
        self.fixture()
        for lines, text in ((["start", "devloop-stage: build", "x", "devloop-stage: tagging", "y"], "發版 tagging"),
                            (["start", "plain"], "發版 執行中")):
            with self.subTest(text=text):
                log = self.log("release.log", lines)
                self.put("release-g21", kind="release", stamp=self.alive(self.pool_stamp(self.S)), group="g21",
                         sha="c" * 40, log=log)
                lane = self.lane(self.page())
                self.assertEqual(self.title(lane), "g21 · PR(待驗) · %s · %s 起 · 14 分 · 上次 regression:紅 3 條 · 2/10 張"
                                 % (text, self.hhmm(self.S)))
                self.assertEqual(lane.get("data-now"), "release")

    # ---- item 6
    def test_last_regression_parts_and_group_link(self):
        import board
        self.fixture(None)
        other = "flaky runner said something went wrong here\nsecond line"
        for summary, part, red in (
                ("green", "上次 regression:綠", None),
                ("3 failing:\n- a\n- b\n- c", "上次 regression:紅 3 條", "3"),
                ("12 failing:\n- x", "上次 regression:紅 12 條", "12"),
                ("environment not usable: no disk", "上次 regression:環境不能用", None),
                ("could not measure: timeout", "上次 regression:沒量到", None),
                (other, "上次 regression:" + board._clip(other.splitlines()[0], 30), None),
                (None, None, None),
                ("", None, None)):
            with self.subTest(summary=summary):
                self.group(summary)
                lane = self.lane(self.page())
                links = [a for a in self.lh(lane).iter(self.NS + "a")]
                all_links = [a for a in lane.iter(self.NS + "a") if (a.get("href") or "").startswith("?group=")]
                self.assertEqual(lane.get("data-red"), red)
                if part is None:
                    self.assertEqual(self.title(lane), "g21 · PR(待驗) · 2/10 張")
                    self.assertEqual(all_links, [])
                else:
                    self.assertEqual(self.title(lane), "g21 · PR(待驗) · %s · 2/10 張" % part)
                    self.assertEqual([(a.get("href"), "".join(a.itertext())) for a in links],
                                     [("?group=g21", part)])

    # ---- item 7
    def test_nothing_runs_no_now_parts(self):
        self.fixture()
        self.add(3, state="merged", group="g22")
        self.pool.update(lambda tx: tx.put_group({"id": "g22", "state": "open", "branch": "int/g22",
                                                  "members": ["3"]}), "test: g22")
        self.regression("full", group="g22")
        self.put("fixer-g21", kind="fixer", stamp=self.dead(osx.now()), group="g21", round=2, max_rounds=3)
        self.put("release-g21", kind="release", stamp=self.dead(osx.now()), group="g21", sha="c" * 40,
                 log=self.log("r.log", ["devloop-stage: tagging"]))
        import board
        states = {r["name"]: r["state"] for r in board.live_rows(self.cfg(), self.snap())}
        self.assertEqual(states.get("fixer-g21"), "ended")
        self.assertEqual(states.get("release-g21"), "ended")
        page = self.page()
        lane = self.lane(page)
        self.assertEqual(self.title(lane), "g21 · PR(待驗) · 上次 regression:紅 3 條 · 2/10 張")
        self.assertIsNone(lane.get("data-now"))
        other = self.lane(page, "g22")
        self.assertEqual(other.get("data-now"), "regression")
        self.assertIn(" · regression full · ", self.title(other))

    # ---- item 8
    def test_refresh_link_toggle_and_legend(self):
        import board
        self.fixture()
        for done, link, toggle in (
                (False, '<a class="refresh" href="?tab=dag">重新整理</a>', "顯示已落地與已取消"),
                (True, '<a class="refresh" href="?tab=dag&amp;done=1">重新整理</a>', "收起已落地與已取消")):
            with self.subTest(done=done):
                page = self.page(done=done)
                start = page.index('<div id="tab-dag" class="tab">')
                body = page[start:page.index(board.PAGE_SCRIPT, start)]
                self.assertIn(link, body)
                self.assertIn(toggle, body)
                for label in sorted(set(board.STATE_TEXT.values())):
                    self.assertIn(label, body, label)

    # ---- items 1 and 2 on a served board
    def test_served_board_lane_title(self):
        import http.client
        import socket
        self.fixture()
        started = int(time.time()) - 860
        self.regression("full", started=started)
        key = os.path.join(self.tmp, "key")
        with open(key, "w") as fh:
            fh.write("k1\n")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "board.py"), "serve", "--port", str(port),
                                 "--key-file", key], cwd=self.proj, env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)

        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request("GET", path, headers={"X-Board-Key": "k1"})
                resp = conn.getresponse()
                return resp.status, resp.read().decode("utf-8")
            finally:
                conn.close()
        for _ in range(100):
            try:
                get("/")
                break
            except OSError:
                time.sleep(0.1)
        status, page = get("/?tab=dag")
        self.assertLess(time.time() - started, 899, "the GET came too late for the 14 分 oracle")
        self.assertEqual(status, 200)
        lane = self.lane(page)
        self.assertEqual(self.title(lane), "g21 · PR(待驗) · regression full · %s 起 · 14 分 · 上次 regression:紅 3 條 · 2/10 張"
                         % self.hhmm(started))
        self.assertEqual(lane.get("data-now"), "regression")
        self.assertEqual(lane.get("data-red"), "3")
        self.assertEqual(lane.get("data-members"), "2/10")


# T82Acceptance borrows BoardLiveTab's helpers only; its inherited cases are not #82's.
for _name in [n for n in dir(BoardLiveTab) if n.startswith("test_")]:
    if _name not in T82Acceptance.__dict__:
        setattr(T82Acceptance, _name, None)
del _name


class DagTabUnit(LoopCase):
    """Helpers of the board's DAG tab (#42): blockers, blocked chains, shown set, layers, lanes, speed."""

    def fake(self, tickets, dag, groups=None):
        from types import SimpleNamespace
        return SimpleNamespace(tickets=dict((str(k), dict(v, id=str(k))) for k, v in tickets.items()),
                               dag=dict((str(k), {"depends_on": [str(d) for d in v]}) for k, v in dag.items()),
                               groups=groups or {})

    def test_blockers_and_blocked(self):
        import board
        snap = self.fake({1: {"state": "stuck"}, 2: {"state": "ready"}, 3: {"state": "landed"},
                          4: {"state": "ready"}, 5: {"state": "merged", "group": "g2"},
                          6: {"state": "merged", "group": "g3"}, 7: {"state": "ready"},
                          8: {"state": "cancelled"}, 9: {"state": "running"}, 10: {"state": "ready"}},
                         {2: [1], 4: [3], 7: [5, 6, 8, 2], 9: [1], 10: [11]},
                         {"g2": {"id": "g2", "state": "stuck"}, "g3": {"id": "g3", "state": "open"}})
        blockers = board.dag_blockers(snap)
        self.assertEqual(blockers, {"1", "5", "8"})
        blocked = board.dag_blocked(snap, blockers)
        # 4 waits on a landed ticket, 9 is running, 10 waits on a ticket missing from the pool
        self.assertEqual(blocked, {"2": ["1"], "7": ["1", "5", "8"]})

    def test_blocked_stops_at_landed_and_at_blockers(self):
        import board
        snap = self.fake({1: {"state": "stuck"}, 2: {"state": "landed"}, 3: {"state": "cancelled"},
                          4: {"state": "ready"}, 5: {"state": "ready"}},
                         {2: [1], 3: [1], 4: [2], 5: [3]})
        blocked = board.dag_blocked(snap, board.dag_blockers(snap))
        self.assertEqual(blocked, {"5": ["3"]})

    def test_blocked_by_sorts_numerically_and_survives_cycles(self):
        import board
        snap = self.fake({2: {"state": "stuck"}, 10: {"state": "stuck"}, 3: {"state": "ready"},
                          4: {"state": "ready"}}, {3: [10, 4], 4: [3, 2]})
        self.assertEqual(board.dag_blocked(snap, board.dag_blockers(snap))["3"], ["2", "10"])

    def test_shown_and_layers(self):
        import board
        snap = self.fake({1: {"state": "landed"}, 2: {"state": "ready"}, 3: {"state": "ready"},
                          4: {"state": "cancelled"}, 5: {"state": "cancelled"}, 6: {"state": "ready"}},
                         {2: [1], 3: [2, 1], 6: [4]})
        blocked = board.dag_blocked(snap, board.dag_blockers(snap))
        shown = board.dag_shown(snap, blocked, False)
        self.assertEqual(shown, {"2", "3", "4", "6"})
        self.assertEqual(board.dag_layers(snap, shown), {"2": 0, "3": 1, "4": 0, "6": 1})
        everything = board.dag_shown(snap, blocked, True)
        self.assertEqual(everything, {"1", "2", "3", "4", "5", "6"})
        self.assertEqual(board.dag_layers(snap, everything)["3"], 2)

    def test_lanes_in_group_number_order_ungrouped_last(self):
        import board
        snap = self.fake({1: {"state": "ready", "group": "g10"}, 2: {"state": "ready", "group": "g9"},
                          3: {"state": "ready"}, 4: {"state": "ready", "group": "g7"}}, {},
                         {"g9": {"id": "g9", "state": "open"}, "g10": {"id": "g10", "state": "stuck"}})
        lanes = board.dag_lanes(snap, {"1", "2", "3"})
        self.assertEqual(lanes, [("g9", "收票中"), ("g10", "卡住"), ("", "未分組")])
        self.assertEqual(board.dag_lanes(snap, {"4"}), [("g7", board.UNKNOWN)])

    def test_node_live_lines_grow_the_node(self):
        import board
        import xml.etree.ElementTree as ET
        from types import SimpleNamespace
        snap = self.fake({5: {"state": "running", "group": "g3"}, 6: {"state": "ready", "group": "g3"}}, {},
                         {"g3": {"id": "g3", "state": "open", "members": ["5", "6"]}})
        rows = [{"kind": "agent", "state": "running", "started": 100, "attrs": {"data-model": "m-v"},
                 "rec": {"ticket": "5", "role": "verifier", "round": 2}, "text": "執行中"},
                {"kind": "agent", "state": "quota", "started": 160, "attrs": {},
                 "rec": {"ticket": "5", "role": "worker_simple"}, "text": "額度用完,到 X"},
                {"kind": "agent", "state": "running", "started": 100, "attrs": {},
                 "rec": {"ticket": None, "role": "fixer"}, "text": "執行中"}]
        cfg = SimpleNamespace(max_rounds=3, max_group_size=10)
        got = board.dag_node_live("5", rows, cfg, 960)
        self.assertEqual(got[0][0], "驗證者寫案例 第 2/3 輪 · m-v · 14 分")
        self.assertEqual(got[1][0], "worker · 13 分 · 額度用完,到 X")
        self.assertEqual(got[1][1], {"data-role": "worker_simple", "data-wait": "quota"})
        self.assertEqual(board.dag_node_live("6", rows, cfg, 960), [])
        svg = board.dag_svg(snap, {"5", "6"}, {"5": 0, "6": 0}, {}, set(), cfg, rows, 960)
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        rects = {}
        for g in root.iter(ns + "g"):
            if g.get("data-id"):
                r = g.find(ns + "rect")
                rects[g.get("data-id")] = (float(r.get("y")), float(r.get("height")))
                for t in g.iter(ns + "text"):
                    self.assertTrue(rects[g.get("data-id")][0] <= float(t.get("y")) <= sum(rects[g.get("data-id")]))
        self.assertEqual(rects["5"][1], 38 + 15 * 2)
        self.assertGreaterEqual(rects["6"][0], sum(rects["5"]))

    def test_svg_escapes_titles(self):
        import board
        import xml.etree.ElementTree as ET
        snap = self.fake({1: {"state": "ready", "title": "a <b> & 'c' \"d\""}}, {})
        svg = board.dag_svg(snap, {"1"}, {"1": 0}, {}, set())
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        self.assertEqual(root.find(".//%stitle" % ns).text, "#1 a <b> & 'c' \"d\"")

    def test_forty_tickets_in_eight_groups_under_a_second(self):
        import board
        from core.config import Config

        def change(tx):
            dag = tx.data.dag
            for n in range(1, 41):
                state = ("ready", "running", "merged", "stuck", "cancelled", "landed")[n % 6]
                tx.put_ticket({"id": str(n), "title": "ticket %d" % n, "state": state,
                               "group": "g%d" % (1 + n % 8), "round": 0, "feedback": []})
                dag[str(n)] = {"depends_on": [str(d) for d in (n - 1, n - 3, n - 7) if d > 0], "verify": "together"}
            tx.put_dag({"tickets": dag})
            for g in range(1, 9):
                tx.put_group({"id": "g%d" % g, "members": [], "state": ("open", "stuck", "pr", "landed")[g % 4]})
        self.pool.update(change, "test: forty")
        cfg, snap = Config(self.proj), self.snap()
        start = time.monotonic()
        page = board.render(cfg, snap, query={"tab": "dag", "done": "1"})
        took = time.monotonic() - start
        self.assertEqual(page.count('class="lane"'), 8)
        self.assertEqual(page.count('<g class="node '), 40)
        self.assertLess(took, 1.0)


    def test_dag_last_regression_parts(self):
        import board
        f = board.dag_last_regression
        self.assertEqual(f("green"), ("上次 regression:綠", None))
        self.assertEqual(f("3 failing:\n- a"), ("上次 regression:紅 3 條", "3"))
        self.assertEqual(f("environment not usable: x")[0], "上次 regression:環境不能用")
        self.assertEqual(f("could not measure y")[0], "上次 regression:沒量到")
        self.assertEqual(f("")[0], "")
        self.assertEqual(f(None)[0], "")
        self.assertTrue(f("x" * 50)[0].endswith("…"))

    def test_dag_group_rows_filters_by_group_and_kind(self):
        import board
        rows = [{"kind": "regression", "rec": {"group": "g21"}}, {"kind": "agent", "rec": {"group": "g21"}},
                {"kind": "fixer", "rec": {"group": "g22"}}, {"kind": "release", "rec": {"group": "g21"}}]
        self.assertEqual([r["kind"] for r in board.dag_group_rows("g21", rows)], ["regression", "release"])
        self.assertEqual(board.dag_group_rows("g21", None), [])

    def wait_fixture(self, lock=True, max_parallel=2):
        """#84's fixture F as a fake snapshot, its live regression row, and a config stub."""
        from types import SimpleNamespace
        root = os.path.join(self.tmp, "wt-%s-%s" % (lock, max_parallel))
        os.makedirs(root, exist_ok=True)
        if lock:
            with open(os.path.join(root, "controller.lock"), "w") as fh:
                fh.write(json.dumps(osx.stamp()))
        snap = self.fake({1: {"state": "merged", "group": "g21"}, 2: {"state": "ready"},
                          3: {"state": "running", "group": "g22"}, 4: {"state": "ready", "group": "g22"},
                          5: {"state": "ready"}, 6: {"state": "ready"}, 7: {"state": "ready"},
                          8: {"state": "stuck", "group": "g22"}, 9: {"state": "ready"},
                          10: {"state": "cancelled"}, 11: {"state": "ready"}, 12: {"state": "ready"}},
                         {2: [1], 5: [3], 6: [5], 9: [8], 11: [10], 12: [99]},
                         {"g21": {"id": "g21", "state": "pr", "members": ["1", "7"]},
                          "g22": {"id": "g22", "state": "open", "members": ["3", "4", "8"]}})
        rows = [{"kind": "regression", "state": "running", "started": None, "attrs": {}, "text": "full",
                 "rec": {"group": "g21", "stage": "full"}}]
        cfg = SimpleNamespace(worktree_root=root, max_parallel=max_parallel, max_rounds=3, max_group_size=10)
        return cfg, snap, rows

    def test_dag_waits_name_what_each_ready_ticket_waits_for(self):
        import board
        cfg, snap, rows = self.wait_fixture()
        waits = board.dag_waits(cfg, snap, set(snap.tickets), rows)
        self.assertEqual(waits, {
            "2": ("deps", "等 g21 落地 — regression 跑中", ["1"], ""),
            "4": ("next", "下一輪派出", [], "g22"),
            "5": ("deps", "等 #3(做中)", ["3"], ""),
            "6": ("deps", "等 #5(待派)", ["5"], ""),
            "7": ("next", "下一輪派出", [], ""),
            "9": ("deps", "等你判 #8", ["8"], ""),
            "11": ("deps", "#10 已取消,不會落地", ["10"], ""),
            "12": ("deps", "#99 不存在", ["99"], "")})
        cfg, snap, rows = self.wait_fixture(lock=False)
        waits = board.dag_waits(cfg, snap, set(snap.tickets), rows)
        self.assertEqual(waits["4"][:2], ("controller", "controller 沒在跑"))
        self.assertEqual(waits["7"][:2], ("controller", "controller 沒在跑"))
        self.assertEqual(waits["5"][1], "等 #3(做中)")
        cfg, snap, rows = self.wait_fixture(max_parallel=1)
        waits = board.dag_waits(cfg, snap, set(snap.tickets), rows)
        self.assertEqual(waits["4"][:2], ("parallel", "等空位(同時最多 1 張在做)"))
        self.assertEqual(waits["7"][:2], ("parallel", "等空位(同時最多 1 張在做)"))

    def test_dag_group_now_and_one_part_per_group(self):
        import board
        cfg, snap, rows = self.wait_fixture()
        g = snap.groups["g21"]
        now = board.dag_group_now
        self.assertEqual(now(cfg, g, "g21", rows), "regression 跑中")
        slot = [dict(rows[0], state="slot", rec={"group": "g21", "stage": "machine"})]
        self.assertEqual(now(cfg, g, "g21", slot), "regression 等機器名額")
        fixer = [{"kind": "fixer", "state": "running", "rec": {"group": "g21", "round": 2, "max_rounds": 3}}]
        self.assertEqual(now(cfg, g, "g21", fixer), "修補第 2/3 輪")
        release = [{"kind": "release", "state": "running", "rec": {"group": "g21"}}]
        self.assertEqual(now(cfg, g, "g21", release), "發版中")
        self.assertEqual(now(cfg, {"state": "stuck"}, "g21", []), "卡住")
        self.assertEqual(now(cfg, {"state": "verified"}, "g21", rows[:0]), "已驗,待落地")
        snap.tickets["13"] = {"id": "13", "state": "merged", "group": "g21"}
        snap.tickets["14"] = {"id": "14", "state": "ready"}
        snap.tickets["15"] = {"id": "15", "state": "merged"}
        snap.dag["14"] = {"depends_on": ["13", "1", "15", "3"]}
        waits = board.dag_waits(cfg, snap, set(snap.tickets), rows)
        self.assertEqual(waits["14"], ("deps", "等 g21 落地 — regression 跑中、等 #3(做中)、等 #15 落地",
                                       ["1", "3", "13", "15"], ""))

    def test_dag_wait_marks_follow_to_a_moving_source(self):
        import board
        cfg, snap, rows = self.wait_fixture()
        shown = set(snap.tickets)
        edges, lanes = board.dag_wait_marks(snap, shown, board.dag_waits(cfg, snap, shown, rows))
        self.assertEqual(edges, {("3", "5"), ("5", "6")})
        self.assertEqual(lanes, [("g21", "2", False)])
        snap.groups["g21"]["state"] = "stuck"
        edges, lanes = board.dag_wait_marks(snap, shown, board.dag_waits(cfg, snap, shown, rows))
        self.assertEqual(lanes, [("g21", "2", True)])
        # a cycle of ready tickets reaches no source and does not loop
        snap.dag["5"] = {"depends_on": ["6"]}
        edges, _ = board.dag_wait_marks(snap, shown, board.dag_waits(cfg, snap, shown, rows))
        self.assertEqual(edges, set())

    def test_dag_wait_marks_walk_through_ready_and_skip_missing_only(self):
        import board
        cfg, snap, rows = self.wait_fixture()
        for ident, deps in (("20", []), ("21", ["20"]), ("22", ["2"]), ("23", ["12"])):
            snap.tickets[ident] = {"id": ident, "state": "ready"}
            snap.dag[ident] = {"depends_on": deps}
        shown = set(snap.tickets)
        waits = board.dag_waits(cfg, snap, shown, rows)
        self.assertEqual(waits["23"][:3], ("deps", "等 #12(待派)", ["12"]))
        edges, lanes = board.dag_wait_marks(snap, shown, waits)
        self.assertIn(("20", "21"), edges)  # a ready ticket with nothing unsatisfied goes next
        self.assertIn(("2", "22"), edges)  # through #2 to #1 merged in a pr group
        self.assertNotIn(("12", "23"), edges)  # only a missing ticket at the end: neither
        self.assertEqual(lanes, [("g21", "2", False)])

    def test_dag_svg_draws_wait_lines_marks_and_lane_lines(self):
        import board
        import xml.etree.ElementTree as ET
        cfg, snap, rows = self.wait_fixture()
        shown = set(snap.tickets)
        blockers = board.dag_blockers(snap)
        blocked = board.dag_blocked(snap, blockers)
        svg = board.dag_svg(snap, shown, board.dag_layers(snap, shown), blocked, blockers, cfg, rows, 0)
        ns = "{http://www.w3.org/2000/svg}"
        root = ET.fromstring(svg)
        nodes = dict((g.get("data-id"), g) for g in root.iter(ns + "g") if g.get("data-id"))
        for ident, g in nodes.items():
            waits = [t for t in g.iter(ns + "text") if t.get("class") == "wait"]
            if g.get("data-state") == "ready":
                self.assertEqual(len(waits), 1, ident)
                self.assertTrue(waits[0].text, ident)
            else:
                self.assertEqual(waits, [], ident)
        st = dict((i, g.find(ns + "text[@class='st']").text) for i, g in nodes.items())
        self.assertEqual((st["4"], nodes["4"].get("data-member"), nodes["4"].get("data-group")),
                         ("組內待做", "g22", "g22"))
        self.assertEqual((st["7"], nodes["7"].get("data-member"), nodes["7"].get("data-group")), ("待派", None, ""))
        self.assertEqual(nodes["2"].get("data-wait-on"), "1")
        self.assertEqual(nodes["4"].get("data-wait"), "next")
        paths = dict(((p.get("data-from"), p.get("data-to")), p.get("class").split()) for p in root.iter(ns + "path"))
        self.assertIn("wait", paths[("3", "5")])
        self.assertIn("wait", paths[("5", "6")])
        self.assertEqual(paths[("8", "9")], ["edge", "chain"])
        self.assertEqual(paths[("10", "11")], ["edge", "chain"])
        self.assertEqual(paths[("1", "2")], ["edge"])
        self.assertEqual(paths[("lane:g21", "2")], ["lanewait"])
        self.assertEqual(len([k for k in paths if k[0].startswith("lane:")]), 1)
        self.assertIn("等待路徑", board.render_dag_legend())

    def test_opener_pages_rules_and_order(self):
        import board
        from types import SimpleNamespace

        def pg(kind, ref, text, at, **kw):
            return dict(kind=kind, ref=ref, text=text, at=at, **kw)
        snap = SimpleNamespace(inbox={
            "a": pg("decision", "request", "line one\nline two", "2026-11-08T10:00:00Z"),
            "b": pg("decision", "5", "#5 needs your decision (raised by the worker): which file?", "2026-11-08T11:00:00Z"),
            "c": pg("decision", "x", "input from the web was NOT acted on: x", "2026-11-08T12:00:00Z"),
            "d": pg("decision", "g21", "group g21 is still red after 3 fix rounds", "2026-11-08T13:00:00Z"),
            "e": pg("decision", "5", "#5 is stuck (rounds): x", "2026-11-08T13:00:00Z"),
            "f": pg("env", "request", "x", "2026-11-08T13:00:00Z"),
            "g": pg("decision", "request", "x", "2026-11-08T13:00:00Z", acked=True),
        })
        self.assertEqual([n for n, _ in board.opener_pages(snap)], ["c", "b", "a"])
        items = board.dag_opening(snap, [], 0)
        self.assertEqual([t for _, t, _ in items],
                         ["要你判:input from the web was NOT acted on: x",
                          "要你判:" + board._clip("#5 needs your decision (raised by the worker): which file?", 40),
                          "要你判:line one"])
        self.assertTrue(all(h == "?tab=decide" for _, _, h in items))


if __name__ == "__main__":
    unittest.main()
