"""the issuer: rounds and feedback, cases, revisions, frozen tickets, hard_block of the own change, interrupted issuer cost rows, timeout and recovery"""

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


class RoundsAndFeedback(LoopCase):
    def test_failing_cases_send_the_worker_back_and_round_two_lands(self):
        self.add(1)
        self.plan(worker={"1": ["wrong", "ok"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual(t["state"], "landed")
        self.assertEqual(t["round"], 2)

    def test_out_of_rounds_is_stuck_and_the_rest_of_the_group_lands(self):
        self.add(1)
        self.add(2)
        self.plan(worker={"1": ["wrong"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "stuck")
        self.assertEqual(self.snap().ticket("1")["stuck"]["reason"], "rounds")
        self.assertEqual(self.state(2), "landed")
        self.assertIn("decision", self.inbox_kinds())

    def test_a_dependent_of_a_stuck_ticket_is_never_dispatched(self):
        self.add(1)
        self.add(2, deps=[1])
        self.plan(worker={"1": ["wrong"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "stuck")
        self.assertEqual(self.state(2), "ready")

    def test_review_fail_is_feedback_like_any_other(self):
        self.add(1, complexity="complex")
        self.plan(reviewer={"1": ["fail", "pass"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["round"]), ("landed", 2))
        fed = [h for h in t["history"] if h["event"] == "feedback"]
        self.assertEqual([h.get("round") for h in fed], [1])

    def test_a_worker_that_needs_the_user_stops_the_ticket(self):
        self.add(1)
        self.plan(worker={"1": ["blocked"]})
        self.controller()
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["stuck"]["reason"]), ("stuck", "decision"))

    def test_touching_hard_block_stops_before_merging(self):
        self.add(1)
        self.plan(worker={"1": ["touch_decisions"]})
        self.controller()
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["stuck"]["reason"]), ("stuck", "hard_block"))
        self.assertFalse(self.main_has("impl_1.txt"))


class T100Acceptance(LoopCase):
    """Ticket #100: a round that ended on a merge conflict with the group's target or on a
    cases_wrong revision is free; the issuer stops for rounds after devloop.max_rounds rounds that
    were not free or 2 x max_rounds rounds in all, and the stuck page tallies real and free rounds."""

    def setUp(self):
        super().setUp()
        for name in [k for k in self.env if k.startswith("DEVLOOP_")]:
            del self.env[name]

    # ------------------------------------------------------------ acceptance 5: free_rounds

    @staticmethod
    def rs(n):
        return {"event": "round start", "round": n}

    @staticmethod
    def fb(n, free):
        h = {"event": "feedback", "round": n}
        if free is not None:
            h["free"] = free
        return h

    def free_rounds(self, t):
        import ticket_issuer
        self.assertTrue(hasattr(ticket_issuer, "free_rounds"), "ticket_issuer.free_rounds is missing")
        return ticket_issuer.free_rounds(t)

    def test_5_free_rounds_counts_free_feedback_events_since_the_last_round_one(self):
        rs, fb = self.rs, self.fb
        cases = [
            ({"round": 3, "history": [rs(1), fb(1, "merge"), rs(2), fb(2, "revision"), rs(3), fb(3, None)]},
             {"merge": 1, "revision": 1, "agent_env": 0}),
            ({"round": 1, "history": [rs(1), fb(1, "merge"), rs(2), fb(2, "merge"),
                                      {"event": "user decision: ready"}, rs(1), fb(1, "revision")]},
             {"merge": 0, "revision": 1, "agent_env": 0}),
            ({"round": 0, "history": [rs(1), fb(1, "merge"), rs(2), fb(2, "merge"),
                                      {"event": "user decision: ready"}]},
             {"merge": 0, "revision": 0, "agent_env": 0}),
            ({"round": 1, "history": [{"event": "round start"}, {"event": "feedback", "round": 1}]},
             {"merge": 0, "revision": 0, "agent_env": 0}),
            ({}, {"merge": 0, "revision": 0, "agent_env": 0}),
        ]
        for t, want in cases:
            with self.subTest(t=t):
                self.assertEqual(self.free_rounds(t), want)

    # ------------------------------------------------------------ end to end (acceptance 1, 2, 8-10)

    def run_loop(self):
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return self.snap()

    def decision_texts(self, snap, ident):
        return [row.get("text") or "" for row in snap.inbox.values()
                if row["kind"] == "decision" and row["ref"] == str(ident)]

    @staticmethod
    def feedback_events(t):
        return [h for h in t.get("history") or [] if h.get("event") == "feedback"]

    def test_8_two_tickets_of_a_group_on_the_same_file_one_lands_the_other_gets_free_merge_rounds(self):
        self.config(max_rounds=1)
        self.add(1, group="g1")
        self.add(2, group="g1")
        g = {"id": "g1", "branch": "int/g1", "state": "open", "members": ["1", "2"], "fix_round": 0,
             "job": None, "created": "2026-11-23T00:00:00Z", "history": []}
        self.pool.update(lambda tx: tx.put_group(g), "test: group g1")   # #118: one group, as an opening writes it
        self.plan(worker={"1": ["shared"], "2": ["shared"]})
        snap = self.run_loop()
        states = {i: snap.ticket(i)["state"] for i in ("1", "2")}
        landed = [i for i, s in states.items() if s == "landed"]
        stuck = [i for i, s in states.items() if s == "stuck"]
        self.assertEqual((len(landed), len(stuck)), (1, 1), states)
        t = snap.ticket(stuck[0])
        self.assertEqual((t["stuck"]["reason"], t["round"]), ("rounds", 2), t.get("history"))
        fed = self.feedback_events(t)
        self.assertEqual([h.get("free") for h in fed], ["merge", "merge"], fed)
        texts = self.decision_texts(snap, stuck[0])
        want = "2 rounds used and still not passing (0 real red, 2 free: 2 merge conflict, 0 revision)"
        self.assertTrue(any(want in x for x in texts), texts)

    def test_9_a_cases_wrong_revision_round_is_free_and_the_ticket_lands_in_round_two(self):
        self.config(max_rounds=1)
        self.add(1)
        self.plan(worker={"1": ["revise", "ok"]}, creator={"1": ["revised"]})
        t = self.run_loop().ticket("1")
        self.assertEqual((t["state"], t["round"]), ("landed", 2), t.get("history"))
        r1 = [h for h in self.feedback_events(t) if h.get("round") == 1]
        self.assertEqual(len(r1), 1, t.get("history"))
        self.assertEqual(r1[0].get("free"), "revision", r1)

    def test_10_a_free_revision_round_then_a_real_red_is_stuck_at_round_two(self):
        self.config(max_rounds=1)
        self.add(1)
        self.plan(worker={"1": ["revise", "wrong"]}, creator={"1": ["revised"]})
        snap = self.run_loop()
        t = snap.ticket("1")
        self.assertEqual(t["state"], "stuck", t.get("history"))
        self.assertEqual((t["stuck"]["reason"], t["round"]), ("rounds", 2), t.get("history"))
        texts = self.decision_texts(snap, 1)
        want = "2 rounds used and still not passing (1 real red, 1 free: 0 merge conflict, 1 revision)"
        self.assertTrue(any(want in x for x in texts), texts)


class T62Acceptance(LoopCase):
    """hard_block counts only the ticket's own change, not what was merged in from main."""

    def layout(self, main_change, patterns=None, branch_from="int/g1"):
        """int/g1 at the first commit; main one commit ahead (main_change writes it); a checkout
        of t/1 created from branch_from. Returns the checkout path."""
        if patterns is not None:
            path = os.path.join(self.proj, "board", "config.json")
            with open(path) as fh:
                cfg = json.load(fh)
            cfg["hard_block"]["patterns"] = patterns
            with open(path, "w") as fh:
                json.dump(cfg, fh)
        os.makedirs(os.path.join(self.proj, "docs"), exist_ok=True)
        with open(os.path.join(self.proj, "docs", "DECISIONS.md"), "w") as fh:
            fh.write("# decisions\n")
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "--amend", "-m", "init")
        git(self.proj, "branch", "int/g1", "main")
        main_change(self.proj)
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "main moves on")
        wt = os.path.join(self.tmp, "t1")
        git(self.proj, "worktree", "add", "-q", wt, "-b", "t/1", branch_from)
        return wt

    def commit(self, wt, rel, line, msg):
        with open(os.path.join(wt, rel), "a") as fh:
            fh.write(line + "\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", msg)

    def hard_block(self, wt):
        import ticket_issuer
        from core.config import Config
        from core.gitx import Git
        issuer = ticket_issuer.Issuer(Config(self.proj), self.pool, "1", "int/g1")
        issuer.hard_block(Git(wt))

    def no_stuck(self, wt):
        import ticket_issuer
        try:
            self.hard_block(wt)
        except ticket_issuer.Stuck as e:
            self.fail("hard_block stopped a ticket whose own change is clean: %s %s" % (e.reason, e.text))

    @staticmethod
    def edit_decisions(root):
        with open(os.path.join(root, "docs", "DECISIONS.md"), "a") as fh:
            fh.write("a ruling made on main\n")

    def test_decisions_edit_merged_from_main_does_not_stop(self):
        import ticket_issuer
        wt = self.layout(self.edit_decisions)
        git(wt, "merge", "-q", "--no-edit", "main")
        self.commit(wt, "impl.py", "x = 1", "own work")
        self.no_stuck(wt)
        # the ticket's own edit of the same path still stops it
        self.commit(wt, "docs/DECISIONS.md", "a line from the ticket", "own decisions edit")
        with self.assertRaises(ticket_issuer.Stuck) as cm:
            self.hard_block(wt)
        self.assertEqual(cm.exception.reason, "hard_block")
        self.assertIn("docs/DECISIONS.md", cm.exception.text)

    def test_pattern_line_merged_from_main_does_not_stop(self):
        import ticket_issuer

        def add_mark(root):
            with open(os.path.join(root, "main_code.py"), "a") as fh:
                fh.write("y = 'FORBIDDEN_MARK'\n")
        wt = self.layout(add_mark, patterns=["FORBIDDEN_MARK"])
        git(wt, "merge", "-q", "--no-edit", "main")
        self.commit(wt, "impl.py", "x = 1", "own work")
        self.no_stuck(wt)
        # the ticket's own line with the pattern still stops it
        self.commit(wt, "impl.py", "z = 'FORBIDDEN_MARK'", "own pattern line")
        with self.assertRaises(ticket_issuer.Stuck) as cm:
            self.hard_block(wt)
        self.assertEqual(cm.exception.reason, "hard_block")
        self.assertIn("FORBIDDEN_MARK", cm.exception.text)

    def test_branch_started_from_newer_main_does_not_stop(self):
        wt = self.layout(self.edit_decisions, branch_from="main")
        self.commit(wt, "impl.py", "x = 1", "own work")
        self.no_stuck(wt)


class PageSetsOfIssuerAndWriters(LoopCase):
    """Unit tests of the question sets the issuer, creator and memory writers attach."""

    def test_issuer_stuck_sets_are_valid_and_ordered(self):
        import ticket_issuer
        from core.config import Config
        issuer = ticket_issuer.Issuer(Config(self.proj), self.pool, "5", "int/g1")
        for reason in ("rounds", "timeout", "target", "env", "error", "decision"):
            row = issuer.stuck_ask(reason)
            self.assertEqual(pages.problems("5", row), [], reason)
            self.assertEqual([o["action"] for o in row["options"]], ["retry", "revise", "cancel"])
            self.assertIn("#5", row["question"])
        row = issuer.stuck_ask("hard_block")
        self.assertEqual(pages.problems("5", row), [])
        self.assertEqual([o["action"] for o in row["options"]], ["revise", "cancel", "retry"])

    def test_rejected_page_carries_one_ack(self):
        import ticket_creator
        ticket_creator.rejected(self.pool, "request", "bad text", "web")
        row = list(self.snap().inbox.values())[0]
        self.assertEqual([o["action"] for o in row["options"]], ["ack"])
        self.assertIn("web", row["question"])
        self.assertEqual(pages.problems("request", row), [])


class T136Acceptance(LoopCase):
    """#136 (issue #16, D-097): the stuck page of a ticket its open group waits for names that group
    and the groups after it in the chain."""

    def setUp(self):
        super().setUp()
        for k in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(k, None)

    def page_lines(self, unacked):
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = [r for r in self.snap().inbox.values()
                if r.get("kind") == "decision" and str(r.get("ref")) == "1"
                and (not unacked or not r.get("acked"))]
        self.assertEqual(len(rows), 1, "decision pages about #1: %r" % rows)
        return [ln.strip() for ln in str(rows[0].get("text", "")).splitlines()]

    def test_1_a_dependent_ticket_names_the_group_and_the_next_one(self):
        self.add(1)
        self.add(2, deps=[1])
        self.plan(worker={"1": ["blocked"]})
        lines = self.page_lines(unacked=True)
        self.assertIn("g1 waits for this decision; groups after it in the chain: g2", lines)

    def test_2_a_held_last_group_names_none_after(self):
        import ticket_issuer
        from core.config import Config
        self.add(1, state="running", target="int/g1", group="g1")
        self.add(2, group="g1")

        def change(tx):
            tx.put_group({"id": "g1", "branch": "int/g1", "state": "open", "members": ["1", "2"],
                          "fix_round": 0, "job": None, "created": osx.now(), "history": []})
        self.pool.update(change, "test: group g1")
        snap = self.snap()
        self.assertEqual(sorted(snap.groups), ["g1"])
        self.assertEqual(snap.ticket("2")["state"], "ready")

        calls = []
        real = self.pool

        class StandInPool:
            def __getattr__(self, name):
                return getattr(real, name)

            def report(self, kind, ref, text, **kw):
                calls.append((kind, str(ref), text))

        issuer = ticket_issuer.Issuer(Config(self.proj), StandInPool(), "1", "int/g1")
        issuer.stuck("decision", "need the user")
        self.assertEqual(self.snap().ticket("1")["state"], "stuck")
        self.assertEqual([c[:2] for c in calls], [("decision", "1")], calls)
        lines = [ln.strip() for ln in calls[0][2].splitlines()]
        self.assertIn("g1 waits for this decision; groups after it in the chain: none", lines)


class Timeout(LoopCase):
    loop_extra = {"issuer_timeout_seconds": 4}

    def test_an_issuer_past_its_time_is_stuck(self):
        self.add(1)
        self.plan(worker={"1": ["sleep"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["stuck"]["reason"]), ("stuck", "timeout"))


class StuckPageGroupLine(LoopCase):
    """#136 (the implementer's): Issuer.stuck() adds the D-097 'group waits' line to its page only when the ticket holds its open group."""

    group = __import__("test_groups").GroupFollowUnits.group

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)

    def page_text(self):
        import ticket_issuer
        me = mock.Mock(id="1", pool=mock.Mock(snapshot=self.snap), stuck_ask=lambda reason: {})
        me.pool.report = mock.Mock()
        ticket_issuer.Issuer.stuck(me, "decision", "need the user")
        return me.pool.report.call_args[0][2]

    def test_no_line_when_the_ticket_does_not_hold_its_group(self):
        self.add(1, state="stuck", group="g1")
        self.add(2, state="merged", group="g1")
        self.group("g1", ["1", "2"])
        self.assertEqual(self.page_text(), "#1 is stuck (decision): need the user")

    def test_line_names_the_group_when_an_unfinished_member_is_beside_it(self):
        self.add(1, state="stuck", group="g1")
        self.add(2, state="ready", group="g1")
        self.group("g1", ["1", "2"])
        self.assertIn("g1 waits for this decision; groups after it in the chain: none", self.page_text())


class Revise(LoopCase):
    def test_a_worker_can_have_the_ticket_revised_and_cases_rewritten(self):
        self.add(1)
        self.plan(worker={"1": ["revise", "ok"]}, creator={"1": ["revised"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual(t["state"], "landed")
        self.assertEqual(t["acceptance"], ["a possible thing"])
        self.assertTrue(any(h["event"] == "revised" for h in t["history"]))

    def test_a_question_from_the_opener_opens_nothing(self):
        self.plan(creator={"-": ["question"]})
        res = self.run_py("ticket_creator.py", "--request", "make it nicer")
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertEqual(self.snap().tickets, {})
        self.assertEqual(self.inbox_kinds(), ["decision"])

    def test_an_invalid_hand_in_is_retried_then_reported(self):
        self.plan(creator={"-": ["bad"]})
        res = self.run_py("ticket_creator.py", "--request", "x")
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertEqual(self.snap().tickets, {})


class T114Acceptance(LoopCase):
    """Ticket #114 (issue #4a): a ticket stuck on a worker's blocked hand-in always has a page: its
    own, or the one its revise escalation wrote in the same round."""

    def setUp(self):
        super().setUp()
        for name in [k for k in self.env if k.startswith("DEVLOOP_")]:
            del self.env[name]

    def run_loop(self):
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return self.snap()

    def decision_pages(self, snap):
        return {name: row for name, row in snap.inbox.items()
                if row.get("kind") == "decision" and row.get("ref") == "1"}

    def assert_stuck_decision(self, snap):
        t = snap.ticket("1")
        self.assertEqual(t["state"], "stuck")
        self.assertEqual(t["stuck"]["reason"], "decision")
        return t

    def assert_own_page(self, snap, text):
        found = self.decision_pages(snap)
        self.assertEqual(len(found), 1, "decision pages for #1: %r (all pages: %r)"
                         % (found, sorted(r.get("kind") + " " + str(r.get("ref")) for r in snap.inbox.values())))
        page = list(found.values())[0]
        self.assertIn(text, page["text"])
        self.assertEqual([o["action"] for o in page.get("options") or []], ["retry", "revise", "cancel"])
        self.assertEqual(pages.problems("1", page), [])

    @staticmethod
    def escalated(t):
        return [h for h in t.get("history") or [] if h.get("event") == "escalated"]

    # ------------------------------------------------------------ acceptance 1

    def test_1_a_blocked_hand_in_without_a_revise_leaves_one_decision_page(self):
        self.add(1)
        self.plan(worker={"1": ["blocked"]})
        snap = self.run_loop()
        self.assert_stuck_decision(snap)
        self.assert_own_page(snap, "need the user")

    # ------------------------------------------------------------ acceptance 2 and 3

    def test_2_3_an_escalated_revise_records_its_page_and_is_the_only_decision_page(self):
        self.add(1)
        self.plan(worker={"1": ["revise_blocked"]}, creator={"1": ["question"]})
        snap = self.run_loop()
        t = self.assert_stuck_decision(snap)
        found = self.decision_pages(snap)
        self.assertEqual(len(found), 1, "decision pages for #1: %r" % found)
        name, page = list(found.items())[0]
        self.assertTrue(page["text"].startswith("#1 needs your decision (raised by the worker)"), page["text"])
        events = self.escalated(t)
        self.assertEqual(len(events), 1, "escalated events on #1: %r" % t.get("history"))
        self.assertEqual(events[0].get("source"), "worker")
        self.assertEqual(events[0].get("page"), name)

    # ------------------------------------------------------------ acceptance 4

    def test_4_an_escalation_from_an_earlier_run_does_not_count(self):
        self.add(1, history=[{"event": "round start", "round": 1},
                             {"event": "escalated", "source": "worker", "page": "old"}])
        self.plan(worker={"1": ["blocked"]})
        snap = self.run_loop()
        self.assert_stuck_decision(snap)
        self.assert_own_page(snap, "need the user")


class FrozenWrites(LoopCase):
    """D-089: the pool writes of a revision refuse a merged or landed ticket inside the transaction."""

    def setUp(self):
        super().setUp()
        import ticket_creator
        from core.config import Config
        self.tc = ticket_creator
        self.cfg = Config(self.proj)
        self.add(1)

    def set_state(self, state):
        def change(tx):
            row = tx.ticket("1")
            row.update(state=state, group="g1", target="int/g1")
            tx.put_ticket(row)
        self.pool.update(change, "test: #1 %s" % state)
        return self.snap().ticket("1")

    def test_frozen_reason_names_only_merged_and_landed(self):
        from core.pool import frozen_reason
        self.assertEqual(
            frozen_reason("7", "merged"),
            "#7 is feature complete (merged) and frozen: it cannot be revised or reopened. To overturn it, "
            "open a new ticket: actl new --request \"...\" (the new ticket may revert #7).")
        self.assertIn("(landed)", frozen_reason("7", "landed"))
        for state in ("ready", "running", "stuck", "cancelled", None):
            self.assertIsNone(frozen_reason("7", state))

    def test_apply_revised_refuses_merged_and_landed_without_writing(self):
        res = {"kind": "revised", "ticket": {"acceptance": ["changed"]}}
        for state in ("merged", "landed"):
            before = self.set_state(state)
            with self.assertRaises(osx.OSFailure) as ctx:
                self.tc.apply_revised(self.cfg, self.pool, "1", "worker", res, "revise-x")
            self.assertIn("#1 is feature complete (%s)" % state, str(ctx.exception))
            self.assertEqual(self.snap().ticket("1"), before)

    def test_user_reset_refuses_merged_and_landed_without_writing(self):
        for state in ("merged", "landed"):
            before = self.set_state(state)
            for target in ("ready", "cancelled"):
                with self.assertRaises(osx.OSFailure) as ctx:
                    self.tc.user_reset(self.pool, "1", target, "x")
                self.assertIn("frozen", str(ctx.exception))
                self.assertEqual(self.snap().ticket("1"), before)

    def test_user_reset_still_resets_a_stuck_ticket(self):
        self.set_state("stuck")
        self.tc.user_reset(self.pool, "1", "ready", "x")
        self.assertEqual(self.snap().ticket("1")["state"], "ready")


class CasesThrownOut(LoopCase):
    def test_a_cases_wrong_revision_after_the_last_round_start_throws_out_the_cases(self):
        import ticket_issuer
        h = [{"event": "round start"}, {"event": "revised", "cases_wrong": True}]
        self.assertTrue(ticket_issuer.cases_thrown_out({"history": h}))

    def test_a_revision_before_the_round_or_without_cases_wrong_does_not(self):
        import ticket_issuer
        before = [{"event": "revised", "cases_wrong": True}, {"event": "round start"}]
        kept = [{"event": "round start"}, {"event": "revised", "cases_wrong": False}]
        self.assertFalse(ticket_issuer.cases_thrown_out({"history": before}))
        self.assertFalse(ticket_issuer.cases_thrown_out({"history": kept}))
        self.assertFalse(ticket_issuer.cases_thrown_out({}))

    def test_a_ticket_without_a_verifier_and_no_cases_runs_nothing(self):
        import ticket_issuer
        issuer = ticket_issuer.Issuer.__new__(ticket_issuer.Issuer)
        from core.config import Config
        issuer.cfg, issuer.id, issuer.deadline = Config(self.proj), "1", time.time() + 60
        self.assertIsNone(issuer.run_cases({"needs_verifier": False, "cases": None}))


class T137Acceptance(LoopCase):
    """Ticket #137 (D-098): before the agents of an issuer round start, t/<N> and t/<N>-cases are the
    target's current head plus only the ticket's own commits; the target sha is recorded in branch_base."""

    def setUp(self):
        super().setUp()
        for name in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(name, None)
        self.sides = 0

    # ------------------------------------------------------------ fixture helpers

    def side(self, branch, start=None):
        """A throwaway worktree on `branch` (made from `start` when given); the project's main stays checked out."""
        self.sides += 1
        path = os.path.join(self.tmp, "side-%d" % self.sides)
        if start is None:
            git(self.proj, "worktree", "add", "-q", path, branch)
        else:
            git(self.proj, "worktree", "add", "-q", "-b", branch, path, start)
        return path

    def commit_on(self, branch, files, subject, start=None):
        path = self.side(branch, start)
        for rel, text in files.items():
            full = os.path.join(path, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text)
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", subject)
        sha = git(path, "rev-parse", "HEAD")
        git(self.proj, "worktree", "remove", "--force", path)
        return sha

    def merge_on(self, branch, other):
        path = self.side(branch)
        git(path, "merge", "-q", "--no-ff", "--no-edit", other)
        git(self.proj, "worktree", "remove", "--force", path)

    def put_group(self, gid, state, members):
        self.pool.update(lambda tx: tx.put_group({
            "id": gid, "branch": "int/" + gid, "state": state, "members": members, "fix_round": 0,
            "job": None, "created": "2026-11-23T00:00:00Z", "history": []}), "test: group %s" % gid)

    def main_files(self):
        return set(git(self.proj, "ls-tree", "-r", "--name-only", "main").split())

    def moved_fixture(self, with_cases=False):
        """Items 3-5: t/1 made in g1 (at 'work 5'), took in int/g1 again after 'work 6'; now in g2."""
        a = self.commit_on("int/g1", {"five.txt": "5\n"}, "work 5", start="main")
        self.commit_on("t/1", {"seven.txt": "7\n"}, "work 1 r1", start=a)
        if with_cases:
            self.commit_on("t/1-cases", {"cases/case_1.txt": "case\n"}, "cases 1", start=a)
            self.merge_on("t/1", "t/1-cases")
        self.commit_on("int/g1", {"six.txt": "6\n"}, "work 6")
        self.merge_on("t/1", "int/g1")
        self.commit_on("t/1", {"seven.txt": "7b\n"}, "work 1 r2")
        self.commit_on("int/g2", {"new.txt": "n\n"}, "new group work", start="main")
        self.put_group("g1", "cancelled", [])
        self.put_group("g2", "open", ["1"])
        return a

    def assert_moved_landed(self, res):
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        self.assertEqual(self.state(1), "landed")
        files = self.main_files()
        self.assertEqual(git(self.proj, "show", "main:seven.txt"), "7b")
        self.assertIn("impl_1.txt", files)
        self.assertIn("new.txt", files)
        self.assertNotIn("five.txt", files, "a commit of the old group g1 reached main")
        self.assertNotIn("six.txt", files, "a commit of the old group g1 reached main")

    # ------------------------------------------------------------ items 1 and 2

    def test_1_2_retried_ticket_starts_on_the_targets_head_and_records_branch_base(self):
        self.commit_on("int/g1", {"other.txt": "o\n"}, "other ticket merged", start="main")
        self.commit_on("t/1", {"impl_1.txt": "bad"}, "work 1 old", start="main")
        self.add(1, group="g1")
        self.put_group("g1", "open", ["1"])
        t1 = git(self.proj, "rev-parse", "int/g1")
        starts = os.path.join(self.tmp, "starts.txt")
        wrap = os.path.join(self.tmp, "wrap.sh")
        with open(wrap, "w") as fh:
            fh.write("#!/bin/sh\n"
                     "if git merge-base --is-ancestor %s HEAD; then A=yes; else A=no; fi\n"
                     "echo \"ancestor=$A log=$(git log --format=%%s %s..HEAD | tr '\\n' '|')\" >> %s\n"
                     "exec python3 %s\n" % (t1, t1, starts, os.path.join(HERE, "fake_agent.py")))
        agents = {r: AGENT for r in ROLES}
        agents["worker"] = agents["worker_simple"] = ["sh", wrap]
        self.config(agents=agents)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        self.assertTrue(os.path.exists(starts), "the worker never started")
        with open(starts) as fh:
            first = fh.read().splitlines()[0]
        # item 1: the round-1 worker's HEAD holds T1 and exactly the one own commit on top of it
        self.assertEqual(first, "ancestor=yes log=work 1 old|")
        # item 2
        t = self.snap().ticket("1")
        self.assertEqual(t.get("branch_base"), {"t/1": t1, "t/1-cases": t1})
        rebased = [h for h in t.get("history") or [] if h.get("event") == "rebased"]
        self.assertEqual(len([h for h in rebased if h.get("branch") == "t/1"]), 1, rebased)
        self.assertEqual([h for h in rebased if h.get("branch") == "t/1-cases"], [], rebased)
        self.assertEqual(len(rebased), 1, rebased)
        ev = rebased[0]
        self.assertEqual((ev.get("onto"), ev.get("base"), ev.get("replayed")), ("int/g1", t1, 1), ev)

    # ------------------------------------------------------------ items 3 and 4

    def test_3_moved_ticket_with_a_recorded_base_carries_no_commit_of_the_old_group(self):
        a = self.moved_fixture()
        self.add(1, group="g2", branch_base={"t/1": a}, history=[])
        self.assert_moved_landed(self.controller())

    def test_4_moved_ticket_without_a_recorded_base_uses_the_old_target_from_history(self):
        self.moved_fixture()
        self.add(1, group="g2", history=[{"at": "2026-11-20T00:00:00Z", "event": "issuer started",
                                          "target": "int/g1"}])
        self.assertNotIn("branch_base", self.snap().ticket("1"))
        self.assert_moved_landed(self.controller())

    # ------------------------------------------------------------ item 5

    def test_5_accepted_cases_made_in_the_old_group_are_replayed_onto_the_new_target(self):
        a = self.moved_fixture(with_cases=True)
        self.add(1, group="g2", cases={"branch": "t/1-cases", "run": ["case_1"]},
                 branch_base={"t/1": a, "t/1-cases": a}, history=[])
        g2 = git(self.proj, "rev-parse", "int/g2")
        res = self.controller()
        self.assert_moved_landed(res)
        self.assertIn("cases/case_1.txt", self.main_files())
        self.assertEqual((self.snap().ticket("1").get("branch_base") or {}).get("t/1-cases"), g2)

    # ------------------------------------------------------------ item 6

    def test_6_uncommitted_tracked_changes_become_a_devloop_commit(self):
        self.commit_on("int/g1", {"other.txt": "o\n"}, "other ticket merged", start="main")
        self.commit_on("t/1", {"impl_1.txt": "bad"}, "work 1 old", start="main")
        self.add(1, group="g1", needs_verifier=False)
        self.put_group("g1", "open", ["1"])
        os.makedirs(os.path.join(self.tmp, "wt"))
        wt = os.path.join(self.tmp, "wt", "t1")
        git(self.proj, "worktree", "add", "-q", wt, "t/1")
        with open(os.path.join(wt, "README.md"), "w") as fh:
            fh.write("local\n")
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(git(self.proj, "show", "main:README.md"), "local")
        lines = [ln for ln in git(self.proj, "log", "--format=%an|%s", "main").splitlines()
                 if ln == "devloop|devloop: uncommitted changes in t/1 at the start of round 1"]
        self.assertEqual(len(lines), 1, git(self.proj, "log", "--format=%an|%s", "main"))


class ApplyUnapplied(LoopCase):
    """ticket_creator.apply_unapplied: which revise-* hand-ins the issuer writes into the pool."""

    REVISED = {"kind": "revised", "ticket": {"acceptance": ["changed"]}, "cases_wrong": True}

    def setUp(self):
        super().setUp()
        import ticket_creator
        from core.config import Config
        self.tc = ticket_creator
        self.cfg = Config(self.proj)
        self.add(1, cases=["c"])
        self.since = time.time()

    def hand_in(self, name, source="worker", result=REVISED, raw=None, age=None, meta=True):
        d = os.path.join(self.tmp, "wt", "runs", "1", name)
        os.makedirs(d)
        if meta:
            osx.write_json(os.path.join(d, "revise.json"), {"ticket": "1", "source": source})
        path = os.path.join(d, "result.json")
        if raw is not None:
            with open(path, "w") as fh:
                fh.write(raw)
        elif result is not None:
            osx.write_json(path, result)
        if age is not None and os.path.exists(path):
            os.utime(path, (self.since - age, self.since - age))
        return name

    def apply(self):
        return self.tc.apply_unapplied(self.cfg, self.pool, "1", self.since)

    def revised(self):
        return [h for h in self.snap().ticket("1").get("history", []) if h["event"] == "revised"]

    def assert_skipped(self):
        self.assertEqual(self.apply(), [])
        t = self.snap().ticket("1")
        self.assertEqual(self.revised(), [])
        self.assertEqual(t["acceptance"], ["a"])
        self.assertEqual(t["cases"], ["c"])

    def test_a_fresh_worker_revision_is_applied_once_by_the_issuer(self):
        name = self.hand_in("revise-2026-11-01T100000")
        self.assertEqual(self.apply(), [name])
        t = self.snap().ticket("1")
        self.assertEqual(t["acceptance"], ["changed"])
        self.assertIsNone(t["cases"])
        ev = self.revised()
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["run"], ev[0]["applied_by"], ev[0]["source"], ev[0]["cases_wrong"]),
                         (name, "issuer", "worker", True))
        self.assertEqual(self.apply(), [], "a second scan applied the same hand-in again")
        self.assertEqual(len(self.revised()), 1)

    def test_several_are_applied_in_name_order(self):
        b = self.hand_in("revise-2026-11-01T100002")
        a = self.hand_in("revise-2026-11-01T100001")
        self.assertEqual(self.apply(), [a, b])
        self.assertEqual([e["run"] for e in self.revised()], [a, b])

    def test_no_runs_directory_is_not_an_error(self):
        self.assertEqual(self.apply(), [])

    def test_a_user_revision_is_skipped(self):
        self.hand_in("revise-x", source="user")
        self.assert_skipped()

    def test_a_hand_in_older_than_the_issuer_is_skipped(self):
        self.hand_in("revise-x", age=60)
        self.assert_skipped()

    def test_another_kind_is_skipped(self):
        for i, kind in enumerate(("unchanged", "question", "rejected")):
            self.hand_in("revise-%d" % i, result={"kind": kind, "text": "t"})
        self.assert_skipped()

    def test_a_missing_or_broken_result_is_skipped(self):
        self.hand_in("revise-a", result=None)
        self.hand_in("revise-b", raw="{not json")
        self.hand_in("revise-c", raw="[1, 2]")
        self.assert_skipped()

    def test_a_directory_without_revise_json_is_skipped(self):
        self.hand_in("revise-x", meta=False)
        self.assert_skipped()

    def test_a_hand_in_already_written_by_its_creator_is_skipped(self):
        name = self.hand_in("revise-x")
        self.tc.apply_revised(self.cfg, self.pool, "1", "worker", self.REVISED, name)
        self.assertEqual(self.apply(), [])
        ev = self.revised()
        self.assertEqual(len(ev), 1)
        self.assertNotIn("applied_by", ev[0])
        self.assertEqual(ev[0]["run"], name)


class FreeRounds(LoopCase):
    """Rounds that ended on a merge conflict with the group or a cases_wrong revision are free."""

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            del self.env[k]

    def run_one(self, **plan):
        self.add(1, **plan.pop("extra", {}))
        self.plan(**plan)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return self.snap().ticket("1")

    @staticmethod
    def of(t, name):
        return [h for h in t["history"] if h["event"] == name]

    def test_a_red_of_the_cases_is_not_free_and_round_starts_carry_their_number(self):
        t = self.run_one(worker={"1": ["wrong", "ok"]})
        self.assertEqual((t["state"], t["round"]), ("landed", 2))
        self.assertEqual([h.get("round") for h in self.of(t, "round start")], [1, 2])
        fed = self.of(t, "feedback")
        self.assertEqual(len(fed), 1)
        self.assertNotIn("free", fed[0])

    def test_a_review_failure_is_not_free(self):
        t = self.run_one(extra={"complexity": "complex"}, reviewer={"1": ["fail", "pass"]})
        self.assertEqual((t["state"], t["round"]), ("landed", 2))
        self.assertNotIn("free", self.of(t, "feedback")[0])

    def test_only_free_rounds_stop_at_twice_max_rounds(self):
        self.config(max_rounds=1)
        t = self.run_one(worker={"1": ["revise"]}, creator={"1": ["revised"]})
        self.assertEqual((t["state"], t["stuck"]["reason"], t["round"]), ("stuck", "rounds", 2))
        self.assertEqual([h.get("free") for h in self.of(t, "feedback")], ["revision", "revision"])
        self.assertIn("2 rounds used and still not passing (0 real red, 2 free: 0 merge conflict, "
                      "2 revision); last feedback:\nround 2: the ticket was revised", t["stuck"]["text"])

    def test_the_rounds_text_splits_real_and_free(self):
        import ticket_issuer
        t = {"round": 4, "feedback": ["round 3: x", "round 4: last"],
             "history": [{"event": "round start", "round": 1},
                         {"event": "feedback", "round": 1, "free": "merge"},
                         {"event": "feedback", "round": 2},
                         {"event": "feedback", "round": 3, "free": "revision"},
                         {"event": "feedback", "round": 4, "free": "merge"}]}
        self.assertEqual(ticket_issuer.rounds_text(t),
                         "4 rounds used and still not passing (1 real red, 3 free: 2 merge "
                         "conflict, 1 revision); last feedback:\nround 4: last")


class NotOwnedWrites(LoopCase):
    """Issuer.save() and integrate() write nothing and move nothing once the ticket is no longer
    running under the issuer's claim stamp; _go() turns that into one stderr line and exit 2."""

    def setUp(self):
        super().setUp()
        import ticket_issuer
        from core.config import Config
        self.ti = ticket_issuer
        self.add(1, state="running", target="int/g1", group="g1")
        self.issuer = ticket_issuer.Issuer(Config(self.proj), self.pool, "1", "int/g1")
        self.issuer.claim()

    def take_away(self, **fields):
        def change(tx):
            row = tx.ticket("1")
            row.update(fields)
            row.setdefault("history", []).append({"event": "taken away by the test"})
            tx.put_ticket(row)
        self.pool.update(change, "test: #1 taken away")
        return self.snap().ticket("1")

    def assert_refused(self, fn, before):
        with self.assertRaises(self.ti.NotOwned) as ctx:
            fn()
        self.assertEqual(self.snap().ticket("1"), before)
        return str(ctx.exception)

    def test_claim_keeps_the_stamp_it_wrote_and_its_own_ticket_is_still_written(self):
        self.assertEqual(self.snap().ticket("1")["run"], self.issuer.run_stamp)
        self.issuer.save(lambda t: t.__setitem__("round", 1), "round start")
        self.assertEqual(self.snap().ticket("1")["round"], 1)

    def test_a_released_ticket_takes_no_write(self):
        before = self.take_away(state="ready", run=None)
        msg = self.assert_refused(lambda: self.issuer.save(lambda t: t.__setitem__("round", 9), "x"),
                                  before)
        self.assertEqual(msg, "#1 is no longer this issuer's ticket (state ready)")
        self.assert_refused(lambda: self.issuer.stuck("env", "broken"), before)
        self.assert_refused(lambda: self.issuer.release("signal 15", [{"role": "worker"}]), before)
        self.assertEqual([p for p in self.snap().inbox.values() if str(p.get("ref")) == "1"], [])

    def test_another_stamp_takes_no_write(self):
        other = {"pid": 1, "host": "another-host", "boot": "b", "started": osx.now()}
        before = self.take_away(run=other)
        self.assert_refused(lambda: self.issuer.on_quota("wait", row={"role": "worker"},
                                                         until=time.time() + 60), before)
        self.assert_refused(lambda: self.issuer.on_quota("over"), before)
        self.assert_refused(lambda: self.issuer.save(lambda t: self.issuer._merged(t, []), "merged"),
                            before)

    def test_a_merged_ticket_is_not_turned_into_stuck(self):
        before = self.take_away(state="merged", run=None)
        msg = self.assert_refused(lambda: self.issuer.stuck("rounds", "x"), before)
        self.assertIn("(state merged)", msg)

    def fake_git(self, behind):
        repo, g = mock.Mock(), mock.Mock()
        repo.sha.return_value = "old"
        g.is_ancestor.return_value = not behind
        g.merge.return_value = (True, "")
        g.sha.return_value = "new"
        self.issuer.repo = repo
        return repo, g

    def test_integrate_neither_merges_nor_moves_a_ticket_that_is_not_its_own(self):
        before = self.take_away(state="ready", run=None)
        for behind in (True, False):
            repo, g = self.fake_git(behind)
            self.assert_refused(lambda: self.issuer.integrate(g, before), before)
            g.merge.assert_not_called()
            repo.move_ref.assert_not_called()

    def test_integrate_moves_the_target_of_its_own_ticket(self):
        repo, g = self.fake_git(False)
        repo.move_ref.return_value = True
        self.assertIsNone(self.issuer.integrate(g, self.snap().ticket("1")))
        repo.move_ref.assert_called_once_with("int/g1", "new", "old")

    def run_go(self, issuer):
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            rc = self.ti._go(issuer)
        return rc, [line for line in err.getvalue().splitlines() if line.strip()]

    def test_go_ends_a_stuck_or_interrupted_issuer_whose_ticket_is_gone_with_one_line_and_2(self):
        before = self.take_away(state="ready", run=None)
        for raised in (self.ti.Stuck("env", "broken"), self.ti.Interrupted("signal 15")):
            def go(exc=raised):
                raise exc
            with mock.patch.object(self.issuer, "go", go):
                rc, lines = self.run_go(self.issuer)
            self.assertEqual(rc, 2)
            self.assertEqual(lines, ["ticket_issuer: #1 is no longer this issuer's ticket (state "
                                     "ready); stopped without writing"])
            self.assertEqual(self.snap().ticket("1"), before)
            self.assertEqual([p for p in self.snap().inbox.values() if str(p.get("ref")) == "1"], [])


class T115Acceptance(LoopCase):
    """Ticket #115 (issue #4b): a blocked hand-in's failing ids are run by devloop; when they pass,
    the worker runs again in a round that does not count. Expected values come from the ticket and
    the fakes (fake_agent 'blocked_failing', fake_tests' rerun rows), never from ticket_issuer.py."""

    def setUp(self):
        super().setUp()
        for name in [k for k in self.env if k.startswith("DEVLOOP_")]:
            del self.env[name]
        clean = {k: v for k, v in os.environ.items() if not k.startswith("DEVLOOP_")}
        patch = mock.patch.dict(os.environ, clean, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.run_ids = os.path.join(self.tmp, "run-ids.txt")
        self.env["FAKE_RUN_IDS"] = self.run_ids

    def run_loop(self):
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return self.snap()

    def ids_run(self):
        if not os.path.exists(self.run_ids):
            return []
        with open(self.run_ids) as fh:
            return [line.strip() for line in fh if line.strip()]

    def decision_pages(self, snap):
        return [row for row in snap.inbox.values() if row.get("kind") == "decision" and row.get("ref") == "1"]

    def worker_rows(self, t):
        return [(row.get("round"), row.get("model")) for row in t.get("cost") or [] if row.get("role") == "worker"]

    # ------------------------------------------------------------ acceptance 1

    def test_1_the_worker_prompt_shows_a_blocked_hand_in_with_failing_ids(self):
        import string
        from core import agents
        fields = {f for _, f, _, _ in string.Formatter().parse(agents.raw("worker.md")) if f}
        text = agents.render("worker.md", {f: "VALUE_%s" % f for f in fields})
        self.assertIn('"blocked"', text)
        self.assertIn('"failing"', text, "worker.md does not show a blocked hand-in with a \"failing\" list")

    # ------------------------------------------------------------ acceptance 2

    def test_2_passing_ids_give_a_free_agent_env_round_and_the_ticket_lands(self):
        self.config(max_rounds=1)
        self.add(1)
        self.plan(worker={"1": ["blocked_failing", "ok"]})
        snap = self.run_loop()
        t = snap.ticket("1")
        self.assertEqual((t["state"], t["round"]), ("landed", 2), t.get("stuck"))
        fed = [h for h in t["history"] if h["event"] == "feedback" and h.get("round") == 1]
        self.assertEqual([h.get("free") for h in fed], ["agent_env"], t["history"])
        prompt = os.path.join(self.tmp, "wt", "runs", "1", "r2-worker", "prompt.md")
        self.assertTrue(os.path.exists(prompt), "no round-2 worker prompt at %s" % prompt)
        with open(prompt) as fh:
            text = fh.read()
        self.assertIn("passed when devloop ran them", text)
        self.assertIn("suite.base", text)
        self.assertEqual(self.ids_run(), ["suite.base"])
        self.assertEqual(self.decision_pages(snap), [])

    # ------------------------------------------------------------ acceptance 3

    def test_3_the_free_round_runs_the_next_command_of_a_two_command_chain(self):
        self.config(agents=dict({r: AGENT for r in ROLES},
                                worker_simple=[AGENT + ["--model", "w1"], AGENT + ["--model", "w2"]]))
        self.add(1)
        self.plan(worker={"1": ["blocked_failing", "ok"]})
        t = self.run_loop().ticket("1")
        self.assertEqual(t["state"], "landed", t.get("stuck"))
        self.assertEqual(self.worker_rows(t), [(1, "w1"), (2, "w2")])

    def test_3_a_chain_of_one_command_runs_the_same_command_again(self):
        self.config(agents=dict({r: AGENT for r in ROLES}, worker_simple=[AGENT + ["--model", "w1"]]))
        self.add(1)
        self.plan(worker={"1": ["blocked_failing", "ok"]})
        t = self.run_loop().ticket("1")
        self.assertEqual(t["state"], "landed", t.get("stuck"))
        self.assertEqual(self.worker_rows(t), [(1, "w1"), (2, "w1")])

    # ------------------------------------------------------------ acceptance 4

    def test_4_ids_that_do_not_pass_leave_one_decision_page(self):
        self.env["FAKE_FAILING"] = '["impl.impl_1.txt"]'
        self.add(1)
        self.plan(worker={"1": ["blocked_failing"]})
        snap = self.run_loop()
        t = snap.ticket("1")
        self.assertEqual(t["state"], "stuck")
        self.assertEqual(t["stuck"]["reason"], "decision")
        self.assertEqual(self.ids_run(), ["impl.impl_1.txt"], "devloop did not run the worker's ids")
        found = self.decision_pages(snap)
        self.assertEqual(len(found), 1, found)
        self.assertIn("cannot reach 127.0.0.1 from this sandbox", found[0]["text"])
        self.assertIn("impl.impl_1.txt", found[0]["text"])

    # ------------------------------------------------------------ acceptance 5

    def test_5_a_worker_that_keeps_blocking_stops_at_twice_max_rounds(self):
        self.config(max_rounds=1)
        self.add(1)
        self.plan(worker={"1": ["blocked_failing"]})
        t = self.run_loop().ticket("1")
        self.assertEqual((t["state"], t["stuck"]["reason"], t["round"]), ("stuck", "rounds", 2), t.get("stuck"))
        self.assertIn("2 rounds used and still not passing (0 real red, 2 free: 0 merge conflict, "
                      "0 revision, 2 agent environment)", t["stuck"]["text"])


class T11Acceptance(LoopCase):
    """Ticket #11: a revision the opener finished reaches the pool even when the worker is stopped."""

    def runs_dir(self):
        return os.path.join(self.tmp, "wt", "runs", "1")

    def revise_dirs(self):
        return [d for d in os.listdir(self.runs_dir()) if d.startswith("revise-")]

    def test_v1_a_revision_left_by_a_stopped_worker_is_applied_before_round_two(self):
        """Items 1-3: one run with a worker that overruns its timeout while the opener hands in."""
        self.config(agent_timeout_seconds=5)
        self.add(1)
        self.plan(worker={"1": ["revise_overrun", "ok"]}, creator={"1": ["revised_hold"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        # item 1
        prompt = os.path.join(self.runs_dir(), "r2-worker", "prompt.md")
        self.assertTrue(os.path.exists(prompt), "no round-2 worker prompt")
        with open(prompt) as fh:
            self.assertIn("a possible thing", fh.read())
        # item 2
        t = self.snap().ticket("1")
        names = [h.get("event") for h in t["history"]]
        self.assertEqual(names.count("revised"), 1, names)
        starts = [i for i, n in enumerate(names) if n == "round start"]
        self.assertGreaterEqual(len(starts), 2, names)
        self.assertLess(names.index("revised"), starts[1], names)
        revised = t["history"][names.index("revised")]
        self.assertEqual(revised.get("applied_by"), "issuer", revised)
        dirs = self.revise_dirs()
        self.assertEqual(len(dirs), 1, dirs)
        self.assertEqual(revised.get("run"), dirs[0], revised)
        # item 3
        self.assertTrue(os.path.isdir(os.path.join(self.runs_dir(), "r2-verifier")),
                        "round 2 did not start the verifier again")
        self.assertEqual(t["state"], "landed")
        self.assertEqual(t["acceptance"], ["a possible thing"])

    def test_v2_a_revision_on_the_normal_path_records_its_run(self):
        """Item 4: the worker waits for the opener; the event names its run and no applied_by."""
        self.add(1)
        self.plan(worker={"1": ["revise", "ok"]}, creator={"1": ["revised"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual(t["state"], "landed")
        events = [h for h in t["history"] if h.get("event") == "revised"]
        self.assertEqual(len(events), 1, t["history"])
        dirs = self.revise_dirs()
        self.assertEqual(len(dirs), 1, dirs)
        self.assertEqual(events[0].get("run"), dirs[0], events[0])
        self.assertNotIn("applied_by", events[0])


class RebaseRoundUnits(LoopCase):
    """Issuer.rebase_round (D-098) called on its own: what it leaves alone and its interim
    behaviour on a failed replay."""

    def setUp(self):
        super().setUp()
        for name in [k for k in self.env if k.startswith("DEVLOOP_")]:
            del self.env[name]
        from core.config import Config
        self.sides = 0
        self.side("int/g1", "main", "README.md", "group\n", "group work")
        self.target = git(self.proj, "rev-parse", "int/g1")
        self.cfg = Config(self.proj)

    def side(self, branch, start, path, text, subject):
        self.sides += 1
        where = os.path.join(self.tmp, "side-%d" % self.sides)
        new = ["-b", branch] if start else []
        git(self.proj, "worktree", "add", "-q", *new, where, start or branch)
        full = os.path.join(where, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(text)
        git(where, "add", "-A")
        git(where, "commit", "-q", "-m", subject)
        git(self.proj, "worktree", "remove", "--force", where)
        return git(self.proj, "rev-parse", branch)

    def issuer(self, **extra):
        import ticket_issuer
        self.add(1, state="running", target="int/g1", group="g1", **extra)
        issuer = ticket_issuer.Issuer(self.cfg, self.pool, "1", "int/g1")
        issuer.claim()
        return issuer

    def events(self, name):
        return [h for h in self.snap().ticket("1").get("history") or [] if h.get("event") == name]

    def test_a_branch_already_on_its_start_with_only_its_own_commits_is_kept(self):
        head = self.side("t/1", "int/g1", "mine.txt", "m\n", "mine")
        t = self.issuer().rebase_round(self.snap().ticket("1"), 1)
        self.assertEqual(git(self.proj, "rev-parse", "t/1"), head)
        self.assertEqual(self.events("rebased"), [])
        self.assertEqual(t["branch_base"], {"t/1": self.target})

    def test_a_failed_replay_puts_both_branches_back_and_records_nothing(self):
        cases = self.side("t/1-cases", "main", "cases/c.txt", "case\n", "cases 1")
        head = self.side("t/1", "main", "README.md", "mine\n", "work 1")
        issuer = self.issuer(cases={"branch": "t/1-cases", "run": ["c"]})
        before = self.snap().ticket("1")
        t = issuer.rebase_round(before, 1)
        self.assertEqual(git(self.proj, "rev-parse", "t/1"), head)
        self.assertEqual(git(self.proj, "rev-parse", "t/1-cases"), cases)
        wt = git(issuer.wt, "status", "--porcelain")
        self.assertEqual(wt, "")
        self.assertFalse(os.path.exists(os.path.join(git(issuer.wt, "rev-parse", "--git-dir"),
                                                     "rebase-merge")))
        self.assertNotIn("branch_base", self.snap().ticket("1"))
        self.assertEqual(self.events("rebased"), [])
        self.assertEqual(t, before)
        self.assertFalse(os.path.exists(os.path.join(self.cfg.worktree_root, "rebase-t1-cases")))

    def test_uncommitted_changes_become_a_devloop_commit_that_is_replayed(self):
        self.side("t/1", "main", "mine.txt", "m\n", "work 1")
        issuer = self.issuer()
        os.makedirs(self.cfg.worktree_root, exist_ok=True)
        git(self.proj, "worktree", "add", "-q", issuer.wt, "t/1")
        with open(os.path.join(issuer.wt, "mine.txt"), "w") as fh:
            fh.write("local\n")
        issuer.rebase_round(self.snap().ticket("1"), 2)
        self.assertEqual(git(self.proj, "log", "--format=%an|%cn|%s", self.target + "..t/1").splitlines(),
                         ["devloop|devloop|devloop: uncommitted changes in t/1 at the start of round 2",
                          "t|devloop|work 1"])
        ev = self.events("rebased")
        self.assertEqual(len(ev), 1, ev)
        self.assertEqual((ev[0]["branch"], ev[0]["onto"], ev[0]["base"], ev[0]["replayed"]),
                         ("t/1", "int/g1", self.target, 2))


class T20Acceptance(LoopCase):
    """Ticket #20: a ticket that needs a verifier merges only on cases written after its latest
    cases_wrong revision; never with an empty case list."""

    def runs_dir(self):
        return os.path.join(self.tmp, "wt", "runs", "1")

    def run_loop(self):
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return self.snap().ticket("1")

    def assert_merged_on_fresh_cases(self, t, nth_start):
        names = [h.get("event") for h in t["history"]]
        starts = [i for i, n in enumerate(names) if n == "round start"]
        self.assertGreaterEqual(len(starts), nth_start, names)
        start = starts[nth_start - 1]
        merged = [i for i, n in enumerate(names) if n == "merged"]
        self.assertEqual(len(merged), 1, names)
        self.assertGreater(merged[0], start, names)
        self.assertTrue(any(n == "cases ready" for n in names[start + 1:]), names)

    def test_1_a_revision_during_a_verifier_round_lands_in_round_two(self):
        self.plan(worker={"1": ["revise", "ok"]}, creator={"1": ["revised"]})
        t = self.run_loop()
        self.assertEqual((t["state"], t["round"]), ("landed", 2), t["history"])
        self.assert_merged_on_fresh_cases(t, 2)
        self.assertTrue(os.path.isdir(os.path.join(self.runs_dir(), "r2-verifier")))
        self.assertTrue(os.path.isdir(os.path.join(self.runs_dir(), "r2-worker")))

    def test_2_a_revision_in_a_round_without_a_verifier_lands_in_round_three(self):
        self.plan(worker={"1": ["wrong", "revise", "ok"]}, creator={"1": ["revised"]})
        t = self.run_loop()
        self.assertEqual((t["state"], t["round"]), ("landed", 3), t["history"])
        self.assert_merged_on_fresh_cases(t, 3)
        roles = [(c["role"], c["round"]) for c in t.get("cost") or []]
        self.assertIn(("verifier", 3), roles)
        prompt = os.path.join(self.runs_dir(), "r3-worker", "prompt.md")
        self.assertTrue(os.path.exists(prompt), "no round-3 worker prompt")
        with open(prompt) as fh:
            self.assertIn("threw out the verifier's cases", fh.read())

    def test_3_a_revision_in_the_last_round_goes_stuck_not_merged(self):
        self.config(max_rounds=1)
        self.plan(worker={"1": ["revise"]}, creator={"1": ["revised"]})
        t = self.run_loop()
        self.assertEqual(t["state"], "stuck", t["history"])
        self.assertEqual(t["stuck"]["reason"], "rounds")
        self.assertFalse(self.main_has("impl_1.txt"))
        self.assertTrue(t["feedback"], "no feedback entry")
        self.assertIn("threw out the verifier's cases", str(t["feedback"][-1]))

    def test_4_run_cases_refuses_a_ticket_that_needs_a_verifier_and_has_no_cases(self):
        import ticket_issuer
        from core.config import Config
        self.assertTrue(hasattr(ticket_issuer.Issuer, "run_cases"))
        issuer = ticket_issuer.Issuer.__new__(ticket_issuer.Issuer)
        issuer.cfg, issuer.id, issuer.deadline = Config(self.proj), "1", time.time() + 60
        for t in ({"needs_verifier": True, "cases": None},
                  {"cases": {"branch": "t/1-cases", "run": []}}):
            out = issuer.run_cases(t)
            self.assertIsInstance(out, str, t)
            self.assertTrue(out, t)
            self.assertIn("no acceptance cases", out)


class T22Acceptance(LoopCase):
    """Ticket #22: an interrupted ticket_issuer records one cost row for every agent run of the
    interrupted round; Pending.stop() returns the stopped run's row."""

    KEYS = ("role", "model", "effort", "command", "round", "seconds", "at", "outcome", "run_dir",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "usd",
            "session_id")

    # ------------------------------------------------------------ Pending.stop() (items 1, 2)

    def start_worker(self, command):
        from core import agents
        from core.config import Config
        self.config(agents=dict({r: AGENT for r in ROLES}, worker=command))
        saved = {k: os.environ.get(k) for k in ("DEVLOOP_SLOT", "AC_ROOT")}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        os.environ.pop("DEVLOOP_SLOT", None)
        os.environ["AC_ROOT"] = self.proj
        cfg = Config(self.proj)
        p = agents.start(cfg, "worker", "prompt", self.proj, os.path.join(self.tmp, "run"), 60)
        self.addCleanup(p.stop)
        return p

    def test_1_stop_returns_an_interrupted_row_with_unknown_tokens(self):
        p = self.start_worker(["python3", "-c", "import sys, time; sys.stdin.read(); time.sleep(60)"])
        time.sleep(0.6)
        row = p.stop()
        self.assertIsInstance(row, dict, "Pending.stop() returned %r, not a cost row" % (row,))
        for k in self.KEYS:
            self.assertIn(k, row, "cost row lacks %r: %r" % (k, row))
        self.assertEqual(row["outcome"], "interrupted")
        self.assertEqual(row["role"], "worker")
        self.assertIsInstance(row["seconds"], (int, float))
        self.assertGreater(row["seconds"], 0)
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "usd",
                  "session_id"):
            self.assertIsNone(row[k], "%s should be unknown (None): %r" % (k, row))

    def test_2_stop_reads_a_claude_runs_tokens_from_its_transcript(self):
        bindir = os.path.join(self.tmp, "fake-claude-bin")
        os.makedirs(bindir)
        exe = os.path.join(bindir, "claude")
        with open(exe, "w") as fh:
            fh.write("#!/bin/sh\ncat > /dev/null\nexec sleep 60\n")
        os.chmod(exe, 0o755)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        old_home = os.environ.get("HOME")
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old_home) if old_home is not None
                        else os.environ.pop("HOME", None))
        os.environ["HOME"] = home
        p = self.start_worker([exe])
        import re
        folder = os.path.join(home, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(self.proj)))
        os.makedirs(folder)
        time.sleep(0.3)
        lines = [{"type": "assistant", "message": {"id": "msg_a", "usage": {
                     "input_tokens": 11, "output_tokens": 5, "cache_read_input_tokens": 100,
                     "cache_creation_input_tokens": 7}}},
                 {"type": "assistant", "message": {"id": "msg_b", "usage": {
                     "input_tokens": 30, "output_tokens": 9, "cache_read_input_tokens": 200,
                     "cache_creation_input_tokens": 0}}},
                 {"type": "assistant", "message": {"id": "msg_a", "usage": {
                     "input_tokens": 11, "output_tokens": 5, "cache_read_input_tokens": 100,
                     "cache_creation_input_tokens": 7}}}]
        with open(os.path.join(folder, "sess-t22.jsonl"), "w") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")
        time.sleep(0.3)
        row = p.stop()
        self.assertIsInstance(row, dict, "Pending.stop() returned %r, not a cost row" % (row,))
        self.assertEqual(row.get("outcome"), "interrupted", row)
        self.assertEqual(row.get("command"), "claude", row)
        self.assertEqual(row.get("input_tokens"), 41, row)
        self.assertIs(row.get("from_transcript"), True, row)
        self.assertEqual(row.get("session_id"), "sess-t22", row)

    # ------------------------------------------------------------ the issuer (items 4, 5, 6)

    def issue(self, roles, sig, **ticket):
        # when the suite itself runs inside a devloop agent, its ticket must not leak into the issuer
        self.env.pop("DEVLOOP_TICKET", None)
        self.env.pop("DEVLOOP_GROUP", None)
        self.add(1, state="running", target="int/g1", group="g1", **ticket)
        self.pool.update(lambda tx: tx.put_group({"id": "g1", "state": "open", "members": ["1"],
                                                  "job": None}), "test: group g1")
        git(self.proj, "branch", "int/g1", "main")
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "ticket_issuer.py"), "--id", "1",
                                 "--target", "int/g1"], cwd=self.proj, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.terminate())
        logs = [os.path.join(self.tmp, "wt", "runs", "1", "r1-%s" % r, "agent-1.log") for r in roles]
        deadline = time.time() + 60
        while not all(os.path.exists(f) for f in logs):
            if proc.poll() is not None:
                out, err = proc.communicate()
                self.fail("issuer exited (rc %s) before %s ran: %s" % (proc.returncode, roles, out + err))
            if time.time() > deadline:
                proc.terminate()
                out, err = proc.communicate(timeout=60)
                self.fail("%s never started; runs: %s; issuer said: %s" % (
                    roles, sorted(glob.glob(os.path.join(self.tmp, "wt", "runs", "*", "*", "*"))),
                    out + err + json.dumps(self.snap().ticket("1").get("feedback"))))
            time.sleep(0.1)
        time.sleep(1)
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 4, out + err)
        return self.snap().ticket("1")

    def test_4_sigterm_while_worker_and_verifier_run_records_both(self):
        self.plan(worker={"1": ["sleep"]}, verifier={"1": ["sleep"]})
        t = self.issue(["worker", "verifier"], signal.SIGTERM)
        self.assertEqual(t["state"], "ready")
        rows = t.get("cost") or []
        self.assertEqual(sorted(r.get("role") for r in rows), ["verifier", "worker"], rows)
        for r in rows:
            self.assertEqual(r.get("outcome"), "interrupted", r)
            self.assertEqual(r.get("round"), 1, r)
            self.assertTrue(str(r.get("run_dir")).rstrip("/").endswith("r1-%s" % r["role"]), r)

    def test_5_sigint_while_the_only_worker_runs_records_it(self):
        self.plan(worker={"1": ["sleep"]})
        t = self.issue(["worker"], signal.SIGINT, needs_verifier=False)
        rows = t.get("cost") or []
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0].get("role"), rows[0].get("outcome"), rows[0].get("round")),
                         ("worker", "interrupted", 1), rows)

    def test_6_sigterm_while_the_reviewer_runs_keeps_the_handed_in_rows(self):
        self.plan(reviewer={"1": ["sleep"]})
        t = self.issue(["reviewer"], signal.SIGTERM, complexity="complex")
        rows = t.get("cost") or []
        self.assertEqual(sorted((r.get("role"), r.get("outcome"), r.get("round")) for r in rows),
                         [("reviewer", "interrupted", 1), ("verifier", "cases", 1), ("worker", "done", 1)],
                         rows)


class T131Acceptance(LoopCase):
    """#131: an issuer whose ticket is no longer its own (released to ready, or claimed under another
    stamp) while its worker runs stops without writing anything, moves no branch and exits 2."""

    FIELDS = ("state", "run", "round", "feedback", "cost", "cases", "history")

    def setUp(self):
        super().setUp()
        for k in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(k, None)

    def take_away(self, change):
        self.add(1, state="running", target="int/g1", group="g1", needs_verifier=False)
        self.pool.update(lambda tx: tx.put_group({"id": "g1", "state": "open", "members": ["1"],
                                                  "branch": "int/g1", "job": None}), "test: group g1")
        git(self.proj, "branch", "int/g1", "main")
        hold = os.path.join(self.tmp, "hold.sh")
        mark = os.path.join(self.tmp, "held")
        with open(hold, "w") as fh:
            fh.write("#!/bin/sh\nif [ ! -e %s ]; then touch %s; sleep 8; fi\nexec python3 %s\n"
                     % (mark, mark, os.path.join(HERE, "fake_agent.py")))
        self.config(agents={r: ["sh", hold] for r in ("worker", "worker_simple", "verifier", "reviewer")})
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "ticket_issuer.py"), "--id", "1",
                                 "--target", "int/g1"], cwd=self.proj, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and osx.kill_tree(proc.pid, grace=1))
        log = os.path.join(self.tmp, "wt", "runs", "1", "r1-worker", "agent-1.log")
        deadline = time.time() + 30
        while not os.path.exists(log):
            if proc.poll() is not None:
                out, err = proc.communicate()
                self.fail("issuer exited (rc %s) before its worker ran: %s" % (proc.returncode, out + err))
            if time.time() > deadline:
                self.fail("the worker never started (no %s)" % log)
            time.sleep(0.1)
        self.assertIsNone(proc.poll(), "the issuer ended before the test's write")
        start = git(self.proj, "rev-parse", "int/g1")

        def write(tx):
            t = tx.ticket("1")
            change(t)
            tx.put_ticket(t)
        self.pool.update(write, "test: take #1 away from its issuer")
        after = self.snap().ticket("1")
        out, err = proc.communicate(timeout=90)
        return proc.returncode, out, err, start, after

    def check(self, rc, out, err, start, after, event):
        self.assertEqual(rc, 2, "issuer rc %s; stdout: %s; stderr: %s" % (rc, out, err))
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, "stderr should be exactly one non-empty line: %r" % err)
        self.assertIn("#1 is no longer this issuer's ticket", lines[0])
        now = self.snap().ticket("1")
        for f in self.FIELDS:
            self.assertEqual(now.get(f), after.get(f), "ticket field %r changed after the test's write" % f)
        self.assertEqual((now.get("history") or [{}])[-1].get("event"), event, now.get("history"))
        self.assertEqual(git(self.proj, "rev-parse", "int/g1"), start, "int/g1 moved")
        about = [r for r in self.snap().inbox.values() if str(r.get("ref")) == "1"]
        self.assertEqual(about, [], "pages about #1: %r" % about)

    def test_1_released_to_ready_under_a_live_issuer(self):
        def release(t):   # what Controller._release_orphan writes
            t["state"], t["run"] = "ready", None
            t.setdefault("history", []).append({"event": "released by the test"})
        rc, out, err, start, after = self.take_away(release)
        self.assertEqual((after.get("state"), after.get("run")), ("ready", None))
        self.check(rc, out, err, start, after, "released by the test")

    def test_2_run_replaced_by_another_stamp_under_a_live_issuer(self):
        other = {"pid": 1, "host": "another-host", "boot": "b", "started": osx.now()}

        def claim(t):
            t["run"] = dict(other)
            t.setdefault("history", []).append({"event": "claimed by the test"})
        rc, out, err, start, after = self.take_away(claim)
        self.assertEqual((after.get("state"), after.get("run")), ("running", other))
        self.check(rc, out, err, start, after, "claimed by the test")
        self.assertEqual(self.snap().ticket("1").get("run"), other)


class T27Acceptance(LoopCase):
    """D-089 item 2: a merged or landed ticket is frozen for every caller (#27)."""

    def line(self, state):
        return ('#1 is feature complete (%s) and frozen: it cannot be revised or reopened. To overturn it, '
                'open a new ticket: actl new --request "..." (the new ticket may revert #1).' % state)

    def frozen(self, state):
        self.add(1)

        def change(tx):
            t = tx.ticket("1")
            t.update(state=state, group="g1", target="int/g1")
            tx.put_ticket(t)
            tx.write("inbox/p1.json", {"kind": "decision", "ref": "1", "text": "x", "at": osx.now(),
                                       "acked": False})
        self.pool.update(change, "test: #1 %s" % state)
        self.plan(creator={"1": ["revised"]})

    def assert_refused(self, res, state, prefix, page=True):
        self.assertEqual(res.returncode, 2, (res.stdout, res.stderr))
        self.assertEqual(res.stdout, "")
        lines = res.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, res.stderr)
        self.assertIn("#1 is feature complete", lines[0])
        self.assertIn("actl new --request", lines[0])
        self.assertEqual(lines[0], prefix + self.line(state))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.plan_path), "count-creator-1")),
                         "no opener started")
        t = self.snap().ticket("1")
        self.assertEqual(t["state"], state)
        self.assertEqual(t["acceptance"], ["a"])
        self.assertFalse(any(h.get("event") == "revised" for h in t.get("history") or []))
        if page:
            self.assertFalse(self.snap().inbox["p1"]["acked"], "a refused answer leaves the page unread")

    def revise(self, *args):
        return self.run_py("ticket_creator.py", "--revise", "1", *args)

    # acceptance 1
    def test_agents_cannot_revise_a_merged_ticket(self):
        for role in ("worker", "verifier", "fixer"):
            with self.subTest(role=role):
                self.setUp()
                self.frozen("merged")
                self.assert_refused(self.revise("--from", role, "--reason", "x"), "merged", "ticket_creator: ")

    # acceptance 2
    def test_a_worker_cannot_revise_a_landed_ticket(self):
        self.frozen("landed")
        self.assert_refused(self.revise("--from", "worker", "--reason", "x"), "landed", "ticket_creator: ")

    # acceptance 3
    def test_user_decisions_on_a_merged_ticket_are_refused(self):
        cases = [("--decision", "retry", "--page", "p1", "--via", "web"),
                 ("--decision", "cancel", "--page", "p1"),
                 ("--decision", "revise", "--reason", "do it differently", "--page", "p1", "--via", "web")]
        for args in cases:
            with self.subTest(decision=args[1]):
                self.setUp()
                self.frozen("merged")
                self.assert_refused(self.revise("--from", "user", *args), "merged", "ticket_creator: ")

    def test_a_revise_decision_on_a_landed_ticket_is_refused(self):
        self.frozen("landed")
        res = self.revise("--from", "user", "--decision", "revise", "--reason", "do it differently",
                          "--page", "p1", "--via", "web")
        self.assert_refused(res, "landed", "ticket_creator: ")

    # acceptance 4
    def test_tickets_ready_refuses_a_merged_ticket_and_cancel_still_works(self):
        self.frozen("merged")
        self.assert_refused(self.run_py("tickets.py", "ready", "1"), "merged", "tickets: ")
        res = self.run_py("tickets.py", "cancel", "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "cancelled")

    # acceptance 5
    def test_the_board_shows_no_answer_form_for_a_merged_ticket(self):
        import board
        from core.config import Config
        self.frozen("merged")
        page = board.render(Config(self.proj), self.snap(), key="k", query={"tab": "tickets"})
        self.assertIn("decision · 1 · ", page, "the page is listed by its header")
        self.assertNotIn("不改,再試一次", page)

    # acceptance 6
    def test_the_readme_contracts_say_merged_and_landed_are_frozen(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            text = fh.read()
        self.assertIn("## Contracts", text)
        section = text.split("## Contracts", 1)[1].split("\n## ", 1)[0]
        bullets = [b for b in section.split("\n- ") if "frozen" in b]
        self.assertTrue(bullets, "a Contracts bullet says frozen")
        self.assertTrue(any("actl new --request" in b and "merged" in b and "landed" in b for b in bullets),
                        bullets)


class IssuerLock(LoopCase):
    """core/ticketlock helpers and the controller's reading of a free lock (#130)."""

    def setUp(self):
        super().setUp()
        from core.config import Config
        self.cfg = Config(self.proj)
        self.lock = os.path.join(self.tmp, "wt", "locks", "issuer-1.lock")

    def other_holds(self):
        """A child process that holds the lock until its stdin closes."""
        code = ("import fcntl, sys; fh = open(sys.argv[1], 'a'); fcntl.flock(fh, fcntl.LOCK_EX); "
                "print('held', flush=True); sys.stdin.read()")
        os.makedirs(os.path.dirname(self.lock), exist_ok=True)
        proc = subprocess.Popen([sys.executable, "-c", code, self.lock], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        self.assertEqual(proc.stdout.readline().strip(), "held")
        self.addCleanup(lambda: (proc.stdin.close(), proc.wait(timeout=10), proc.stdout.close()))
        return proc

    def test_path_is_under_the_worktree_root(self):
        from core import ticketlock
        self.assertEqual(ticketlock.path(self.cfg, "1"), self.lock)

    def test_take_creates_the_folder_holds_and_never_removes_the_file(self):
        import fcntl
        from core import ticketlock
        self.assertFalse(os.path.exists(os.path.dirname(self.lock)))
        fd = ticketlock.take(self.cfg, "1")
        self.assertIsNotNone(fd)
        try:
            self.assertIsNone(ticketlock.take(self.cfg, "1"))       # a second open description is refused
            with open(self.lock, "a") as fh, self.assertRaises(BlockingIOError):
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ino = os.stat(self.lock).st_ino
        finally:
            os.close(fd)
        self.assertEqual(os.stat(self.lock).st_ino, ino)
        fd = ticketlock.take(self.cfg, "1")
        self.assertIsNotNone(fd)
        os.close(fd)

    def test_held_reads_another_process_and_leaves_a_free_lock_free(self):
        from core import ticketlock
        self.assertFalse(ticketlock.held(self.cfg, "1"))
        self.assertFalse(ticketlock.held(self.cfg, "1"))            # the probe dropped what it took
        proc = self.other_holds()
        self.assertTrue(ticketlock.held(self.cfg, "1"))
        self.assertFalse(ticketlock.held(self.cfg, "2"))
        proc.stdin.close()
        proc.wait(timeout=10)
        self.assertFalse(ticketlock.held(self.cfg, "1"))
        self.assertTrue(os.path.exists(self.lock))

    def ctl(self, boot):
        import ticket_controller
        self.add(1, state="running", target="int/g1", group="g1", needs_verifier=False,
                 claimed_at="2026-01-01T00:00:00Z",
                 run={"pid": os.getpid(), "host": osx.host(), "boot": boot, "started": osx.now()})
        self.pool.update(lambda tx: tx.put_group({"id": "g1", "state": "open", "members": ["1"],
                                                  "branch": "int/g1", "job": None}), "test: g1")
        c = ticket_controller.Controller(self.cfg, self.pool)
        c.forever = False
        c.issued = []
        c.start_issuer = lambda ident, gid: c.issued.append((ident, gid))
        return c

    def test_free_lock_and_dead_stamp_release_the_ticket(self):
        c = self.ctl("not-this-boot")
        c.finish_jobs(self.snap())
        t = self.snap().tickets["1"]
        self.assertEqual(t["state"], "ready")
        self.assertIn("issuer ended without an outcome; dispatch again",
                      [h.get("event") for h in t.get("history") or []])

    def test_free_lock_and_live_stamp_adopt_the_issuer(self):
        c = self.ctl(osx.boot_id())
        run = self.snap().tickets["1"]["run"]
        c.finish_jobs(self.snap())
        t = self.snap().tickets["1"]
        self.assertEqual((t["state"], t["run"]), ("running", run))


class RedCheck(LoopCase):
    def feedback_of(self, ident):
        t = self.snap().ticket(str(ident))
        return [h for h in t["history"] if h["event"] == "feedback"], t

    def _round_two(self, action, words):
        self.add(1)
        self.plan(verifier={"1": [action, "ok"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        fed, t = self.feedback_of(1)
        self.assertEqual((t["state"], t["round"], len(fed)), ("landed", 2, 1))
        log = git(self.pool_path, "log", "--format=%H", "--grep", "#1: round 1 feedback")
        old = json.loads(git(self.pool_path, "show", "%s:tickets/1.json" % log.split()[0]))
        self.assertIn(words, old["feedback"][-1])
        roles = [(c["role"], c["round"]) for c in t["cost"]]
        self.assertEqual(roles, [("worker", 1), ("verifier", 1), ("verifier", 2)],
                         "only the verifier's cases went back: the worker's hand-in stands")

    def test_cases_that_pass_before_the_change_go_back_to_the_verifier(self):
        self._round_two("weak", "already pass before the change")

    def test_cases_that_error_instead_of_failing_go_back(self):
        self._round_two("error", "fail with an error instead of their assertion")

    def test_cases_without_the_ticket_mark_go_back(self):
        self._round_two("unnamed", "no acceptance case named T1Acceptance")

    def test_the_unnamed_message_points_at_result_json(self):
        self._round_two("unnamed", "result.json")


class Recovery(LoopCase):
    def test_a_running_ticket_whose_issuer_died_is_dispatched_again(self):
        self.add(1)

        def change(tx):
            t = tx.ticket("1")
            t.update(state="running", target="int/g1", group="g1",
                     run={"pid": 999999, "host": osx.host(), "boot": osx.boot_id(), "started": osx.now()})
            tx.put_ticket(t)
            tx.put_group({"id": "g1", "branch": "int/g1", "base": git(self.proj, "rev-parse", "main"),
                          "state": "open", "alone": False, "members": ["1"], "fix_round": 0, "job": None})
        self.pool.update(change, "test: orphan")
        git(self.proj, "branch", "int/g1", "main")
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")


class T130Acceptance(LoopCase):
    """#15: one issuer per ticket - the issuer holds a per-ticket lock and the controller honours it (#130)."""

    def setUp(self):
        super().setUp()
        for k in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(k, None)
        self.lock_path = os.path.join(self.tmp, "wt", "locks", "issuer-1.lock")

    def hold_lock(self):
        import fcntl
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        fh = open(self.lock_path, "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh

    def probe(self):
        """True when LOCK_EX|LOCK_NB on the lock path succeeds from this process (lock free)."""
        import fcntl
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        finally:
            os.close(fd)

    def running_layout(self):
        self.add(1, state="running", target="int/g1", group="g1", needs_verifier=False)

        def change(tx):
            tx.put_group({"id": "g1", "state": "open", "members": ["1"], "branch": "int/g1", "job": None})
        self.pool.update(change, "test: group g1")
        git(self.proj, "branch", "int/g1", "main")

    def ctl(self):
        from core.config import Config
        import ticket_controller
        c = ticket_controller.Controller(Config(self.proj), self.pool)
        c.forever = False
        c.issued = []
        c.start_issuer = lambda ident, gid: c.issued.append((ident, gid))
        return c

    def history_events(self, ident):
        return [h.get("event") for h in self.snap().ticket(str(ident)).get("history") or []]

    # acceptance 1
    def test_issuer_holds_the_lock_while_its_worker_runs(self):
        self.running_layout()
        held = os.path.join(self.tmp, "held")
        hold = os.path.join(self.tmp, "hold.sh")
        with open(hold, "w") as fh:
            fh.write("#!/bin/sh\nif [ ! -e %s ]; then touch %s; sleep 10; fi\nexec python3 %s\n"
                     % (held, held, os.path.join(HERE, "fake_agent.py")))
        os.chmod(hold, 0o755)
        agents = {r: AGENT for r in ROLES}
        for r in ("worker", "worker_simple", "verifier", "reviewer"):
            agents[r] = ["sh", hold]
        self.config(agents=agents)
        out = open(os.path.join(self.tmp, "issuer.out"), "w")
        self.addCleanup(out.close)
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "ticket_issuer.py"), "--id", "1",
                                 "--target", "int/g1"], cwd=self.proj, env=self.env,
                                stdout=out, stderr=subprocess.STDOUT, text=True)

        def reap():
            if proc.poll() is None:
                proc.terminate()
            proc.wait(60)
        self.addCleanup(reap)
        log = os.path.join(self.tmp, "wt", "runs", "1", "r1-worker", "agent-1.log")
        deadline = time.time() + 30
        while not os.path.exists(log) and proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if not os.path.exists(log):
            self.fail("the worker never started (rc %s); output: %s" % (proc.poll(), open(out.name).read()))
        self.assertTrue(proc.poll() is None, "the issuer ended before the probe")
        self.assertFalse(self.probe(), "the issuer's lock %s is not held while its worker runs" % self.lock_path)
        proc.communicate(timeout=90)
        self.assertEqual(proc.returncode, 0, open(out.name).read())
        self.assertTrue(self.probe(), "the lock is still held after the issuer exited")

    # acceptance 2
    def test_second_issuer_refuses_and_changes_nothing(self):
        self.running_layout()
        self.hold_lock()
        live = os.path.join(self.tmp, "wt", "runs", "live", "issuer-1.json")
        os.makedirs(os.path.dirname(live), exist_ok=True)
        with open(live, "w") as fh:
            json.dump({"kind": "issuer", "ticket": "1"}, fh)
        with open(live, "rb") as fh:
            before_bytes = fh.read()
        head = git(self.pool_path, "rev-parse", "HEAD")
        t0 = time.time()
        res = self.run_py("ticket_issuer.py", "--id", "1", "--target", "int/g1", timeout=60)
        took = time.time() - t0
        self.assertEqual(res.returncode, 2, (res.stdout, res.stderr))
        self.assertLess(took, 10)
        lines = [ln for ln in res.stderr.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, res.stderr)
        self.assertIn("#1", lines[0])
        self.assertIn(self.lock_path, lines[0])
        self.assertEqual(git(self.pool_path, "rev-parse", "HEAD"), head, "the pool moved")
        self.assertTrue(os.path.exists(live), "the live record was removed")
        with open(live, "rb") as fh:
            self.assertEqual(fh.read(), before_bytes)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "wt", "t1")))

    # acceptance 3
    def test_finish_jobs_leaves_a_running_ticket_whose_lock_is_held(self):
        run = {"pid": os.getpid(), "host": osx.host(), "boot": "not-this-boot", "started": osx.now()}
        self.assertTrue(osx.stamp_dead(run), "fixture: the stamp must look dead")
        self.add(1, state="running", target="int/g1", group="g1", claimed_at="2026-01-01T00:00:00Z", run=run)

        def change(tx):
            tx.put_group({"id": "g1", "state": "open", "members": ["1"], "branch": "int/g1", "job": None})
        self.pool.update(change, "test: group g1")
        git(self.proj, "branch", "int/g1", "main")
        self.hold_lock()
        c = self.ctl()
        c.finish_jobs(self.snap())
        t = self.snap().ticket("1")
        self.assertEqual(t["state"], "running")
        self.assertEqual(t.get("run"), run)
        self.assertNotIn("issuer ended without an outcome; dispatch again", self.history_events(1))

    # acceptance 4
    def test_dispatch_skips_a_ready_ticket_whose_lock_is_held(self):
        self.add(1, group="g1")

        def change(tx):
            tx.put_group({"id": "g1", "state": "open", "members": ["1"], "branch": "int/g1", "job": None})
        self.pool.update(change, "test: group g1")
        git(self.proj, "branch", "int/g1", "main")
        fh = self.hold_lock()
        c = self.ctl()
        c.dispatch(self.snap())
        self.assertEqual(c.issued, [], "dispatched while another process holds the issuer lock")
        fh.close()
        c.dispatch(self.snap())
        self.assertEqual(c.issued, [("1", "g1")])


class HardBlockOwnChange(LoopCase):
    def _issuer_for(self, from_main, own_file, own_text, patterns=None):
        import ticket_issuer
        from core.config import Config
        from core.gitx import Git
        if patterns is not None:
            path = os.path.join(self.proj, "board", "config.json")
            with open(path) as fh:
                cfg = json.load(fh)
            cfg["hard_block"]["patterns"] = patterns
            with open(path, "w") as fh:
                json.dump(cfg, fh)
        os.makedirs(os.path.join(self.proj, "docs"), exist_ok=True)
        with open(os.path.join(self.proj, "docs", "DECISIONS.md"), "w") as fh:
            fh.write("d\n")
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "first")
        git(self.proj, "branch", "int/g1", "main")
        with open(os.path.join(self.proj, "docs", "DECISIONS.md"), "a") as fh:
            fh.write("main edit\n")
        with open(os.path.join(self.proj, "main_code.py"), "w") as fh:
            fh.write("x = 1  # FORBIDDEN_MARK\n")
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "main change")
        wt = os.path.join(self.tmp, "t1")
        git(self.proj, "worktree", "add", "-q", wt, "-b", "t/1", "main" if from_main else "int/g1")
        if not from_main:
            git(wt, "merge", "-q", "--no-edit", "main")
        with open(os.path.join(wt, own_file), "a") as fh:
            fh.write(own_text)
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "own")
        issuer = ticket_issuer.Issuer(Config(self.proj), self.pool, "1", "int/g1")
        return issuer, Git(wt)

    def test_merged_in_main_change_does_not_count_but_own_change_does(self):
        import ticket_issuer
        issuer, g = self._issuer_for(False, "impl.py", "y = 2\n")
        issuer.hard_block(g)
        with open(os.path.join(g.cwd, "docs", "DECISIONS.md"), "a") as fh:
            fh.write("own\n")
        git(g.cwd, "commit", "-q", "-am", "own decisions")
        with self.assertRaises(ticket_issuer.Stuck) as cm:
            issuer.hard_block(g)
        self.assertEqual(cm.exception.reason, "hard_block")
        self.assertIn("docs/DECISIONS.md", str(cm.exception))

    def test_pattern_from_main_does_not_count_but_own_line_does(self):
        import ticket_issuer
        issuer, g = self._issuer_for(False, "impl.py", "y = 2\n", patterns=["FORBIDDEN_MARK"])
        issuer.hard_block(g)
        with open(os.path.join(g.cwd, "impl.py"), "a") as fh:
            fh.write("z = 3  # FORBIDDEN_MARK\n")
        git(g.cwd, "commit", "-q", "-am", "own mark")
        with self.assertRaises(ticket_issuer.Stuck) as cm:
            issuer.hard_block(g)
        self.assertIn("FORBIDDEN_MARK", str(cm.exception))

    def test_branch_started_from_newer_main_does_not_count_main(self):
        issuer, g = self._issuer_for(True, "impl.py", "y = 2\n", patterns=["FORBIDDEN_MARK"])
        issuer.hard_block(g)


class BlockedCheckUnits(unittest.TestCase):
    """Issuer.blocked_check and the agent-environment free round (implementer's tests of #115)."""

    def issuer(self, chain_len=2):
        import ticket_issuer
        iss = object.__new__(ticket_issuer.Issuer)
        iss.id, iss.branch, iss._free, iss.started_agents = "9", "t/9", None, []
        iss.cases_branch, iss.target = "t/9-cases", "int/g1"
        iss.wt, iss.deadline = "/nowhere", time.time() + 600
        iss.cfg = mock.Mock()
        iss.cfg.agent_chain.return_value = [["a"]] * chain_len
        iss.cfg.runs_dir.return_value = "/nowhere/runs/9"
        iss.test_slot = mock.Mock()
        return iss

    def work(self, index):
        return mock.Mock(index=index, chain_role="worker", role="worker")

    def test_invalid_failing_runs_nothing_and_is_stuck_with_the_text(self):
        import ticket_issuer
        for bad in (None, [], "suite.base", [1], ["a", None], {"a": 1}):
            with self.subTest(failing=bad):
                iss = self.issuer()
                with mock.patch.object(ticket_issuer.testrun, "run") as run:
                    with self.assertRaises(ticket_issuer.Stuck) as cm:
                        iss.blocked_check(self.work(0), "no net", bad)
                run.assert_not_called()
                iss.test_slot.assert_not_called()
                self.assertEqual((cm.exception.reason, cm.exception.text, cm.exception.page),
                                 ("decision", "no net", True))

    def test_green_makes_the_round_free_and_moves_to_the_next_command(self):
        import ticket_issuer
        green = mock.Mock(status=ticket_issuer.testrun.GREEN)
        for index, size, want in ((0, 2, 1), (1, 2, 1), (0, 1, 0)):
            with self.subTest(index=index, size=size):
                iss = self.issuer(size)
                with mock.patch.object(ticket_issuer.testrun, "run", return_value=green) as run:
                    text = iss.blocked_check(self.work(index), "no net", ["suite.base", "x.y"])
                self.assertEqual(run.call_count, 1)
                args, kw = run.call_args
                self.assertEqual(args[1:4], ("/nowhere", "run", ["suite.base", "x.y"]))
                self.assertEqual(kw["keep_dir"], os.path.join("/nowhere/runs/9", "blocked-check"))
                iss.test_slot.return_value.release.assert_called_once_with()
                self.assertEqual((iss._free, iss.next_worker), ("agent_env", want))
                self.assertIn("passed when devloop ran them on t/9", text)
                self.assertIn("(no net)", text)
                self.assertIn("(suite.base x.y)", text)

    def test_not_green_is_stuck_with_the_summary(self):
        import ticket_issuer
        for status in (ticket_issuer.testrun.RED, ticket_issuer.testrun.ENV,
                       ticket_issuer.testrun.UNMEASURED):
            with self.subTest(status=status):
                iss = self.issuer()
                out = mock.Mock(status=status)
                out.summary.return_value = "SUMMARY"
                with mock.patch.object(ticket_issuer.testrun, "run", return_value=out):
                    with self.assertRaises(ticket_issuer.Stuck) as cm:
                        iss.blocked_check(self.work(0), "no net", ["impl.impl_1.txt"])
                self.assertEqual(cm.exception.reason, "decision")
                self.assertEqual(cm.exception.text,
                                 "no net\ndevloop ran the ids the worker named: SUMMARY")
                self.assertIsNone(iss._free)
                self.assertEqual(iss.next_worker, 0)

    def test_rounds_text_names_agent_environment_only_when_there_is_one(self):
        import ticket_issuer
        hist = [{"event": "round start", "round": 1},
                {"event": "feedback", "round": 1, "free": "agent_env"},
                {"event": "feedback", "round": 2, "free": "merge"},
                {"event": "feedback", "round": 3}]
        t = {"round": 3, "feedback": ["round 3: last"], "history": hist}
        self.assertEqual(ticket_issuer.free_rounds(t), {"merge": 1, "revision": 0, "agent_env": 1})
        self.assertEqual(ticket_issuer.rounds_text(t),
                         "3 rounds used and still not passing (1 real red, 2 free: 1 merge "
                         "conflict, 0 revision, 1 agent environment); last feedback:\nround 3: last")
        t["history"] = hist[:1] + hist[2:]
        self.assertIn("(1 real red, 1 free: 1 merge conflict, 0 revision); last",
                      ticket_issuer.rounds_text(dict(t, round=2)))

    def test_the_next_worker_index_is_used_once(self):
        import ticket_issuer
        iss = self.issuer()
        iss.next_worker, iss.pending = 1, []
        iss.cfg.max_rounds, iss.cfg.agent_timeout_seconds = 1, 60
        iss.cfg.base_test_ids, iss.cfg.index_path = [], None
        iss.cfg.agent.return_value = ["a"]
        iss.check_time = lambda: None
        firsts = []
        with mock.patch.object(ticket_issuer.agents, "start",
                               side_effect=lambda *a, **kw: firsts.append(kw.get("first")) or mock.Mock(waited=0.0)), \
                mock.patch.object(ticket_issuer.agents, "render", return_value="p"), \
                mock.patch.object(ticket_issuer.memory, "for_prompt", return_value=""), \
                mock.patch.object(ticket_issuer.memory, "project_path", return_value=""):
            t = {"id": "9", "complexity": "complex"}
            iss.start("verifier", "verifier.md", t, 1, "/nowhere")
            iss.start("worker", "worker.md", t, 1, "/nowhere")
            iss.start("worker", "worker.md", t, 2, "/nowhere")
        self.assertEqual(firsts, [0, 1, 0])


if __name__ == "__main__":
    unittest.main()
