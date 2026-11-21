"""the board's Token and 主機 tabs"""

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


class T43Acceptance(LoopCase):
    """Ticket #43: the Token tab shows where tokens and dollars went, from every cost row in the
    pool (tickets, integration branches, memory runs). Expected values are worked out by hand from
    the ticket's fixture."""

    MEASURES = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "usd")
    TITLES = ("近 30 天", "每天", "依角色", "依模型", "每張落地票", "最貴的十張", "估計與實際")

    def setUp(self):
        super().setUp()
        old_tz = os.environ.get("TZ")

        def restore():
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()
        self.addCleanup(restore)
        os.environ["TZ"] = "UTC"
        time.tzset()
        import calendar
        self.now = calendar.timegm((2026, 11, 6, 12, 0, 0))

        def row(role, model, at, i, o, cr, cw, usd):
            return {"role": role, "model": model, "at": at, "input_tokens": i, "output_tokens": o,
                    "cache_read_tokens": cr, "cache_write_tokens": cw, "usd": usd}
        self.add(1, state="landed", complexity="simple", token_estimate=100000,
                 cost=[row("worker", "sonnet", "2026-11-06T10:00:00Z", 10, 1000, 50000, 2000, 0.50),
                       row("verifier", "opus", "2026-11-05T09:00:00Z", 20, 2000, 100000, 3000, 1.00)])
        self.add(2, state="landed", complexity="complex", token_estimate=50000,
                 cost=[row("worker", "opus", "2026-11-03T11:00:00Z", None, None, None, None, None)])
        self.add(3, state="stuck", cost=[row("worker", "opus", "2026-10-01T00:00:00Z", 5, 5, 0, 0, 3.00)])
        self.pool.update(lambda tx: tx.put_group({
            "id": "g1", "state": "landed", "members": ["1"],
            "cost": [row("fixer", "opus", "2026-11-06T08:00:00Z", 1, 100, 1000, 10, 0.10)]}), "test: g1")
        self.pool.update(lambda tx: tx.write("memory/state.json", {"runs": [{"cost": [
            row("consolidator", "fable", "2026-11-04T00:00:00Z", 2, 200, 0, 0, 0.02)]}]}), "test: memory")

    # ------------------------------------------------------------ helpers

    def page(self):
        import inspect
        import board
        self.assertTrue(hasattr(board, "render_tokens_tab"), "no render_tokens_tab")
        params = inspect.signature(board.render).parameters
        for name in ("query", "now"):
            self.assertIn(name, params, "render() takes %s" % name)
        from core.config import Config
        page = board.render(Config(self.proj), self.snap(), query={"tab": "tokens"}, now=self.now)
        at = page.find('id="tab-tokens"')
        self.assertGreaterEqual(at, 0, "no Token tab element")
        return page[page.find(">", at) + 1:]

    @staticmethod
    def squash(text):
        return "".join(text.split())

    def parse(self, body):
        """Walk the tab: a stream of ('text', block text) and ('table', table) items in page order,
        and the svg charts. A table: {'caption', 'rows': [{'kind': 'th'|'td', 'cells', 'hrefs'}]}.
        A chart: {'measure', 'bars': [rect attrs]}."""
        from html.parser import HTMLParser
        blocks = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "section", "br", "table",
                  "caption", "header", "footer", "ul", "ol", "dl", "dt", "dd", "svg", "details",
                  "summary", "figcaption", "figure"}

        class P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stream, self.charts = [], []
                self.buf = []
                self.table = None
                self.row = None
                self.cell = None
                self.caption = False
                self.skip = 0
                self.svg = None

            def flush(self):
                text = " ".join("".join(self.buf).split())
                self.buf = []
                if text:
                    self.stream.append(("text", text))

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag in ("style", "script"):
                    self.skip += 1
                    return
                if tag == "svg":
                    self.svg = {"measure": a.get("data-measure"),
                                "chart": "chart" in (a.get("class") or "").split(), "bars": []}
                    self.charts.append(self.svg)
                if tag == "rect" and self.svg is not None and "bar" in (a.get("class") or "").split():
                    self.svg["bars"].append(a)
                if self.table is None and tag in blocks:
                    self.flush()
                if tag == "table":
                    self.table = {"caption": "", "rows": []}
                    self.stream.append(("table", self.table))
                elif self.table is not None:
                    if tag == "caption":
                        self.caption = True
                    elif tag == "tr":
                        self.row = {"kind": "th", "cells": [], "hrefs": []}
                        self.table["rows"].append(self.row)
                    elif tag in ("td", "th") and self.row is not None:
                        if tag == "td":
                            self.row["kind"] = "td"
                        self.cell = []
                        self.row["cells"].append(self.cell)
                    elif tag == "a" and self.row is not None:
                        self.row["hrefs"].append(a.get("href") or "")

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)
                if tag in ("svg",):
                    self.svg = None

            def handle_endtag(self, tag):
                if tag in ("style", "script"):
                    self.skip = max(0, self.skip - 1)
                    return
                if tag == "svg":
                    self.svg = None
                if tag == "table" and self.table is not None:
                    for r in self.table["rows"]:
                        r["cells"] = [" ".join("".join(c).split()) for c in r["cells"]]
                    self.table = self.row = self.cell = None
                    return
                if self.table is not None:
                    if tag == "caption":
                        self.caption = False
                    elif tag in ("td", "th"):
                        self.cell = None
                    return
                if tag in blocks:
                    self.flush()

            def handle_data(self, data):
                if self.skip or self.svg is not None:
                    return
                if self.table is not None:
                    if self.caption:
                        self.table["caption"] += data
                    elif self.cell is not None:
                        self.cell.append(data)
                    return
                self.buf.append(data)

        p = P()
        p.feed(body)
        p.close()
        p.flush()
        return p.stream, p.charts

    def find_table(self, stream, title):
        """The first table whose caption, or the last text block before it, holds the title."""
        want = self.squash(title)
        last = ""
        for kind, item in stream:
            if kind == "text":
                last = item
            else:
                if want in self.squash(item["caption"]) or want in self.squash(last):
                    return item
                last = ""
        self.fail("no table titled %s" % title)

    def head_body(self, table):
        heads = [r["cells"] for r in table["rows"] if r["kind"] == "th"]
        self.assertTrue(heads, "table has no header row: %r" % table)
        body = [r for r in table["rows"] if r["kind"] == "td" and r["cells"]]
        return heads[0], body

    def col(self, head, name):
        names = [self.squash(h) for h in head]
        self.assertIn(self.squash(name), names, "no column %s in %r" % (name, head))
        return names.index(self.squash(name))

    def window(self, stream, start, n):
        """Squashed text of n stream items from index start on (tables as their cell text)."""
        out = []
        for kind, item in stream[start:start + n]:
            if kind == "text":
                out.append(item)
            else:
                out.append(item["caption"])
                for r in item["rows"]:
                    out.extend(r["cells"])
        return self.squash(" ".join(out))

    def days(self):
        import datetime
        last = datetime.date(2026, 11, 6)
        return [(last - datetime.timedelta(days=29 - i)).isoformat() for i in range(30)]

    # ------------------------------------------------------------ cases

    def test_last_30_days_table(self):
        stream, _ = self.parse(self.page())
        head, body = self.head_body(self.find_table(stream, "近 30 天"))
        self.assertEqual(len(body), 1, body)
        cells = body[0]["cells"]
        self.assertEqual(cells[self.col(head, "次數")], "5")
        for want in ("33(1 筆未知)", "3,300(1 筆未知)", "151,000(1 筆未知)", "5,010(1 筆未知)",
                     "$1.62(1 筆未知)"):
            self.assertIn(want, cells)

    def test_daily_table(self):
        stream, _ = self.parse(self.page())
        head, body = self.head_body(self.find_table(stream, "每天"))
        date, count = self.col(head, "日期"), self.col(head, "次數")
        self.assertEqual([r["cells"][date] for r in body], self.days())
        by_day = dict((r["cells"][date], r["cells"]) for r in body)

        def measures(day):
            return [c for i, c in enumerate(by_day[day]) if i not in (date, count)]
        self.assertEqual(measures("2026-11-03"), ["未知"] * 5)
        self.assertEqual(by_day["2026-11-03"][count], "1")
        self.assertEqual(measures("2026-11-02"), ["—"] * 5)
        self.assertEqual(by_day["2026-11-06"][count], "2")
        for want in ("11", "1,100", "51,000", "2,010", "$0.60"):
            self.assertIn(want, measures("2026-11-06"))
        self.assertEqual(by_day["2026-11-05"][count], "1")
        for want in ("20", "2,000", "100,000", "3,000", "$1.00"):
            self.assertIn(want, measures("2026-11-05"))

    def test_daily_bars(self):
        _, charts = self.parse(self.page())
        charts = [c for c in charts if c["chart"] and c["measure"]]
        self.assertEqual(sorted(c["measure"] for c in charts), sorted(self.MEASURES))
        values = {}
        for c in charts:
            self.assertEqual([b.get("data-day") for b in c["bars"]], self.days(), c["measure"])
            values[c["measure"]] = dict((b.get("data-day"), b.get("data-value")) for b in c["bars"])
        self.assertEqual(values["input_tokens"]["2026-11-06"], "11")
        self.assertEqual(values["input_tokens"]["2026-11-05"], "20")
        self.assertEqual(values["input_tokens"]["2026-11-03"], "")
        self.assertEqual(values["input_tokens"]["2026-11-02"], "")
        self.assertEqual(values["cache_read_tokens"]["2026-11-06"], "51000")
        self.assertEqual(values["usd"]["2026-11-05"], "1.00")
        self.assertEqual(values["usd"]["2026-11-06"], "0.60")
        self.assertEqual(values["usd"]["2026-11-03"], "")

    def test_by_role_and_by_model(self):
        stream, _ = self.parse(self.page())
        head, body = self.head_body(self.find_table(stream, "依角色"))
        count = self.col(head, "次數")
        self.assertEqual([r["cells"][0] for r in body], ["consolidator", "fixer", "verifier", "worker"])
        rows = dict((r["cells"][0], r["cells"]) for r in body)
        self.assertEqual(rows["worker"][count], "2")
        self.assertIn("10(1 筆未知)", rows["worker"])
        self.assertIn("$0.50(1 筆未知)", rows["worker"])
        self.assertIn("$1.00", rows["verifier"])
        head, body = self.head_body(self.find_table(stream, "依模型"))
        count = self.col(head, "次數")
        self.assertEqual([r["cells"][0] for r in body], ["fable", "opus", "sonnet"])
        rows = dict((r["cells"][0], r["cells"]) for r in body)
        self.assertEqual(rows["opus"][count], "3")
        self.assertIn("21(1 筆未知)", rows["opus"])
        self.assertIn("$1.10(1 筆未知)", rows["opus"])
        self.assertEqual(rows["sonnet"][count], "1")

    def test_per_landed_ticket_median(self):
        stream, _ = self.parse(self.page())
        at = [i for i, (k, item) in enumerate(stream)
              if k == "text" and self.squash("每張落地票") in self.squash(item)]
        at += [i for i, (k, item) in enumerate(stream)
               if k == "table" and self.squash("每張落地票") in self.squash(item["caption"])]
        self.assertTrue(at, "no 每張落地票")
        text = self.window(stream, min(at), 4)
        self.assertIn("153,030(1張)", text)
        self.assertIn("$1.50(1張)", text)

    def test_ten_most_expensive(self):
        stream, _ = self.parse(self.page())
        head, body = self.head_body(self.find_table(stream, "最貴的十張"))
        cols = dict((n, self.col(head, n)) for n in ("票", "標題", "狀態", "token", "快取寫", "美元", "次數"))
        self.assertEqual(len(body), 2, body)
        self.assertTrue(any(h.endswith("?ticket=3") for h in body[0]["hrefs"]), body[0])
        self.assertTrue(any(h.endswith("?ticket=1") for h in body[1]["hrefs"]), body[1])
        got = [[r["cells"][cols[n]] for n in ("標題", "token", "快取寫", "美元", "次數")] for r in body]
        self.assertEqual(got, [["ticket 3", "10", "0", "$3.00", "1"],
                               ["ticket 1", "153,030", "5,000", "$1.50", "2"]])

    def test_estimate_against_actual(self):
        stream, _ = self.parse(self.page())
        table = self.find_table(stream, "估計與實際")
        head, body = self.head_body(table)
        ticket, est = self.col(head, "票"), self.col(head, "估計")
        actual, ratio = self.col(head, "實際"), self.col(head, "比")
        self.assertEqual([r["cells"][ticket].lstrip("#") for r in body], ["1", "2"])
        self.assertEqual(body[0]["cells"][actual], "153,030")
        self.assertEqual(body[0]["cells"][ratio], "1.53")
        self.assertIn(body[0]["cells"][est].replace(",", ""), ("100000",))
        self.assertEqual(body[1]["cells"][actual], "未知")
        self.assertEqual(body[1]["cells"][ratio], "未知")
        after = [i for i, (k, item) in enumerate(stream) if item is table][0] + 1
        text = self.window(stream, after, 6)
        self.assertGreaterEqual(text.count("1.53(1張)"), 2, text)
        self.assertIn("未知", text)

    def test_opener_and_reviser_not_counted(self):
        stream, _ = self.parse(self.page())
        lines = [item for k, item in stream if k == "text"]
        self.assertTrue(any("開題者" in t and ("修" in t or "改" in t or "revis" in t) for t in lines),
                        "no line about the opener's and reviser's runs: %r" % lines)


class TokenTabUnit(LoopCase):
    """Helpers of board.py's Token tab: cost rows, local days, the cell rule, medians."""

    def setUp(self):
        super().setUp()
        old = os.environ.get("TZ")

        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.addCleanup(restore)
        os.environ["TZ"] = "UTC"
        time.tzset()

    @staticmethod
    def row(role, model, at, i, o, cr, cw, usd):
        return {"role": role, "model": model, "at": at, "input_tokens": i, "output_tokens": o,
                "cache_read_tokens": cr, "cache_write_tokens": cw, "usd": usd}

    def fixture(self):
        self.add(1, state="landed", token_estimate=100000, cost=[
            self.row("worker", "sonnet", "2026-11-06T10:00:00Z", 10, 1000, 50000, 2000, 0.50),
            self.row("verifier", "opus", "2026-11-05T09:00:00Z", 20, 2000, 100000, 3000, 1.00)])
        self.add(2, state="landed", complexity="complex", token_estimate=50000, cost=[
            self.row("worker", "opus", "2026-11-03T11:00:00Z", None, None, None, None, None)])
        self.add(3, state="stuck", cost=[self.row("worker", "opus", "2026-10-01T00:00:00Z", 5, 5, 0, 0, 3.00)])

        def change(tx):
            tx.write("groups/g1.json", {"id": "g1", "state": "open", "tickets": [], "cost": [
                self.row("fixer", "opus", "2026-11-06T08:00:00Z", 1, 100, 1000, 10, 0.10)]})
            tx.write("memory/state.json", {"runs": [{"cost": [
                self.row("consolidator", "fable", "2026-11-04T00:00:00Z", 2, 200, 0, 0, 0.02)]}]})
        self.pool.update(change, "test: token fixture")

    def test_cost_rows_reads_tickets_groups_and_memory_runs(self):
        import board
        self.fixture()
        roles = sorted(r["role"] for r in board.cost_rows(self.snap()))
        self.assertEqual(roles, ["consolidator", "fixer", "verifier", "worker", "worker", "worker"])

    def test_row_day_and_token_days(self):
        import calendar
        import board
        self.assertEqual(board.row_day({"at": "2026-11-05T23:59:59Z"}), "2026-11-05")
        self.assertIsNone(board.row_day({"at": "yesterday"}))
        self.assertIsNone(board.row_day({}))
        days = board.token_days(calendar.timegm((2026, 11, 6, 12, 0, 0)))
        self.assertEqual((len(days), days[0], days[-1]), (30, "2026-10-08", "2026-11-06"))
        os.environ["TZ"] = "Asia/Taipei"
        time.tzset()
        self.assertEqual(board.row_day({"at": "2026-11-05T20:00:00Z"}), "2026-11-06")

    def test_cell_rule(self):
        import board
        self.assertEqual(board.cell([], "usd"), "—")
        self.assertEqual(board.cell([{"usd": None}], "usd"), "未知")
        self.assertEqual(board.cell([{"usd": 1.5}, {"usd": 0.25}], "usd"), "$1.75")
        self.assertEqual(board.cell([{"input_tokens": 1500}, {}], "input_tokens"), "1,500(1 筆未知)")
        self.assertEqual(board.cell([{"input_tokens": True}], "input_tokens"), "未知")

    def test_ticket_values_and_complexity(self):
        import board
        self.assertIsNone(board.ticket_tokens({"cost": [{"usd": 1}]}))
        self.assertEqual(board.ticket_tokens({"cost": [{"input_tokens": 1, "cache_read_tokens": 4,
                                                        "cache_write_tokens": 100}]}), 5)
        self.assertIsNone(board.ticket_dollars({"cost": [{"input_tokens": 1}]}))
        self.assertEqual(board.ticket_complexity({"complexity": "complex"}), "complex")
        self.assertEqual(board.ticket_complexity({"needs_review": True}), "complex")
        self.assertEqual(board.ticket_complexity({}), "simple")
        self.assertEqual(board.median_text([], str), "未知")
        self.assertEqual(board.median_text([1.0, 2.0], lambda m: "%.2f" % m), "1.50(2 張)")

    def test_tab_renders_fixture(self):
        import calendar
        import board
        from core.config import Config
        self.fixture()
        page = board.render(Config(self.proj), self.snap(), query={"tab": "tokens"},
                            now=calendar.timegm((2026, 11, 6, 12, 0, 0)))
        self.assertIn("$1.62(1 筆未知)", page)
        self.assertIn("153,030(1 張)", page)
        self.assertEqual(page.count('<rect class="bar"'), 150)
        self.assertIn('data-day="2026-11-06" data-value="11"', page)
        self.assertIn('data-day="2026-11-03" data-value=""', page)
        self.assertLess(page.index('href="?ticket=3"'), page.index('href="?ticket=1"'))


class T45Acceptance(LoopCase):
    """Ticket #45: the 主機 tab draws each host file in <worktrees>/runs/host/ as SVG line charts over
    the last 24 hours and 7 days. Expected values are worked out by hand from the ticket and the
    fixture, never from the code."""

    GIB = 2 ** 30
    CHARTS = (("load", ("load1", "load5")), ("memory", ("mem_available",)), ("disk", ("disk_free",)),
              ("agents", ("agents", "slots_used")))

    def setUp(self):
        super().setUp()
        import calendar
        self.now = calendar.timegm((2026, 11, 6, 12, 0, 0))
        self.host_dir = os.path.join(self.tmp, "wt", "runs", "host")

    # ------------------------------------------------------------ helpers

    @staticmethod
    def at(t):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

    def sample(self, t, **values):
        row = {"at": self.at(t), "host": "h", "load1": 1.0, "load5": 0.5, "cpus": 4,
               "mem_available": 2 * self.GIB, "disk_free": 10 * self.GIB, "agents": 1, "slots_used": 1,
               "slots": 2}
        row.update(values)
        return json.dumps(row)

    def write_host(self, name, lines):
        os.makedirs(self.host_dir, exist_ok=True)
        with open(os.path.join(self.host_dir, name), "w") as fh:
            fh.write("".join(line + "\n" for line in lines))

    def fixture(self):
        N = self.now
        # written out of name order, so the page's order is its own sorting
        self.write_host("gamma.jsonl", [self.sample(N - 50, load1=0.5, load5=None, cpus=2, mem_available=None,
                                                    disk_free=None, agents=0, slots_used=None, slots=None)])
        self.write_host("beta.jsonl", [self.sample(N - 700000)])
        self.write_host("alpha.jsonl", [
            self.sample(N - 100, load1=1.0, load5=0.5, cpus=4, mem_available=2147483648,
                        disk_free=10737418240, agents=1, slots_used=1, slots=2),
            self.sample(N - 200, load1=3.0, load5=1.5, cpus=4, mem_available=1073741824,
                        disk_free=10737418240, agents=3, slots_used=2, slots=2),
            self.sample(N - 400, load1=2.0, mem_available=None),
            self.sample(N - 90100, load1=4.0),
            self.sample(N - 700000, load1=9.0),
            "not json",
            "[1, 2]",
            json.dumps({"load1": 50.0}),
            json.dumps({"at": "yesterday", "load1": 99.0}),
        ])
        with open(os.path.join(self.host_dir, "notes.txt"), "w") as fh:
            fh.write(self.sample(N - 100) + "\n")

    def tab(self):
        """The 主機 tab element as a tree: {'tag', 'attrs', 'children', 'text'} nodes."""
        import inspect
        import board
        self.assertTrue(hasattr(board, "render_host_tab"), "no render_host_tab")
        params = inspect.signature(board.render).parameters
        for name in ("query", "now"):
            self.assertIn(name, params, "render() takes %s" % name)
        from core.config import Config
        page = board.render(Config(self.proj), self.snap(), query={"tab": "host"}, now=self.now)
        root = self.tree(page)
        tabs = [n for n in self.walk(root) if n["attrs"].get("id") == "tab-host"]
        self.assertEqual(len(tabs), 1, "no 主機 tab element")
        return tabs[0]

    @staticmethod
    def tree(page):
        from html.parser import HTMLParser
        void = {"br", "hr", "img", "input", "meta", "link", "wbr", "col", "area", "base", "source"}
        root = {"tag": "#root", "attrs": {}, "children": [], "text": []}

        class P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stack = [root]

            def handle_starttag(self, tag, attrs):
                node = {"tag": tag, "attrs": dict((k, "" if v is None else v) for k, v in attrs),
                        "children": [], "text": []}
                self.stack[-1]["children"].append(node)
                if tag not in void:
                    self.stack.append(node)

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)
                if tag not in void:
                    self.stack.pop()

            def handle_endtag(self, tag):
                for i in range(len(self.stack) - 1, 0, -1):
                    if self.stack[i]["tag"] == tag:
                        del self.stack[i:]
                        return

            def handle_data(self, data):
                node = {"tag": "#text", "attrs": {}, "children": [], "text": [data]}
                self.stack[-1]["children"].append(node)

        p = P()
        p.feed(page)
        p.close()
        return root

    def walk(self, node, skip=()):
        for child in node["children"]:
            yield child
            if child["tag"] not in skip:
                yield from self.walk(child, skip)

    def text(self, node, skip=()):
        return "".join("".join(n["text"]) for n in self.walk(node, skip) if n["tag"] == "#text")

    @staticmethod
    def squash(text):
        return "".join(text.split())

    @staticmethod
    def classes(node):
        return node["attrs"].get("class", "").split()

    def sections(self, tab):
        return [n for n in self.walk(tab) if n["tag"] == "section" and "host" in self.classes(n)]

    def section(self, tab, name):
        found = [s for s in self.sections(tab) if s["attrs"].get("data-host") == name]
        self.assertEqual(len(found), 1, "one section for host %s" % name)
        return found[0]

    def chart(self, section, window, chart):
        found = [n for n in self.walk(section) if n["tag"] == "svg" and "chart" in self.classes(n)
                 and n["attrs"].get("data-window") == window and n["attrs"].get("data-chart") == chart]
        self.assertEqual(len(found), 1, "one %s %s chart" % (window, chart))
        return found[0]

    def series(self, svg, field):
        found = [n for n in self.walk(svg) if n["tag"] == "g" and "series" in self.classes(n)
                 and n["attrs"].get("data-series") == field]
        self.assertEqual(len(found), 1, "one %s series" % field)
        return found[0]

    def values(self, section, window, chart, field):
        g = self.series(self.chart(section, window, chart), field)
        self.assertIn("data-values", g["attrs"])
        return g["attrs"]["data-values"].split(",")

    @staticmethod
    def expect(size, filled):
        out = [""] * size
        for k, v in filled.items():
            out[k] = v
        return out

    def polyline_points(self, g):
        import re
        out = []
        for n in self.walk(g):
            if n["tag"] == "polyline":
                nums = re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", n["attrs"].get("points", ""))
                out.append(len(nums) // 2)
        return out

    # ------------------------------------------------------------ item 1

    def test_item1_one_section_per_jsonl_file_sorted(self):
        self.fixture()
        tab = self.tab()
        self.assertEqual([s["attrs"].get("data-host") for s in self.sections(tab)], ["alpha", "beta", "gamma"])
        for s in self.sections(tab):
            self.assertEqual(s["attrs"].get("class"), "host")

    def test_item1_lines_without_object_or_parseable_at_are_skipped(self):
        self.fixture()
        alpha = self.section(self.tab(), "alpha")
        # the 'not json', '[1, 2]', no-'at' (50.0) and unparseable-'at' (99.0) lines add nothing
        self.assertEqual(self.values(alpha, "24h", "load", "load1"), self.expect(288, {286: "2.00", 287: "2.00"}))
        self.assertEqual(self.values(alpha, "7d", "load", "load1"), self.expect(168, {142: "4.00", 167: "2.00"}))

    # ------------------------------------------------------------ item 2

    def test_item2_bucket_edges(self):
        N = self.now
        self.write_host("edge.jsonl", [
            self.sample(N - 604801, load1=70.0),     # before the 7 d window
            self.sample(N - 604800, load1=8.0),      # 7d bucket 0
            self.sample(N - 86401, load1=60.0),      # before the 24 h window; 7d bucket 143
            self.sample(N - 86400, load1=7.0),       # 24h bucket 0; 7d bucket 144
            self.sample(N - 301, load1=6.0),         # 24h bucket 286; 7d bucket 167
            self.sample(N - 300, load1=5.0),         # 24h bucket 287; 7d bucket 167
        ])
        edge = self.section(self.tab(), "edge")
        self.assertEqual(self.values(edge, "24h", "load", "load1"),
                         self.expect(288, {0: "7.00", 286: "6.00", 287: "5.00"}))
        self.assertEqual(self.values(edge, "7d", "load", "load1"),
                         self.expect(168, {0: "8.00", 143: "60.00", 144: "7.00", 167: "5.50"}))

    # ------------------------------------------------------------ item 3

    def test_item3_four_charts_per_window_with_their_series(self):
        self.fixture()
        alpha = self.section(self.tab(), "alpha")
        for window, size in (("24h", 288), ("7d", 168)):
            charts = [n for n in self.walk(alpha) if n["tag"] == "svg" and "chart" in self.classes(n)
                      and n["attrs"].get("data-window") == window]
            self.assertEqual(sorted(n["attrs"].get("data-chart") for n in charts),
                             sorted(c for c, _ in self.CHARTS), window)
            for chart, fields in self.CHARTS:
                svg = self.chart(alpha, window, chart)
                got = sorted(n["attrs"].get("data-series") for n in self.walk(svg)
                             if n["tag"] == "g" and "series" in self.classes(n))
                self.assertEqual(got, sorted(fields), "%s %s" % (window, chart))
                for field in fields:
                    self.assertEqual(len(self.values(alpha, window, chart, field)), size,
                                     "%s %s %s" % (window, chart, field))

    def test_item3_24h_means(self):
        self.fixture()
        alpha = self.section(self.tab(), "alpha")
        want = {
            ("load", "load1"): {287: "2.00", 286: "2.00"},
            ("load", "load5"): {287: "1.00", 286: "0.50"},
            ("memory", "mem_available"): {287: "1.50"},
            ("disk", "disk_free"): {287: "10.00", 286: "10.00"},
            ("agents", "agents"): {287: "2.00", 286: "1.00"},
            ("agents", "slots_used"): {287: "1.50", 286: "1.00"},
        }
        for (chart, field), filled in want.items():
            self.assertEqual(self.values(alpha, "24h", chart, field), self.expect(288, filled),
                             "%s %s" % (chart, field))

    def test_item3_7d_means(self):
        self.fixture()
        alpha = self.section(self.tab(), "alpha")
        want = {
            ("load", "load1"): {167: "2.00", 142: "4.00"},
            ("load", "load5"): {167: "0.83", 142: "0.50"},
            ("memory", "mem_available"): {167: "1.50", 142: "2.00"},
            ("disk", "disk_free"): {167: "10.00", 142: "10.00"},
            ("agents", "agents"): {167: "1.67", 142: "1.00"},
            ("agents", "slots_used"): {167: "1.33", 142: "1.00"},
        }
        for (chart, field), filled in want.items():
            self.assertEqual(self.values(alpha, "7d", chart, field), self.expect(168, filled),
                             "%s %s" % (chart, field))

    def test_item3_polylines_break_at_empty_buckets(self):
        self.fixture()
        alpha = self.section(self.tab(), "alpha")
        # 24h load1: buckets 286 and 287 are one run of two -> one polyline of two points, no longer one
        points = self.polyline_points(self.series(self.chart(alpha, "24h", "load"), "load1"))
        self.assertIn(2, points, "the run 286-287 is one polyline")
        self.assertLessEqual(max(points), 2, points)
        # 7d load1: buckets 142 and 167 are runs of one -> no polyline joins them
        points = self.polyline_points(self.series(self.chart(alpha, "7d", "load"), "load1"))
        self.assertLessEqual(max(points or [0]), 1, points)

    # ------------------------------------------------------------ item 4

    def limits(self, svg):
        return [n for n in self.walk(svg) if "limit" in self.classes(n)]

    def test_item4_limit_lines(self):
        self.fixture()
        tab = self.tab()
        alpha, gamma = self.section(tab, "alpha"), self.section(tab, "gamma")
        for window in ("24h", "7d"):
            self.assertTrue(self.limits(self.chart(alpha, window, "load")), "alpha %s cpus line" % window)
            self.assertTrue(self.limits(self.chart(alpha, window, "agents")), "alpha %s slots line" % window)
            self.assertTrue(self.limits(self.chart(gamma, window, "load")), "gamma %s cpus line" % window)
            self.assertEqual(self.limits(self.chart(gamma, window, "agents")), [], "gamma slots is null")

    # ------------------------------------------------------------ item 5

    def test_item5_newest_sample(self):
        import re
        self.fixture()
        alpha = self.section(self.tab(), "alpha")
        shown = self.squash(self.text(alpha, skip=("svg", "style")))
        self.assertIn(self.at(self.now - 100), shown)
        self.assertRegex(shown, r"1(\.0+)?/0\.50*/4")          # load1 / load5 / cpus
        self.assertIn("2.0", shown)                            # mem_available in GiB
        self.assertIn("10.0", shown)                           # disk_free in GiB
        self.assertNotIn("2.00", shown)
        self.assertNotIn("10.00", shown)
        self.assertRegex(shown, r"(?<![\d.])1/2(?![\d.])")     # slots_used / slots
        self.assertNotIn("未知", shown)
        self.assertTrue(re.search(r"(?<![\d.])1(?![\d.])", shown.replace("1/2", "")), "agents 1")

    def test_item5_null_reads_unknown(self):
        self.fixture()
        gamma = self.section(self.tab(), "gamma")
        shown = self.squash(self.text(gamma, skip=("svg", "style")))
        self.assertIn(self.at(self.now - 50), shown)
        # load5, mem_available, disk_free, slots_used and slots are null
        self.assertGreaterEqual(shown.count("未知"), 5, shown)

    # ------------------------------------------------------------ item 6

    def test_item6_host_without_recent_lines(self):
        self.fixture()
        beta = self.section(self.tab(), "beta")
        self.assertIn("近7天沒有紀錄", self.squash(self.text(beta, skip=("style",))))
        self.assertEqual([n for n in self.walk(beta) if n["tag"] == "svg"], [])
        alpha = self.section(self.tab(), "alpha")
        self.assertNotIn("近7天沒有紀錄", self.squash(self.text(alpha, skip=("style",))))

    def test_item6_empty_folder(self):
        os.makedirs(self.host_dir)
        tab = self.tab()
        shown = self.squash(self.text(tab, skip=("style",)))
        self.assertIn("還沒有主機紀錄", shown)
        self.assertIn("devloop.host_sample_seconds", shown)
        self.assertEqual(self.sections(tab), [])
        self.assertEqual([n for n in self.walk(tab) if n["tag"] == "svg"], [])

    def test_item6_no_folder(self):
        tab = self.tab()
        shown = self.squash(self.text(tab, skip=("style",)))
        self.assertIn("還沒有主機紀錄", shown)
        self.assertIn("devloop.host_sample_seconds", shown)
        self.assertEqual(self.sections(tab), [])


class HostTabHelpers(LoopCase):
    """#45 implementer's checks of the 主機 tab's helpers (bucketing, runs, file reading, rendering)."""

    def setUp(self):
        super().setUp()
        import calendar
        self.now = calendar.timegm((2026, 11, 6, 12, 0, 0))
        self.dir = os.path.join(self.tmp, "wt", "runs", "host")

    def write(self, name, lines):
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, name), "w") as fh:
            for ln in lines:
                fh.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")

    def at(self, t):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

    def test_buckets_edges(self):
        import board
        n = self.now
        samples = [(n - 300, {"load1": 1.0}), (n - 301, {"load1": 3.0}), (n, {"load1": 9.0}),
                   (n - 1, {"load1": 5.0}), (n - 3000, {"load1": None}), (n - 3001, {"load1": True})]
        vals = board.host_buckets(samples, "load1", n, 10, 300)
        self.assertEqual(vals[9], 3.0)        # n-300 and n-1 share the last bucket; n itself is outside
        self.assertEqual(vals[8], 3.0)        # n-301
        self.assertIsNone(vals[0])            # null and bool are not values
        self.assertEqual(board.host_buckets([(n - 1, {"disk_free": 2 ** 31})], "disk_free", n, 1, 300), [2.0])

    def test_runs(self):
        import board
        self.assertEqual(board.host_runs([None, 1, 2, None, 3, None]), [(1, [1, 2]), (4, [3])])
        self.assertEqual(board.host_runs([None, None]), [])

    def test_samples_skip_bad_lines(self):
        import board
        self.write("a.jsonl", ["not json", "[1]", {"at": "yesterday"}, {"load1": 1},
                               {"at": self.at(self.now - 5), "load1": 2}])
        got = board.host_samples(os.path.join(self.dir, "a.jsonl"))
        self.assertEqual([s["load1"] for _, s in got], [2])
        self.assertEqual(board.host_samples(os.path.join(self.dir, "missing.jsonl")), [])

    def test_polyline_breaks_and_limit(self):
        import board
        from core.config import Config
        n = self.now
        self.write("h.jsonl", [{"at": self.at(n - 100), "load1": 1.0, "cpus": 8, "slots": None, "agents": 1},
                               {"at": self.at(n - 1000), "load1": 2.0, "cpus": 4, "agents": 1}])
        html = board.render_host_tab(Config(self.proj), None, {}, n)
        svg = html.split('data-window="24h" data-chart="load"')[1].split("</svg>")[0]
        load1 = svg.split('data-series="load1"')[1].split("</g>")[0]
        self.assertEqual(load1.count("<polyline"), 2)                    # buckets 284 and 287, gap between
        self.assertIn('class="limit" data-value="8.00"', svg)           # the newest sample's cpus
        agents = html.split('data-window="24h" data-chart="agents"')[1].split("</svg>")[0]
        self.assertNotIn('class="limit"', agents)                        # slots null: no limit line

    def test_order_and_empty(self):
        import board
        from core.config import Config
        self.assertIn("還沒有主機紀錄", board.render_host_tab(Config(self.proj), None, {}, self.now))
        self.write("zeta.jsonl", [{"at": self.at(self.now - 10)}])
        self.write("alpha.jsonl", [{"at": self.at(self.now - 10)}])
        self.write("notes.txt", ["x"])
        html = board.render_host_tab(Config(self.proj), None, {}, self.now)
        self.assertLess(html.index('data-host="alpha"'), html.index('data-host="zeta"'))
        self.assertEqual(html.count('<section class="host"'), 2)
        self.assertIn(board.UNKNOWN, html)


if __name__ == "__main__":
    unittest.main()
