"""the opener (ticket_creator): cost rows, limits in its prompt, required fields"""

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


class OpenerCostRows(LoopCase):
    """ticket_creator.split, board.run_count / cost_rows and the opener's cost rows (implementer's unit cases)."""

    ROW = {"role": "creator", "model": "m", "run_dir": "/r/a1", "input_tokens": 1001, "output_tokens": 300,
           "cache_read_tokens": 5000, "cache_write_tokens": None, "usd": 0.9, "seconds": 3, "session_id": "s1"}

    def setUp(self):
        super().setUp()
        for k in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(k, None)

    def test_split_gives_the_remainder_to_the_lowest_id_and_keeps_nulls(self):
        import ticket_creator
        a, b, c = ticket_creator.split(self.ROW, ["10", "9", "11"])
        self.assertEqual([r["input_tokens"] for r in (a, b, c)], [335, 333, 333])
        self.assertEqual([r["output_tokens"] for r in (a, b, c)], [100, 100, 100])
        self.assertEqual([r["cache_write_tokens"] for r in (a, b, c)], [None] * 3)
        self.assertAlmostEqual(a["usd"], 0.3, delta=1e-9)
        self.assertAlmostEqual(c["seconds"], 1.0, delta=1e-9)
        self.assertEqual(a["split_over"], ["9", "10", "11"])
        self.assertEqual(a["session_id"], "s1")
        self.assertNotIn("split_over", self.ROW)

    def test_run_count_counts_a_split_run_once(self):
        import board
        import ticket_creator
        shares = ticket_creator.split(self.ROW, ["1", "2"])
        other = dict(self.ROW, run_dir="/r/a2")
        self.assertEqual(board.run_count(shares + ticket_creator.split(other, ["1", "2"]) + [self.ROW, self.ROW]), 4)
        self.assertEqual(board.run_count([]), 0)

    def test_cost_rows_reads_inbox_pages(self):
        import board
        self.pool.report("decision", "request", "q", cost=[self.ROW, "junk"])
        self.assertEqual([r["input_tokens"] for r in board.cost_rows(self.snap())], [1001])

    def test_request_rows_go_on_the_opened_tickets_and_question_page(self):
        self.plan(creator={"-": ["tickets"]})
        res = self.run_py("ticket_creator.py", "--request", "two things")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        rows = [snap.ticket(i)["cost"] for i in ("1", "2")]
        self.assertEqual([len(r) for r in rows], [1, 1])
        self.assertEqual(rows[0][0]["split_over"], ["1", "2"])
        self.assertEqual(rows[0][0]["run_dir"], rows[1][0]["run_dir"])
        self.plan(creator={"-": ["question"]})
        res = self.run_py("ticket_creator.py", "--request", "which")
        self.assertEqual(res.returncode, 3, res.stdout + res.stderr)
        page = [p for p in self.snap().inbox.values() if p.get("kind") == "decision"][0]
        self.assertEqual([(r["role"], "split_over" in r) for r in page["cost"]], [("creator", False)])

    def test_revise_appends_one_row_even_without_a_hand_in(self):
        self.add(1)
        self.plan(creator={"1": ["nothing"]})
        res = self.run_py("ticket_creator.py", "--revise", "1", "--from", "worker", "--reason", "x")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        cost = self.snap().ticket("1")["cost"]
        self.assertEqual([(r["role"], r["outcome"]) for r in cost], [("creator", "no hand-in")])
        self.assertFalse(any(p.get("cost") for p in self.snap().inbox.values()))

    def test_refused_revise_and_retry_add_no_row(self):
        self.add(1, state="merged")
        self.add(2, state="stuck")
        res = self.run_py("ticket_creator.py", "--revise", "1", "--from", "worker", "--reason", "x")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        res = self.run_py("ticket_creator.py", "--revise", "2", "--from", "user", "--decision", "retry",
                          "--reason", "again")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        snap = self.snap()
        self.assertEqual([snap.ticket(i).get("cost") for i in ("1", "2")], [None, None])

class T103Acceptance(LoopCase):
    """#103: an out-of-quota opener makes one attempt, exits 1, and its one page and stdout say out of
    quota, the CLI's excerpt and the reset time (verifier's acceptance cases 1-5)."""

    HEAD = "the opener is out of quota; nothing was opened:\n"
    LATER = ("the reset time could not be read from the message: send the request again once the limit "
             "has reset, with `actl new --request`")

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(k)

    def open_out_of_quota(self, quota_text):
        self.plan(creator={"*": ["quota"]})
        self.env["FAKE_QUOTA_TEXT"] = quota_text
        res = self.run_py("ticket_creator.py", "--request", "x", timeout=120)
        counter = os.path.join(os.path.dirname(self.plan_path), "count-creator--")
        with open(counter) as fh:
            runs = int(fh.read())
        folders = sorted(glob.glob(os.path.join(self.tmp, "wt", "runs", "creator", "*")))
        pages_ = [row for row in self.snap().inbox.values()]
        return res, runs, folders, pages_

    def excerpt(self, folders):
        from core import agents
        self.assertTrue(hasattr(agents, "_excerpt") and hasattr(agents, "_log_tail"))
        text, _ = agents._log_tail(os.path.join(folders[0], "agent-1.log"))
        return agents._excerpt(text)

    def check(self, quota_text, when, must_contain):
        res, runs, folders, pages_ = self.open_out_of_quota(quota_text)
        out = res.stdout + res.stderr
        # acceptance 1: one attempt in total, exit 1
        self.assertEqual(runs, 1, out)
        self.assertEqual(len(folders), 1, folders)
        self.assertEqual(res.returncode, 1, out)
        # acceptance 2: exactly one page, decision/request, request + cost rows, one 'ack' option
        self.assertEqual(len(pages_), 1, pages_)
        page = pages_[0]
        self.assertEqual((page.get("kind"), page.get("ref")), ("decision", "request"))
        self.assertEqual(page.get("request"), "x")
        self.assertEqual([(r.get("role"), r.get("outcome")) for r in page.get("cost") or []],
                         [("creator", "out of quota")])
        self.assertEqual([o.get("action") for o in page.get("options") or []], ["ack"])
        expected = self.HEAD + self.excerpt(folders) + "\n" + when
        self.assertEqual(page.get("text"), expected)
        for piece in must_contain:
            self.assertIn(piece, page.get("text"))
        # acceptance 3: the same text on stdout
        self.assertIn(expected, res.stdout)
        for piece in must_contain:
            self.assertIn(piece, res.stdout)

    def test_epoch_message_gives_one_attempt_and_the_reset_time(self):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(1900000000))
        self.assertEqual(stamp, "2030-03-17T17:46:40Z")
        when = ("the limit resets at %s: send the request again after that time with `actl new --request`"
                % stamp)
        self.check("Claude AI usage limit reached|1900000000", when,
                   ["out of quota", "Claude AI usage limit reached|1900000000", stamp])

    def test_unreadable_reset_says_it_could_not_be_read(self):
        self.check("Error: usage limit reached", self.LATER,
                   ["out of quota", "the reset time could not be read"])

    def test_long_request_is_cut_to_2000_characters(self):
        self.plan(creator={"*": ["quota"]})
        self.env["FAKE_QUOTA_TEXT"] = "Error: usage limit reached"
        request = "".join(chr(97 + i % 26) for i in range(2500))
        res = self.run_py("ticket_creator.py", "--request", request, timeout=120)
        pages_ = list(self.snap().inbox.values())
        self.assertEqual(len(pages_), 1, pages_)
        self.assertIn("out of quota", pages_[0].get("text", ""), res.stdout + res.stderr)
        self.assertEqual(pages_[0].get("request"), request[:2000])


class T59Acceptance(LoopCase):
    """#59: every opener run leaves a cost row - split over the tickets it opened, on the revised
    ticket, or unsplit on the decision page of a request that opened none. Expected numbers come
    from the usage line of acceptance 2 (input 1001, output 300, cache read 5000, cache write 7,
    usd 0.9), not from the code."""

    USAGE = ('{"type": "result", "usage": {"input_tokens": 1001, "output_tokens": 300, '
             '"cache_read_input_tokens": 5000, "cache_creation_input_tokens": 7}, '
             '"total_cost_usd": 0.9, "session_id": "s1"}')
    FIELDS = ("role", "model", "effort", "command", "round", "seconds", "at", "outcome", "run_dir",
              "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "usd", "session_id")

    def setUp(self):
        super().setUp()
        for name in ("DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_SLOT"):
            self.env.pop(name, None)
            os.environ.pop(name, None)     # restored by GitIsolatedCase's patch.dict
        self.creator("exec python3 %s" % os.path.join(HERE, "fake_agent.py"))

    # ------------------------------------------------------------ helpers

    def creator(self, then):
        """The creator command: print the usage line into the run's log, then `then`."""
        self.config(agents=dict({r: AGENT for r in ROLES},
                                creator=["sh", "-c", "echo '%s'; %s" % (self.USAGE, then)]))

    def request(self, want_rc):
        res = self.run_py("ticket_creator.py", "--request", "build two things")
        self.assertEqual(res.returncode, want_rc, res.stdout + res.stderr)
        return res

    def creator_rows(self, ident):
        t = self.snap().tickets.get(str(ident)) or {}
        return [r for r in t.get("cost") or [] if isinstance(r, dict) and r.get("role") == "creator"]

    def request_page(self):
        pages = [p for p in self.snap().inbox.values() if p.get("ref") == "request" and p.get("kind") == "decision"]
        self.assertEqual(len(pages), 1, pages)
        return pages[0]

    def tokens_tab(self):
        import board
        from core.config import Config
        return board.render(Config(self.proj), self.snap(), query={"tab": "tokens"}, now=time.time())

    def section(self, page, heading):
        start = page.find("<h2>%s</h2>" % heading)
        self.assertNotEqual(start, -1, heading)
        return page[start:page.find("</section>", start)]

    def assert_share(self, row, inp, out, read, write):
        self.assertEqual((row.get("input_tokens"), row.get("output_tokens"), row.get("cache_read_tokens"),
                          row.get("cache_write_tokens")), (inp, out, read, write), row)
        self.assertIsInstance(row.get("usd"), (int, float), row)
        self.assertAlmostEqual(row.get("usd"), 0.45, delta=1e-9)

    # ------------------------------------------------------------ acceptance 1 and 2

    def test_an_opening_run_leaves_one_row_with_the_agent_fields_and_split_over_on_each_ticket(self):
        self.request(0)
        self.assertEqual(sorted(self.snap().tickets), ["1", "2"])
        for ident in ("1", "2"):
            rows = self.creator_rows(ident)
            self.assertEqual(len(rows), 1, "#%s: one creator row per opener run" % ident)
            missing = [f for f in self.FIELDS + ("split_over",) if f not in rows[0]]
            self.assertEqual(missing, [], rows[0])
            self.assertEqual(rows[0]["split_over"], ["1", "2"])

    def test_an_opening_run_is_split_over_the_tickets_it_opened(self):
        self.request(0)
        one, two = self.creator_rows(1), self.creator_rows(2)
        self.assertEqual((len(one), len(two)), (1, 1))
        one, two = one[0], two[0]
        self.assert_share(one, 501, 150, 2500, 4)
        self.assert_share(two, 500, 150, 2500, 3)
        self.assertEqual((one.get("split_over"), two.get("split_over")), (["1", "2"], ["1", "2"]))
        self.assertEqual((one.get("model"), one.get("session_id")), ("sh", "s1"))
        self.assertIsInstance(one.get("seconds"), (int, float))
        self.assertAlmostEqual(one["seconds"], two.get("seconds"), delta=1e-9)   # value / N on both
        for f in ("role", "model", "effort", "command", "round", "at", "outcome", "run_dir", "session_id"):
            self.assertEqual(one.get(f), two.get(f), "%s is copied" % f)

    # ------------------------------------------------------------ acceptance 3

    def test_a_failed_first_attempt_is_split_too(self):
        self.plan(creator={"-": ["bad", "tickets"]})
        self.request(0)
        self.assertEqual(sorted(self.snap().tickets), ["1", "2"])
        for ident, share in (("1", (501, 150, 2500, 4)), ("2", (500, 150, 2500, 3))):
            rows = self.creator_rows(ident)
            self.assertEqual(len(rows), 2, "#%s: one row per opener run" % ident)
            self.assertEqual(len({r.get("run_dir") for r in rows}), 2, rows)
            for r in rows:
                self.assertEqual(r.get("split_over"), ["1", "2"])
                self.assert_share(r, *share)

    # ------------------------------------------------------------ acceptance 4

    def no_ticket_request(self, action, rc, runs):
        self.plan(creator={"-": [action]})
        self.request(rc)
        self.assertEqual(self.snap().tickets, {})
        page = self.request_page()
        rows = [r for r in page.get("cost") or [] if isinstance(r, dict)]
        self.assertEqual(len(rows), runs, page)
        self.assertEqual(len({r.get("run_dir") for r in rows}), runs, "one row per opener run")
        for r in rows:
            self.assertEqual(r.get("role"), "creator")
            self.assertNotIn("split_over", r)
            self.assertEqual((r.get("input_tokens"), r.get("output_tokens"), r.get("cache_read_tokens"),
                              r.get("cache_write_tokens"), r.get("usd")), (1001, 300, 5000, 7, 0.9))

    def test_a_question_request_keeps_its_row_on_the_decision_page(self):
        self.no_ticket_request("question", 3, 1)

    def test_a_rejected_request_keeps_its_row_on_the_decision_page(self):
        self.no_ticket_request("rejected", 3, 1)

    def test_a_request_invalid_twice_keeps_both_rows_on_the_decision_page(self):
        self.no_ticket_request("bad", 1, 2)

    # ------------------------------------------------------------ acceptance 5

    UNCHANGED = "cat >/dev/null; printf '{\"kind\": \"unchanged\", \"note\": \"n\"}' > \"$DEVLOOP_RESULT\""

    def revise_once(self, source, action, command=None):
        """One --revise of #1 that starts the opener; #1 must then hold exactly one unsplit creator row."""
        self.add(1, state="stuck" if source == "user" else "ready")
        self.plan(creator={"1": [action]})
        if command:
            self.creator(command)
        args = ["--revise", "1", "--from", source, "--reason", "x"]
        if source == "user":
            args += ["--decision", "revise"]
        res = self.run_py("ticket_creator.py", *args)
        rows = self.creator_rows(1)
        self.assertEqual(len(rows), 1, res.stdout + res.stderr)
        self.assertNotIn("split_over", rows[0])
        self.assertEqual(rows[0].get("input_tokens"), 1001)
        return rows[0]

    def test_a_worker_revise_that_revises_leaves_one_row(self):
        self.revise_once("worker", "revised")

    def test_a_user_revise_that_revises_leaves_one_row(self):
        self.revise_once("user", "revised")

    def test_a_worker_revise_that_leaves_the_ticket_unchanged_leaves_one_row(self):
        self.revise_once("worker", "unchanged", self.UNCHANGED)

    def test_a_user_revise_that_leaves_the_ticket_unchanged_leaves_one_row(self):
        self.revise_once("user", "unchanged", self.UNCHANGED)

    def test_a_revise_that_asks_a_question_leaves_one_row(self):
        self.revise_once("worker", "question")

    def test_a_revise_that_is_rejected_leaves_one_row(self):
        self.revise_once("worker", "rejected")

    def test_a_worker_revise_without_a_hand_in_leaves_one_no_hand_in_row(self):
        self.assertEqual(self.revise_once("worker", "nothing").get("outcome"), "no hand-in")

    def test_a_user_revise_without_a_hand_in_leaves_one_no_hand_in_row(self):
        self.assertEqual(self.revise_once("user", "nothing").get("outcome"), "no hand-in")

    # ------------------------------------------------------------ acceptance 7, 8, 9

    def test_a_question_runs_row_on_its_page_reaches_the_token_tab(self):
        import board
        self.plan(creator={"-": ["question"]})
        self.request(3)
        rows = [r for r in board.cost_rows(self.snap()) if r.get("role") == "creator"]
        self.assertEqual(len(rows), 1, "cost_rows returns the page's row")
        self.assertIn("<td>creator</td>", self.section(self.tokens_tab(), "依角色"))

    def test_the_token_tab_counts_one_split_opening_run_once(self):
        self.request(0)
        now = osx.now()
        self.add(9, cost=[{"role": "worker", "model": "m", "at": now, "run_dir": "same", "input_tokens": 1},
                          {"role": "worker", "model": "m", "at": now, "run_dir": "same", "input_tokens": 1}])
        page = self.tokens_tab()
        self.assertIn('<tr><td>creator</td><td class="n">1</td>', page)
        self.assertIn('<tr><td>sh</td><td class="n">1</td>', self.section(page, "依模型"))
        self.assertIn('<tr><td>worker</td><td class="n">2</td>', page, "rows without split_over count as before")
        self.assertIn('<tr><td class="n">3</td>', self.section(page, "近 30 天"))
        today = time.strftime("%Y-%m-%d", time.localtime())
        self.assertIn('<tr><td>%s</td><td class="n">3</td>' % today, self.section(page, "每天"))

    def test_the_token_tab_drops_the_no_cost_rows_caveat(self):
        self.assertNotIn("不記 cost 列", self.tokens_tab())


class RequiredFields(LoopCase):
    def test_a_project_field_the_opener_left_out_is_rejected(self):
        self.config(required_fields={"vision": "which VISION metric this improves"})
        res = self.run_py("ticket_creator.py", "--request", "x")
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertEqual(self.snap().tickets, {})
        page = list(self.snap().inbox.values())[0]["text"]
        self.assertIn('"vision" is required by this project', page)


class T111Acceptance(LoopCase):
    """#111: --decision is never silently ignored - --from main acts like --from user, every other
    use of --decision is refused. Expected values come from the ticket's acceptance text."""

    UNCHANGED = "cat >/dev/null; printf '{\"kind\": \"unchanged\", \"note\": \"n\"}' > \"$DEVLOOP_RESULT\""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        self.add(1, state="stuck", stuck={"reason": "decision", "text": "x"}, round=3)

    # ------------------------------------------------------------ helpers

    def creator_runs(self, ticket):
        return os.path.exists(os.path.join(os.path.dirname(self.plan_path), "count-creator-%s" % ticket))

    def ticket(self):
        return self.snap().ticket("1")

    def answers(self):
        return [e for e in self.ticket().get("history") or [] if e.get("event") == "user answer"]

    def tc(self, *args, rc=0):
        res = self.run_py("ticket_creator.py", *args)
        self.assertEqual(res.returncode, rc, res.stdout + res.stderr)
        return res

    def assert_ready_round_0(self):
        t = self.ticket()
        self.assertEqual((t.get("state"), t.get("round"), t.get("stuck")), ("ready", 0, None), t)

    def assert_one_refusal_line(self, res):
        lines = res.stderr.splitlines()
        self.assertEqual(len(lines), 1, res.stderr)
        self.assertTrue(lines[0].strip(), res.stderr)
        self.assertTrue(lines[0].startswith("ticket_creator: "), lines[0])
        self.assertIn("--decision", lines[0])

    # ------------------------------------------------------------ acceptance 1

    def test_main_retry_makes_the_ticket_ready_round_0(self):
        self.tc("--revise", "1", "--via", "main", "--from", "main", "--decision", "retry", "--reason", "go")
        self.assert_ready_round_0()
        answers = self.answers()
        self.assertTrue(answers, self.ticket().get("history"))
        self.assertEqual((answers[-1].get("decision"), answers[-1].get("by")), ("retry", "main-session"))

    # ------------------------------------------------------------ acceptance 2

    def test_main_cancel_cancels_the_ticket(self):
        self.tc("--revise", "1", "--from", "main", "--decision", "cancel", "--reason", "no")
        self.assertEqual(self.ticket().get("state"), "cancelled")

    # ------------------------------------------------------------ acceptance 3

    def test_main_revise_with_a_revised_hand_in_makes_the_ticket_ready_with_the_new_acceptance(self):
        self.plan(creator={"1": ["revised"]})
        self.tc("--revise", "1", "--from", "main", "--decision", "revise", "--reason", "drop item 2")
        self.assertTrue(self.creator_runs("1"), "the opener ran")
        self.assert_ready_round_0()
        self.assertEqual(self.ticket().get("acceptance"), ["a possible thing"])

    def test_main_revise_with_an_unchanged_hand_in_makes_the_ticket_ready_unchanged(self):
        self.config(agents=dict({r: AGENT for r in ROLES}, creator=["sh", "-c", self.UNCHANGED]))
        self.tc("--revise", "1", "--from", "main", "--decision", "revise", "--reason", "drop item 2")
        t = self.ticket()
        self.assertEqual((t.get("state"), t.get("round")), ("ready", 0), t)
        self.assertEqual(t.get("acceptance"), ["a"])

    # ------------------------------------------------------------ acceptance 4

    def test_main_without_decision_means_revise(self):
        self.plan(creator={"1": ["revised"]})
        self.tc("--revise", "1", "--from", "main", "--reason", "drop item 2")
        self.assertEqual(self.ticket().get("state"), "ready")

    def test_main_without_decision_and_without_reason_is_refused_like_user(self):
        res = self.tc("--revise", "1", "--from", "main", rc=2)
        self.assertIn("a revise decision needs the user's words", res.stderr)
        self.assertFalse(self.creator_runs("1"))
        self.assertEqual(self.ticket().get("state"), "stuck")
        # the positive half: the same setup with a reason goes through to ready
        self.plan(creator={"1": ["revised"]})
        self.tc("--revise", "1", "--from", "main", "--reason", "drop item 2")
        self.assertEqual(self.ticket().get("state"), "ready")

    # ------------------------------------------------------------ acceptance 5

    def page(self):
        return self.pool.report("decision", "1", "#1 is stuck")

    def test_by_name_is_the_answer_and_ack_name_for_main(self):
        page = self.page()
        self.tc("--revise", "1", "--from", "main", "--decision", "retry", "--reason", "go",
                "--by", "alice", "--page", page)
        self.assertEqual(self.answers()[-1].get("by"), "alice")
        row = self.snap().inbox[page]
        self.assertTrue(row.get("acked"), row)
        self.assertEqual(row.get("acked_by"), "alice")

    def test_main_without_by_is_main_session_for_answer_and_ack(self):
        page = self.page()
        self.tc("--revise", "1", "--from", "main", "--decision", "retry", "--reason", "go", "--page", page)
        self.assertEqual(self.answers()[-1].get("by"), "main-session")
        row = self.snap().inbox[page]
        self.assertTrue(row.get("acked"), row)
        self.assertEqual(row.get("acked_by"), "main-session")

    def test_user_answer_by_unchanged(self):
        self.tc("--revise", "1", "--from", "user", "--decision", "retry", "--reason", "go")
        self.assertEqual(self.answers()[-1].get("by"), "user")
        self.add(1, state="stuck", stuck={"reason": "decision", "text": "x"}, round=3)
        page = self.page()
        self.tc("--revise", "1", "--from", "user", "--decision", "retry", "--reason", "go",
                "--by", "bob", "--page", page)
        self.assertEqual(self.answers()[-1].get("by"), "bob")
        self.assertEqual(self.snap().inbox[page].get("acked_by"), "bob")
        # the --from main half of the same item (fails on code that ignores --decision for main)
        self.add(1, state="stuck", stuck={"reason": "decision", "text": "x"}, round=3)
        self.tc("--revise", "1", "--from", "main", "--decision", "retry", "--reason", "go", "--by", "carol")
        self.assertEqual(self.answers()[-1].get("by"), "carol")

    # ------------------------------------------------------------ acceptance 6

    def test_decision_with_another_source_is_refused_and_changes_nothing(self):
        self.plan(creator={"1": ["revised"]})
        for source in ("worker", "verifier", "fixer", "someone"):
            for decision in ("retry", "cancel", "revise"):
                with self.subTest(source=source, decision=decision):
                    before = json.dumps(self.ticket(), sort_keys=True)
                    res = self.tc("--revise", "1", "--from", source, "--decision", decision,
                                  "--reason", "go", rc=2)
                    self.assert_one_refusal_line(res)
                    self.assertFalse(self.creator_runs("1"), "no opener may start")
                    self.assertEqual(json.dumps(self.ticket(), sort_keys=True), before)

    # ------------------------------------------------------------ acceptance 7

    def test_decision_without_revise_is_refused_and_opens_nothing(self):
        path = os.path.join(self.tmp, "request.txt")
        with open(path, "w") as fh:
            fh.write("build two things\n")
        for args in (("--request", "x", "--decision", "cancel"),
                     ("--request-file", path, "--decision", "retry"),
                     ("--request", "x", "--decision", "revise")):
            with self.subTest(args=args):
                res = self.tc(*args, rc=2)
                self.assert_one_refusal_line(res)
                self.assertEqual(sorted(self.snap().tickets), ["1"], "no ticket opened")
                self.assertFalse(self.creator_runs("-"), "no opener may start")


class T12Acceptance(LoopCase):
    """#12: the opener is told the full suite is the regression's, and is given the time limits."""

    SENTENCES = (
        "Acceptance and test_plan never ask the worker or the verifier to run the full suite: "
        "the full suite is the regression's.",
        "The worker runs only the verifier's cases, its own unit tests and the base set.",
        "Every check you give the worker or the verifier must finish well within one agent call "
        "(see \"Time limits\" below).",
    )

    def full_result(self, group, content, age):
        d = os.path.join(self.tmp, "wt", "runs", "regression", group, "full")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "result.json")
        with open(path, "w") as fh:
            fh.write(content if isinstance(content, str) else json.dumps(content))
        t = time.time() - age
        os.utime(path, (t, t))

    def opener_prompt(self):
        res = self.run_py("ticket_creator.py", "--request", "x")
        self.assertEqual(res.returncode, 0, res.stderr)
        root = os.path.join(self.tmp, "wt", "runs", "creator")
        newest = sorted(os.listdir(root))[-1]
        with open(os.path.join(root, newest, "prompt.md")) as fh:
            return fh.read()

    def test_creator_chain_forbids_full_suite_for_worker_and_verifier(self):
        text = ""
        for path in sorted(glob.glob(os.path.join(LOOP, "prompts", "creator", "*.md"))):
            with open(path) as fh:
                text += fh.read()
        for sentence in self.SENTENCES:
            self.assertIn(sentence, text)

    def test_prompt_states_the_agent_time_limit(self):
        self.config(agent_timeout_seconds=75)
        lines = self.opener_prompt().splitlines()
        self.assertIn("## Time limits", lines)
        head = lines.index("## Time limits")
        self.assertIn("One agent call is limited to 75 seconds (devloop.agent_timeout_seconds).",
                      lines[head + 1:])

    def test_prompt_states_the_newest_full_run(self):
        self.full_result("int-g1", {"schema": 1, "command": "full", "seconds": 2520.4}, 0)
        self.full_result("int-g2", {"schema": 1, "command": "full", "seconds": 30}, 3600)
        self.assertIn("The project's last full run took 2520 seconds.", self.opener_prompt())

    def test_prompt_says_no_full_run_when_none_recorded(self):
        self.assertIn("No full run of this project is recorded yet.", self.opener_prompt())

    def test_newest_without_numeric_seconds_is_skipped(self):
        for bad in ({"schema": 1, "command": "full"},
                    {"schema": 1, "command": "full", "seconds": "2520"},
                    "{not json"):
            with self.subTest(bad=bad):
                shutil.rmtree(os.path.join(self.tmp, "wt", "runs"), ignore_errors=True)
                self.full_result("int-g1", bad, 0)
                self.full_result("int-g2", {"schema": 1, "command": "full", "seconds": 30}, 3600)
                self.assertIn("The project's last full run took 30 seconds.", self.opener_prompt())

class T127Acceptance(LoopCase):
    """#127 acceptance 4 (a)-(e) (the test_plan's ReviseDagAcceptance): a revised hand-in's 'dag' part
    changes the ticket's depends_on and group. The creator is fake_agent.py's 'revised_dag' action,
    which hands in json.loads($FAKE_REVISE_DAG) as 'dag'. Expected values come from the ticket text."""

    STAMP = "2026-11-24T00:00:00Z"

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        self.plan(creator={"1": ["revised_dag"]})

    # ------------------------------------------------------------ helpers

    def groups(self, **groups):
        """groups: gid=[members], each written raw as an open group."""
        def change(tx):
            for gid, members in groups.items():
                tx.put_group({"id": gid, "branch": "int/" + gid, "state": "open", "members": list(members),
                              "fix_round": 0, "job": None, "created": self.STAMP})
        self.pool.update(change, "test: groups")

    def setup_two(self, state1="stuck", deps1=(), **extra1):
        """#1 in open g1 (stuck by default), ready #2 in open g2."""
        self.groups(g1=["1"], g2=["2"])
        if state1 == "stuck":
            extra1.setdefault("stuck", {"reason": "decision", "text": "x"})
        self.add(2, group="g2", target="int/g2")
        self.add(1, deps=deps1, state=state1, group="g1", target="int/g1", **extra1)

    def revise(self, dag, *args):
        self.env["FAKE_REVISE_DAG"] = json.dumps(dag)
        return self.run_py("ticket_creator.py", "--revise", "1", *args)

    def main_revise(self, dag):
        return self.revise(dag, "--from", "main", "--decision", "revise", "--reason", "x")

    def ok(self, res):
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def failed(self, res, *needles):
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("REVISE FAILED", res.stdout, res.stdout + res.stderr)
        for n in needles:
            self.assertIn(n, res.stdout, res.stdout + res.stderr)

    def ticket(self):
        return self.snap().ticket("1")

    @staticmethod
    def without_history(t):
        return {k: v for k, v in t.items() if k != "history"}

    # ------------------------------------------------------------ (a)

    def test_a_main_revise_sets_depends_on_and_reorders_the_chain(self):
        from core import chain
        self.setup_two()
        self.ok(self.main_revise({"depends_on": ["2"]}))
        snap = self.snap()
        t = snap.ticket("1")
        self.assertEqual(t.get("state"), "ready", t)
        self.assertEqual(t.get("acceptance"), ["a possible thing"])
        self.assertEqual(dagmod.deps(snap.dag, "1"), ["2"])
        events = [h for h in t.get("history") or [] if h.get("event") == "dag: depends on #2"]
        self.assertTrue(events, t.get("history"))
        self.assertEqual([h.get("by") for h in events], ["opener"], events)
        self.assertEqual(chain.order(snap), ["g2", "g1"])

    # ------------------------------------------------------------ (b)

    def test_b_main_revise_moves_the_ticket_to_a_new_group(self):
        self.setup_two()
        self.ok(self.main_revise({"group": "new"}))
        snap = self.snap()
        t = snap.ticket("1")
        self.assertEqual(t.get("state"), "ready", t)
        self.assertEqual(t.get("acceptance"), ["a possible thing"])
        self.assertEqual(t.get("group"), "g3", t)
        self.assertIn("g3", snap.groups)
        self.assertEqual(snap.groups["g3"].get("members"), ["1"])

    # ------------------------------------------------------------ (c)

    def test_c_a_missing_dependency_is_refused_and_writes_nothing_of_the_revision(self):
        self.setup_two()
        before = self.snap()
        t0, dag0 = before.ticket("1"), dict(before.dag.get("1") or {})
        res = self.main_revise({"depends_on": ["9"]})
        self.failed(res, "#9 does not exist")
        after = self.snap()
        t1 = after.ticket("1")
        self.assertEqual(self.without_history(t1), self.without_history(t0))
        self.assertEqual(t1.get("state"), "stuck")
        self.assertEqual(dict(after.dag.get("1") or {}), dag0)
        old = t0.get("history") or []
        new = t1.get("history") or []
        self.assertEqual(new[:len(old)], old)
        self.assertEqual([h.get("event") for h in new[len(old):]], ["user answer"], new[len(old):])

    # ------------------------------------------------------------ (d)

    def test_d_a_mentioned_dependency_may_be_dropped_by_the_opener(self):
        self.setup_two(deps1=["2"], objective="o, after #2")
        self.ok(self.main_revise({"depends_on": []}))
        snap = self.snap()
        self.assertEqual(snap.ticket("1").get("state"), "ready")
        self.assertEqual(dagmod.deps(snap.dag, "1"), [])

    # ------------------------------------------------------------ (e)

    def test_e_a_worker_revision_with_a_dag_part_is_refused(self):
        self.setup_two(state1="running")
        before = self.snap()
        t0, dag0 = before.ticket("1"), dict(before.dag.get("1") or {})
        res = self.revise({"depends_on": []}, "--from", "worker", "--reason", "x")
        self.failed(res, "#1 is running")
        after = self.snap()
        t1 = after.ticket("1")
        self.assertEqual(self.without_history(t1), self.without_history(t0))
        self.assertEqual(dict(after.dag.get("1") or {}), dag0)


class LastFullSecondsUnit(unittest.TestCase):
    """limits_section / last_full_seconds helpers of ticket_creator."""

    def test_newest_valid_wins_and_bad_files_skipped(self):
        import ticket_creator
        tmp = tempfile.mkdtemp(prefix="lfs-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        class Cfg:
            agent_timeout_seconds = 75

            def runs_dir(self, *parts):
                return os.path.join(tmp, *parts)
        cfg = Cfg()
        self.assertIn("No full run", ticket_creator.limits_section(cfg))
        now = time.time()
        for name, body, age in (("a", '{"seconds": 30}', 3600), ("b", "not json", 0)):
            d = os.path.join(tmp, "regression", name, "full")
            os.makedirs(d)
            f = os.path.join(d, "result.json")
            with open(f, "w") as fh:
                fh.write(body)
            os.utime(f, (now - age, now - age))
        text = ticket_creator.limits_section(cfg)
        self.assertIn("limited to 75 seconds", text)
        self.assertIn("took 30 seconds.", text)


class OpenerGroupChoice(LoopCase):
    """#119 (issue #6 part 3): ticket_creator.group_problems / write_new (implementer's unit cases)."""

    @staticmethod
    def ticket(key):
        return {"key": key, "title": key, "objective": "o", "acceptance": ["a"], "needs_verifier": True,
                "complexity": "simple", "token_estimate": 200000}

    def group(self, gid, state, members):
        def change(tx):
            tx.put_group({"id": gid, "branch": "int/" + gid, "state": state, "members": list(members),
                          "fix_round": 0, "job": None, "created": "2026-11-23T00:00:00Z", "history": []})
        self.pool.update(change, "test: group %s" % gid)

    def test_new_groups_that_depend_on_each_other_without_a_cycle_are_accepted(self):
        """#122: the DAG orders the groups, so a new group depending on a later new group is no cycle."""
        import ticket_creator
        result = {"kind": "tickets", "tickets": [self.ticket("new-1"), self.ticket("new-2")],
                  "dag": {"new-1": {"depends_on": ["new-2"], "group": "new-g1"},
                          "new-2": {"depends_on": [], "group": "new-g2"}}}
        self.assertEqual(ticket_creator.check_new(result, self.snap()), [])
        result["dag"]["new-1"]["group"] = "new-g2"
        result["dag"]["new-2"]["group"] = "new-g1"
        self.assertEqual(ticket_creator.check_new(result, self.snap()), [])
        result["tickets"].reverse()
        self.assertEqual(ticket_creator.check_new(result, self.snap()), [])

    def test_a_landed_dependency_or_one_without_a_group_imposes_nothing(self):
        import ticket_creator
        self.add(1, group="g1", state="ready")
        self.add(2, group="g2", state="landed")
        self.add(3)
        self.group("g1", "open", ["1"])
        self.group("g2", "landed", ["2"])
        result = {"kind": "tickets", "tickets": [self.ticket("new-1")],
                  "dag": {"new-1": {"depends_on": ["2", "3"], "group": "g1"}}}
        self.assertEqual(ticket_creator.check_new(result, self.snap()), [])

    def test_the_write_refuses_a_group_that_stopped_being_open(self):
        import ticket_creator
        self.add(1, group="g1", state="ready")
        self.group("g1", "open", ["1"])
        result = {"kind": "tickets", "tickets": [self.ticket("new-1")],
                  "dag": {"new-1": {"depends_on": [], "group": "g1"}}}
        self.assertEqual(ticket_creator.check_new(result, self.snap()), [])
        self.group("g1", "pr", ["1"])          # went to pr while the opener worked
        before = self.pool.git.run("rev-parse", "HEAD").out.strip()
        ids, errors = ticket_creator.write_new(self.pool, result, "req")
        self.assertEqual((ids, errors), ([], ["new-1: group g1 is pr and takes no new tickets"]))
        self.assertEqual(self.pool.git.run("rev-parse", "HEAD").out.strip(), before)
        snap = self.snap()
        self.assertEqual(sorted(snap.tickets), ["1"])
        self.assertEqual(snap.groups["g1"]["members"], ["1"])

    def test_the_write_appends_to_an_open_group_and_makes_new_ones(self):
        import ticket_creator
        self.add(1, group="g1", state="ready")
        self.group("g1", "open", ["1"])
        result = {"kind": "tickets", "tickets": [self.ticket("new-1"), self.ticket("new-2"), self.ticket("new-3")],
                  "dag": {"new-1": {"depends_on": [], "group": "new-g2"},
                          "new-2": {"depends_on": [], "group": "g1"},
                          "new-3": {"depends_on": ["new-1"], "group": "new-g1"}}}
        self.assertEqual(ticket_creator.check_new(result, self.snap()), [])
        ids, errors = ticket_creator.write_new(self.pool, result, "req")
        self.assertEqual((ids, errors), (["2", "3", "4"], []))
        snap = self.snap()
        self.assertEqual(sorted(snap.groups), ["g1", "g2", "g3"])
        self.assertEqual(snap.groups["g1"]["members"], ["1", "3"])
        self.assertEqual(snap.groups["g1"]["history"][-1]["event"], "member #3 added by the opener")
        self.assertEqual(snap.groups["g2"]["members"], ["2"])
        self.assertEqual(snap.groups["g3"]["members"], ["4"])
        self.assertEqual([snap.tickets[i]["group"] for i in ("2", "3", "4")], ["g2", "g1", "g3"])
        self.assertEqual(snap.dag["4"], {"depends_on": ["2"]})

    def test_groups_section(self):
        import ticket_creator
        self.add(1, group="g1", state="ready")
        self.add(2, group="g2", state="landed")
        self.group("g1", "stuck", ["1"])
        self.group("g2", "landed", ["2"])
        self.assertEqual(ticket_creator.groups_section(self.snap()).split("\n")[2:],
                         ["The chain, oldest first; a new group goes at the end.",
                          "- g1 (stuck, takes no new tickets): #1 [ready] ticket 1"])


if __name__ == "__main__":
    unittest.main()
