"""the board's 要你判 tab: cards, options and their buttons"""

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


class T56Acceptance(LoopCase):
    """#56: the board's first tab 要你判 - one card per thing waiting for the user, shown by default
    when something waits, counted on its label and in the <title>. Expected values come from the
    ticket and this fixture (worked out by hand), never from board.py."""

    NOW = calendar.timegm((2026, 11, 6, 12, 0, 0, 0, 0, 0))
    QUOTA = {"reason": "quota", "since": "2026-11-06T10:00:00Z", "until": "2026-11-06T11:50:00Z",
             "text": "usage limit reached"}
    PAGES = {
        "p1": {"kind": "decision", "ref": "1", "text": "ticket 1 needs you", "at": "2026-11-06T09:00:01Z", "acked": False},
        "p3": {"kind": "decision", "ref": "3", "text": "ticket 3 question", "at": "2026-11-06T07:00:00Z", "acked": False},
        "p6": {"kind": "decision", "ref": "6", "text": "old answered page", "at": "2026-11-06T11:59:00Z", "acked": True},
        "pg1": {"kind": "decision", "ref": "g1", "text": "g1 red after 3 rounds", "at": "2026-11-06T10:30:01Z", "acked": False},
        "pg2": {"kind": "env", "ref": "g2", "text": "g2 preflight failed", "at": "2026-11-06T11:00:01Z", "acked": False},
        "preq": {"kind": "decision", "ref": "request", "text": "Which <one> & why?", "at": "2026-11-06T06:00:00Z", "acked": False},
        "pc": {"kind": "created", "ref": "1,2", "text": "opened 1, 2", "at": "2026-11-06T11:59:30Z", "acked": False},
        "pl": {"kind": "landed", "ref": "g1", "text": "g1 landed", "at": "2026-11-06T11:59:40Z", "acked": False},
    }
    CARD_RE = (r'<section class="wait" data-kind="([^"]*)" data-subject="([^"]*)" data-page="([^"]*)">'
               r'(.*?)</section>')
    TICKET_PARAMS = ("ticket", "group", "state", "cat", "q", "done")

    def fill(self):
        self.add(1, state="stuck", feedback=["round 3: verifier cases: 1 failed"],
                 stuck={"reason": "rounds", "text": "verifier cases: 1 failed", "at": "2026-11-06T09:00:00Z"})
        self.add(2, state="stuck", feedback=[],
                 stuck={"reason": "env", "text": "screen locked", "at": "2026-11-06T08:00:00Z"})
        self.add(3, state="ready")
        self.add(4, state="running", waiting=dict(self.QUOTA))
        self.add(5, state="running", waiting=dict(self.QUOTA, until="2026-11-06T11:58:20Z"))
        self.add(6, state="landed")

        def rest(tx):
            tx.put_group({"id": "g1", "branch": "int/g1", "state": "stuck", "members": ["1"], "fix_round": 3,
                          "history": [{"at": "2026-11-06T10:30:00Z", "event": "red after 3 fix rounds"}],
                          "last_regression": "1 failing"})
            tx.put_group({"id": "g2", "branch": "int/g2", "state": "pr", "members": [], "fix_round": 0,
                          "history": [], "job": None, "last_regression": "preflight: screen locked",
                          "paused": {"reason": "env", "at": "2026-11-06T11:00:00Z", "text": "screen locked"}})
            tx.put_group({"id": "g3", "branch": "int/g3", "state": "fixing", "members": [], "fix_round": 1,
                          "history": [], "job": None,
                          "paused": {"reason": "unmeasured", "at": "2026-11-06T11:10:00Z", "text": "no slot"}})
            for name, row in self.PAGES.items():
                tx.write("inbox/%s.json" % name, dict(row))
        self.pool.update(rest, "test: #56 fixture")

    def page(self, query=None):
        import inspect
        import board
        from core.config import Config
        self.assertTrue(hasattr(board, "render"), "board.render is missing")
        params = inspect.signature(board.render).parameters
        self.assertIn("query", params)
        self.assertIn("now", params)
        return board.render(Config(self.proj), self.snap(), query=query, now=self.NOW)

    def cards(self, page):
        out = []
        for m in re.finditer(self.CARD_RE, page, re.S):
            inner = m.group(4)
            want = re.findall(r'<p class="want">(.*?)</p>', inner, re.S)
            cat = re.findall(r'<span class="cat">(.*?)</span>', inner, re.S)
            fb = re.findall(r'<pre class="feedback">(.*?)</pre>', inner, re.S)
            text = re.findall(r'<pre class="page-text">(.*?)</pre>', inner, re.S)
            tl = re.findall(r'<a class="timeline" href="([^"]*)">時間軸</a>', inner)
            out.append({"key": m.groups()[:3], "want": want, "cat": cat, "feedback": fb, "text": text, "timeline": tl})
        return out

    def shown(self, page):
        ids = re.findall(r'id="tab-([a-z]+)"', page)
        return ids

    def nav(self, page):
        m = re.search(r'<nav class="tabs">(.*?)</nav>', page, re.S)
        self.assertIsNotNone(m, "no <nav class=\"tabs\">")
        return re.findall(r'<a href="([^"]*)"[^>]*>(.*?)</a>', m.group(1), re.S)

    def doc_title(self, page):
        m = re.search(r"<title>(.*?)</title>", page, re.S)
        self.assertIsNotNone(m, "no <title>")
        return m.group(1)

    def h1(self, page):
        m = re.search(r"<h1>(.*?)</h1>", page, re.S)
        self.assertIsNotNone(m, "no <h1>")
        return m.group(1)

    # ------------------------------------------------------------ item 1

    def test_item1_decide_is_the_first_tab_with_six_links_and_a_counted_label(self):
        import board
        self.assertTrue(hasattr(board, "TABS"), "board.TABS is missing")
        self.assertGreaterEqual(len(board.TABS), 1)
        self.assertEqual(tuple(board.TABS[0][:2]), ("decide", "要你判"), "TABS' first entry is decide / 要你判")
        self.assertTrue(callable(board.TABS[0][2]), "the decide entry carries its renderer")
        self.fill()
        for query in (None, {"tab": "decide"}, {"tab": "tickets"}, {"tab": "dag"}):
            links = self.nav(self.page(query))
            self.assertEqual([h for h, _ in links], ["?tab=decide", "?tab=live", "?tab=tickets", "?tab=dag", "?tab=tokens",
                                                     "?tab=host"], "nav links for %r" % (query,))
            self.assertEqual([t for _, t in links], ["要你判 (8)", "進行中", "票", "DAG", "Token", "主機"],
                             "nav texts for %r" % (query,))
        page = self.page({"tab": "decide"})
        self.assertEqual(page.count('id="tab-decide"'), 1, "exactly one tab-decide element")
        for other in ("live", "tickets", "dag", "tokens", "host"):
            self.assertNotIn('id="tab-%s"' % other, page)

    def test_item1_label_has_no_count_without_cards(self):
        links = self.nav(self.page(None))
        self.assertEqual([h for h, _ in links][:2], ["?tab=decide", "?tab=live"], "要你判 is the first link, 進行中 second")
        self.assertEqual(len(links), 6, "six links")
        self.assertEqual(links[0][1], "要你判", "no count without cards")

    # ------------------------------------------------------------ item 2

    def test_item2_decide_is_shown_by_default_when_something_waits(self):
        self.fill()
        for query in (None, {}, {"tab": ""}, {"tab": "nope"}):
            self.assertEqual(self.shown(self.page(query)), ["decide"], "query %r shows 要你判" % (query,))
        for name in self.TICKET_PARAMS:
            value = "1" if name in ("ticket", "done") else ("g1" if name == "group" else
                                                             ("stuck" if name == "state" else
                                                              ("red" if name == "cat" else "ticket")))
            for query in ({name: value}, {"tab": "nope", name: value}):
                self.assertEqual(self.shown(self.page(query)), ["tickets"], "query %r shows 票" % (query,))
        self.assertEqual(self.shown(self.page({"state": "stuck"})), ["tickets"])
        self.assertEqual(self.shown(self.page({"ticket": "1"})), ["tickets"])

    def test_item2_live_is_the_default_when_nothing_waits(self):
        for query in (None, {}, {"tab": ""}, {"tab": "nope"}):
            self.assertEqual(self.shown(self.page(query)), ["live"], "empty pool, query %r shows 進行中" % (query,))
        self.assertEqual(self.shown(self.page({"tab": "decide"})), ["decide"], "an asked-for 要你判 is shown")

    def test_item2_html_command_without_query_shows_decide(self):
        self.fill()
        res = self.run_py("board.py", "html")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('id="tab-decide"', res.stdout)
        self.assertNotIn('id="tab-tickets"', res.stdout)

    # ------------------------------------------------------------ item 3

    def test_item3_title_carries_the_count_and_h1_does_not(self):
        self.fill()
        for query in (None, {"tab": "tickets"}, {"tab": "decide"}):
            page = self.page(query)
            head = self.h1(page)
            self.assertNotIn("(", head, "the <h1> never carries the count")
            self.assertTrue(head, "the <h1> keeps today's title")
            self.assertEqual(self.doc_title(page), "(8) " + head, "<title> is '(8) ' + today's title")

    def test_item3_title_without_cards_has_no_count(self):
        page = self.page(None)
        self.assertNotIn("(", self.doc_title(page))
        self.assertEqual(self.doc_title(page), self.h1(page))
        links = self.nav(page)
        self.assertEqual(links[0], ("?tab=decide", "要你判"), "要你判 first, no count")

    # ------------------------------------------------------------ items 4 and 5

    def test_items4_5_cards_which_and_in_what_order(self):
        self.fill()
        cards = self.cards(self.page({"tab": "decide"}))
        self.assertEqual([c["key"] for c in cards], [
            ("quota", "4", ""), ("group", "g3", ""), ("group", "g2", "pg2"), ("group", "g1", "pg1"),
            ("ticket", "1", "p1"), ("ticket", "2", ""), ("ticket", "3", "p3"), ("other", "request", "preq")])

    def test_item4_no_card_for_a_short_quota_wait_or_read_and_info_pages(self):
        self.fill()
        page = self.page({"tab": "decide"})
        keys = [c["key"] for c in self.cards(page)]
        self.assertEqual(len(keys), 8, keys)
        self.assertNotIn("5", [k[1] for k in keys], "a quota wait 100 s past its until is no card")
        for name in ("p6", "pc", "pl"):
            self.assertNotIn(name, [k[2] for k in keys], "%s is no card" % name)
            self.assertNotIn('data-page="%s"' % name, page)

    # ------------------------------------------------------------ item 6

    def test_item6_card_contents(self):
        self.fill()
        cards = self.cards(self.page({"tab": "decide"}))
        self.assertEqual(len(cards), 8, [c["key"] for c in cards])
        want = ["#4 等額度重置,已過了 2026-11-06T11:50:00Z 還在等",
                "g3 的 regression 沒跑成:環境修好後重跑",
                "g2 的 regression 沒跑成:環境修好後重跑",
                "g1 卡住了:帶著你的話重驗,或取消這組",
                "#1 卡住了:照你的話改、再試一次或取消",
                "#2 卡住了:照你的話改、再試一次或取消",
                "#3 有一頁要你看",
                "開題者開票前要你決定:到「票」分頁交代新的事時一起回答"]
        cat = [["額度用完"], [], ["環境"], ["紅燈"], ["紅燈"], ["環境"], [], []]
        feedback = [["usage limit reached"], [], ["preflight: screen locked"], ["1 failing"],
                    ["round 3: verifier cases: 1 failed"], ["screen locked"], [], []]
        timeline = [["?ticket=4"], ["?group=g3"], ["?group=g2"], ["?group=g1"], ["?ticket=1"], ["?ticket=2"],
                    ["?ticket=3"], []]
        for i, c in enumerate(cards):
            self.assertEqual(c["want"], [html.escape(want[i])], "want line of %r" % (c["key"],))
            self.assertNotIn("\n", c["want"][0].strip(), "one line")
            self.assertEqual(c["cat"], [html.escape(x) for x in cat[i]], "cat of %r" % (c["key"],))
            self.assertEqual(c["feedback"], [html.escape(x) for x in feedback[i]], "feedback of %r" % (c["key"],))
            self.assertEqual(c["timeline"], timeline[i], "timeline of %r" % (c["key"],))
            page_name = c["key"][2]
            expected_text = [html.escape(self.PAGES[page_name]["text"])] if page_name else []
            self.assertEqual(c["text"], expected_text, "page-text of %r" % (c["key"],))
        self.assertEqual(sum(1 for c in cards if c["text"]), 5, "page-text on exactly the five cards with a page")

    def test_item6_text_is_escaped(self):
        self.fill()
        page = self.page({"tab": "decide"})
        cards = [c for c in self.cards(page) if c["key"] == ("other", "request", "preq")]
        self.assertEqual(len(cards), 1, "one card for preq")
        self.assertEqual(cards[0]["text"], ["Which &lt;one&gt; &amp; why?"])
        self.assertNotIn("Which <one>", page)

    def test_item6_other_page_line_names_its_ref(self):
        self.add(1, state="ready")
        self.pool.update(lambda tx: tx.write("inbox/px.json", {"kind": "env", "ref": "host", "text": "disk <full>",
                                                               "at": "2026-11-06T11:00:00Z", "acked": False}), "px")
        cards = self.cards(self.page({"tab": "decide"}))
        self.assertEqual([c["key"] for c in cards], [("other", "host", "px")])
        self.assertEqual(cards[0]["want"], [html.escape("有一頁要你看(host)")])
        self.assertEqual(cards[0]["cat"], [])
        self.assertEqual(cards[0]["timeline"], [])
        self.assertEqual(cards[0]["text"], ["disk &lt;full&gt;"])

    # ------------------------------------------------------------ item 7

    def test_item7_no_card_says_quiet(self):
        page = self.page({"tab": "decide"})
        self.assertEqual(self.shown(page), ["decide"])
        self.assertIn('<p class="quiet">沒有要你判的事。</p>', page)
        self.assertEqual(self.cards(page), [])


class DecideTabCards(LoopCase):
    """#49's helpers: waiting_cards, decide_default, pool_seconds, render_nav's count."""

    NOW = calendar.timegm((2026, 11, 6, 12, 0, 0, 0, 0, 0))

    def page(self, name, kind, ref, at, acked=False):
        self.pool.update(lambda tx: tx.write("inbox/%s.json" % name, {"kind": kind, "ref": ref, "text": "text " + name,
                                                                     "at": at, "acked": acked}), "test: page")

    def test_pool_seconds_reads_the_pool_format_and_gives_none_otherwise(self):
        import board
        self.assertEqual(board.pool_seconds("2026-11-06T12:00:00Z"), self.NOW)
        for bad in (None, "", "yesterday", 5):
            self.assertIsNone(board.pool_seconds(bad), bad)

    def test_a_quota_wait_is_a_card_only_past_300_seconds(self):
        import board
        for until, expected in (("2026-11-06T11:55:00Z", []), ("2026-11-06T11:54:59Z", ["4"])):
            self.add(4, state="running", waiting={"reason": "quota", "until": until, "text": "limit"})
            cards = board.waiting_cards(self.snap(), self.NOW)
            self.assertEqual([c["subject"] for c in cards], expected, until)
        self.assertEqual(cards[0]["kind"], "quota")
        self.assertEqual(cards[0]["timeline"], "?ticket=4")
        self.assertEqual(cards[0]["feedback"], "limit")

    def test_a_page_about_a_stuck_ticket_is_its_only_card_and_acked_or_landed_pages_are_none(self):
        import board
        self.add(1, state="stuck", stuck={"reason": "env", "text": "locked", "at": "2026-11-06T08:00:00Z"})
        self.page("p1", "decision", "1", "2026-11-06T09:00:00Z")
        self.page("p2", "decision", "1", "2026-11-06T09:30:00Z", acked=True)
        self.page("p3", "landed", "1", "2026-11-06T09:40:00Z")
        cards = board.waiting_cards(self.snap(), self.NOW)
        self.assertEqual([(c["kind"], c["subject"], c["page"]) for c in cards], [("ticket", "1", "p1")])
        self.assertEqual(cards[0]["text"], "text p1")

    def test_newest_first_missing_time_last_ties_by_subject(self):
        import board
        for ident, at in ((1, None), (2, "2026-11-06T08:00:00Z"), (3, "2026-11-06T08:00:00Z"),
                          (4, "2026-11-06T09:00:00Z")):
            self.add(ident, state="stuck", stuck={"reason": "env", "text": "x", "at": at})
        cards = board.waiting_cards(self.snap(), self.NOW)
        self.assertEqual([c["subject"] for c in cards], ["4", "2", "3", "1"])

    def test_default_tab_needs_a_card_and_no_ticket_parameter(self):
        import board
        self.assertEqual(board.decide_default(self.snap(), None, self.NOW), "live")
        self.add(1, state="stuck", stuck={"reason": "env", "text": "x", "at": "2026-11-06T08:00:00Z"})
        snap = self.snap()
        self.assertEqual(board.decide_default(snap, {}, self.NOW), "decide")
        for name in ("ticket", "group", "state", "cat", "q", "done"):
            self.assertEqual(board.decide_default(snap, {name: "1"}, self.NOW), "tickets", name)

    def test_nav_label_carries_the_count(self):
        import board
        self.assertIn(">要你判</a>", board.render_nav("decide"))
        self.assertIn(">要你判 (3)</a>", board.render_nav("decide", 3))

    def test_no_sign_in_code_comes_back(self):
        """D-093: render keeps main's parameters and board.py names no sign-in piece."""
        import inspect
        import board
        self.assertEqual(list(inspect.signature(board.render).parameters), ["cfg", "snap", "key", "query", "now"])
        with open(board.__file__, encoding="utf-8") as f:
            source = f.read().lower()
        for word in ("goo" + "gle", "log" + "in", "log" + "out", "board_" + "session", "render_" + "user", "oa" + "uth"):
            self.assertNotIn(word, source, word)


class T79Acceptance(LoopCase):
    """Ticket #79: a 要你判 card of a page that carries options shows each option as a button that runs
    its action (recommended first, marked 建議, its prepared note editable), release included. Each
    case runs a real board subprocess; page rows are written here, which is where expected values
    come from. Server helpers are T57Acceptance's, called as plain functions (no inheritance)."""

    HEADER = "X-Auth-Request-Email"
    NAME = "a@x.com"
    KEY = "s3cret"
    release = "exit 7"
    LABELS = {"revise": "照我的話改", "retry": "再試一次", "cancel": "取消", "group-ready": "帶著我的話重驗",
              "group-cancel": "取消這組", "rerun": "環境修好了,重跑", "release": "重跑發版", "ack": "知道了"}
    NOTE_ACTIONS = ("revise", "retry", "cancel", "group-ready", "group-cancel", "rerun")

    def setUp(self):
        super().setUp()
        for k in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(k, None)
        self.port = None
        self.by = None
        self.auth = {}

    # ------------------------------------------------------------ borrowed helpers (not inherited)

    def serve(self, mode):
        return T57Acceptance.serve(self, mode)

    def call(self, *a, **kw):
        return T57Acceptance.call(self, *a, **kw)

    def get(self, query, auth=True, headers=None):
        status, _, text = self.call("GET", "/" + query, auth=auth, headers=headers)
        self.assertEqual(status, 200, text[:300])
        return text

    def act(self, auth=True, **form):
        return T57Acceptance.act(self, auth=auth, **form)

    def web_root(self):
        return os.path.join(self.tmp, "wt", "runs", "web")

    def web_jobs(self):
        return T57Acceptance.web_jobs(self)

    def wait_rc(self, folder):
        return T57Acceptance.wait_rc(self, folder)

    def wait_all_jobs(self):
        for name in sorted(self.web_jobs()):
            self.wait_rc(os.path.join(self.web_root(), name))

    # ------------------------------------------------------------ fixture

    @staticmethod
    def opt(action, consequence, note=None):
        o = {"action": action, "consequence": consequence}
        if note is not None:
            o["note"] = note
        return o

    def rows(self):
        """{page name: (ref, kind, question keys)} of the pages that carry options."""
        o = self.opt
        return {
            "p1": ("1", "decision", {
                "question": "What should happen to #1?",
                "options": [o("retry", "#1 runs again from round 0", "retry <b>& one"),
                            o("revise", "the opener rewrites #1", "revise <b>& two\nsecond line"),
                            o("cancel", "#1 is dropped", "cancel <b>& three"),
                            o("ack", "the page goes away, #1 stays stuck")],
                "recommendation": "retry: the failure looked flaky"}),
            "pg1": ("g1", "decision", {
                "question": "Verify g1 again or drop it?",
                "options": [o("group-ready", "g1 is verified again with the note", "ready <b>& note"),
                            o("group-cancel", "g1 is cancelled", "cancel <b>& why"),
                            o("ack", "the page goes away")],
                "recommendation": "verify again: the fix is in"}),
            "pg2": ("g2", "env", {
                "question": "Is the environment fixed?",
                "options": [o("rerun", "g2 runs its regression again", "rerun <b>& fixed"),
                            o("ack", "the page goes away, g2 stays paused")],
                "recommendation": "rerun once the screen is unlocked"}),
            "pg3": ("g3", "decision", {
                "question": "Run the release of g3 again?",
                "options": [o("ack", "the release stays failed"),
                            o("release", "the release command runs once more")],
                "recommendation": "ack: the release target is down"}),
        }

    def fixture(self):
        from core import pages
        self.add(1)
        self.add(7, state="merged", group="g1")
        self.add(3, state="merged", group="g3")
        git(self.proj, "branch", "int/g1")
        sha = git(self.proj, "rev-parse", "main")
        rows = self.rows()
        for name, (ref, _, keys) in rows.items():
            self.assertEqual(pages.problems(ref, keys), [], "the case's own row %s passes problems()" % name)

        def change(tx):
            t = tx.ticket("1")
            t.update(state="stuck", round=3, stuck={"reason": "rounds", "text": "x"})
            tx.put_ticket(t)
            tx.put_group({"id": "g1", "branch": "int/g1", "state": "stuck", "members": ["7"], "fix_round": 3,
                          "job": None})
            tx.put_group({"id": "g2", "branch": "int/g2", "state": "pr", "members": [], "fix_round": 0,
                          "history": [], "job": None,
                          "paused": {"reason": "env", "at": osx.now(), "text": "screen locked"}})
            tx.put_group({"id": "g3", "branch": "int/g3", "state": "landed", "members": ["3"], "fix_round": 0,
                          "history": [], "job": None, "landed_sha": sha, "release": "failed"})
            for name, (ref, kind, keys) in rows.items():
                row = {"kind": kind, "ref": ref, "text": "page text of " + name, "at": osx.now(), "acked": False}
                row.update(keys)
                tx.write("inbox/%s.json" % name, row)
        self.pool.update(change, "test: #79 fixture")

    # ------------------------------------------------------------ page parsing

    @staticmethod
    def cards(page):
        """{subject: card}: card has 'page', 'forms' (every form attrs), 'seq' (document order of
        page-text / question / option / recommendation / act markers), 'question', 'recommendation'
        (texts of those p elements) and 'options' (form.option or div.option: tag, attrs, hidden,
        textareas [(attrs, text)], buttons [text], consequences [text], text)."""
        from html.parser import HTMLParser

        class P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.out, self.card, self.depth = {}, None, 0
                self.opt, self.optdepth = None, 0
                self.cap = None          # (kind, list) of the p/button/textarea being read

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                cls = (a.get("class") or "").split()
                if tag == "section":
                    if self.card is None and "wait" in cls:
                        self.card = {"subject": a.get("data-subject"), "page": a.get("data-page"), "forms": [],
                                     "seq": [], "question": [], "recommendation": [], "options": []}
                        self.depth = 0
                        return
                    if self.card is not None:
                        self.depth += 1
                if self.card is None:
                    return
                c = self.card
                if tag == "form":
                    c["forms"].append(a)
                    if "act" in cls:
                        c["seq"].append("act")
                if tag in ("form", "div") and "option" in cls and self.opt is None:
                    self.opt = {"tag": tag, "attrs": a, "hidden": [], "textareas": [], "buttons": [],
                                "consequences": [], "text": []}
                    self.optdepth = 0
                    c["options"].append(self.opt)
                    c["seq"].append("option")
                    return
                if tag == "div" and self.opt is not None and self.opt["tag"] == "div":
                    self.optdepth += 1
                if tag == "pre" and "page-text" in cls:
                    c["seq"].append("page-text")
                if tag == "p" and "question" in cls:
                    c["seq"].append("question")
                    self.cap = ("question", [], "p")
                elif tag == "p" and "recommendation" in cls:
                    c["seq"].append("recommendation")
                    self.cap = ("recommendation", [], "p")
                elif tag == "p" and "consequence" in cls and self.opt is not None:
                    self.cap = ("consequence", [], "p")
                elif tag == "button" and self.opt is not None:
                    self.cap = ("button", [], "button")
                elif tag == "textarea" and self.opt is not None:
                    self.cap = ("textarea", [], "textarea", a)
                elif tag == "input" and self.opt is not None and a.get("type") == "hidden":
                    self.opt["hidden"].append((a.get("name"), a.get("value")))

            def handle_endtag(self, tag):
                if self.card is None:
                    return
                if self.cap is not None and tag == self.cap[2]:
                    kind, text = self.cap[0], "".join(self.cap[1])
                    if kind in ("question", "recommendation"):
                        self.card[kind].append(text.strip())
                    elif kind == "consequence":
                        self.opt["consequences"].append(text.strip())
                    elif kind == "button":
                        self.opt["buttons"].append(text.strip())
                    elif kind == "textarea":
                        self.opt["textareas"].append((self.cap[3], text))
                    self.cap = None
                    return
                if self.opt is not None and tag == self.opt["tag"]:
                    if tag == "div" and self.optdepth:
                        self.optdepth -= 1
                    else:
                        self.opt["text"] = "".join(self.opt["text"])
                        self.opt = None
                    return
                if tag == "section":
                    if self.depth:
                        self.depth -= 1
                    else:
                        self.out[self.card["subject"]] = self.card
                        self.card = None

            def handle_data(self, data):
                if self.cap is not None:
                    self.cap[1].append(data)
                if self.opt is not None:
                    self.opt["text"].append(data)

        p = P()
        p.feed(page)
        return p.out

    def common(self, cards, subject, name):
        """Question, consequences and recommendation of one card, and their order."""
        ref, _, keys = self.rows()[name]
        self.assertIn(subject, cards, "a card for %s" % subject)
        card = cards[subject]
        self.assertEqual(card["page"], name, subject)
        self.assertEqual(len(card["question"]), 1, "one p.question on %s" % subject)
        self.assertIn(keys["question"], card["question"][0], subject)
        self.assertEqual(len(card["recommendation"]), 1, "one p.recommendation on %s" % subject)
        self.assertIn(keys["recommendation"], card["recommendation"][0], subject)
        opts = card["options"]
        self.assertEqual([o["attrs"].get("data-action") for o in opts], [x["action"] for x in keys["options"]],
                         "options of %s in the page's order" % subject)
        for i, (o, spec) in enumerate(zip(opts, keys["options"]), 1):
            self.assertEqual(o["attrs"].get("data-recommended"), "1" if i == 1 else None,
                             "data-recommended of option %d on %s" % (i, subject))
            self.assertEqual(len(o["consequences"]), 1, "one p.consequence in option %d of %s" % (i, subject))
            self.assertIn(spec["consequence"], o["consequences"][0], subject)
        seq = [s for s in card["seq"] if s != "act"]
        self.assertEqual(seq, ["page-text", "question"] + ["option"] * len(opts) + ["recommendation"],
                         "order in card %s" % subject)
        return card, keys

    def check_forms(self, page):
        self.assertNotIn("<b>&", page, "every note is HTML-escaped")
        cards = self.cards(page)
        subjects = {"1": "p1", "g1": "pg1", "g2": "pg2", "g3": "pg3"}
        for subject, name in subjects.items():
            card, keys = self.common(cards, subject, name)
            for i, (o, spec) in enumerate(zip(card["options"], keys["options"]), 1):
                where = "option %d (%s) of %s" % (i, spec["action"], subject)
                a = spec["action"]
                self.assertEqual(o["tag"], "form", where)
                self.assertEqual((o["attrs"].get("method") or "").lower(), "post", where)
                self.assertEqual(o["attrs"].get("action"), "act", where)
                self.assertEqual(sorted(o["hidden"]), sorted([("subject", subject), ("page", name), ("action", a),
                                                              ("option", str(i))]), where)
                if a in self.NOTE_ACTIONS:
                    self.assertEqual(len(o["textareas"]), 1, "one textarea in " + where)
                    attrs, text = o["textareas"][0]
                    self.assertEqual(attrs.get("name"), "text", where)
                    self.assertEqual(text.lstrip("\n"), spec["note"], "the prepared note in " + where)
                    self.assertEqual(attrs.get("data-draft"), "act-%s-%s-o%d" % (subject, name, i), where)
                else:
                    self.assertEqual(o["textareas"], [], "no textarea in " + where)
                self.assertEqual(len(o["buttons"]), 1, "one button in " + where)
                label = self.LABELS[a]
                if i == 1:
                    self.assertIn(label, o["buttons"][0], where)
                    self.assertIn("建議", o["buttons"][0], where)
                else:
                    self.assertEqual(o["buttons"][0], label, where)
            acts = [f for f in card["forms"] if "act" in (f.get("class") or "").split()]
            self.assertEqual(len(acts), 1, "card %s keeps its form.act" % subject)
            self.assertEqual(acts[0].get("action"), "act", subject)
            self.assertEqual(len(card["forms"]), 1 + len(keys["options"]), "forms of %s" % subject)

    # ------------------------------------------------------------ item 1

    def test_item1_key_and_cookie_viewer_gets_option_forms(self):
        self.fixture()
        self.serve("key")
        self.check_forms(self.get("?tab=decide"))
        status, headers, _ = self.call("GET", "/?key=" + self.KEY, auth=False)
        self.assertEqual(status, 303)
        cookie = headers.get("Set-Cookie", "").split(";")[0]
        self.assertTrue(cookie, "the key is traded for a cookie")
        self.check_forms(self.get("?tab=decide", auth=False, headers={"Cookie": cookie}))

    def test_item1_trusted_viewer_gets_option_forms(self):
        self.fixture()
        self.serve("trusted")
        self.check_forms(self.get("?tab=decide"))

    # ------------------------------------------------------------ item 2

    def test_item2_read_only_viewer_gets_options_without_forms(self):
        self.fixture()
        self.serve("trusted")
        page = self.get("?tab=decide", auth=False)
        cards = self.cards(page)
        for subject, name in {"1": "p1", "g1": "pg1", "g2": "pg2", "g3": "pg3"}.items():
            card, keys = self.common(cards, subject, name)
            self.assertEqual(card["forms"], [], "no form at all in read-only card %s" % subject)
            for i, o in enumerate(card["options"], 1):
                self.assertEqual(o["tag"], "div", "option %d of %s is a div.option" % (i, subject))
                if i == 1:
                    self.assertIn("建議", o["text"], "the recommended mark on %s" % subject)
                else:
                    self.assertNotIn("建議", o["text"], "option %d of %s is not marked" % (i, subject))

    # ------------------------------------------------------------ item 4

    def test_item4_option_field_shapes_the_sent_key_or_is_400(self):
        import urllib.parse
        self.fixture()
        self.serve("key")
        base = "./?tab=decide&sent="
        for form in (dict(subject="1", page="p1", action="ack", text=""),
                     dict(subject="1", page="p1", action="ack", text="", option="")):
            status, headers, body = self.act(**form)
            self.assertEqual(status, 303, "%r: %s" % (form, body[:200]))
            self.assertEqual(headers.get("Location"), base + urllib.parse.quote("act-1-p1"), form)
        for n in ("1", "2", "3", "4"):
            status, headers, body = self.act(subject="1", page="p1", action="ack", text="", option=n)
            self.assertEqual(status, 303, "option %s: %s" % (n, body[:200]))
            self.assertEqual(headers.get("Location"), base + urllib.parse.quote("act-1-p1-o" + n), n)
        status, headers, body = self.act(subject="g1", page="pg1", action="group-cancel", text="x", option="2")
        self.assertEqual(status, 303, body[:200])
        self.assertEqual(headers.get("Location"), base + urllib.parse.quote("act-g1-pg1-o2"))
        for bad in ("0", "5", "-1", "01", "1.0", "12", "a", " 1", "1 2"):
            before = self.web_jobs()
            status, _, body = self.act(subject="1", page="p1", action="ack", text="", option=bad)
            self.assertEqual(status, 400, "option %r: %s" % (bad, body[:200]))
            self.assertEqual(self.web_jobs(), before, "nothing started for option %r" % bad)
        self.wait_all_jobs()

    # ------------------------------------------------------------ item 5

    def test_item5_release_is_accepted_for_a_group_only(self):
        import urllib.parse
        self.fixture()
        self.serve("key")
        for subject, page in (("1", "p1"), ("3", "pg3")):
            before = self.web_jobs()
            status, _, body = self.act(subject=subject, page=page, action="release", text="", option="2")
            self.assertEqual(status, 400, "release on ticket %s: %s" % (subject, body[:200]))
            self.assertEqual(self.web_jobs(), before, "nothing started for release on %s" % subject)
        status, headers, body = self.act(subject="g3", page="pg3", action="release", text="", option="2")
        self.assertEqual(status, 303, body[:200])
        self.assertEqual(headers.get("Location"), "./?tab=decide&sent=" + urllib.parse.quote("act-g3-pg3-o2"))
        self.wait_all_jobs()

    def release_end_to_end(self, mode):
        import urllib.parse
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        g = self.snap().groups["g1"]
        self.assertEqual((g["state"], g.get("release")), ("landed", "failed"), "precondition: release failed")
        o = self.opt
        keys = {"question": "Run the release of g1 again?",
                "options": [o("ack", "the release stays failed"), o("release", "the release runs once more")],
                "recommendation": "ack: the target is down"}
        from core import pages
        self.assertEqual(pages.problems("g1", keys), [])

        def change(tx):
            row = {"kind": "decision", "ref": "g1", "text": "release failed", "at": osx.now(), "acked": False}
            row.update(keys)
            tx.write("inbox/prel.json", row)
        self.pool.update(change, "test: release page")
        self.serve(mode)
        before = self.web_jobs()
        status, headers, body = self.act(subject="g1", page="prel", action="release", text="", option="2")
        self.assertEqual(status, 303, body[:200])
        self.assertEqual(headers.get("Location"), "./?tab=decide&sent=" + urllib.parse.quote("act-g1-prel-o2"))
        deadline = time.time() + 5
        new = sorted(self.web_jobs() - before)
        while not new and time.time() < deadline:
            time.sleep(0.1)
            new = sorted(self.web_jobs() - before)
        self.assertEqual(len(new), 1, "one new job under <worktrees>/runs/web/")
        folder = os.path.join(self.web_root(), new[0])
        self.wait_rc(folder)
        with open(os.path.join(folder, "job.json")) as fh:
            job = json.load(fh)
        self.assertEqual(job.get("action"), "release")
        snap = self.snap()
        self.assertTrue(snap.inbox["prel"].get("acked"), "the clicked page is acked")
        ev = [e for e in snap.groups["g1"].get("history") or [] if str(e.get("event", "")).startswith("release re-run")]
        self.assertTrue(ev, "g1 has a 'release re-run' event")
        self.assertEqual(ev[-1].get("by"), self.by)

    def test_item5_key_release_end_to_end(self):
        self.release_end_to_end("key")

    def test_item5_trusted_release_end_to_end(self):
        self.release_end_to_end("trusted")

    # ------------------------------------------------------------ item 6

    def test_item6_group_ready_with_an_edited_note_end_to_end(self):
        import urllib.parse
        self.add(7, state="merged", group="g1")
        git(self.proj, "branch", "int/g1")
        o = self.opt
        keys = {"question": "Verify g1 again?",
                "options": [o("group-ready", "g1 is verified again", "prepared <b>& note"),
                            o("group-cancel", "g1 is cancelled", "drop it")],
                "recommendation": "verify again"}
        from core import pages
        self.assertEqual(pages.problems("g1", keys), [])

        def change(tx):
            tx.put_group({"id": "g1", "branch": "int/g1", "state": "stuck", "members": ["7"], "fix_round": 3,
                          "job": None})
            row = {"kind": "decision", "ref": "g1", "text": "g1 stuck", "at": osx.now(), "acked": False}
            row.update(keys)
            tx.write("inbox/p1.json", row)
        self.pool.update(change, "test: stuck g1")
        self.serve("key")
        edited = "my edited note: use the second approach"
        before = self.web_jobs()
        status, headers, body = self.act(subject="g1", page="p1", action="group-ready", option="1", text=edited)
        self.assertEqual(status, 303, body[:200])
        self.assertEqual(headers.get("Location"), "./?tab=decide&sent=" + urllib.parse.quote("act-g1-p1-o1"))
        new = sorted(self.web_jobs() - before)
        self.assertEqual(len(new), 1, "one new job")
        self.wait_rc(os.path.join(self.web_root(), new[0]))
        snap = self.snap()
        g = snap.groups["g1"]
        self.assertEqual(g["state"], "pr")
        self.assertEqual((g.get("notes") or [{}])[-1].get("text"), edited)
        self.assertTrue(snap.inbox["p1"].get("acked"), "p1 acked")

    # ------------------------------------------------------------ item 8

    def test_item8_readme_dashboard_has_release_row_and_options_paragraph(self):
        with open(os.path.join(LOOP, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"^## Dashboard\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        self.assertTrue(m, "README has '## Dashboard'")
        section = m.group(1)
        rows = [line for line in section.splitlines() if line.startswith("|")]
        self.assertTrue(any("tickets.py release gN" in r for r in rows), "a button-table row runs tickets.py release gN")
        paras = [p for p in re.split(r"\n\s*\n", section) if not p.lstrip().startswith("|")]
        need = ("建議", "question", "consequence", "recommendation", "note")
        hits = [p for p in paras if all(w in p.lower() for w in need) and re.search(r"read[- ]only", p.lower())]
        self.assertTrue(hits, "one paragraph says how options appear (%s, read-only)" % ", ".join(need))


class BoardPageOptions(LoopCase):
    """#79, the implementer's pieces: a page's question keys reach its card only when valid, the option
    forms and their read-only twin, the option field and the release button's shape, the draft script."""

    ASK = {"question": "Which way?",
           "options": [{"action": "group-ready", "note": "n <b>&", "consequence": "c1 <i>"},
                       {"action": "release", "consequence": "c2"},
                       {"action": "ack", "consequence": "c3"}],
           "recommendation": "r <u>"}

    def snap(self, row):
        import types
        page = dict({"kind": "decision", "ref": "g1", "at": "2026-01-01T00:00:00Z", "text": "t"}, **row)
        return types.SimpleNamespace(tickets={}, groups={"g1": {"state": "stuck", "id": "g1"}},
                                     inbox={"p1": page})

    def card(self, row):
        import board
        cards = board.waiting_cards(self.snap(row), 0)
        self.assertEqual(len(cards), 1)
        return cards[0]

    def test_waiting_cards_copy_valid_question_keys_only(self):
        import board
        c = self.card(self.ASK)
        self.assertEqual((c["question"], c["options"], c["recommendation"]),
                         (self.ASK["question"], self.ASK["options"], self.ASK["recommendation"]))
        broken = [{}, {"question": "q"}, dict(self.ASK, options=[]), dict(self.ASK, question="a\nb"),
                  dict(self.ASK, options=[{"action": "retry", "consequence": "x"}, {"action": "ack", "consequence": "y"}])]
        for row in broken:
            with self.subTest(row=row):
                c = self.card(row)
                self.assertNotIn("options", c)
                html_ = board.render_card(c, ["group-ready", "group-cancel", "ack"], None, True)
                self.assertNotIn('class="option"', html_)
                self.assertNotIn('class="question"', html_)
                self.assertIn('class="act"', html_)

    def test_option_forms_for_a_viewer_who_may_submit(self):
        import board
        c = self.card(self.ASK)
        out = board.render_card(c, ["group-ready", "group-cancel", "ack"], None, True)
        self.assertLess(out.index('class="page-text"'), out.index('<p class="question">Which way?</p>'))
        self.assertLess(out.index('class="question"'), out.index('class="recommendation"'))
        self.assertLess(out.index('class="recommendation"'), out.index('class="act"'))
        forms = re.findall(r'<form method="post" action="act" class="option"(.*?)</form>', out)
        self.assertEqual(len(forms), 3)
        self.assertIn(' data-action="group-ready" data-recommended="1">', forms[0])
        self.assertIn('<input type="hidden" name="option" value="1">', forms[0])
        self.assertIn('<textarea name="text" maxlength="8000" rows="2" data-draft="act-g1-p1-o1">'
                      'n &lt;b&gt;&amp;</textarea>', forms[0])
        self.assertIn("帶著我的話重驗(建議)</button>", forms[0])
        self.assertIn('<p class="consequence">c1 &lt;i&gt;</p>', forms[0])
        self.assertIn(' data-action="release">', forms[1])
        self.assertIn('<input type="hidden" name="action" value="release">', forms[1])
        self.assertIn('<input type="hidden" name="option" value="2">', forms[1])
        for f in forms[1:]:
            self.assertNotIn("textarea", f)
            self.assertNotIn("建議", f)
            self.assertNotIn("data-recommended", f)
        self.assertIn("重跑發版</button>", forms[1])
        self.assertIn("知道了</button>", forms[2])
        self.assertIn('<p class="recommendation">r &lt;u&gt;</p>', out)

    def test_read_only_viewer_sees_options_without_forms(self):
        import board
        out = board.render_card(self.card(self.ASK), (), None, False)
        self.assertNotIn("<form", out)
        self.assertNotIn("<textarea", out)
        divs = re.findall(r'<div class="option"(.*?)</div>', out)
        self.assertEqual(len(divs), 3)
        self.assertIn('data-action="group-ready" data-recommended="1"', divs[0])
        self.assertIn("建議", divs[0])
        self.assertIn('data-action="ack">', divs[2])
        self.assertIn('<p class="question">Which way?</p>', out)
        self.assertIn('<p class="recommendation">r &lt;u&gt;</p>', out)

    def test_every_page_action_has_a_label_and_the_board_takes_it(self):
        import board
        from core import pages
        self.assertEqual(set(board.ACT_LABELS), set(pages.ACTIONS))
        self.assertEqual(set(board.ACTIONS), set(pages.ACTIONS))
        self.assertEqual(board.ACT_LABELS["release"], "重跑發版")

    def test_act_problem_option_and_release(self):
        import board
        for option in ("", "1", "2", "3", "4"):
            with self.subTest(option=option):
                self.assertIsNone(board.act_problem("ack", "g1", "p1", "", option))
        for option in ("0", "5", "01", "1 ", "a", "-1"):
            with self.subTest(option=option):
                self.assertIsInstance(board.act_problem("ack", "g1", "p1", "", option), str)
        self.assertIsNone(board.act_problem("release", "g3", "p1", ""))
        self.assertIsInstance(board.act_problem("release", "3", "p1", ""), str)

    def test_act_command_release(self):
        import board
        self.assertEqual(board.act_command("release", "g3", "p1", "ignored", "a@x.com"),
                         ("tickets.py", ["release", "g3", "--by", "a@x.com", "--page", "p1"]))
        self.assertEqual(board.act_command("release", "g3", "", "", "user"),
                         ("tickets.py", ["release", "g3", "--by", "user"]))

    def test_page_script_keeps_prefilled_boxes_out_of_busy(self):
        """Static only (no browser here): restore over a prefilled box, busy only when edited."""
        import board
        self.assertIn("box.value === box.defaultValue", board.PAGE_SCRIPT)
        self.assertIn("b.value !== b.defaultValue", board.PAGE_SCRIPT)
        self.assertNotIn("!box.value", board.PAGE_SCRIPT)

    def test_readme_dashboard_names_release_and_options(self):
        with open(os.path.join(LOOP, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        section = text[text.index("## Dashboard"):]
        section = section[:section.index("\n## ", 1)]
        self.assertIn("| 重跑發版 | `tickets.py release gN` |", section)
        for word in ("question", "建議", "recommendation", "Read-only viewers", "free-text box"):
            self.assertIn(word, section)


class T57Acceptance(LoopCase):
    """Ticket #57: every 要你判 card carries the buttons its subject allows; pressing one (POST act)
    runs the matching script under the viewer's name (the trusted user's entry, else 'user'),
    acknowledges the card's page and leaves the outcome on the card. Each case runs on a real board
    subprocess, as a trusted user (B = 'a@x.com') or with the key (B = 'user'). Expected values come
    from the ticket text, never from board.py."""

    HEADER = "X-Auth-Request-Email"
    NAME = "a@x.com"
    KEY = "s3cret"
    TICKET_BUTTONS = [("revise", "照我的話改"), ("retry", "再試一次"), ("cancel", "取消")]
    GROUP_BUTTONS = [("group-ready", "帶著我的話重驗"), ("group-cancel", "取消這組")]
    RERUN_BUTTONS = [("rerun", "環境修好了,重跑")]
    ACK = ("ack", "知道了")

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)
        self.fixture()
        self.port = None
        self.by = None
        self.auth = {}

    # ------------------------------------------------------------ fixture

    def fixture(self):
        self.plan(creator={"2": ["revised"]})
        for ident in (1, 2, 5):
            self.add(ident)
        self.add(7, state="merged", group="g1")
        self.add(8, state="merged", group="g4")
        self.add(3, state="merged", group="g9")
        self.add(4, state="running", waiting={"reason": "quota", "until": "2026-01-01T00:00:00Z", "text": "limit"})
        git(self.proj, "branch", "int/g1")
        git(self.proj, "branch", "int/g4")

        def page(tx, name, kind, ref):
            tx.write("inbox/%s.json" % name, {"kind": kind, "ref": ref, "text": "page " + name,
                                              "at": osx.now(), "acked": False})

        def change(tx):
            for ident in ("1", "2", "5"):
                t = tx.ticket(ident)
                t.update(state="stuck", round=3, stuck={"reason": "rounds", "text": "x"})
                tx.put_ticket(t)
                page(tx, "p" + ident, "decision", ident)
            for gid, member in (("g1", "7"), ("g4", "8")):
                tx.put_group({"id": gid, "branch": "int/" + gid, "state": "stuck", "members": [member],
                              "fix_round": 3, "job": None})
            tx.put_group({"id": "g2", "branch": "int/g2", "state": "pr", "members": [], "fix_round": 0,
                          "history": [], "job": None,
                          "paused": {"reason": "env", "at": osx.now(), "text": "screen locked"}})
            tx.put_group({"id": "g9", "branch": "int/g9", "state": "pr", "members": ["3"], "fix_round": 0,
                          "history": [], "job": None})
            page(tx, "pg1", "decision", "g1")
            page(tx, "pg2", "env", "g2")
            page(tx, "preq", "decision", "request")
            page(tx, "p3", "decision", "3")
        self.pool.update(change, "test: #57 fixture")

    # ------------------------------------------------------------ server

    def serve(self, mode):
        """Start a board in mode 'trusted' or 'key' (the whole fixture is already written) and set
        self.auth (the headers every request carries) and self.by (B)."""
        extra = ["--host", "127.0.0.1"]
        if mode == "trusted":
            path = os.path.join(self.proj, "board", "config.json")
            with open(path) as fh:
                cfg = json.load(fh)
            cfg["board"] = {"trusted_user_header": self.HEADER, "allowed_users": [self.NAME]}
            with open(path, "w") as fh:
                json.dump(cfg, fh)
            self.auth, self.by = {self.HEADER: self.NAME}, self.NAME
        else:
            key = os.path.join(self.tmp, "key")
            with open(key, "w") as fh:
                fh.write(self.KEY + "\n")
            extra += ["--key-file", key]
            self.auth, self.by = {"X-Board-Key": self.KEY}, "user"
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "board.py"), "serve", "--port", str(self.port)]
                                + extra, cwd=self.proj, env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        deadline = time.time() + 5
        while True:
            try:
                self.call("GET", "/", auth=False)
                return
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.1)

    def call(self, method, path, auth=True, body=None, headers=None):
        """(status, headers dict, body text); a 303 is not followed."""
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            h = {"Content-Type": "application/x-www-form-urlencoded"}
            if auth:
                h.update(self.auth)
            h.update(headers or {})
            if method == "POST":
                h = own_origin(self.port, h)
            conn.request(method, path, body=body, headers=h)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read().decode("utf-8", "replace")
        finally:
            conn.close()

    def get(self, query, auth=True):
        status, _, text = self.call("GET", "/" + query, auth=auth)
        self.assertEqual(status, 200, text[:300])
        return text

    def act(self, auth=True, path="/act", headers=None, **form):
        import urllib.parse
        return self.call("POST", path, auth=auth, headers=headers, body=urllib.parse.urlencode(form))

    def web_root(self):
        return os.path.join(self.tmp, "wt", "runs", "web")

    def web_jobs(self):
        try:
            return set(os.listdir(self.web_root()))
        except FileNotFoundError:
            return set()

    def press(self, subject, page, action, text=""):
        """POST act; asserts the 303 and its Location and returns (job folder, job.json)."""
        import urllib.parse
        before = self.web_jobs()
        status, headers, body = self.act(subject=subject, page=page, action=action, text=text)
        self.assertEqual(status, 303, "POST act %s %s %s: %s" % (action, subject, page, body[:300]))
        draft = "act-" + subject + ("-" + page if page else "")
        self.assertEqual(headers.get("Location"), "./?tab=decide&sent=" + urllib.parse.quote(draft))
        new = sorted(self.web_jobs() - before)
        deadline = time.time() + 5
        while not new and time.time() < deadline:
            time.sleep(0.1)
            new = sorted(self.web_jobs() - before)
        self.assertEqual(len(new), 1, "one new job folder under <worktrees>/runs/web/")
        folder = os.path.join(self.web_root(), new[0])
        path = os.path.join(folder, "job.json")
        deadline = time.time() + 5
        while not os.path.isfile(path) and time.time() < deadline:
            time.sleep(0.1)
        with open(path) as fh:
            job = json.load(fh)
        for k in ("at", "what", "subject", "page", "action", "by"):
            self.assertIn(k, job, "job.json has %s" % k)
        self.assertEqual((job["subject"], job["page"], job["action"], job["by"]), (subject, page, action, self.by))
        return folder, job

    def wait_for(self, check, what):
        deadline = time.time() + 30
        while time.time() < deadline:
            snap = self.snap()
            if check(snap):
                return snap
            time.sleep(0.3)
        self.fail("within 30 s: " + what)

    def wait_rc(self, folder):
        deadline = time.time() + 30
        while time.time() < deadline and not os.path.isfile(os.path.join(folder, "rc")):
            time.sleep(0.2)
        self.assertTrue(os.path.isfile(os.path.join(folder, "rc")), "the job finished within 30 s")
        with open(os.path.join(folder, "rc")) as fh:
            return fh.read().strip()

    @staticmethod
    def events(row, name):
        return [e for e in row.get("history") or [] if e.get("event") == name]

    # ------------------------------------------------------------ page parsing

    @staticmethod
    def cards(page):
        """{subject: card} for every <section class="wait">: forms (attrs, hidden inputs, textareas,
        buttons as (value, name, label)) and last-act texts."""
        from html.parser import HTMLParser

        class P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.out, self.card, self.depth, self.form, self.button, self.last = {}, None, 0, None, None, None

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "section":
                    if self.card is None and "wait" in (a.get("class") or "").split():
                        self.card = {"subject": a.get("data-subject"), "page": a.get("data-page"),
                                     "kind": a.get("data-kind"), "forms": [], "last_act": []}
                        self.depth = 0
                    elif self.card is not None:
                        self.depth += 1
                if self.card is None:
                    return
                if tag == "form":
                    self.form = {"attrs": a, "hidden": [], "textareas": [], "buttons": []}
                    self.card["forms"].append(self.form)
                elif tag == "input" and self.form is not None and a.get("type") == "hidden":
                    self.form["hidden"].append((a.get("name"), a.get("value")))
                elif tag == "textarea" and self.form is not None:
                    self.form["textareas"].append(a)
                elif tag == "button" and self.form is not None:
                    self.button = [a.get("value"), a.get("name"), ""]
                elif tag == "p" and "last-act" in (a.get("class") or "").split():
                    self.last = []

            def handle_endtag(self, tag):
                if self.card is None:
                    return
                if tag == "button" and self.button is not None:
                    self.button[2] = self.button[2].strip()
                    self.form["buttons"].append(tuple(self.button))
                    self.button = None
                elif tag == "p" and self.last is not None:
                    self.card["last_act"].append("".join(self.last).strip())
                    self.last = None
                elif tag == "form":
                    self.form = None
                elif tag == "section":
                    if self.depth:
                        self.depth -= 1
                    else:
                        self.out[self.card["subject"]] = self.card
                        self.card = None

            def handle_data(self, data):
                if self.button is not None:
                    self.button[2] += data
                if self.last is not None:
                    self.last.append(data)

        p = P()
        p.feed(page)
        return p.out

    # ------------------------------------------------------------ item 1

    def check_forms(self):
        cards = self.cards(self.get("?tab=decide"))
        expected = {
            "1": ("p1", self.TICKET_BUTTONS + [self.ACK]),
            "2": ("p2", self.TICKET_BUTTONS + [self.ACK]),
            "5": ("p5", self.TICKET_BUTTONS + [self.ACK]),
            "g1": ("pg1", self.GROUP_BUTTONS + [self.ACK]),
            "g4": ("", self.GROUP_BUTTONS),
            "g2": ("pg2", self.RERUN_BUTTONS + [self.ACK]),
            "3": ("p3", [self.ACK]),
            "request": ("preq", [self.ACK]),
        }
        self.assertEqual(sorted(s for s, c in cards.items() if c["kind"] != "quota"), sorted(expected),
                         "the fixture's cards")
        for subject, (page_name, buttons) in expected.items():
            card = cards[subject]
            self.assertEqual(card["page"], page_name, subject)
            forms = [f for f in card["forms"] if f["attrs"].get("action") == "act"]
            self.assertEqual(len(forms), 1, "card %s holds one act form" % subject)
            self.assertEqual(len(card["forms"]), 1, "card %s holds only that form" % subject)
            form = forms[0]
            self.assertEqual((form["attrs"].get("method") or "").lower(), "post", subject)
            self.assertIn("act", (form["attrs"].get("class") or "").split(), subject)
            self.assertEqual(sorted(form["hidden"]), sorted([("subject", subject), ("page", page_name)]),
                             "hidden inputs of %s" % subject)
            self.assertEqual(len(form["textareas"]), 1, "one textarea on %s" % subject)
            box = form["textareas"][0]
            self.assertEqual(box.get("name"), "text", subject)
            self.assertEqual(box.get("maxlength"), "8000", subject)
            self.assertEqual(box.get("data-draft"), "act-" + subject + ("-" + page_name if page_name else ""), subject)
            self.assertEqual([(v, l) for v, _, l in form["buttons"]], buttons, "buttons of %s" % subject)
            self.assertEqual({n for _, n, _ in form["buttons"]}, {"action"}, "button names of %s" % subject)
        quota = [c for c in cards.values() if c["kind"] == "quota"]
        self.assertEqual([c["subject"] for c in quota], ["4"], "the quota card of #4 is shown")
        self.assertEqual(quota[0]["forms"], [], "a quota card has no form")

    def test_item1_trusted_each_card_has_its_buttons_in_order(self):
        self.serve("trusted")
        self.check_forms()

    def test_item1_key_each_card_has_its_buttons_in_order(self):
        self.serve("key")
        self.check_forms()

    def test_item1_a_read_only_viewer_gets_the_cards_without_forms(self):
        self.serve("trusted")
        cards = self.cards(self.get("?tab=decide"))
        self.assertIn("1", cards)
        self.assertEqual(len([f for f in cards["1"]["forms"] if f["attrs"].get("action") == "act"]), 1,
                         "a trusted viewer gets the act form on #1")
        plain = self.get("?tab=decide", auth=False)
        plain_cards = self.cards(plain)
        self.assertEqual(sorted(plain_cards), sorted(cards), "the read-only viewer gets the same cards")
        self.assertEqual(sum(len(c["forms"]) for c in plain_cards.values()), 0, "no form for a read-only viewer")
        self.assertNotRegex(plain, r"""action=["']?act["' >]""")

    # ------------------------------------------------------------ item 2

    def check_bad_posts(self):
        long_ = "a" * 121
        bad = [
            dict(subject="1", page="p1", action="bogus", text=""),
            dict(subject="1", page="p1", text=""),                       # no action
            dict(subject="1", page="p1", action="answer", text=""),
            dict(subject="g1", page="pg1", action="revise", text="x"),
            dict(subject="g1", page="pg1", action="retry", text=""),
            dict(subject="abc", page="p1", action="cancel", text=""),
            dict(subject="1", page="p1", action="group-ready", text="x"),
            dict(subject="5", page="p5", action="group-cancel", text=""),
            dict(subject="x2", page="pg2", action="rerun", text=""),
            dict(subject="g", page="pg2", action="rerun", text=""),
            dict(subject="request", page="", action="ack", text=""),
            dict(subject="request", action="ack", text=""),                # no page field
            dict(subject="a b", page="preq", action="ack", text=""),
            dict(subject="", page="preq", action="ack", text=""),
            dict(subject=long_, page="preq", action="ack", text=""),
            dict(subject="request", page="p/q", action="ack", text=""),
            dict(subject="1", page="p 1", action="retry", text=""),
            dict(subject="1", page=long_, action="retry", text=""),
            dict(subject="1", page="p1", action="revise", text=""),
            dict(subject="1", page="p1", action="revise", text="  \n "),
        ]
        for form in bad:
            before = self.web_jobs()
            status, _, body = self.act(**form)
            self.assertEqual(status, 400, "POST act %r: %s" % (form, body[:200]))
            self.assertTrue(body.strip(), "a 400 says why: %r" % (form,))
            self.assertNotIn("\n", body.strip(), "one line for %r" % (form,))
            self.assertEqual(self.web_jobs(), before, "nothing started for %r" % (form,))

    def test_item2_trusted_bad_posts_are_400_and_no_header_is_403(self):
        self.serve("trusted")
        self.check_bad_posts()
        before = self.web_jobs()
        status, _, _ = self.act(auth=False, subject="1", page="p1", action="retry", text="")
        self.assertEqual(status, 403, "POST act without the trusted header")
        self.assertEqual(self.web_jobs(), before)
        snap = self.snap()
        self.assertEqual(snap.ticket("1")["state"], "stuck")
        self.assertFalse(snap.inbox["p1"].get("acked"))

    def test_item2_key_bad_posts_are_400_and_a_wrong_key_is_403(self):
        self.serve("key")
        self.check_bad_posts()
        before = self.web_jobs()
        status, _, _ = self.act(auth=False, headers={"X-Board-Key": "wrong"},
                                subject="1", page="p1", action="retry", text="")
        self.assertEqual(status, 403, "POST act with a wrong key")
        self.assertEqual(self.web_jobs(), before)
        snap = self.snap()
        self.assertEqual(snap.ticket("1")["state"], "stuck")
        self.assertFalse(snap.inbox["p1"].get("acked"))

    # ------------------------------------------------------------ items 2-4: each action end to end

    def check_retry(self):
        self.press("1", "p1", "retry")
        snap = self.wait_for(lambda s: s.ticket("1")["state"] == "ready" and s.inbox["p1"].get("acked"),
                             "#1 ready and p1 acked")
        self.assertEqual(snap.ticket("1")["round"], 0)
        self.assertEqual(snap.inbox["p1"].get("acked_by"), self.by)
        ev = self.events(snap.ticket("1"), "user answer")
        self.assertTrue(ev, "#1 has a 'user answer' event")
        self.assertEqual((ev[-1].get("by"), ev[-1].get("decision")), (self.by, "retry"), ev[-1])

    def check_revise(self):
        text = "drop the second item please"
        self.press("2", "p2", "revise", text)
        snap = self.wait_for(lambda s: s.ticket("2")["state"] == "ready" and s.inbox["p2"].get("acked"),
                             "#2 ready and p2 acked")
        prompts = glob.glob(os.path.join(self.tmp, "wt", "runs", "2", "revise-*", "prompt.md"))
        self.assertEqual(len(prompts), 1, prompts)
        with open(prompts[0], encoding="utf-8") as fh:
            prompt = fh.read()
        inside = re.findall(r"<<<WEB INPUT(.*?)WEB INPUT>>>", prompt, re.S)
        self.assertTrue(any(text in part for part in inside), "the text sits between the web-input markers")
        self.assertEqual(snap.inbox["p2"].get("acked_by"), self.by)
        ev = self.events(snap.ticket("2"), "user answer")
        self.assertTrue(ev, "#2 has a 'user answer' event")
        self.assertEqual((ev[-1].get("by"), ev[-1].get("decision")), (self.by, "revise"), ev[-1])

    def check_cancel(self):
        self.press("5", "p5", "cancel", "not needed")
        snap = self.wait_for(lambda s: s.ticket("5")["state"] == "cancelled" and s.inbox["p5"].get("acked"),
                             "#5 cancelled and p5 acked")
        self.assertEqual(snap.inbox["p5"].get("acked_by"), self.by)
        ev = self.events(snap.ticket("5"), "user answer")
        self.assertTrue(ev, "#5 has a 'user answer' event")
        self.assertEqual((ev[-1].get("by"), ev[-1].get("decision")), (self.by, "cancel"), ev[-1])

    def check_group_ready(self):
        text = "use the newer output"
        self.press("g1", "pg1", "group-ready", text)
        snap = self.wait_for(lambda s: s.groups["g1"]["state"] == "pr" and s.inbox["pg1"].get("acked"),
                             "g1 state pr and pg1 acked")
        note = (snap.groups["g1"].get("notes") or [{}])[-1]
        self.assertEqual((note.get("text"), note.get("by"), note.get("via")), (text, self.by, "web"), note)
        ev = self.events(snap.groups["g1"], "ready again by the user")
        self.assertTrue(ev, "g1 has 'ready again by the user'")
        self.assertEqual(ev[-1].get("by"), self.by)
        self.assertEqual(snap.inbox["pg1"].get("acked_by"), self.by)

    def check_group_cancel(self):
        text = "not worth it"
        self.press("g4", "", "group-cancel", text)
        snap = self.wait_for(lambda s: s.groups["g4"]["state"] == "cancelled", "g4 cancelled")
        ev = self.events(snap.groups["g4"], "cancelled by the user")
        self.assertTrue(ev, "g4 has 'cancelled by the user'")
        self.assertEqual((ev[-1].get("by"), ev[-1].get("why")), (self.by, text), ev[-1])

    def check_rerun(self):
        text = "screen unlocked"
        self.press("g2", "pg2", "rerun", text)
        snap = self.wait_for(lambda s: not s.groups["g2"].get("paused") and s.inbox["pg2"].get("acked"),
                             "g2 not paused and pg2 acked")
        ev = self.events(snap.groups["g2"], "environment fixed: verify again")
        self.assertTrue(ev, "g2 has 'environment fixed: verify again'")
        self.assertEqual(ev[-1].get("by"), self.by)
        self.assertEqual(snap.inbox["pg2"].get("acked_by"), self.by)

    def check_ack(self):
        self.press("3", "p3", "ack")
        snap = self.wait_for(lambda s: s.inbox["p3"].get("acked"), "p3 acked")
        self.assertEqual(snap.inbox["p3"].get("acked_by"), self.by)
        ev = [e for e in self.events(snap.ticket("3"), "page acknowledged") if e.get("page") == "p3"]
        self.assertTrue(ev, "#3 has 'page acknowledged' for p3")
        self.assertEqual(ev[-1].get("by"), self.by)
        self.press("request", "preq", "ack")
        snap = self.wait_for(lambda s: s.inbox["preq"].get("acked"), "preq acked")
        self.assertEqual(snap.inbox["preq"].get("acked_by"), self.by)

    def test_item4_trusted_retry(self):
        self.serve("trusted")
        self.check_retry()

    def test_item4_key_retry(self):
        self.serve("key")
        self.check_retry()

    def test_item4_trusted_revise(self):
        self.serve("trusted")
        self.check_revise()

    def test_item4_key_revise(self):
        self.serve("key")
        self.check_revise()

    def test_item4_trusted_cancel(self):
        self.serve("trusted")
        self.check_cancel()

    def test_item4_key_cancel(self):
        self.serve("key")
        self.check_cancel()

    def test_item4_trusted_group_ready(self):
        self.serve("trusted")
        self.check_group_ready()

    def test_item4_key_group_ready(self):
        self.serve("key")
        self.check_group_ready()

    def test_item4_trusted_group_cancel(self):
        self.serve("trusted")
        self.check_group_cancel()

    def test_item4_key_group_cancel(self):
        self.serve("key")
        self.check_group_cancel()

    def test_item4_trusted_rerun(self):
        self.serve("trusted")
        self.check_rerun()

    def test_item4_key_rerun(self):
        self.serve("key")
        self.check_rerun()

    def test_item4_trusted_ack(self):
        self.serve("trusted")
        self.check_ack()

    def test_item4_key_ack(self):
        self.serve("key")
        self.check_ack()

    # ------------------------------------------------------------ item 5

    def check_refused(self):
        folder, job = self.press("3", "p3", "retry")
        self.assertEqual(self.wait_rc(folder), "2", "ticket_creator refuses a merged ticket")
        self.assertFalse(self.snap().inbox["p3"].get("acked"), "the refused answer leaves p3 unread")
        cards = self.cards(self.get("?tab=decide"))
        self.assertIn("3", cards, "the card of #3 is still shown")
        lines = cards["3"]["last_act"]
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("上次的動作:" + job["what"] + " · " + self.by + " · "), lines[0])
        self.assertIn("沒有執行(rc 2)", lines[0])
        self.assertIn("frozen", lines[0])

    def test_item5_trusted_a_refused_retry_shows_on_the_card(self):
        self.serve("trusted")
        self.check_refused()

    def test_item5_key_a_refused_retry_shows_on_the_card(self):
        self.serve("key")
        self.check_refused()

    def test_item5_a_finished_action_shows_done_and_leaves_only_ack(self):
        self.serve("key")
        folder, job = self.press("g1", "", "group-ready", "go again")
        self.assertEqual(self.wait_rc(folder), "0")
        cards = self.cards(self.get("?tab=decide"))
        self.assertIn("g1", cards, "pg1 is unread: g1 still has a card")
        self.assertEqual(cards["g1"]["last_act"], ["上次的動作:%s · %s · 完成" % (job["what"], self.by)])
        forms = cards["g1"]["forms"]
        self.assertEqual(len(forms), 1)
        self.assertEqual([(v, l) for v, _, l in forms[0]["buttons"]], [self.ACK],
                         "a group neither stuck nor paused has only 知道了")
        self.assertEqual(cards["1"]["last_act"], [], "no job for #1: no last-act line")

    # ------------------------------------------------------------ item 6 (first sentence)

    def check_tickets_tab(self):
        page = self.get("?tab=tickets")
        self.assertTrue("要你決定的事在「要你判」分頁" in page, "the 票 tab points to 要你判")
        self.assertIsNone(re.search(r"""action=["']?answer""", page), "no answer form on the 票 tab")
        self.assertFalse("不改,再試一次" in page, "no 不改,再試一次 on the 票 tab")
        before = self.web_jobs()
        status, _, _ = self.act(path="/answer", ticket="1", page="p1", decision="retry", text="")
        self.assertEqual(status, 404, "POST answer")
        self.assertEqual(self.web_jobs(), before)

    def test_item6_trusted_tickets_tab_has_no_answer_form_and_answer_is_gone(self):
        self.serve("trusted")
        self.check_tickets_tab()

    def test_item6_key_tickets_tab_has_no_answer_form_and_answer_is_gone(self):
        self.serve("key")
        self.check_tickets_tab()


if __name__ == "__main__":
    unittest.main()
