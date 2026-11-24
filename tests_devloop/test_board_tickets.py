"""the board's tabs and nav, the 票 tab, its filters, stuck categories and detail views"""

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


class T26Acceptance(LoopCase):
    """Ticket #26: a merged ticket reads 功能完成 and a landed one 驗證完成 on the board and in
    tickets.py list/show, while the pool keeps the names merged / landed (ruling D-089 item 1)."""

    def setUp(self):
        super().setUp()
        self.add(1)
        self.add(2)
        self.add(3)

        def change(tx):
            for ident, state in (("1", "merged"), ("2", "landed")):
                t = tx.ticket(ident)
                t["state"] = state
                tx.put_ticket(t)
        self.pool.update(change, "test: #1 merged, #2 landed")

    def line_of(self, out, ident):
        for line in out.splitlines():
            fields = line.split()
            if fields and fields[0] == "#%s" % ident:
                return fields
        self.fail("no line for #%s in:\n%s" % (ident, out))

    def show(self, ident):
        res = self.run_py("tickets.py", "show", str(ident))
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def test_item1_board_state_cells(self):
        res = self.run_py("board.py", "html", "--query", "done=1")
        self.assertEqual(res.returncode, 0, res.stderr)
        page = res.stdout
        self.assertIn("<td>#1</td><td>功能完成</td>", page)
        self.assertIn("<td>#2</td><td>驗證完成</td>", page)
        self.assertNotIn("已合進整合分支", page)

    def test_item2_list_state_column(self):
        res = self.run_py("tickets.py", "list")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.line_of(res.stdout, 1)[1], "功能完成")
        res = self.run_py("tickets.py", "list", "--all")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.line_of(res.stdout, 2)[1], "驗證完成")

    def test_item3_show_state_text(self):
        for ident, text, stored in (("1", "功能完成", "merged"), ("2", "驗證完成", "landed"),
                                    ("3", "ready", "ready")):
            out = self.show(ident)
            self.assertIn("state_text", out, "show %s has a top-level state_text" % ident)
            self.assertEqual(out["state_text"], text, "show %s" % ident)
            self.assertEqual(out["ticket"]["state"], stored, "show %s keeps the stored state" % ident)

    def test_item4_readme_list_show_row(self):
        with open(os.path.join(HOME, "devloop", "README.md")) as fh:
            readme = fh.read()
        section = readme.split("## What the main session runs", 1)[1].split("\n## ", 1)[0]
        rows = [line for line in section.splitlines() if "`python3 devloop/tickets.py list`" in line]
        self.assertEqual(len(rows), 1, "one list / show N row in 'What the main session runs'")
        row = rows[0]
        self.assertIn("show N", row)
        self.assertIn("功能完成", row)
        self.assertIn("驗證完成", row)
        self.assertIn("merged", row)
        self.assertIn("landed", row)


class T38Acceptance(LoopCase):
    """Ticket #38: the board is four tabs (票 DAG Token 主機), each at its own relative ?tab= URL."""

    TABS = ("tickets", "dag", "tokens", "host")
    NAV = [("要你判", "?tab=decide"), ("進行中", "?tab=live"), ("票", "?tab=tickets"), ("DAG", "?tab=dag"), ("Token", "?tab=tokens"), ("主機", "?tab=host")]

    def setUp(self):
        super().setUp()
        self.add(1)
        self.add(2, deps=[1])

    def board(self):
        import inspect
        import board
        params = inspect.signature(board.render).parameters
        for name in ("key", "query", "now"):
            self.assertIn(name, params, "render() takes %s" % name)
        return board

    def page(self, query=None, key=None, now=None):
        from core.config import Config
        return self.board().render(Config(self.proj), self.snap(), key=key, query=query, now=now)

    @staticmethod
    def ids(page):
        import re
        return re.findall(r"""\bid=["']([^"']+)["']""", page)

    @staticmethod
    def parse(page, tab_id):
        """Walk the page: inside/outside text of the element with id tab_id, card count outside it,
        the <a> links of every <nav class="tabs">, every href/action attribute value."""
        from html.parser import HTMLParser

        class P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.depth = 0          # >0 while inside the tab element
                self.inside, self.outside = [], []
                self.cards_out = self.cards_in = 0
                self.navs = []          # list of link lists
                self.in_nav = 0
                self.link = None
                self.urls = []

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                for k, v in attrs:
                    if k in ("href", "action"):
                        self.urls.append(v or "")
                if self.depth:
                    self.depth += 1
                elif a.get("id") == tab_id:
                    self.depth = 1
                if "card" in (a.get("class") or "").split():
                    if self.depth:
                        self.cards_in += 1
                    else:
                        self.cards_out += 1
                if tag == "nav":
                    if "tabs" in (a.get("class") or "").split():
                        self.navs.append([])
                        self.in_nav = 1
                    elif self.in_nav:
                        self.in_nav += 1
                if tag == "a" and self.in_nav:
                    self.link = [a.get("href"), a.get("aria-current"), ""]
                    self.navs[-1].append(self.link)

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)
                self.handle_endtag(tag)

            def handle_endtag(self, tag):
                if self.depth:
                    self.depth -= 1
                if tag == "nav" and self.in_nav:
                    self.in_nav -= 1
                if tag == "a":
                    self.link = None

            def handle_data(self, data):
                (self.inside if self.depth else self.outside).append(data)
                if self.link is not None:
                    self.link[2] += data

        p = P()
        p.feed(page)
        p.close()
        return p

    def test_item1_query_picks_exactly_one_tab(self):
        for t in self.TABS:
            with self.subTest(tab=t):
                ids = self.ids(self.page(query={"tab": t}))
                self.assertEqual(ids.count("tab-" + t), 1, ids)
                for other in self.TABS:
                    if other != t:
                        self.assertNotIn("tab-" + other, ids)
        for q in (None, {}, {"tab": ""}, {"tab": "nope"}):
            with self.subTest(query=q):
                ids = self.ids(self.page(query=q))
                self.assertEqual(ids.count("tab-live"), 1, ids)
                for other in ("tickets", "dag", "tokens", "host"):
                    self.assertNotIn("tab-" + other, ids)

    def test_item2_one_nav_with_four_links_and_the_current_one_marked(self):
        import re
        for t in self.TABS:
            for key in (None, "k"):
                with self.subTest(tab=t, key=key):
                    page = self.page(query={"tab": t}, key=key)
                    self.assertEqual(len(re.findall(r'<nav class="tabs">', page)), 1, "one <nav class=\"tabs\">")
                    self.assertEqual(len(re.findall(r"<nav\b", page)), 1)
                    p = self.parse(page, "tab-" + t)
                    self.assertEqual(len(p.navs), 1)
                    links = p.navs[0]
                    self.assertEqual([(text.strip(), href) for href, _, text in links], self.NAV)
                    current = [href for href, aria, _ in links if aria is not None]
                    self.assertEqual(current, ["?tab=" + t])
                    self.assertEqual([aria for _, aria, _ in links if aria is not None], ["page"])

    def test_item3_the_four_numbers_are_on_every_tab_outside_the_tab(self):
        for t in self.TABS:
            with self.subTest(tab=t):
                page = self.page(query={"tab": t})
                p = self.parse(page, "tab-" + t)
                self.assertIn("tab-" + t, self.ids(page))
                self.assertIn("四個指標", "".join(p.outside))
                self.assertNotIn("四個指標", "".join(p.inside))
                self.assertEqual((p.cards_out, p.cards_in), (5, 0), "five cards, none inside tab-" + t)
                for label in ("每張落地票的 token", "每張落地票的時間", "一次就過", "每張票要你介入幾次", "等你處理的"):
                    self.assertIn(label, "".join(p.outside))

    def test_item5_every_link_and_action_is_relative(self):
        for t in self.TABS:
            for key in (None, "k"):
                with self.subTest(tab=t, key=key):
                    page = self.page(query={"tab": t}, key=key)
                    for _, href in self.NAV:
                        self.assertIn('href="%s"' % href, page.replace("'", '"'))
                    p = self.parse(page, "tab-" + t)
                    self.assertEqual(len(p.navs), 1, "the tab nav is there")
                    for v in p.urls:
                        self.assertFalse(v.startswith("/") or "://" in v, v)

    def test_item6_now_is_the_page_clock(self):
        import re
        for t in self.TABS:
            with self.subTest(tab=t):
                page = self.page(query={"tab": t}, now=0)
                header = re.search(r"<header\b.*?</header>", page, re.S)
                self.assertIsNotNone(header)
                self.assertIn("1970-01-01T00:00:00Z", header.group(0))
                page = self.page(query={"tab": t}, now=86400 * 365)
                self.assertIn("1971-01-01T00:00:00Z", re.search(r"<header\b.*?</header>", page, re.S).group(0))
        before = time.strftime("%Y-%m-%d", time.gmtime())
        page = self.page()
        self.assertTrue(before in page or time.strftime("%Y-%m-%d", time.gmtime()) in page,
                        "now=None is the current time")
        self.assertNotIn("1970-01-01T00:00:00Z", page)

    def test_item7_html_takes_a_query(self):
        res = self.run_py("board.py", "html", "--query", "tab=dag")
        self.assertEqual(res.returncode, 0, res.stderr)
        ids = self.ids(res.stdout)
        self.assertIn("tab-dag", ids)
        self.assertNotIn("tab-tickets", ids)
        out = os.path.join(self.tmp, "host.html")
        res = self.run_py("board.py", "html", "--out", out, "--query", "tab=host")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.isfile(out))
        with open(out) as fh:
            ids = self.ids(fh.read())
        self.assertIn("tab-host", ids)
        self.assertNotIn("tab-tickets", ids)
        res = self.run_py("board.py", "html")
        self.assertEqual(res.returncode, 0, res.stderr)
        ids = self.ids(res.stdout)
        self.assertIn("tab-live", ids)
        self.assertNotIn("tab-tickets", ids)
        self.assertNotIn("tab-dag", ids)

    def test_item8_served_tabs_under_any_prefix(self):
        import http.client
        import socket
        key = os.path.join(self.tmp, "key")
        with open(key, "w") as fh:
            fh.write("s3cret\n")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "board.py"), "serve", "--port", str(port),
                                 "--key-file", key], cwd=self.proj, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(proc.kill)

        def call(method, path, body=None, retry=False):
            deadline = time.time() + 5
            while True:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                try:
                    headers = {"X-Board-Key": "s3cret"}
                    if body is not None:
                        headers["Content-Type"] = "application/x-www-form-urlencoded"
                    conn.request(method, path, body=body, headers=headers)
                    resp = conn.getresponse()
                    return resp.status, resp.read().decode("utf-8")
                except OSError:
                    if not retry or time.time() > deadline:
                        raise
                    time.sleep(0.1)
                finally:
                    conn.close()
        call("GET", "/", retry=True)
        for path in ("/?tab=tokens", "/index.html?tab=tokens", "/board/?tab=tokens", "/a/b/?tab=tokens"):
            with self.subTest(path=path):
                status, page = call("GET", path)
                self.assertEqual(status, 200, path)
                ids = self.ids(page)
                self.assertIn("tab-tokens", ids)
                self.assertNotIn("tab-tickets", ids)
        self.assertEqual(call("GET", "/board/other")[0], 404)
        web = os.path.join(self.tmp, "wt", "runs", "web")

        def folders():
            return set(os.listdir(web)) if os.path.isdir(web) else set()
        self.assertEqual(call("POST", "/request", "text=x")[0], 303)
        before = folders()
        self.assertEqual(call("POST", "/board/request", "text=x")[0], 303)
        self.assertTrue(folders() - before, "a new job folder under runs/web/")


class BoardTabs(LoopCase):
    """Helpers of the board's tabs (#38): the tab a query picks, the nav, the clock, routing."""

    def test_shown_tab_falls_back_to_the_first_tab(self):
        import board
        self.assertEqual([t[0] for t in board.TABS], ["decide", "live", "tickets", "dag", "tokens", "host"])
        for query in (None, {}, {"tab": ""}, {"tab": "nope"}, {"other": "dag"}):
            self.assertEqual(board.shown_tab(query), "tickets", query)
        self.assertEqual(board.shown_tab({"tab": "host"}), "host")

    def test_render_nav_marks_only_the_shown_tab(self):
        import board
        nav = board.render_nav("tokens")
        self.assertEqual(nav.count("aria-current"), 1)
        self.assertIn('<a href="?tab=tokens" aria-current="page">Token</a>', nav)
        self.assertIn('<a href="?tab=host">主機</a>', nav)

    def test_clock_is_the_pool_format(self):
        import board
        self.assertEqual(board.clock(0), "1970-01-01T00:00:00Z")
        self.assertEqual(board.clock(86400 + 61), "1970-01-02T00:01:01Z")

    def test_last_segment(self):
        import board
        from urllib.parse import urlparse
        for path, want in (("/", ""), ("/index.html", "index.html"), ("/board/", ""), ("/a/b/request", "request"),
                           ("/board/other", "other")):
            self.assertEqual(board.last_segment(urlparse(path + "?tab=x")), want, path)

    def test_tabs_get_their_query_and_now(self):
        # #43 and #45 replaced the stub Token and 主機 tabs; each still gets render's now.
        import board
        import time
        from core.config import Config
        page = board.render(Config(self.proj), self.snap(), query={"tab": "tokens"}, now=0)
        self.assertIn('<div id="tab-tokens" class="tab">', page)
        self.assertIn("<h2>近 30 天</h2>", page)
        self.assertIn("<tr><td>%s</td>" % time.strftime("%Y-%m-%d", time.localtime(0)), page)
        self.assertNotIn("<h2>記憶</h2>", page)
        page = board.render(Config(self.proj), self.snap(), query={"tab": "host"}, now=0)
        self.assertIn('<div id="tab-host" class="tab">', page)
        self.assertIn("<h2>主機</h2>", page)
        self.assertIn("還沒有主機紀錄", page)
        self.assertNotIn("<h2>記憶</h2>", page)

    def test_served_tabs_within_five_seconds_each_show_their_own(self):
        import http.client
        import socket
        import time
        key = os.path.join(self.tmp, "key")
        with open(key, "w") as fh:
            fh.write("k1\n")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "board.py"), "serve", "--port", str(port),
                                 "--key-file", key], cwd=self.proj, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(proc.kill)

        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request("GET", path, headers={"X-Board-Key": "k1"})
                resp = conn.getresponse()
                return resp.status, resp.read().decode("utf-8")
            finally:
                conn.close()
        for _ in range(50):
            try:
                get("/")
                break
            except OSError:
                time.sleep(0.1)
        self.assertIn('id="tab-dag"', get("/x/?tab=dag")[1])
        self.assertIn('id="tab-host"', get("/x/index.html?tab=host")[1])
        self.assertEqual(get("/x/nothing")[0], 404)


class BoardWaitingCount(LoopCase):
    """The 等你處理的 number leaves out unread created pages (GitHub issue #10)."""

    @staticmethod
    def snap():
        from core.pool import Snapshot
        inbox = {str(i): {"kind": k, "acked": a} for i, (k, a) in enumerate(
            [("created", False), ("created", False), ("decision", False), ("env", False),
             ("landed", False), ("decision", True)])}
        return Snapshot({"dag": {"tickets": {}}, "tickets": {"1": {"id": "1", "state": "stuck"}}, "groups": {},
                         "inbox": inbox, "lessons": {}, "memory_state": {}})

    def test_waiting_skips_created_pages(self):
        import board
        self.assertEqual(board.metrics(self.snap())["waiting"], "3 頁 + 1 張卡住")

    def test_card_note_says_created_is_left_out(self):
        import board
        self.assertIn("未讀頁(created 除外)+ 卡住的票", board.render_metrics(self.snap()))


class T39Acceptance(LoopCase):
    """#39: a stuck or quota-waiting ticket or group shows why as one of eight categories.
    Expected values come from the ticket's acceptance items and the texts it quotes, not from board.py."""

    LABELS = {"quota": "額度用完", "slot": "等機器名額", "timeout": "逾時", "red": "紅燈",
              "decision": "要你決定", "env": "環境", "conflict": "合併衝突", "other": "其他"}
    SLOT_WHY = "no machine slot came free within 120 s (devloop.machine_slots 1, all busy)"

    def board(self, *names):
        import board
        for name in names:
            self.assertTrue(hasattr(board, name), "board.%s is missing" % name)
        return board

    def ticket_category(self):
        import inspect
        board = self.board("ticket_category")
        self.assertEqual(len(inspect.signature(board.ticket_category).parameters), 1)
        return board.ticket_category

    def group_category(self):
        import inspect
        board = self.board("group_category")
        self.assertEqual(len(inspect.signature(board.group_category).parameters), 2)
        return board.group_category

    @staticmethod
    def stuck(reason, text, ident="1", round_=3, feedback=(), cost=None):
        t = {"id": ident, "state": "stuck", "round": round_, "feedback": list(feedback),
             "stuck": {"reason": reason, "text": text}}
        if cost is not None:
            t["cost"] = cost
        return t

    @staticmethod
    def snapshot(tickets=None, groups=None, inbox=None):
        from core.pool import Snapshot
        return Snapshot({"dag": {"tickets": {}}, "tickets": tickets or {}, "groups": groups or {},
                         "inbox": inbox or {}, "lessons": {}, "memory_state": {}})

    @staticmethod
    def group(gid, state, events=(), members=(), **extra):
        g = {"id": gid, "state": state, "branch": "int/" + gid, "members": list(members), "fix_round": 0,
             "history": [{"at": "2026-11-06T10:%02d:00" % i, "event": ev} for i, ev in enumerate(events)]}
        g.update(extra)
        return g

    # 1
    def test_category_text_in_order(self):
        board = self.board("CATEGORY_TEXT")
        self.assertEqual(board.CATEGORY_TEXT, self.LABELS)
        self.assertEqual(list(board.CATEGORY_TEXT), list(self.LABELS))

    # 2
    def test_quota_wait_and_not_stuck(self):
        cat = self.ticket_category()
        waiting = {"reason": "quota", "since": "2026-11-06T10:00:00", "until": "2026-11-06T15:00:00",
                   "text": "resets 3pm"}
        self.assertEqual(cat({"id": "1", "state": "running", "round": 1, "feedback": [], "waiting": waiting}), "quota")
        for state in ("ready", "running", "merged", "landed", "cancelled"):
            with self.subTest(state=state):
                self.assertIsNone(cat({"id": "1", "state": state, "round": 1, "feedback": []}))

    # 3
    def test_stuck_reasons(self):
        cat = self.ticket_category()
        cases = [
            (self.stuck("quota", "out of quota"), "quota"),
            (self.stuck("env", "git worktree add failed"), "env"),
            (self.stuck("decision", "the fixer asks"), "decision"),
            (self.stuck("hard_block", "touches docs/DECISIONS.md"), "decision"),
            (self.stuck("timeout", "issuer ran longer than 120 s waiting for a test run: " + self.SLOT_WHY), "slot"),
            (self.stuck("timeout", "issuer ran longer than 10800 s"), "timeout"),
            (self.stuck("timeout", "issuer ran longer than 10800 s",
                        feedback=["round 3: " + self.SLOT_WHY]), "slot"),
            (self.stuck("error", "worker crashed"), "other"),
            (self.stuck("target", "no target"), "other"),
            (self.stuck("missing", "branch missing"), "other"),
        ]
        for t, want in cases:
            with self.subTest(reason=t["stuck"]["reason"], text=t["stuck"]["text"], feedback=t["feedback"]):
                self.assertEqual(cat(t), want)

    # 4
    def test_rounds(self):
        cat = self.ticket_category()
        rounds = "3 rounds used and still not passing; last feedback:\n%s"
        latest = ("merging the latest int/g6 into t/27 failed (conflict) - another ticket of this group changed "
                  "the same files; merge int/g6 yourself and resolve")
        verifier = "merging the verifier's branch t/5-cases into t/5 failed (conflict); merge it yourself and resolve"
        cases = [
            ("latest conflict", self.stuck("rounds", rounds % latest), "conflict"),
            ("verifier conflict", self.stuck("rounds", rounds % verifier), "conflict"),
            ("conflict in feedback", self.stuck("rounds", rounds % "x", feedback=["round 3: " + latest]), "conflict"),
            ("conflict beats quota", self.stuck("rounds", rounds % latest,
                                                cost=[{"round": 3, "outcome": "out of quota"}]), "conflict"),
            ("merging without failed", self.stuck("rounds", rounds % "merging the latest int/g6 into t/27 went fine"),
             "red"),
            ("quota this round", self.stuck("rounds", rounds % "verifier cases: 2 failed",
                                            cost=[{"round": 3, "outcome": "out of quota"}]), "quota"),
            ("quota outcome", self.stuck("rounds", rounds % "verifier cases: 2 failed",
                                         cost=[{"round": 3, "outcome": "quota"}]), "quota"),
            ("quota earlier round", self.stuck("rounds", rounds % "verifier cases: 2 failed",
                                               cost=[{"round": 2, "outcome": "out of quota"},
                                                     {"round": 3, "outcome": "done"}]), "red"),
            ("quota beats slot", self.stuck("rounds", rounds % self.SLOT_WHY,
                                            cost=[{"round": 3, "outcome": "out of quota"}]), "quota"),
            ("slot in feedback", self.stuck("rounds", rounds % "verifier cases: 2 failed", feedback=[
                "round 3: no machine slot came free within 60 s (devloop.machine_slots 1, all busy)"]), "slot"),
            ("red", self.stuck("rounds", rounds % "verifier cases: 2 failed"), "red"),
        ]
        for name, t, want in cases:
            with self.subTest(name):
                self.assertEqual(cat(t), want)

    # 5
    def test_group_quota_and_stuck_events(self):
        cat = self.group_category()
        waiting = {"reason": "quota", "since": "2026-11-06T10:00:00", "until": "2026-11-06T15:00:00", "text": "t"}
        g = self.group("g2", "fixing", ["fix round 1"], waiting=waiting)
        self.assertEqual(cat(self.snapshot(groups={"g2": g}), g), "quota")
        cases = [
            (["fix round 3", "red after 3 fix rounds"], "red"),
            (["fix round 1", "fixer needs a decision"], "decision"),
            (["opened from main abc", "main kept moving"], "conflict"),
            (["main kept moving", "red after 3 fix rounds"], "red"),
            (["red after 3 fix rounds", "main kept moving", "resumed by you"], "conflict"),
            (["opened from main abc"], "other"),
            ([], "other"),
        ]
        for events, want in cases:
            with self.subTest(events=events):
                g = self.group("g2", "stuck", events)
                self.assertEqual(cat(self.snapshot(groups={"g2": g}), g), want)

    def test_group_no_ticket_merged_takes_first_stuck_member(self):
        cat = self.group_category()
        red = self.stuck("rounds", "3 rounds used and still not passing; last feedback:\nverifier cases: 2 failed",
                         ident="4")
        env = self.stuck("env", "git worktree add failed", ident="5")
        g = self.group("g3", "stuck", ["no ticket of the group got merged"], members=["5", "10", "4"])
        tickets = {"4": red, "5": env, "10": {"id": "10", "state": "cancelled", "round": 1, "feedback": []}}
        self.assertEqual(cat(self.snapshot(tickets=tickets, groups={"g3": g}), g), "red")
        # numeric order: 9 before 10
        t9 = self.stuck("timeout", "issuer ran longer than 10800 s", ident="9")
        t10 = self.stuck("env", "git worktree add failed", ident="10")
        g = self.group("g3", "stuck", ["no ticket of the group got merged"], members=["10", "9"])
        self.assertEqual(cat(self.snapshot(tickets={"9": t9, "10": t10}, groups={"g3": g}), g), "timeout")
        g = self.group("g3", "stuck", ["no ticket of the group got merged"], members=["4"])
        merged = {"4": {"id": "4", "state": "cancelled", "round": 1, "feedback": []}}
        self.assertEqual(cat(self.snapshot(tickets=merged, groups={"g3": g}), g), "other")

    def test_group_pages(self):
        cat = self.group_category()
        g = self.group("g2", "pr", ["opened from main abc"])
        page = lambda kind, acked=False, ref="g2": {"kind": kind, "ref": ref, "at": "2026-11-06T10:00:00",
                                                    "text": "x", "acked": acked}
        cases = [
            ({"p1": page("env")}, "env"),
            ({"p1": page("env", acked=True)}, None),
            ({"p1": page("decision")}, "decision"),
            ({"p1": page("decision"), "p2": page("env")}, "env"),
            ({"p1": page("env", ref="g7")}, None),
            ({"p1": page("landed")}, None),
            ({}, None),
        ]
        for inbox, want in cases:
            with self.subTest(inbox=inbox):
                self.assertEqual(cat(self.snapshot(groups={"g2": g}, inbox=inbox), g), want)

    # 6, 7
    def render(self):
        from core.config import Config
        board = self.board("CATEGORY_TEXT", "ticket_category", "group_category")
        return board.render(Config(self.proj), self.snap(), query={"tab": "tickets"})

    @staticmethod
    def cells(page, needle):
        # needle is a '<td>' cell; rfind('<tr', 0, at) then finds this row's own '<tr'
        import re
        at = page.find(needle)
        if at < 0:
            return None
        row = page[page.rfind("<tr", 0, at):page.find("</tr>", at)]
        return re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)

    def test_ticket_note_starts_with_category(self):
        import html
        text = "3 rounds used and still not passing; last feedback:\nverifier cases: 2 failed"
        self.add(1, state="stuck", round=3, stuck={"reason": "rounds", "text": text})
        self.add(2, state="stuck", round=1, stuck={"reason": "hard_block", "text": "touches docs/DECISIONS.md"})
        self.add(3, state="running", round=1, run={"started": "2026-11-06T10:00:00"},
                 waiting={"reason": "quota", "since": "2026-11-06T10:00:00", "until": "2026-11-06T15:00:00",
                          "text": "resets 3pm"})
        page = self.render()
        heads = ["票", "狀態", "輪", "整合分支", "相依", "驗證者", "覆核", "token", "快取讀", "快取寫",
                 "分鐘", "估 token", "說明", "標題"]
        note = heads.index("說明")
        state = heads.index("狀態")
        for ident, label, stored in (("1", "紅燈", text), ("2", "要你決定", "touches docs/DECISIONS.md"),
                                     ("3", "額度用完", "")):
            with self.subTest(ticket=ident):
                cells = self.cells(page, "<td>#%s</td>" % ident)
                self.assertIsNotNone(cells)
                cell = html.unescape(cells[note])
                self.assertTrue(cell.startswith(label + ":" + stored), cell)
        self.assertEqual(html.unescape(self.cells(page, "<td>#1</td>")[state]), "卡住")

    def test_group_table_reason_column(self):
        import html
        page = self.render()
        start = page.find("<h2>整合分支")
        self.assertGreaterEqual(start, 0)
        self.assertIn("<th>狀態</th><th>原因</th>", page[start:page.find("</tr>", start)])

        def group(gid, state, events, **extra):
            def change(tx):
                tx.put_group(self.group(gid, state, events, **extra))
            self.pool.update(change, "test: group %s" % gid)
        group("g1", "stuck", ["fix round 3", "red after 3 fix rounds"])
        group("g2", "stuck", ["main kept moving"])
        group("g3", "fixing", ["fix round 1"], waiting={"reason": "quota", "since": "2026-11-06T10:00:00",
                                                        "until": "2026-11-06T15:00:00", "text": "t"})
        group("g4", "pr", ["opened from main abc"])
        page = self.render()
        start = page.find("<h2>整合分支")
        part = page[start:]
        heads = ["組", "狀態", "原因"]
        for gid, want in (("g1", "紅燈"), ("g2", "合併衝突"), ("g3", "額度用完"), ("g4", "")):
            with self.subTest(group=gid):
                cells = self.cells(part, "<td>%s</td>" % gid)
                self.assertIsNotNone(cells)
                self.assertEqual(html.unescape(cells[heads.index("原因")]).strip(), want)


class StuckCategoryHelpers(LoopCase):
    def test_merge_failed_needs_lead_then_failed(self):
        import board
        self.assertTrue(board.merge_failed("merging the latest a into b failed (conflict) x"))
        self.assertTrue(board.merge_failed("merging the verifier's branch a into b failed (x)"))
        self.assertFalse(board.merge_failed("failed ( then merging the latest a"))
        self.assertFalse(board.merge_failed("2 failed"))

    def test_ticket_category_uses_last_feedback(self):
        import board
        t = {"state": "stuck", "stuck": {"reason": "rounds", "text": "3 rounds"},
             "feedback": ["round 3: no machine slot came free within 60 s"]}
        self.assertEqual(board.ticket_category(t), "slot")
        self.assertIsNone(board.ticket_category({"state": "ready"}))


class T40Acceptance(LoopCase):
    """#40: the 票 tab filters tickets and integration branches by the URL's state, group, cat, q
    and done. Expected values come from the ticket and this fixture, not from board.py."""

    STATE_OPTIONS = [("", "全部"), ("ready", "待派"), ("running", "做中"), ("merged", "功能完成"),
                     ("landed", "驗證完成"), ("stuck", "卡住"), ("cancelled", "取消")]
    CATEGORY_KEYS = ["quota", "slot", "timeout", "red", "decision", "env", "conflict", "other"]

    def setUp(self):
        super().setUp()
        self.add(1, state="landed", group="g1", title="Landed work")
        self.add(2, state="cancelled", title="Dropped idea")
        self.add(3, state="ready", title="Queued item")
        self.add(4, state="running", group="g2", title="Running item")
        self.add(5, state="stuck", group="g2", title="Merge trouble",
                 stuck={"reason": "rounds", "text": "merging the latest main into t/5 failed (conflict in a.py)"})
        self.add(6, state="stuck", title="Broken host", stuck={"reason": "env", "text": "python3 is missing"})
        self.add(7, state="merged", group="g2", title="Fix Board Filters")

        def groups(tx):
            tx.put_group({"id": "g1", "branch": "int/g1", "state": "landed", "members": ["1", "8"],
                          "fix_round": 0, "history": []})
            tx.put_group({"id": "g2", "branch": "int/g2", "state": "open", "members": ["4", "5", "7"],
                          "fix_round": 0, "history": []})
            tx.put_group({"id": "g3", "branch": "int/g3", "state": "stuck", "members": [], "fix_round": 3,
                          "history": [{"at": "2026-11-01T00:00:00Z", "event": "red after 3 fix rounds"}]})
        self.pool.update(groups, "test: groups g1 landed, g2 open, g3 stuck")

    # ------------------------------------------------------------ helpers

    def page(self, **query):
        import inspect
        import board
        from core.config import Config
        self.assertTrue(hasattr(board, "render"), "board.render is missing")
        self.assertIn("query", inspect.signature(board.render).parameters, "board.render takes query")
        q = {"tab": "tickets"}
        q.update(query)
        return board.render(Config(self.proj), self.snap(), query=q)

    def table(self, page, header):
        i = page.find(header)
        self.assertGreaterEqual(i, 0, "no table with %s" % header)
        start = page.rfind("<table", 0, i)
        return page[start:page.find("</table>", i)]

    def tickets(self, page):
        part = self.table(page, "<th>票</th>")
        return {str(n) for n in range(1, 8) if "<td>#%d</td>" % n in part}

    def groups(self, page):
        part = self.table(page, "<th>組</th>")
        return {g for g in ("g1", "g2", "g3") if "<td>%s</td>" % g in part}

    def form(self, page):
        from html.parser import HTMLParser

        class Collect(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.forms, self.cur, self.select, self.option = [], None, None, None

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "form":
                    self.cur = {"attrs": a, "inputs": [], "selects": {}, "text": ""}
                    self.forms.append(self.cur)
                elif self.cur is None:
                    return
                elif tag == "input":
                    self.cur["inputs"].append(a)
                elif tag == "select":
                    self.select = self.cur["selects"].setdefault(a.get("name"), [])
                elif tag == "option" and self.select is not None:
                    self.option = {"value": a.get("value"), "selected": "selected" in a, "label": ""}
                    self.select.append(self.option)

            def handle_endtag(self, tag):
                if tag == "form":
                    self.cur = None
                elif tag == "select":
                    self.select = None
                elif tag == "option":
                    self.option = None

            def handle_data(self, data):
                if self.option is not None:
                    self.option["label"] += data
                if self.cur is not None:
                    self.cur["text"] += data

        c = Collect()
        c.feed(page)
        found = [f for f in c.forms if "filters" in (f["attrs"].get("class") or "").split()]
        self.assertEqual(len(found), 1, "one <form class=\"filters\"> on the 票 tab")
        f = found[0]
        for o in [o for opts in f["selects"].values() for o in opts]:
            o["label"] = o["label"].strip()
            if o["value"] is None:
                o["value"] = o["label"]
        return f

    def options(self, form, name):
        self.assertIn(name, form["selects"], "<select name=%r> in the filter form" % name)
        return form["selects"][name]

    def selected(self, form, name):
        return [o["value"] for o in self.options(form, name) if o["selected"]]

    def input_named(self, form, name, type_):
        rows = [i for i in form["inputs"] if i.get("name") == name]
        self.assertEqual(len(rows), 1, "one input name=%r" % name)
        self.assertEqual((rows[0].get("type") or "").lower(), type_)
        return rows[0]

    # ------------------------------------------------------------ item 1

    def test_item1_form_controls(self):
        import board
        self.assertTrue(hasattr(board, "CATEGORY_TEXT"), "board.CATEGORY_TEXT is missing")
        f = self.form(self.page())
        self.assertEqual((f["attrs"].get("method") or "").lower(), "get")
        self.assertNotIn("action", f["attrs"], "the filter form has no action attribute")
        tab = self.input_named(f, "tab", "hidden")
        self.assertEqual(tab.get("value"), "tickets")
        self.assertEqual([(o["value"], o["label"]) for o in self.options(f, "state")], self.STATE_OPTIONS)
        self.assertEqual([(o["value"], o["label"]) for o in self.options(f, "group")],
                         [("", "全部"), ("g1", "g1"), ("g2", "g2"), ("g3", "g3"), ("none", "未分組")])
        self.assertEqual(list(board.CATEGORY_TEXT), self.CATEGORY_KEYS)
        self.assertEqual([(o["value"], o["label"]) for o in self.options(f, "cat")],
                         [("", "全部")] + [(k, board.CATEGORY_TEXT[k]) for k in self.CATEGORY_KEYS])
        self.input_named(f, "q", "search")
        done = self.input_named(f, "done", "checkbox")
        self.assertEqual(done.get("value"), "1")
        self.assertIn("顯示已落地與已取消", f["text"])

    def test_item1_controls_show_no_filter(self):
        f = self.form(self.page())
        for name in ("state", "group", "cat"):
            self.assertIn(self.selected(f, name), ([], [""]), "%s: nothing but 全部 selected" % name)
        self.assertIn(self.input_named(f, "q", "search").get("value"), (None, ""))
        self.assertNotIn("checked", self.input_named(f, "done", "checkbox"))

    def test_item1_controls_show_the_current_query(self):
        f = self.form(self.page(state="stuck", group="g2", cat="conflict", q="Board", done="1"))
        self.assertEqual(self.selected(f, "state"), ["stuck"])
        self.assertEqual(self.selected(f, "group"), ["g2"])
        self.assertEqual(self.selected(f, "cat"), ["conflict"])
        self.assertEqual(self.input_named(f, "q", "search").get("value"), "Board")
        self.assertIn("checked", self.input_named(f, "done", "checkbox"))
        f = self.form(self.page(group="none"))
        self.assertEqual(self.selected(f, "group"), ["none"])

    # ------------------------------------------------------------ item 2

    def test_item2_default_leaves_out_landed_and_cancelled_done_shows_all(self):
        self.assertEqual(self.tickets(self.page()), {"3", "4", "5", "6", "7"})
        self.assertEqual(self.tickets(self.page(done="1")), {"1", "2", "3", "4", "5", "6", "7"})

    def test_item2_state_landed_or_cancelled_without_done(self):
        self.assertEqual(self.tickets(self.page(state="landed")), {"1"})
        self.assertEqual(self.tickets(self.page(state="cancelled")), {"2"})

    # ------------------------------------------------------------ item 3

    def test_item3_state(self):
        self.assertEqual(self.tickets(self.page(state="stuck")), {"5", "6"})
        self.assertEqual(self.tickets(self.page(state="merged")), {"7"})

    def test_item3_group(self):
        self.assertEqual(self.tickets(self.page(group="g2")), {"4", "5", "7"})
        self.assertEqual(self.tickets(self.page(group="g1", done="1")), {"1"})

    def test_item3_group_none(self):
        self.assertEqual(self.tickets(self.page(group="none")), {"3", "6"})
        self.assertEqual(self.tickets(self.page(group="none", done="1")), {"2", "3", "6"})

    def test_item3_cat(self):
        self.assertEqual(self.tickets(self.page(cat="conflict")), {"5"})
        self.assertEqual(self.tickets(self.page(cat="env")), {"6"})

    def test_item3_q_title_ignores_case(self):
        self.assertEqual(self.tickets(self.page(q="board")), {"7"})
        self.assertEqual(self.tickets(self.page(q="FIX BOARD")), {"7"})

    def test_item3_q_id(self):
        self.assertEqual(self.tickets(self.page(q="#5")), {"5"})
        self.assertEqual(self.tickets(self.page(q="5")), {"5"})
        self.assertEqual(self.tickets(self.page(q="##5")), set(), "only one leading # is removed")

    def test_item3_filters_combine_with_and(self):
        self.assertEqual(self.tickets(self.page(state="stuck", group="g2")), {"5"})
        self.assertEqual(self.tickets(self.page(state="stuck", cat="env", group="g2")), set())

    # ------------------------------------------------------------ item 4

    def test_item4_count_line(self):
        self.assertIn("顯示 5 / 共 7 張票", self.page())
        self.assertIn("顯示 2 / 共 7 張票", self.page(state="stuck"))
        self.assertIn("顯示 7 / 共 7 張票", self.page(done="1"))
        page = self.page()
        self.assertLess(page.find("顯示 5 / 共 7 張票"), page.find("<th>票</th>"), "the count is above the table")

    # ------------------------------------------------------------ item 5

    def test_item5_groups_default_and_done(self):
        self.assertEqual(self.groups(self.page()), {"g2", "g3"})
        self.assertEqual(self.groups(self.page(done="1")), {"g1", "g2", "g3"})

    def test_item5_groups_by_group_and_cat(self):
        self.assertEqual(self.groups(self.page(group="g2")), {"g2"})
        self.assertEqual(self.groups(self.page(group="g1", done="1")), {"g1"})
        self.assertEqual(self.groups(self.page(cat="red")), {"g3"})
        self.assertEqual(self.groups(self.page(cat="conflict")), set())

    def test_item5_state_and_q_do_not_filter_groups(self):
        self.assertEqual(self.groups(self.page(state="ready")), {"g2", "g3"})
        self.assertEqual(self.groups(self.page(q="board")), {"g2", "g3"})
        self.assertEqual(self.groups(self.page(state="landed", done="1")), {"g1", "g2", "g3"})

    # ------------------------------------------------------------ item 6

    def test_item6_no_meta_refresh(self):
        import re
        for tab in ("tickets", "dag", "tokens", "host"):
            page = self.page(tab=tab)
            self.assertIsNone(re.search(r"http-equiv\s*=\s*[\"']?refresh", page, re.I), "tab=%s" % tab)


class BoardTicketFilters(LoopCase):
    """The 票 tab's filter helpers (ticket #40): ticket_filters, ticket_shown, group_shown, render_filters."""

    def setUp(self):
        super().setUp()
        import board
        self.board = board

    def f(self, **query):
        return self.board.ticket_filters(query)

    def test_ticket_filters_reads_the_query(self):
        self.assertEqual(self.f(), {"state": "", "group": "", "cat": "", "q": "", "done": False})
        self.assertEqual(self.f(state="stuck", group="g2", cat="env", q="  Fix ", done="1"),
                         {"state": "stuck", "group": "g2", "cat": "env", "q": "Fix", "done": True})
        self.assertFalse(self.f(done="yes")["done"])
        self.assertEqual(self.board.ticket_filters(None)["state"], "")

    def test_ticket_shown(self):
        shown = self.board.ticket_shown
        landed = {"id": "1", "state": "landed", "title": "A"}
        ready = {"id": "12", "state": "ready", "title": "Board Filters", "group": "g3"}
        env = {"id": "6", "state": "stuck", "title": "x", "stuck": {"reason": "env", "text": "t"}}
        self.assertFalse(shown(landed, self.f()))
        self.assertTrue(shown(landed, self.f(done="1")))
        self.assertTrue(shown(landed, self.f(state="landed")))
        self.assertFalse(shown(ready, self.f(state="landed")))
        self.assertTrue(shown(ready, self.f(group="g3")))
        self.assertFalse(shown(ready, self.f(group="g1")))
        self.assertFalse(shown(ready, self.f(group="none")))
        self.assertTrue(shown(env, self.f(group="none")))
        self.assertTrue(shown(env, self.f(cat="env")))
        self.assertFalse(shown(ready, self.f(cat="env")))
        self.assertTrue(shown(ready, self.f(q="board")))
        self.assertTrue(shown(ready, self.f(q="#12")))
        self.assertTrue(shown(ready, self.f(q="12")))
        self.assertFalse(shown(ready, self.f(q="1")), "an id matches whole, not as a prefix")
        self.assertFalse(shown(ready, self.f(q="##12")), "only one leading # is removed")
        self.assertFalse(shown(ready, self.f(q="board", group="g1")), "filters combine with AND")

    def test_group_shown_ignores_state_and_q(self):
        snap = self.snap()
        shown = self.board.group_shown
        landed = {"id": "g1", "state": "landed"}
        stuck = {"id": "g3", "state": "stuck", "history": [{"event": "red after 3 fix rounds"}]}
        self.assertFalse(shown(snap, landed, self.f()))
        self.assertFalse(shown(snap, landed, self.f(state="landed")))
        self.assertTrue(shown(snap, landed, self.f(done="1")))
        self.assertTrue(shown(snap, stuck, self.f(state="ready", q="nothing")))
        self.assertTrue(shown(snap, stuck, self.f(group="g3", cat="red")))
        self.assertFalse(shown(snap, stuck, self.f(group="g2")))
        self.assertFalse(shown(snap, stuck, self.f(cat="env")))

    def test_render_filters_marks_the_current_query(self):
        from unittest import mock
        snap = mock.Mock(groups={"g10": {}, "g2": {}, "g1": {}})
        page = self.board.render_filters(snap, self.f(state="stuck", group="g10", cat="env", q='a"b', done="1"))
        self.assertIn('<option value="stuck" selected>卡住</option>', page)
        self.assertIn('<option value="g10" selected>', page)
        self.assertIn('<option value="env" selected>環境</option>', page)
        self.assertIn('value="a&quot;b"', page)
        self.assertIn('name="done" value="1" checked', page)
        self.assertLess(page.index('value="g1"'), page.index('value="g2"'))
        self.assertLess(page.index('value="g2"'), page.index('value="g10"'))
        self.assertLess(page.index('value="g10"'), page.index('value="none"'))
        plain = self.board.render_filters(snap, self.f())
        self.assertNotIn(" checked", plain)
        self.assertEqual(plain.count(" selected"), 3, "the '' option of each select")


class T41Acceptance(LoopCase):
    """#41: ?ticket=N and ?group=gN open a detail view (timeline + cost table) inside the 票 tab.
    Expected values come from the ticket and this fixture, never from board.py."""

    COLUMNS = ["角色", "模型", "強度", "輪", "秒", "結果", "input", "output", "快取讀", "快取寫", "美元", "時間"]

    def setUp(self):
        super().setUp()
        self.add(1, state="stuck", round=2, group="g1", objective="Show one ticket in detail",
                 acceptance=["first acceptance item", "second acceptance item"],
                 stuck={"reason": "rounds", "text": "2 rounds used"},
                 feedback=["round 1: verifier cases: 1 failed"],
                 history=[{"at": "2026-11-01T10:00:00Z", "event": "opened"},
                          {"at": "2026-11-01T10:00:05Z", "event": "dispatched", "target": "int/g1"},
                          {"at": "2026-11-01T10:00:06Z", "event": "round start"},
                          {"at": "2026-11-01T10:05:00Z", "event": "cases ready"},
                          {"at": "2026-11-01T10:06:00Z", "event": "feedback", "round": 1}],
                 cost=[{"role": "worker", "model": "sonnet", "round": 1, "seconds": 290.4,
                        "at": "2026-11-01T10:04:56Z", "outcome": "done", "input_tokens": 10,
                        "output_tokens": 20, "cache_read_tokens": 30, "cache_write_tokens": 40, "usd": 0.5},
                       {"role": "verifier", "model": "opus", "round": 1, "seconds": 250,
                        "at": "2026-11-01T10:04:20Z", "outcome": "cases", "input_tokens": None,
                        "output_tokens": None, "cache_read_tokens": None, "cache_write_tokens": None,
                        "usd": None}])
        self.pool.update(lambda tx: tx.put_group({
            "id": "g1", "state": "fixing", "branch": "int/g1", "members": ["1"], "fix_round": 1,
            "last_regression": "regression said xyz-red", "release": "failed",
            "history": [{"at": "2026-11-01T10:30:00Z", "event": "regression started"},
                        {"at": "2026-11-01T10:40:00Z", "event": "fix round 1"}],
            "cost": [{"role": "fixer", "model": "opus", "round": 1, "seconds": 120.7,
                      "at": "2026-11-01T10:35:00Z", "outcome": "green", "input_tokens": 5,
                      "output_tokens": 6, "cache_read_tokens": 7, "cache_write_tokens": 8, "usd": 0.25}],
            "notes": [{"text": "use the new main", "by": "user", "at": "2026-11-01T11:00:00Z"}]}),
            "test: group g1 fixing")

    # ------------------------------------------------------------ helpers

    def page(self, query):
        import inspect
        import board
        from core.config import Config
        self.assertTrue(hasattr(board, "render"), "board.render is missing")
        self.assertIn("query", inspect.signature(board.render).parameters, "board.render takes query")
        return board.render(Config(self.proj), self.snap(), query=query)

    def section(self, page, attr, value):
        import re
        starts = [m for m in re.finditer(r'<section\b[^>]*>', page)
                  if re.search(r'\bclass="detail"', m.group(0))
                  and re.search(r'\b%s="%s"' % (attr, re.escape(value)), m.group(0))]
        self.assertEqual(len(starts), 1, 'one <section class="detail" %s="%s">' % (attr, value))
        start = starts[0].start()
        depth, pos = 0, start
        for m in re.finditer(r'<(/?)section\b', page[start:]):
            depth += -1 if m.group(1) else 1
            if depth == 0:
                pos = start + m.end()
                break
        self.assertGreater(pos, start, "the detail section is closed")
        return page[start:pos]

    def text(self, fragment):
        import html
        import re
        return html.unescape(re.sub(r"<[^>]+>", " ", fragment))

    def timeline(self, sec):
        """[(kind, at, text)] of the one <ol class="timeline">."""
        import re
        ols = re.findall(r'<ol\b[^>]*\bclass="timeline"[^>]*>(.*?)</ol>', sec, re.S)
        self.assertEqual(len(ols), 1, 'one <ol class="timeline">')
        out = []
        for attrs, body in re.findall(r'<li\b([^>]*)>(.*?)</li>', ols[0], re.S):
            kind = re.search(r'\bdata-kind="([^"]*)"', attrs)
            at = re.search(r'\bdata-at="([^"]*)"', attrs)
            self.assertTrue(kind and at, "every <li> has data-kind and data-at: %s" % attrs)
            out.append((kind.group(1), at.group(1), self.text(body)))
        return out

    def cost_table(self, sec):
        """Rows of the one <table class="cost">, each a list of cell texts (th and td)."""
        import re
        tables = re.findall(r'<table\b[^>]*\bclass="cost"[^>]*>(.*?)</table>', sec, re.S)
        self.assertEqual(len(tables), 1, 'one <table class="cost">')
        rows = []
        for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>', tables[0], re.S):
            rows.append([self.text(c).strip() for c in re.findall(r'<t[hd]\b[^>]*>(.*?)</t[hd]>', tr, re.S)])
        return rows

    def cells(self, rows):
        """Header row checked, then the other rows as {column: text}."""
        self.assertGreaterEqual(len(rows), 1, "the cost table has a header row")
        self.assertEqual(rows[0], self.COLUMNS, "cost table columns")
        return [dict(zip(self.COLUMNS, r)) for r in rows[1:]]

    def hrefs(self, page):
        import re
        return re.findall(r'''\bhref\s*=\s*["']([^"']*)["']''', page)

    # ------------------------------------------------------------ 1: the ticket's detail

    def test_item1_ticket_detail_in_tickets_tab(self):
        import re
        page = self.page({"ticket": "1"})
        tab = page.find('<div id="tab-tickets"')
        self.assertGreaterEqual(tab, 0, "the page shows the tab-tickets element")
        self.assertRegex(page, r'<a [^>]*aria-current="page"[^>]*>票</a>', "the nav marks 票 as current")
        sec = self.section(page, "data-ticket", "1")
        self.assertGreater(page.find(sec), tab, "the detail section is inside tab-tickets")
        self.assertEqual(len(re.findall(r'<section\b[^>]*\bclass="detail"', page)), 1, "one detail section")
        text = self.text(sec)
        for want in ("#1", "ticket 1", "卡住", "紅燈", "g1", "Show one ticket in detail",
                     "first acceptance item", "second acceptance item"):
            self.assertIn(want, text)
        rest = self.text(re.sub(r'<ol\b.*?</ol>|<table\b.*?</table>', "", sec, flags=re.S))
        self.assertRegex(rest, r"(?<![\d:-])2(?![\d:-])", "the round (2) is shown outside the timeline and table")

    # ------------------------------------------------------------ 2: timeline entries and order

    def test_item2_ticket_timeline_order(self):
        tl = self.timeline(self.section(self.page({"ticket": "1"}), "data-ticket", "1"))
        self.assertEqual([(k, a) for k, a, _ in tl], [
            ("event", "2026-11-01T10:00:00Z"), ("event", "2026-11-01T10:00:05Z"),
            ("run", "2026-11-01T10:00:05Z"), ("event", "2026-11-01T10:00:06Z"),
            ("run", "2026-11-01T10:00:10Z"), ("event", "2026-11-01T10:05:00Z"),
            ("event", "2026-11-01T10:06:00Z")])
        self.assertIn("dispatched", tl[1][2])
        self.assertIn("worker", tl[2][2])
        self.assertIn("verifier", tl[4][2])

    def test_item2_no_seconds_and_stored_order(self):
        self.add(2, history=[{"at": "2026-11-02T09:00:00Z", "event": "opened"},
                             {"at": "2026-11-02T09:00:00Z", "event": "dispatched"}],
                 cost=[{"role": "worker", "model": "sonnet", "round": 1, "at": "2026-11-02T09:00:00Z",
                        "outcome": "done"},
                       {"role": "reviewer", "model": "opus", "round": 1, "seconds": 30,
                        "at": "2026-11-02T09:00:30Z", "outcome": "ok"}])
        tl = self.timeline(self.section(self.page({"ticket": "2"}), "data-ticket", "2"))
        self.assertEqual([(k, a) for k, a, _ in tl], [("event", "2026-11-02T09:00:00Z")] * 2
                         + [("run", "2026-11-02T09:00:00Z")] * 2)
        self.assertIn("opened", tl[0][2])
        self.assertIn("dispatched", tl[1][2])
        self.assertIn("worker", tl[2][2])
        self.assertIn("reviewer", tl[3][2])

    # ------------------------------------------------------------ 3: what each entry shows

    def test_item3_entry_text(self):
        import re
        tl = self.timeline(self.section(self.page({"ticket": "1"}), "data-ticket", "1"))
        self.assertEqual(len(tl), 7)
        self.assertIn("int/g1", tl[1][2], "dispatched shows its target")
        self.assertIn("feedback", tl[6][2])
        self.assertIn("verifier cases: 1 failed", tl[6][2], "feedback round 1 shows the kept entry")
        worker = tl[2][2]
        for want in ("worker", "sonnet", "done"):
            self.assertIn(want, worker)
        self.assertRegex(worker, r"(?<![\d.])290 ?秒")
        self.assertNotIn("290.4", worker)
        verifier = tl[4][2]
        for want in ("verifier", "opus", "cases"):
            self.assertIn(want, verifier)
        self.assertTrue(re.search(r"(?<![\d.])250 ?秒", verifier), verifier)

    def test_item3_event_fields_and_feedback_round(self):
        self.add(3, feedback=["round 1: older-text-aaa", "round 2: newer-text-bbb"],
                 history=[{"at": "2026-11-03T08:00:00Z", "event": "revised", "round": 7,
                           "reason": "reason-qq", "target": "int/g7", "group": "g7", "note": "note-zz",
                           "source": "source-ww"},
                          {"at": "2026-11-03T08:01:00Z", "event": "feedback", "round": 2}])
        tl = self.timeline(self.section(self.page({"ticket": "3"}), "data-ticket", "3"))
        self.assertEqual(len(tl), 2)
        for want in ("revised", "7", "reason-qq", "int/g7", "g7", "note-zz", "source-ww"):
            self.assertIn(want, tl[0][2])
        self.assertIn("newer-text-bbb", tl[1][2])
        self.assertNotIn("older-text-aaa", tl[1][2], "only the entry of the event's own round")

    # ------------------------------------------------------------ 4: cost table

    def test_item4_ticket_cost_table(self):
        rows = self.cells(self.cost_table(self.section(self.page({"ticket": "1"}), "data-ticket", "1")))
        self.assertEqual(len(rows), 3, "two cost rows and 合計")
        w, v, total = rows
        self.assertEqual((w["角色"], w["模型"], w["結果"]), ("worker", "sonnet", "done"))
        self.assertEqual((v["角色"], v["模型"], v["結果"]), ("verifier", "opus", "cases"))
        self.assertEqual(w["強度"], "未知", "effort nobody recorded")
        self.assertEqual(w["輪"], "1")
        self.assertIn("290", w["秒"])
        for col, want in (("input", "10"), ("output", "20"), ("快取讀", "30"), ("快取寫", "40")):
            self.assertEqual(w[col], want, col)
        self.assertIn("0.5", w["美元"])
        self.assertIn("2026-11-01T10:04:56Z", w["時間"])
        for col in ("input", "output", "快取讀", "快取寫", "美元"):
            self.assertEqual(v[col], "未知", "verifier %s" % col)
        self.assertEqual(total["角色"], "合計")
        for col, want in (("input", "10"), ("output", "20"), ("快取讀", "30"), ("快取寫", "40")):
            self.assertEqual(total[col], want, "合計 %s" % col)
        self.assertIn("0.5", total["美元"])
        self.assertIn("540", total["秒"])

    # ------------------------------------------------------------ 5: the group's detail

    def test_item5_group_detail(self):
        import re
        page = self.page({"group": "g1"})
        self.assertGreaterEqual(page.find('<div id="tab-tickets"'), 0)
        sec = self.section(page, "data-group", "g1")
        text = self.text(sec)
        for want in ("g1", "修紅中", "int/g1", "regression said xyz-red", "use the new main"):
            self.assertIn(want, text)
        self.assertTrue("失敗" in text or "failed" in text, "the release is shown")
        member = re.search(r'<a\b[^>]*href="\?ticket=1"[^>]*>.*?</a>', sec, re.S)
        self.assertTrue(member, "member #1 is a link ?ticket=1")
        self.assertIn("卡住", text, "member's state label")
        tl = self.timeline(sec)
        self.assertEqual([(k, a) for k, a, _ in tl], [("event", "2026-11-01T10:30:00Z"),
                                                      ("run", "2026-11-01T10:32:59Z"),
                                                      ("event", "2026-11-01T10:40:00Z")])
        self.assertIn("fixer", tl[1][2])
        self.assertRegex(tl[1][2], r"(?<![\d.])121 ?秒")
        rows = self.cells(self.cost_table(sec))
        self.assertEqual(len(rows), 2, "one fixer row and 合計")
        self.assertEqual((rows[0]["角色"], rows[0]["輪"], rows[0]["input"]), ("fixer", "1", "5"))
        self.assertEqual((rows[1]["角色"], rows[1]["input"], rows[1]["快取寫"]), ("合計", "5", "8"))

    def test_item5_group_category_label(self):
        self.pool.update(lambda tx: tx.put_group({
            "id": "g2", "state": "stuck", "branch": "int/g2", "members": [], "fix_round": 3,
            "history": [{"at": "2026-11-01T00:00:00Z", "event": "red after 3 fix rounds"}]}), "test: g2")
        text = self.text(self.section(self.page({"group": "g2"}), "data-group", "g2"))
        self.assertIn("卡住", text)
        self.assertIn("紅燈", text)

    # ------------------------------------------------------------ 6: unknown ids

    def test_item6_unknown_ids(self):
        for query, msg in (({"ticket": "99"}, "沒有 #99 這張票"), ({"group": "g99"}, "沒有 g99 這個整合分支")):
            page = self.page(query)
            self.assertIn(msg, page)
            self.assertNotIn('class="detail"', page)

    # ------------------------------------------------------------ 7: links

    def group_rows(self, page):
        """{first cell: 分支 cell} of the integration-branch table (the table whose header has <th>分支</th>)."""
        import re
        tables = [t for t in re.findall(r'<table\b[^>]*>(.*?)</table>', page, re.S) if "<th>分支</th>" in t]
        self.assertEqual(len(tables), 1, "one integration-branch table with a 分支 column")
        trs = re.findall(r'<tr\b[^>]*>(.*?)</tr>', tables[0], re.S)
        heads = re.findall(r'<th\b[^>]*>(.*?)</th>', trs[0], re.S)
        col = heads.index("分支")
        out = {}
        for tr in trs[1:]:
            cells = re.findall(r'<td\b[^>]*>.*?</td>', tr, re.S)
            if len(cells) > col:
                out[cells[0]] = cells[col]
        return out

    def test_item7_table_links(self):
        page = self.page({"tab": "tickets"})
        self.assertIn("<td>#1</td><td>卡住</td>", page, "the first two cells stay as they are")
        self.assertRegex(page, r'<a\b[^>]*href="\?ticket=1"[^>]*>\s*ticket 1\s*</a>', "title links ?ticket=1")

    def test_item7_group_branch_cell_links(self):
        self.pool.update(lambda tx: tx.put_group({
            "id": "g2", "state": "fixing", "members": [], "fix_round": 0,
            "history": [{"at": "2026-11-01T00:00:00Z", "event": "regression started"}]}), "test: g2 without branch")
        rows = self.group_rows(self.page({"tab": "tickets"}))
        self.assertIn("<td>g1</td>", rows, "the row's first cell stays exactly <td>g1</td>")
        self.assertIn("<td>g2</td>", rows, "the row's first cell stays exactly <td>g2</td>")
        self.assertRegex(rows["<td>g1</td>"], r'^<td>\s*<a\b[^>]*href="\?group=g1"[^>]*>\s*int/g1\s*</a>\s*</td>$',
                         "g1's 分支 cell links ?group=g1 with the branch as text")
        self.assertRegex(rows["<td>g2</td>"], r'^<td>\s*<a\b[^>]*href="\?group=g2"[^>]*>\s*g2\s*</a>\s*</td>$',
                         "a group without branch: the 分支 cell links ?group=g2 with the group id as text")

    def test_item7_detail_hrefs_relative(self):
        for query in ({"ticket": "1"}, {"group": "g1"}):
            page = self.page(query)
            self.assertIn('class="detail"', page, "a detail page for %s" % query)
            for href in self.hrefs(page):
                self.assertFalse(href.startswith("/") or "://" in href, "%s: href %r" % (query, href))


class BoardDetailHelpers(LoopCase):
    """board.py detail views: their helpers (run_start, timeline, cost cells, which query is a detail)."""

    def test_run_start_floors_at_minus_seconds(self):
        import board
        self.assertEqual(board.run_start({"at": "2026-11-01T10:04:56Z", "seconds": 290.4}), "2026-11-01T10:00:05Z")
        self.assertEqual(board.run_start({"at": "2026-11-01T10:04:56Z", "seconds": 0.9}), "2026-11-01T10:04:55Z")
        self.assertEqual(board.run_start({"at": "2026-11-01T10:04:56Z"}), "2026-11-01T10:04:56Z")
        self.assertEqual(board.run_start({"at": "bad", "seconds": 3}), "bad")

    def test_timeline_events_before_runs_at_equal_times_and_stored_order(self):
        import board
        history = [{"at": "2026-01-01T00:00:05Z", "event": "b"}, {"at": "2026-01-01T00:00:00Z", "event": "a"},
                   {"at": "2026-01-01T00:00:05Z", "event": "c"}]
        rows = [{"role": "r1", "at": "2026-01-01T00:00:06Z", "seconds": 1},
                {"role": "r2", "at": "2026-01-01T00:00:05Z"}]
        got = [(k, at, x.get("event") or x.get("role")) for k, at, x in board.timeline(history, rows)]
        self.assertEqual(got, [("event", "2026-01-01T00:00:00Z", "a"), ("event", "2026-01-01T00:00:05Z", "b"),
                               ("event", "2026-01-01T00:00:05Z", "c"), ("run", "2026-01-01T00:00:05Z", "r1"),
                               ("run", "2026-01-01T00:00:05Z", "r2")])

    def test_detail_wanted_leaves_the_group_filter_alone(self):
        import board
        self.assertEqual(board.detail_wanted({"ticket": "3"}), ("ticket", "3"))
        self.assertEqual(board.detail_wanted({"group": "g2"}), ("group", "g2"))
        self.assertIsNone(board.detail_wanted({"tab": "tickets", "group": "g2"}))
        self.assertIsNone(board.detail_wanted(None))

    def test_cost_cells_unknown_never_zero(self):
        import board
        self.assertEqual(board.cost_value({"input_tokens": None}, "input_tokens"), "未知")
        self.assertEqual(board.cost_value({"input_tokens": 0}, "input_tokens"), "0")
        self.assertEqual(board.cost_value({"usd": 1.234}, "usd"), "$1.23")
        self.assertEqual(board.cost_value({"seconds": 2.6}, "seconds"), "3")
        self.assertEqual(board.feedback_text({"feedback": ["round 1: x", "round 2: y"]}, 2), "y")
        self.assertIsNone(board.feedback_text({"feedback": []}, 1))

    def test_detail_page_hrefs_are_relative(self):
        import re
        import board
        from core.config import Config
        self.add(1, group="g1", history=[{"at": "2026-01-01T00:00:00Z", "event": "opened"}])
        self.pool.update(lambda tx: tx.put_group({"id": "g1", "state": "open", "branch": "int/g1",
                                                  "members": ["1"], "history": [], "cost": []}), "test: g1")
        for query in ({"ticket": "1"}, {"group": "g1"}):
            page = board.render(Config(self.proj), self.snap(), query=query, now=0)
            tab = page.split('<div id="tab-tickets" class="tab">', 1)[1]
            self.assertIn('class="detail"', tab)
            for href in re.findall(r'href="([^"]*)"', tab):
                self.assertFalse(href.startswith("/") or "://" in href, href)


if __name__ == "__main__":
    unittest.main()
