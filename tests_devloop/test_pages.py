"""pages and the inbox: page names, questions and options, answers, wait"""

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


class ReportNameUnit(unittest.TestCase):
    def test_two_pages_of_one_kind_and_ref_in_one_second_both_stay(self):
        path = tempfile.mkdtemp(prefix="pool-report-")
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        pool = Pool(os.path.join(path, "pool"), remote=None).init()
        with mock.patch("core.osx.now", return_value="2026-01-01T00:00:00Z"):
            first = pool.report("decision", "g1", "one")
            second = pool.report("decision", "g1", "two")
        self.assertNotEqual(first, second)
        inbox = pool.snapshot().inbox
        self.assertEqual(sorted(r["text"] for r in inbox.values()), ["one", "two"])
        self.assertEqual(inbox[second]["text"], "two")


class PageLinesAndWait(LoopCase):
    def _tickets(self):
        sys.path.insert(0, LOOP)
        import tickets
        return tickets

    def test_page_lines_is_the_header_and_at_most_eight_text_lines(self):
        t = self._tickets()
        row = {"kind": "decision", "ref": "4", "text": "\n".join("l%d" % i for i in range(12))}
        lines = t.page_lines("p1", row)
        self.assertEqual(lines[0], "* decision 4          p1")
        self.assertEqual(lines[1:], ["    l%d" % i for i in range(8)])
        row["acked"] = True
        self.assertTrue(t.page_lines("p1", row)[0].startswith(". "))

    def test_inbox_prints_what_page_lines_returns(self):
        t = self._tickets()
        row = {"kind": "env", "ref": "g1", "text": "a\nb"}
        self.pool.update(lambda tx: tx.write("inbox/pw1.json", row), "test")
        res = self.run_py("tickets.py", "inbox")
        self.assertEqual(res.stdout, "\n".join(t.page_lines("pw1", row)) + "\n")

    def test_wait_rejects_a_bad_kind_and_times_out_quietly(self):
        res = self.run_py("tickets.py", "wait", "--kind", "nosuch")
        self.assertEqual((res.returncode, res.stdout), (2, ""))
        self.assertTrue(res.stderr.startswith("tickets:"))
        res = self.run_py("tickets.py", "wait", "--timeout", "0")
        self.assertEqual((res.returncode, res.stdout), (2, ""))
        res = self.run_py("tickets.py", "wait", "--timeout", "0.5")
        self.assertEqual(res.returncode, 1)
        self.assertEqual(res.stderr, "")
        self.assertTrue(res.stdout.startswith("wait: no new page"))


class T50Acceptance(LoopCase):
    """#50: a user's answer names who gave it (--by) and closes the page it answered (--page)."""

    NAME = "a@x.com"

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)

    def page(self, name, ref, kind="decision"):
        def change(tx):
            tx.write("inbox/%s.json" % name, {"kind": kind, "ref": str(ref), "text": "stuck",
                                              "at": osx.now(), "acked": False})
        self.pool.update(change, "test: page %s" % name)

    def stuck(self, ident, page=None):
        self.add(ident)

        def change(tx):
            t = tx.ticket(str(ident))
            t.update(state="stuck", round=3, stuck={"reason": "rounds", "text": "x"})
            tx.put_ticket(t)
        self.pool.update(change, "test: stuck")
        if page:
            self.page(page, ident)

    def stuck_group(self, gid="g1", members=("7",)):
        for m in members:
            self.add(m, state="merged", group=gid)
        git(self.proj, "branch", "int/%s" % gid)
        self.pool.update(lambda tx: tx.put_group({"id": gid, "branch": "int/%s" % gid, "state": "stuck",
                                                  "members": list(members), "fix_round": 3, "job": None}),
                         "test: stuck group")

    def commits(self):
        return int(git(self.pool_path, "rev-list", "--count", "HEAD"))

    def new_events(self, before, row):
        hist = row.get("history") or []
        self.assertEqual(hist[:len(before)], before, "earlier history was changed")
        return hist[len(before):]

    def answer_event(self, events, decision, text, by, page=None):
        names = [e.get("event") for e in events]
        self.assertIn("user answer", names, events)
        self.assertEqual(names.count("user answer"), 1, events)
        ev = events[names.index("user answer")]
        self.assertEqual(ev.get("decision"), decision, ev)
        self.assertEqual(ev.get("text"), text, ev)
        self.assertEqual(ev.get("by"), by, ev)
        if page is None:
            self.assertNotIn("page", ev)
        else:
            self.assertEqual(ev.get("page"), page, ev)
        return names.index("user answer")

    def assert_acked(self, row, by, t0, t1):
        self.assertTrue(row.get("acked"), row)
        self.assertEqual(row.get("acked_by"), by, row)
        self.assertRegex(row.get("acked_at") or "", r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertTrue(t0 <= row["acked_at"] <= t1, (t0, row["acked_at"], t1))

    # ------------------------------------------------ item 1 and 3: ticket_creator --from user

    def test_item1_3_retry_and_cancel_record_a_user_answer(self):
        self.stuck(1, page="p1")
        self.stuck(2, page="p2")
        self.stuck(3)
        reason = "r" * 1500 + "s" * 1000
        rfile = os.path.join(self.tmp, "reason.txt")
        with open(rfile, "w") as fh:
            fh.write(reason)
        before = [self.snap().ticket(i).get("history") or [] for i in ("1", "2", "3")]
        t0 = osx.now()
        res = self.run_py("ticket_creator.py", "--revise", "1", "--from", "user", "--decision", "retry",
                          "--reason-file", rfile, "--page", "p1", "--by", self.NAME)
        t1 = osx.now()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        res = self.run_py("ticket_creator.py", "--revise", "2", "--from", "user", "--decision", "cancel",
                          "--reason", "not needed", "--page", "p2")
        t2 = osx.now()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        res = self.run_py("ticket_creator.py", "--revise", "3", "--from", "user", "--decision", "cancel",
                          "--by", self.NAME)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "wt", "runs", "1")), "no agent ran")
        # ticket 1: retry, reason cut to 2000 characters, by NAME, page p1
        t = snap.ticket("1")
        self.assertEqual(t["state"], "ready")
        ev = self.new_events(before[0], t)
        i = self.answer_event(ev, "retry", reason[:2000], self.NAME, "p1")
        names = [e.get("event") for e in ev]
        self.assertIn("user decision: ready", names)
        self.assertLess(i, names.index("user decision: ready"), names)
        self.assertEqual(ev[names.index("user decision: ready")].get("by"), self.NAME)
        self.assert_acked(snap.inbox["p1"], self.NAME, t0, t1)
        # ticket 2: cancel without --by: by 'user', acked_by 'answer'
        t = snap.ticket("2")
        self.assertEqual(t["state"], "cancelled")
        ev = self.new_events(before[1], t)
        i = self.answer_event(ev, "cancel", "not needed", "user", "p2")
        names = [e.get("event") for e in ev]
        self.assertLess(i, names.index("user decision: cancelled"), names)
        self.assert_acked(snap.inbox["p2"], "answer", t1, t2)
        # ticket 3: cancel without reason and without --page: text '', no page field
        t = snap.ticket("3")
        self.assertEqual(t["state"], "cancelled")
        ev = self.new_events(before[2], t)
        i = self.answer_event(ev, "cancel", "", self.NAME)
        names = [e.get("event") for e in ev]
        self.assertLess(i, names.index("user decision: cancelled"), names)

    def test_item1_3_revise_records_a_user_answer_before_the_opener(self):
        self.stuck(1, page="p1")
        self.plan(creator={"1": ["revised"]})
        before = self.snap().ticket("1").get("history") or []
        t0 = osx.now()
        res = self.run_py("ticket_creator.py", "--revise", "1", "--from", "user", "--decision", "revise",
                          "--reason", "drop the second item", "--page", "p1", "--by", self.NAME)
        t1 = osx.now()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        t = snap.ticket("1")
        self.assertEqual(t["state"], "ready")
        ev = self.new_events(before, t)
        i = self.answer_event(ev, "revise", "drop the second item", self.NAME, "p1")
        names = [e.get("event") for e in ev]
        # a user revise goes through user_reset: 'user decision: ready' (by NAME), never 'revised'
        self.assertIn("user decision: ready", names, ev)
        self.assertNotIn("revised", names, ev)
        self.assertLess(i, names.index("user decision: ready"), names)
        self.assertEqual(ev[names.index("user decision: ready")].get("by"), self.NAME, ev)
        self.assert_acked(snap.inbox["p1"], self.NAME, t0, t1)

    def test_item1_unusable_opener_answer_still_leaves_the_user_answer(self):
        self.stuck(1, page="p1")
        self.plan(creator={"1": ["tickets"]})       # not a revise outcome
        before = self.snap().ticket("1").get("history") or []
        res = self.run_py("ticket_creator.py", "--revise", "1", "--from", "user", "--decision", "revise",
                          "--reason", "do it differently", "--page", "p1", "--by", self.NAME)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        snap = self.snap()
        t = snap.ticket("1")
        self.assertEqual(t["state"], "stuck")
        self.answer_event(self.new_events(before, t), "revise", "do it differently", self.NAME, "p1")
        self.assertFalse(snap.inbox["p1"]["acked"], "the decision was not handled: the page stays")

    # ------------------------------------------------ item 2: a running ticket

    def test_item2_running_ticket_refused_before_anything(self):
        self.add(1, state="running")
        self.page("p1", 1)
        self.plan(creator={"1": ["revised"]})
        before = self.snap().ticket("1")
        n = self.commits()
        res = self.run_py("ticket_creator.py", "--revise", "1", "--from", "user", "--decision", "revise",
                          "--reason", "x", "--page", "p1", "--by", self.NAME)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "wt", "runs", "1")),
                         "the opener ran for a running ticket")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        lines = [ln for ln in res.stderr.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, res.stderr)
        self.assertIn("#1", lines[0])
        self.assertIn("running", lines[0])
        snap = self.snap()
        self.assertEqual(snap.ticket("1"), before)
        self.assertFalse(snap.inbox["p1"]["acked"])
        self.assertEqual(self.commits(), n, "a refused answer wrote to the pool")

    # ------------------------------------------------ item 4: ready gN --by --page

    def test_item4_ready_group_by_and_page_in_one_commit(self):
        self.stuck_group()
        self.page("p1", "g1")
        self.page("p2", "g1")
        n = self.commits()
        t0 = osx.now()
        res = self.run_py("tickets.py", "ready", "g1", "--note", "keep the newer output",
                          "--by", self.NAME, "--page", "p1")
        t1 = osx.now()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.commits(), n + 1, "the group change and the page are not one pool commit")
        snap = self.snap()
        g = snap.groups["g1"]
        self.assertEqual(g["state"], "pr", g)
        ev = g["history"][-1]
        self.assertEqual(ev.get("event"), "ready again by the user", ev)
        self.assertEqual(ev.get("by"), self.NAME, ev)
        self.assertEqual(g["notes"][-1]["by"], self.NAME, g["notes"])
        self.assertEqual(g["notes"][-1]["text"], "keep the newer output")
        self.assert_acked(snap.inbox["p1"], self.NAME, t0, t1)
        self.assertFalse(snap.inbox["p2"]["acked"])
        # without --by and --page: by 'user', no page touched
        self.pool.update(lambda tx: tx.put_group(dict(tx.data.groups["g1"], state="stuck", fix_round=3)),
                         "test: stuck again")
        res = self.run_py("tickets.py", "ready", "g1", "--note", "again")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        g = snap.groups["g1"]
        self.assertEqual(g["history"][-1].get("by"), "user", g["history"][-1])
        self.assertEqual(g["notes"][-1]["by"], "user", g["notes"])
        self.assertFalse(snap.inbox["p2"]["acked"])

    # ------------------------------------------------ item 5: cancel gN --by --page

    def test_item5_cancel_group_by_and_page(self):
        self.stuck_group("g1", ("7", "8"))
        self.page("p1", "g1")
        t0 = osx.now()
        res = self.run_py("tickets.py", "cancel", "g1", "--why", "obsolete", "--by", self.NAME, "--page", "p1")
        t1 = osx.now()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        g = snap.groups["g1"]
        self.assertEqual(g["state"], "cancelled")
        ev = g["history"][-1]
        self.assertEqual((ev.get("event"), ev.get("by"), ev.get("why")),
                         ("cancelled by the user", self.NAME, "obsolete"), ev)
        for m in ("7", "8"):
            t = snap.ticket(m)
            self.assertEqual(t["state"], "cancelled")
            ev = t["history"][-1]
            self.assertEqual((ev.get("event"), ev.get("by"), ev.get("why")),
                             ("cancelled with group g1", self.NAME, "obsolete"), ev)
        texts = [r["text"] for r in snap.inbox.values() if r.get("kind") == "cancelled" and r.get("ref") == "g1"]
        self.assertEqual(texts, ["group g1 cancelled by %s (obsolete); cancelled tickets: #7, #8" % self.NAME])
        self.assert_acked(snap.inbox["p1"], self.NAME, t0, t1)
        # p1 is acked in the commit that cancels the group
        last = git(self.pool_path, "log", "-1", "--format=%H", "--", "inbox/p1.json")
        changed = git(self.pool_path, "show", "--name-only", "--format=", last).split()
        self.assertIn("groups/g1.json", changed)
        self.assertIn("tickets/7.json", changed)
        # without --by: today's text
        self.stuck_group("g2", ("9",))
        res = self.run_py("tickets.py", "cancel", "g2", "--why", "w")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        texts = [r["text"] for r in snap.inbox.values() if r.get("kind") == "cancelled" and r.get("ref") == "g2"]
        self.assertEqual(texts, ["group g2 cancelled by the user (w); cancelled tickets: #9"])
        self.assertEqual(snap.groups["g2"]["history"][-1].get("by"), "user")

    # ------------------------------------------------ item 7: ack --by

    def test_item7_ack_by_writes_on_the_subject(self):
        self.add(1, state="stuck")
        self.stuck_group()
        self.page("p1", "1")
        self.page("p2", "g1")
        self.page("p3", "request", kind="created")
        before_t = self.snap().ticket("1").get("history") or []
        before_g = self.snap().groups["g1"].get("history") or []
        before_7 = self.snap().ticket("7").get("history") or []
        for page, subject in (("p1", "ticket"), ("p2", "group"), ("p3", None)):
            n = self.commits()
            t0 = osx.now()
            res = self.run_py("tickets.py", "ack", page, "--by", self.NAME)
            t1 = osx.now()
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(self.commits(), n + 1, "ack %s is not one pool commit" % page)
            self.assert_acked(self.snap().inbox[page], self.NAME, t0, t1)
        snap = self.snap()
        ev = self.new_events(before_t, snap.ticket("1"))
        self.assertEqual([(e.get("event"), e.get("page"), e.get("by")) for e in ev],
                         [("page acknowledged", "p1", self.NAME)])
        ev = self.new_events(before_g, snap.groups["g1"])
        self.assertEqual([(e.get("event"), e.get("page"), e.get("by")) for e in ev],
                         [("page acknowledged", "p2", self.NAME)])
        self.assertEqual(snap.ticket("7").get("history") or [], before_7)

    def test_item7_ack_all_by_and_ack_without_by(self):
        self.add(1, state="stuck")
        self.page("p1", "1")
        self.page("p2", "1")
        self.page("p3", "1")
        res = self.run_py("tickets.py", "ack", "p3")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        self.assertTrue(snap.inbox["p3"]["acked"])
        self.assertNotIn("acked_by", snap.inbox["p3"])
        before = snap.ticket("1").get("history") or []
        self.assertEqual(before, [e for e in before if e.get("event") != "page acknowledged"])
        p3 = dict(snap.inbox["p3"])
        t0 = osx.now()
        res = self.run_py("tickets.py", "ack", "--all", "--by", self.NAME)
        t1 = osx.now()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        for page in ("p1", "p2"):
            self.assert_acked(snap.inbox[page], self.NAME, t0, t1)
        self.assertEqual(snap.inbox["p3"], p3, "an already acked page was changed")
        self.assertEqual(snap.ticket("1").get("history") or [], before, "ack --all wrote an event")


class AnswerNaming(LoopCase):
    """ack_in, ack --by and the user answer event (the implementer's unit cases)."""

    def page(self, name, ref):
        self.pool.update(lambda tx: tx.write("inbox/%s.json" % name, {
            "kind": "decision", "ref": ref, "text": "t", "at": osx.now(), "acked": False}), "test: page")

    def test_ack_in_marks_a_page_and_returns_none_for_a_missing_one(self):
        import tickets
        self.page("p1", "1")
        got = []

        def change(tx):
            got.append(tickets.ack_in(tx, "p1", "a@x.com"))
            got.append(tickets.ack_in(tx, "nope", "a@x.com"))
        self.pool.update(change, "test")
        self.assertIsNone(got[1])
        row = self.snap().inbox["p1"]
        self.assertTrue(row["acked"])
        self.assertEqual(row["acked_by"], "a@x.com")

    def test_ack_by_writes_an_event_on_the_subject_and_all_writes_none(self):
        self.add(1)
        self.page("p1", "1")
        self.page("p2", "1")
        res = self.run_py("tickets.py", "ack", "p1", "--by", "a@x.com")
        self.assertEqual(res.returncode, 0, res.stderr)
        hist = self.snap().tickets["1"]["history"]
        self.assertEqual([(h["event"], h["page"], h["by"]) for h in hist if h["event"] == "page acknowledged"],
                         [("page acknowledged", "p1", "a@x.com")])
        res = self.run_py("tickets.py", "ack", "--all", "--by", "b@x.com")
        self.assertEqual(res.returncode, 0, res.stderr)
        snap = self.snap()
        self.assertEqual(snap.inbox["p2"]["acked_by"], "b@x.com")
        self.assertEqual(len([h for h in snap.tickets["1"]["history"] if h["event"] == "page acknowledged"]), 1)

    def test_cancel_ticket_by_name_acks_its_page(self):
        self.add(1)
        self.page("p1", "1")
        res = self.run_py("tickets.py", "cancel", "1", "--why", "w", "--by", "a@x.com", "--page", "p1")
        self.assertEqual(res.returncode, 0, res.stderr)
        snap = self.snap()
        self.assertEqual(snap.tickets["1"]["history"][-1]["by"], "a@x.com")
        self.assertEqual(snap.inbox["p1"]["acked_by"], "a@x.com")



class PageQuestionPieces(LoopCase):
    """core/pages.py, Pool.report with a question, inbox lines and release --by/--page (#78, the
    implementer's unit cases)."""

    def setUp(self):
        super().setUp()
        for key in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(key, None)

    def opts3(self):
        from core import pages
        return [pages.option("retry", "try again"), pages.option("revise", "rewrite"),
                pages.option("cancel", "drop it")]

    def test_one_line_and_ask(self):
        from core import pages
        got = pages.ask(" q\nx ", [], "r" * 300)
        self.assertEqual(got["question"], "q x")
        self.assertEqual(len(got["recommendation"]), 200)
        self.assertEqual(pages.option("ack", "a\nb"), {"action": "ack", "note": "", "consequence": "a b"})

    def test_problems_accepts_the_valid_sets_and_no_keys(self):
        from core import pages
        self.assertEqual(pages.problems("7", {"text": "x"}), [])
        self.assertEqual(pages.problems("7", pages.ask("q", self.opts3(), "r")), [])
        self.assertEqual(pages.problems("controller", pages.ask("q", [pages.option("ack", "ok")], "r")), [])

    def test_problems_each_rule(self):
        from core import pages
        good = pages.ask("q", self.opts3(), "r")
        bad = {
            "missing": {"question": "q"},
            "question newline": dict(good, question="a\nb"),
            "empty recommendation": dict(good, recommendation="  "),
            "long question": dict(good, question="q" * 201),
            "one non-ack": dict(good, options=[pages.option("retry", "c")]),
            "five": dict(good, options=self.opts3() + self.opts3()[:2]),
            "not dicts": dict(good, options=["retry", "cancel"]),
            "unknown action": dict(good, options=[pages.option("push", "c"), pages.option("ack", "c")]),
            "consequence newline": dict(good, options=[{"action": "retry", "consequence": "a\nb"},
                                                       pages.option("ack", "c")]),
            "note type": dict(good, options=[pages.option("retry", "c", note=3), pages.option("ack", "c")]),
            "note long": dict(good, options=[pages.option("retry", "c", note="n" * 8001),
                                             pages.option("ack", "c")]),
        }
        for name, row in bad.items():
            with self.subTest(name):
                self.assertTrue(pages.problems("7", row))
        with self.subTest("group action on a ticket"):
            self.assertTrue(pages.problems("7", dict(good, options=[pages.option("release", "c"),
                                                                    pages.option("ack", "c")])))
        with self.subTest("ticket action on a group"):
            self.assertTrue(pages.problems("g3", good))
        self.assertEqual(pages.problems("7", dict(good, options=[pages.option("retry", "c", note="a\nb"),
                                                                 pages.option("ack", "c")])), [])

    def test_command_strings(self):
        from core import pages
        self.assertEqual(pages.command("group-ready", "g15", "p1", "follow #67"),
                         "actl tickets ready g15 --note 'follow #67' --page p1")
        self.assertEqual(pages.command("retry", "7", "p2", ""), "actl new --revise 7 --from user --decision retry --page p2")
        self.assertEqual(pages.command("revise", "7", "p2", ""),
                         "actl new --revise 7 --from user --decision revise --reason '<your words>' --page p2")
        self.assertEqual(pages.command("group-cancel", "g3", "p3", ""), "actl tickets cancel g3 --why '' --page p3")
        self.assertEqual(pages.command("ack", "g3", "p3", "x"), "actl tickets ack p3")
        self.assertEqual(pages.command("release", "g3", "p3", "x"), "actl tickets release g3 --page p3")

    def test_pages_does_not_import_pool(self):
        with open(os.path.join(LOOP, "core", "pages.py")) as fh:
            self.assertNotIn("pool", "".join(l for l in fh if l.startswith(("import", "from"))))

    def test_report_stores_a_valid_set_and_drops_a_bad_one(self):
        from core import pages
        name = self.pool.report("decision", "7", "stuck", **pages.ask("q?", self.opts3()[:2], "because"))
        row = self.snap().inbox[name]
        self.assertEqual(row["options"][0], {"action": "retry", "note": "", "consequence": "try again"})
        self.assertEqual((row["question"], row["recommendation"]), ("q?", "because"))
        name = self.pool.report("decision", "8", "stuck", question="q?")
        row = self.snap().inbox[name]
        self.assertFalse(set(pages.KEYS) & set(row))
        self.assertTrue(row["text"].startswith("stuck\n(options dropped: "), row["text"])
        name = self.pool.report("decision", "9", "plain")
        self.assertEqual(self.snap().inbox[name]["text"], "plain")

    def test_inbox_prints_options_and_old_pages_unchanged(self):
        import tickets
        from core import pages
        row = {"kind": "decision", "ref": "7", "text": "t1\nt2", "at": osx.now(), "acked": False}
        old = tickets.page_lines("p0", row)
        self.assertEqual(old, ["* decision 7          p0", "    t1", "    t2"])
        with_q = dict(row, **pages.ask("q?", self.opts3()[:2], "because"))
        self.assertEqual(tickets.page_lines("p0", with_q), old + [
            "    question: q?",
            "    1. retry (recommended): try again",
            "       $ actl new --revise 7 --from user --decision retry --page p0",
            "    2. revise: rewrite",
            "       $ actl new --revise 7 --from user --decision revise --reason '<your words>' --page p0",
            "    recommendation: because"])
        # stored keys that fail problems() (ticket actions on a group) print as a page without them
        self.assertEqual(tickets.page_lines("p0", dict(with_q, ref="g1")), tickets.page_lines("p0", dict(row, ref="g1")))
        self.pool.update(lambda tx: tx.write("inbox/p0.json", with_q), "test: page")
        res = self.run_py("tickets.py", "inbox")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines(), tickets.page_lines("p0", with_q))

    def test_release_by_and_page(self):
        self.config(release="exit 7")
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.pool.update(lambda tx: tx.write("inbox/pp.json", {"kind": "decision", "ref": "g1", "text": "t",
                                                               "at": osx.now(), "acked": False}), "test")
        res = self.run_py("tickets.py", "release", "g1", "--by", "a@x.com", "--page", "pp")
        self.assertEqual(res.returncode, 1, res.stderr)
        snap = self.snap()
        self.assertEqual(snap.inbox["pp"]["acked_by"], "a@x.com")
        ev = [h for h in snap.groups["g1"]["history"] if h["event"].startswith("release re-run")]
        self.assertEqual(ev[-1].get("by"), "a@x.com")
        self.pool.update(lambda tx: tx.write("inbox/pq.json", {"kind": "decision", "ref": "g1", "text": "t",
                                                               "at": osx.now(), "acked": False}), "test")
        self.config(release="true")
        res = self.run_py("tickets.py", "release", "g1", "--page", "pq")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.snap().inbox["pq"]["acked_by"], "answer")
        self.pool.update(lambda tx: tx.write("inbox/pr.json", {"kind": "decision", "ref": "g1", "text": "t",
                                                               "at": osx.now(), "acked": False}), "test")
        res = self.run_py("tickets.py", "release", "g1", "--page", "pr")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertFalse(self.snap().inbox["pr"]["acked"])


class T78Acceptance(LoopCase):
    """#78: pages.py (ACTIONS, option, ask, problems, command), Pool.report storing or dropping the
    question/options/recommendation, inbox/wait printing them, release --by/--page, and the docs."""
    release = "exit 7"

    def setUp(self):
        super().setUp()
        for key in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(key, None)

    def pages(self):
        path = os.path.join(LOOP, "core", "pages.py")
        self.assertTrue(os.path.isfile(path), "devloop/core/pages.py does not exist")
        import importlib
        return importlib.import_module("core.pages")

    @staticmethod
    def opt(action, consequence="c", **more):
        o = {"action": action, "consequence": consequence}
        o.update(more)
        return o

    def valid(self):
        return {"question": "what now?", "recommendation": "retry first",
                "options": [self.opt("retry"), self.opt("revise"), self.opt("cancel")]}

    # ------------------------------------------------------------ item 1

    def test_module_is_stdlib_only_and_does_not_import_pool(self):
        import ast
        self.pages()
        with open(os.path.join(LOOP, "core", "pages.py")) as fh:
            tree = ast.parse(fh.read())
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "relative import in pages.py")
                names.append(node.module or "")
        for n in names:
            self.assertNotIn("pool", n)
            self.assertIn(n.split(".")[0], sys.stdlib_module_names, "non-stdlib import %r" % n)

    def test_actions_option_and_ask(self):
        pages = self.pages()
        self.assertEqual(pages.ACTIONS, ('revise', 'retry', 'cancel', 'group-ready', 'group-cancel', 'rerun',
                                         'release', 'ack'))
        self.assertEqual(pages.option("retry", "runs again"),
                         {"action": "retry", "note": "", "consequence": "runs again"})
        self.assertEqual(pages.option("group-ready", "back to regression", note="follow #67"),
                         {"action": "group-ready", "note": "follow #67", "consequence": "back to regression"})
        opts = [pages.option("retry", "  one\ntwo  "), pages.option("cancel", "y" * 300)]
        got = pages.ask("  is it\nok?  ", opts, "x" * 300)
        self.assertEqual(set(got), {"question", "options", "recommendation"})
        self.assertEqual(got["question"], "is it ok?")
        self.assertEqual(got["recommendation"], "x" * 200)
        self.assertEqual([o["consequence"] for o in got["options"]], ["one two", "y" * 200])
        self.assertEqual([o["action"] for o in got["options"]], ["retry", "cancel"])
        got = pages.ask("q" * 250, [pages.option("ack", "fine")], "r")
        self.assertEqual(got["question"], "q" * 200)

    # ------------------------------------------------------------ item 2

    def test_problems_rules(self):
        pages = self.pages()
        ok = self.valid()
        self.assertEqual(pages.problems("7", ok), [])
        self.assertEqual(pages.problems("7", {}), [])
        self.assertEqual(pages.problems("7", {"kind": "decision", "text": "t"}), [])
        self.assertEqual(pages.problems("g3", {"question": "q", "recommendation": "r", "options": [
            self.opt("group-ready", note="x"), self.opt("group-cancel"), self.opt("ack")]}), [])
        self.assertEqual(pages.problems("controller", {"question": "q", "recommendation": "r",
                                                       "options": [self.opt("ack")]}), [])
        # a note may have newlines; 8000 is allowed
        self.assertEqual(pages.problems("7", dict(ok, options=[self.opt("retry", note="a\nb"),
                                                               self.opt("cancel", note="n" * 8000)])), [])

        def with_options(*opts):
            return dict(ok, options=list(opts))

        broken = {
            "a: question alone": ("7", {"question": "q"}),
            "a: options alone": ("7", {"options": ok["options"]}),
            "a: recommendation alone": ("7", {"recommendation": "r"}),
            "a: no recommendation": ("7", {"question": "q", "options": ok["options"]}),
            "a: no question": ("7", {"recommendation": "r", "options": ok["options"]}),
            "a: no options": ("7", {"question": "q", "recommendation": "r"}),
            "b: question not str": ("7", dict(ok, question=5)),
            "b: question empty": ("7", dict(ok, question="   ")),
            "b: question newline": ("7", dict(ok, question="a\nb")),
            "b: question 201": ("7", dict(ok, question="q" * 201)),
            "b: recommendation not str": ("7", dict(ok, recommendation=["r"])),
            "b: recommendation empty": ("7", dict(ok, recommendation="")),
            "b: recommendation newline": ("7", dict(ok, recommendation="a\nb")),
            "b: recommendation 201": ("7", dict(ok, recommendation="r" * 201)),
            "c: options not list": ("7", dict(ok, options={"action": "retry", "consequence": "c"})),
            "c: options empty": ("7", with_options()),
            "c: one non-ack": ("7", with_options(self.opt("retry"))),
            "c: five": ("7", with_options(*[self.opt("retry")] * 5)),
            "c: not a dict": ("7", with_options(self.opt("retry"), "cancel")),
            "d: unknown action": ("7", with_options(self.opt("retry"), self.opt("nosuch"))),
            "d: consequence not str": ("7", with_options(self.opt("retry"), self.opt("cancel", 5))),
            "d: consequence empty": ("7", with_options(self.opt("retry"), self.opt("cancel", ""))),
            "d: consequence newline": ("7", with_options(self.opt("retry"), self.opt("cancel", "a\nb"))),
            "d: consequence 201": ("7", with_options(self.opt("retry"), self.opt("cancel", "c" * 201))),
            "d: note not str": ("7", with_options(self.opt("retry"), self.opt("cancel", note=3))),
            "d: note 8001": ("7", with_options(self.opt("retry"), self.opt("cancel", note="n" * 8001))),
            "e: retry on g3": ("g3", with_options(self.opt("retry"), self.opt("ack"))),
            "e: revise on 7a": ("7a", with_options(self.opt("revise"), self.opt("ack"))),
            "e: cancel on controller": ("controller", with_options(self.opt("cancel"), self.opt("ack"))),
            "e: group-ready on 7": ("7", with_options(self.opt("group-ready"), self.opt("ack"))),
            "e: group-cancel on controller": ("controller", with_options(self.opt("group-cancel"), self.opt("ack"))),
            "e: rerun on g": ("g", with_options(self.opt("rerun"), self.opt("ack"))),
            "e: release on 7": ("7", with_options(self.opt("release"), self.opt("ack"))),
        }
        for label, (ref, row) in broken.items():
            with self.subTest(label):
                got = pages.problems(ref, row)
                self.assertIsInstance(got, list)
                self.assertTrue(got, "%s gave no problem" % label)
                self.assertTrue(all(isinstance(p, str) for p in got), got)

    # ------------------------------------------------------------ item 4

    def test_command_strings(self):
        pages = self.pages()
        c = pages.command
        self.assertEqual(c('group-ready', 'g15', 'p1', 'follow #67'), "actl tickets ready g15 --note 'follow #67' --page p1")
        self.assertEqual(c('group-ready', 'g15', 'p1', ''), "actl tickets ready g15 --page p1")
        self.assertEqual(c('retry', '7', 'p2', ''), 'actl new --revise 7 --from user --decision retry --page p2')
        self.assertEqual(c('retry', '7', 'p2', 'go on'),
                         "actl new --revise 7 --from user --decision retry --reason 'go on' --page p2")
        self.assertEqual(c('cancel', '7', 'p2', ''), 'actl new --revise 7 --from user --decision cancel --page p2')
        self.assertEqual(c('cancel', '7', 'p2', 'not needed'),
                         "actl new --revise 7 --from user --decision cancel --reason 'not needed' --page p2")
        self.assertEqual(c('revise', '7', 'p2', ''),
                         "actl new --revise 7 --from user --decision revise --reason '<your words>' --page p2")
        self.assertEqual(c('revise', '7', 'p2', 'split it'),
                         "actl new --revise 7 --from user --decision revise --reason 'split it' --page p2")
        self.assertEqual(c('group-cancel', 'g3', 'p3', ''), "actl tickets cancel g3 --why '' --page p3")
        self.assertEqual(c('group-cancel', 'g3', 'p3', 'drop it'), "actl tickets cancel g3 --why 'drop it' --page p3")
        self.assertEqual(c('rerun', 'g3', 'p3', 'env ok'), "actl tickets rerun g3 --why 'env ok' --page p3")
        self.assertEqual(c('release', 'g3', 'p3', ''), 'actl tickets release g3 --page p3')
        self.assertEqual(c('ack', 'g3', 'p3', 'x'), 'actl tickets ack p3')

    # ------------------------------------------------------------ item 3

    def test_report_stores_valid_options_and_drops_bad_ones(self):
        self.pages()
        self.assertIn("idle", Pool.report.__doc__)
        for key in ("question", "options", "recommendation"):
            self.assertIn(key, Pool.report.__doc__)
        name = self.pool.report("decision", "7", "line one\nline two", question="what now?",
                                recommendation="retry first",
                                options=[{"action": "retry", "consequence": "runs again"},
                                         {"action": "revise", "note": "split it", "consequence": "rewrites"}])
        row = self.snap().inbox[name]
        self.assertEqual(row["question"], "what now?")
        self.assertEqual(row["recommendation"], "retry first")
        self.assertEqual(row["options"], [{"action": "retry", "note": "", "consequence": "runs again"},
                                          {"action": "revise", "note": "split it", "consequence": "rewrites"}])
        self.assertEqual(row["text"], "line one\nline two")

        from core import pages
        bad = {"question": "q", "recommendation": "r", "options": [{"action": "retry", "consequence": "c"}]}
        first = pages.problems("8", bad)[0]
        name = self.pool.report("decision", "8", "line one\nline two", **bad)
        row = self.snap().inbox[name]
        for key in ("question", "options", "recommendation"):
            self.assertNotIn(key, row)
        self.assertEqual(row["text"].splitlines(), ["line one", "line two", "(options dropped: %s)" % first])

        alone = {"question": "q"}
        first = pages.problems("9", alone)[0]
        name = self.pool.report("env", "9", "env text", **alone)
        row = self.snap().inbox[name]
        self.assertNotIn("question", row)
        self.assertEqual(row["text"].splitlines(), ["env text", "(options dropped: %s)" % first])

        name = self.pool.report("decision", "10", "plain")
        row = self.snap().inbox[name]
        self.assertEqual(set(row), {"kind", "ref", "text", "at", "acked"})
        self.assertEqual((row["kind"], row["ref"], row["text"], row["acked"]), ("decision", "10", "plain", False))

    # ------------------------------------------------------------ item 5

    PAGE = "20261108T100000-decision-g3"

    def write_page(self, name, ref, text, **extra):
        row = {"kind": "decision", "ref": ref, "text": text, "at": "2026-11-08T00:00:00Z", "acked": False}
        row.update(extra)
        self.pool.update(lambda tx: tx.write("inbox/%s.json" % name, row), "test: page %s" % name)

    def options_page(self):
        return {"question": "what to do with g3?", "recommendation": "the fix follows #67",
                "options": [{"action": "group-ready", "note": "follow #67", "consequence": "back to the regression"},
                            {"action": "group-cancel", "note": "", "consequence": "its tickets are cancelled"},
                            {"action": "ack", "note": "", "consequence": "nothing changes"}]}

    def expected_lines(self, name):
        return ["* decision g3         " + name,
                "    first line",
                "    second line",
                "    question: what to do with g3?",
                "    1. group-ready (recommended): back to the regression",
                "       $ actl tickets ready g3 --note 'follow #67' --page " + name,
                "    2. group-cancel: its tickets are cancelled",
                "       $ actl tickets cancel g3 --why '' --page " + name,
                "    3. ack: nothing changes",
                "       $ actl tickets ack " + name,
                "    recommendation: the fix follows #67"]

    def test_inbox_prints_question_options_commands_and_recommendation(self):
        self.write_page(self.PAGE, "g3", "first line\nsecond line", **self.options_page())
        plain = "20261108T100001-decision-g4"
        self.write_page(plain, "g4", "plain text")
        badname = "20261108T100002-decision-g5"
        bad = self.options_page()
        bad["options"] = bad["options"][:1]  # one option that is not ack: fails problems()
        self.write_page(badname, "g5", "bad text", **bad)
        res = self.run_py("tickets.py", "inbox", timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines(),
                         self.expected_lines(self.PAGE)
                         + ["* decision g4         " + plain, "    plain text",
                            "* decision g5         " + badname, "    bad text"])

    def test_wait_prints_the_same_lines(self):
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "tickets.py"), "wait", "--timeout", "20"],
                                cwd=self.proj, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        self.addCleanup(T77Acceptance.stop, proc)
        time.sleep(1.5)
        self.write_page(self.PAGE, "g3", "first line\nsecond line", **self.options_page())
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertEqual(out.splitlines(), self.expected_lines(self.PAGE))

    # ------------------------------------------------------------ item 6

    def land_failed(self):
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.snap().groups["g1"]["release"], "failed")
        pages = sorted(n for n, r in self.snap().inbox.items() if r["kind"] == "decision" and r["ref"] == "g1")
        self.assertTrue(pages)
        return pages[-1]

    def outcome_commit_files(self):
        sha = git(self.pool_path, "log", "-1", "--format=%H", "--", "groups/g1.json")
        return git(self.pool_path, "show", "--name-only", "--format=", sha).splitlines()

    def test_release_ok_with_by_and_page(self):
        page = self.land_failed()
        self.config(release="true")
        res = self.run_py("tickets.py", "release", "g1", "--by", "alice", "--page", page)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        self.assertEqual(snap.groups["g1"]["release"], "ok")
        self.assertTrue(snap.inbox[page]["acked"])
        self.assertEqual(snap.inbox[page]["acked_by"], "alice")
        ev = [h for h in snap.groups["g1"]["history"] if h["event"] == "release re-run: ok"]
        self.assertEqual(len(ev), 1, ev)
        self.assertEqual(ev[0].get("by"), "alice")
        self.assertIn("inbox/%s.json" % page, self.outcome_commit_files())

    def test_release_failed_with_page_acks_as_answer(self):
        page = self.land_failed()
        res = self.run_py("tickets.py", "release", "g1", "--page", page)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        snap = self.snap()
        self.assertTrue(snap.inbox[page]["acked"])
        self.assertEqual(snap.inbox[page]["acked_by"], "answer")
        self.assertIn("release re-run: failed", [h["event"] for h in snap.groups["g1"]["history"]])
        self.assertIn("inbox/%s.json" % page, self.outcome_commit_files())

    def test_refused_release_leaves_the_page_unread(self):
        page = self.land_failed()
        # not landed: an open group g2, page on it
        self.pool.update(lambda tx: tx.put_group({"id": "g2", "state": "pr", "members": ["1"],
                                                  "branch": "int/g2"}), "test: open group")
        self.write_page("20261108T100000-decision-g2", "g2", "about g2")
        head = git(self.pool_path, "rev-parse", "HEAD")
        res = self.run_py("tickets.py", "release", "g2", "--by", "alice", "--page", "20261108T100000-decision-g2")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertNotIn("unrecognized arguments", res.stderr)
        self.assertEqual(git(self.pool_path, "rev-parse", "HEAD"), head)
        self.assertFalse(self.snap().inbox["20261108T100000-decision-g2"]["acked"])
        # the same flags are accepted when the release may run
        self.config(release="true")
        res = self.run_py("tickets.py", "release", "g1", "--page", page)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(self.snap().inbox[page]["acked"])
        # already released, no --again: refused, page left unread
        self.write_page("20261108T100001-decision-g1", "g1", "again?")
        head = git(self.pool_path, "rev-parse", "HEAD")
        res = self.run_py("tickets.py", "release", "g1", "--by", "bob", "--page", "20261108T100001-decision-g1")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertNotIn("unrecognized arguments", res.stderr)
        self.assertEqual(git(self.pool_path, "rev-parse", "HEAD"), head)
        self.assertFalse(self.snap().inbox["20261108T100001-decision-g1"]["acked"])

    # ------------------------------------------------------------ item 7

    def test_readme_words(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            readme = fh.read().splitlines()
        inbox = [ln for ln in readme if ln.startswith("| `python3 devloop/tickets.py inbox")]
        self.assertEqual(len(inbox), 1)
        for word in ("idle", "cancelled", "question", "option", "command", "recommendation"):
            self.assertIn(word, inbox[0])
        rel = [i for i, ln in enumerate(readme) if ln.startswith("| `python3 devloop/tickets.py release gN")]
        self.assertEqual(len(rel), 1)
        self.assertIn("[--by NAME] [--page P]", readme[rel[0]])
        self.assertTrue(readme[rel[0] - 1].startswith("| `python3 devloop/tickets.py cancel N"))
        self.assertTrue(readme[rel[0] + 1].startswith("| `python3 devloop/tickets.py cancel gN"))
        start = readme.index("## Contracts")
        end = next(i for i in range(start + 1, len(readme)) if readme[i].startswith("## "))
        section = readme[start:end]
        heads = [i for i, ln in enumerate(section) if ln.startswith("- **Pages**")]
        self.assertEqual(len(heads), 1, "no '**Pages**' bullet in ## Contracts")
        body = [section[heads[0]]]
        for ln in section[heads[0] + 1:]:
            if not ln.startswith(" ") or not ln.strip():
                break
            body.append(ln)
        bullet = " ".join(body)
        for word in ("question", "options", "recommendation", "action", "note", "consequence", "ack",
                     "first", "(options dropped:") + ACTIONS78:
            self.assertIn(word, bullet)
        self.assertRegex(bullet, r"2\s*-\s*4|2\s*–\s*4|2 to 4|two to four")


ACTIONS78 = ('revise', 'retry', 'cancel', 'group-ready', 'group-cancel', 'rerun', 'release')


class T77Acceptance(LoopCase):
    """#77: tickets.py wait blocks until a page that was not in the inbox at its start arrives;
    `tickets.py inbox` (run after the wait) is the oracle for what it prints."""

    def setUp(self):
        super().setUp()
        for key in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(key, None)

    def start_wait(self, *args):
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "tickets.py"), "wait"] + list(args),
                                cwd=self.proj, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        self.addCleanup(self.stop, proc)
        return proc

    @staticmethod
    def stop(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()

    def finish(self, proc, timeout=30):
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err

    def write_pages(self, *pages):
        """pages: (name, kind, ref, text[, acked]) - all in one pool change."""
        def change(tx):
            for p in pages:
                name, kind, ref, text = p[:4]
                tx.write("inbox/%s.json" % name, {"kind": kind, "ref": ref, "text": text,
                                                  "at": "2026-11-08T00:00:00Z",
                                                  "acked": bool(p[4]) if len(p) > 4 else False})
        self.pool.update(change, "test: pages %s" % ",".join(p[0] for p in pages))

    def inbox_blocks(self):
        """{page name: its lines in `tickets.py inbox` output}, the oracle."""
        res = self.run_py("tickets.py", "inbox", timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        blocks, cur = {}, None
        for line in res.stdout.splitlines():
            if line.startswith("    ") and cur is not None:
                blocks[cur].append(line)
            elif line[:2] in ("* ", ". "):
                cur = line.split()[-1]
                blocks[cur] = [line]
            else:
                cur = None
        return blocks

    def test_one_new_page_ends_the_wait_and_prints_its_inbox_block(self):
        t0 = time.monotonic()
        proc = self.start_wait("--timeout", "20")
        time.sleep(1.5)
        self.write_pages(("20261108T100000-decision-g1", "decision", "g1", "first line\nsecond line\nthird"))
        rc, out, err = self.finish(proc)
        elapsed = time.monotonic() - t0
        self.assertEqual(rc, 0, out + err)
        self.assertLess(elapsed, 10)
        block = self.inbox_blocks()["20261108T100000-decision-g1"]
        self.assertEqual(len(block), 4, block)
        self.assertEqual(out.splitlines(), block)

    def test_pages_in_the_inbox_at_start_are_never_printed(self):
        self.write_pages(("20261108T090000-landed-g1", "landed", "g1", "old unread"),
                         ("20261108T090001-env-g2", "env", "g2", "old read", True))
        proc = self.start_wait("--timeout", "20")
        time.sleep(1.5)
        self.write_pages(("20261108T100000-decision-g3", "decision", "g3", "new one"))
        rc, out, err = self.finish(proc)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("20261108T100000-decision-g3", out)
        self.assertNotIn("20261108T090000-landed-g1", out)
        self.assertNotIn("20261108T090001-env-g2", out)
        self.assertEqual(out.splitlines(), self.inbox_blocks()["20261108T100000-decision-g3"])

    def check_kind_filter(self, kinds):
        proc = self.start_wait("--timeout", "20", "--kind", kinds)
        time.sleep(1.5)
        self.write_pages(("20261108T100000-landed-g1", "landed", "g1", "landed text"))
        time.sleep(1.5)
        if proc.poll() is not None:
            out, err = proc.communicate()
            self.fail("the wait ended on a landed page with --kind %r: rc %s %s%s"
                      % (kinds, proc.returncode, out, err))
        self.write_pages(("20261108T100001-decision-g2", "decision", "g2", "decide this"))
        rc, out, err = self.finish(proc)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("20261108T100001-decision-g2", out)
        self.assertNotIn("20261108T100000-landed-g1", out)
        self.assertEqual(out.splitlines(), self.inbox_blocks()["20261108T100001-decision-g2"])

    def test_kind_filter_skips_other_kinds(self):
        self.check_kind_filter("decision,env")

    def test_kind_names_are_trimmed(self):
        self.check_kind_filter(" decision , env")

    def test_two_pages_in_one_change_are_both_printed_in_name_order(self):
        proc = self.start_wait("--timeout", "20")
        time.sleep(1.5)
        # written in reverse order; printed in name order
        self.write_pages(("20261108T100002-landed-g2", "landed", "g2", "b text"),
                         ("20261108T100001-env-g1", "env", "g1", "a text\nmore"))
        rc, out, err = self.finish(proc)
        self.assertEqual(rc, 0, out + err)
        blocks = self.inbox_blocks()
        self.assertEqual(out.splitlines(),
                         blocks["20261108T100001-env-g1"] + blocks["20261108T100002-landed-g2"])

    def test_timeout_without_new_page_exits_one(self):
        self.write_pages(("20261108T090000-decision-g1", "decision", "g1", "already here"))
        t0 = time.monotonic()
        proc = self.start_wait("--timeout", "1")
        rc, out, err = self.finish(proc, timeout=30)
        elapsed = time.monotonic() - t0
        self.assertEqual(rc, 1, out + err)
        self.assertLess(elapsed, 6)
        self.assertGreaterEqual(elapsed, 0.9, "exited before the timeout")
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertTrue(out.startswith("wait: no new page"), out)
        self.assertEqual(err, "")

    def assert_refused(self, *args):
        res = self.run_py("tickets.py", "wait", *args, timeout=30)
        self.assertEqual(res.returncode, 2, (args, res.stdout, res.stderr))
        self.assertEqual(res.stdout, "", args)
        lines = res.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, (args, res.stderr))
        self.assertTrue(lines[0].startswith("tickets:"), (args, res.stderr))

    def test_bad_arguments_and_missing_pool_exit_two(self):
        # each refused call carries a short timeout so a wrong implementation ends instead of blocking
        self.assert_refused("--timeout", "3", "--kind", "nosuch")
        self.assert_refused("--timeout", "3", "--kind", ",")
        self.assert_refused("--timeout", "0")
        self.assert_refused("--timeout", "-3")
        res = self.run_py("tickets.py", "wait", "--timeout", "abc", timeout=30)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.config(pool=os.path.join(self.tmp, "no-such-pool"))
        self.assert_refused("--timeout", "3")

    def test_every_known_kind_is_accepted(self):
        # pairs the refusal above: a valid kind list is not refused (ends by timeout instead)
        proc = self.start_wait("--timeout", "1", "--kind", "created,landed,decision,env,cancelled,idle")
        rc, out, err = self.finish(proc)
        self.assertEqual(rc, 1, out + err)
        self.assertEqual(err, "")

    def test_docs_list_the_command(self):
        def read(*parts):
            with open(os.path.join(HOME, *parts)) as fh:
                return fh.read()
        readme = read("devloop", "README.md").splitlines()
        inbox = [i for i, ln in enumerate(readme) if ln.startswith("| `python3 devloop/tickets.py inbox")]
        self.assertEqual(len(inbox), 1)
        self.assertIn("idle", readme[inbox[0]])
        self.assertIn("cancelled", readme[inbox[0]])
        row = readme[inbox[0] + 1]
        self.assertTrue(row.startswith("| `python3 devloop/tickets.py wait"), row)
        self.assertIn("--kind", row)
        self.assertIn("start", row)
        for code in ("0", "1", "2"):
            self.assertRegex(row, r"(?<![0-9.])%s(?![0-9.])" % code)
        cancel = [i for i, ln in enumerate(readme) if ln.startswith("| `python3 devloop/tickets.py cancel")][0]
        self.assertIn("tickets.py release", readme[cancel + 1])
        doc = read("devloop", "tickets.py").split('"""')[1]
        self.assertIn("tickets.py wait", doc)
        actl = [ln for ln in read("bin", "actl").splitlines() if ln.startswith("  tickets ")]
        self.assertEqual(len(actl), 1)
        self.assertIn("|release", actl[0])
        self.assertIn("wait", re.split(r"[\s|]+", actl[0]))
        claude = read("CLAUDE.md").splitlines()
        poll = [ln for ln in claude if "不輪詢" in ln]
        self.assertEqual(len(poll), 1, poll)
        self.assertIn("`actl tickets wait", poll[0])
        self.assertIn("背景", poll[0])
        self.assertEqual(len([ln for ln in claude if "頁的種類" in ln]), 1)


class T101Acceptance(LoopCase):
    """#101: devloop.notify runs a shell command, detached, for every new decision, env or idle page."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)

    # ------------------------------------------------------------ helpers

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def notify_pool(self):
        from core.config import pool_for
        return pool_for(self.cfg())

    def log_path(self):
        return os.path.join(self.tmp, "wt", "runs", "notify.log")

    def recording_spawn(self, calls):
        def fake(argv, cwd=None, env=None, log_path=None, stdin_text=None):
            page = (env or {}).get("DEVLOOP_PAGE")
            calls.append({"argv": argv, "cwd": cwd, "env": dict(env or {}), "log_path": log_path,
                          "page_exists": bool(page) and os.path.isfile(page)})
            return mock.Mock(pid=0)
        return fake

    def report_with_spy(self, kind, ref="5", text="first line\nsecond line"):
        calls = []
        pool = self.notify_pool()
        with mock.patch.object(osx, "spawn", side_effect=self.recording_spawn(calls)):
            name = pool.report(kind, ref, text)
        return name, calls

    def wait_for(self, cond, seconds=10.0):
        end = time.time() + seconds
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return cond()

    def log_text(self):
        try:
            with open(self.log_path()) as fh:
                return fh.read()
        except OSError:
            return ""

    # ------------------------------------------------------------ 1: config

    def test_1_default_and_type_check(self):
        from core import config as config_mod
        self.assertIn("notify", config_mod.DEFAULTS)
        self.assertIsNone(config_mod.DEFAULTS["notify"])
        for bad in (5, "", ["x"]):
            self.config(notify=bad)
            with self.assertRaises(osx.OSFailure, msg="notify=%r accepted" % (bad,)) as ctx:
                config_mod.Config(self.proj)
            self.assertIn("devloop.notify", str(ctx.exception))
        for good in (None, "echo hi"):
            self.config(notify=good)
            self.assertEqual(config_mod.Config(self.proj).values["notify"], good)

    # ------------------------------------------------------------ 2, 3: the spawn call

    def test_2_3_each_asking_kind_spawns_once_with_page_env(self):
        self.config(notify="echo notified")
        runs = os.path.join(self.tmp, "wt", "runs")
        self.assertFalse(os.path.exists(runs))
        for kind in ("decision", "env", "idle"):
            name, calls = self.report_with_spy(kind)
            self.assertEqual(len(calls), 1, "%s: spawn calls %r" % (kind, calls))
            call = calls[0]
            self.assertEqual(call["argv"], ["sh", "-c", "echo notified"])
            self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(self.proj))
            self.assertEqual(call["log_path"], self.log_path())
            self.assertTrue(os.path.isdir(runs), "runs folder not created")
            env = call["env"]
            for key, value in os.environ.items():
                self.assertEqual(env.get(key), value, "os.environ %s not passed" % key)
            self.assertEqual(env["DEVLOOP_PAGE_KIND"], kind)
            self.assertEqual(env["DEVLOOP_PAGE_SUBJECT"], "5")
            self.assertEqual(env["DEVLOOP_PAGE_TEXT"], "first line")
            page = os.path.join(self.pool_path, "inbox", name + ".json")
            self.assertTrue(os.path.isabs(env["DEVLOOP_PAGE"]))
            self.assertEqual(os.path.realpath(env["DEVLOOP_PAGE"]), os.path.realpath(page))
            self.assertTrue(call["page_exists"], "page file missing when spawn was called")

    def test_3_subject_is_ref_as_text(self):
        self.config(notify="true")
        _, calls = self.report_with_spy("decision", ref=7)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["env"]["DEVLOOP_PAGE_SUBJECT"], "7")

    # ------------------------------------------------------------ 4: no spawn

    def test_4_quiet_kinds_and_unset_notify_spawn_nothing(self):
        self.config(notify="echo notified")
        for kind in ("created", "landed", "cancelled"):
            _, calls = self.report_with_spy(kind)
            self.assertEqual(calls, [], kind)
        self.config(notify=None)
        for kind in ("decision", "env", "idle"):
            _, calls = self.report_with_spy(kind)
            self.assertEqual(calls, [], kind)
        # positive control on the same setup: the asking kinds do spawn once notify is set
        self.config(notify="echo notified")
        _, calls = self.report_with_spy("decision")
        self.assertEqual(len(calls), 1)

    # ------------------------------------------------------------ 5: failures never fail the caller

    def test_5_spawn_failure_is_ignored(self):
        self.config(notify="echo notified")
        pool = self.notify_pool()
        with mock.patch.object(osx, "spawn", side_effect=osx.OSFailure("boom")) as spy:
            name = pool.report("decision", "5", "first line\nsecond line")
        self.assertEqual(spy.call_count, 1)
        self.assertIn(name, self.snap().inbox)

    def test_5_slow_or_failing_command_does_not_hold_report(self):
        marker = os.path.join(self.tmp, "slow-started")
        self.config(notify="touch '%s'; sleep 30" % marker)
        pool = self.notify_pool()
        start = time.time()
        name = pool.report("env", "5", "first line")
        self.assertLess(time.time() - start, 15)
        self.assertIn(name, self.snap().inbox)
        self.assertTrue(self.wait_for(lambda: os.path.exists(marker)), "notify command never ran")
        self.config(notify="echo failing; exit 4")
        name = self.notify_pool().report("idle", "controller", "first line")
        self.assertIn(name, self.snap().inbox)
        self.assertTrue(self.wait_for(lambda: "failing" in self.log_text()), self.log_text())

    # ------------------------------------------------------------ 6: the log

    def test_6_output_of_both_pages_appended_to_notify_log(self):
        self.config(notify="echo one")
        self.notify_pool().report("decision", "5", "first line")
        self.assertTrue(self.wait_for(lambda: "one" in self.log_text()), "log: %r" % self.log_text())
        self.config(notify="echo two >&2; exit 3")
        self.notify_pool().report("env", "5", "first line")
        self.assertTrue(self.wait_for(lambda: "two" in self.log_text()), "log: %r" % self.log_text())
        text = self.log_text()
        self.assertIn("one", text)
        self.assertIn("two", text)

    # ------------------------------------------------------------ 7: end to end

    def test_7_controller_stuck_ticket_notifies_decision_and_idle(self):
        out = os.path.join(self.tmp, "notified.txt")
        self.config(max_rounds=1, notify='printf "%%s\\n" "$DEVLOOP_PAGE_KIND|$DEVLOOP_PAGE_SUBJECT|'
                                          '$DEVLOOP_PAGE_TEXT" >> \'%s\'' % out)
        self.add(1)
        self.plan(worker={"1": ["wrong"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "stuck")

        def lines():
            try:
                with open(out) as fh:
                    return fh.read().splitlines()
            except OSError:
                return []

        def both():
            got = lines()
            return (any(l.startswith("decision|1|#1 is stuck (rounds)") for l in got)
                    and any(l.startswith("idle|controller|") for l in got))
        if not self.wait_for(both):
            self.fail("notify lines: %r; inbox kinds %r" % (lines(), self.inbox_kinds()))

    # ------------------------------------------------------------ 9: README

    def test_9_readme_documents_notify(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            text = fh.read()
        for part in ("devloop.notify", "DEVLOOP_PAGE_KIND", "DEVLOOP_PAGE_SUBJECT", "DEVLOOP_PAGE_TEXT",
                     "DEVLOOP_PAGE", "notify.log", "osascript", "ntfy"):
            self.assertIn(part, text)
        self.assertRegex(text, r'"\$DEVLOOP_PAGE(_\w+)?"|"\$\{DEVLOOP_PAGE')


if __name__ == "__main__":
    unittest.main()
