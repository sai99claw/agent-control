"""#164: `actl tickets approve N --page P --reason R` records the user's approval of a hard_block
stop, re-queues the ticket, and its issuer merges it only while its own change still has the
approved hits and fingerprint (a changed change stops again with a fresh page)."""

import inspect
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from loop_harness import LoopCase, git  # noqa: E402

STAMP = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"
FP = "ab" * 32
HB = {"paths": ["docs/DECISIONS.md"],
      "lines": [{"file": "impl.py", "text": "z = 3  # FORBIDDEN_MARK", "patterns": ["FORBIDDEN_MARK"]}],
      "fingerprint": FP}


class T164Acceptance(LoopCase):

    def setUp(self):
        super().setUp()
        for k in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(k, None)

    # ------------------------------------------------------------ helpers (own, not inherited)

    def write(self, root, rel, text, mode="w"):
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full) or root, exist_ok=True)
        with open(full, mode) as fh:
            fh.write(text)

    def commit_all(self, root, msg):
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", msg)

    def set_patterns(self, patterns):
        path = os.path.join(self.proj, "board", "config.json")
        with open(path) as fh:
            cfg = json.load(fh)
        cfg["hard_block"]["patterns"] = patterns
        with open(path, "w") as fh:
            json.dump(cfg, fh)
        self.commit_all(self.proj, "patterns")

    def pool_head(self):
        return git(self.pool_path, "rev-parse", "HEAD")

    def b2_fixture(self, ident=1, page_hb=None, **over):
        stuck = {"reason": "hard_block", "text": "the change touches hard_block: docs/DECISIONS.md",
                 "at": "2026-11-28T00:00:00Z", "hard_block": HB}
        extra = dict(state="stuck", round=1, group=None, stuck=stuck)
        extra.update(over)
        self.add(ident, **extra)
        p = self.pool.report("decision", "1", "#1 is stuck (hard_block): x", hard_block=page_hb or HB)
        q = self.pool.report("decision", "1", "older page")
        self.assertNotEqual(p, q)
        return p, q

    def approve(self, ident, page, reason, by=None):
        args = ["approve", str(ident), "--page", page, "--reason", reason] + (["--by", by] if by else [])
        return self.run_py("tickets.py", *args)

    def decision_pages(self, ref="1"):
        return {n: r for n, r in self.snap().inbox.items() if r.get("kind") == "decision" and str(r.get("ref")) == ref}

    # ------------------------------------------------------------ B1

    def test_b1_approval_pending(self):
        import ticket_issuer
        self.assertTrue(hasattr(ticket_issuer, "approval_pending"), "ticket_issuer has no approval_pending")
        ap = ticket_issuer.approval_pending
        H = [{"event": "stuck"}, {"event": "hard_block approved"}]
        AP = {"fingerprint": "ab" * 32}
        later = H + [{"event": "dag: moved from no group to g2"}, {"event": "dispatched"}, {"event": "issuer started"}]
        cases = [
            ({"approval": AP, "history": H}, True),
            ({"approval": AP, "history": later}, True),
            ({"approval": AP, "history": later + [{"event": "round start", "round": 1}]}, False),
            ({"approval": AP, "history": H + [{"event": "revised"}]}, False),
            ({"approval": AP, "history": H + [{"event": "user decision: ready"}]}, False),
            ({"approval": AP, "history": H + [{"event": "set ready by main"}]}, False),
            ({"history": H}, False),
            ({"approval": AP, "history": []}, False),
        ]
        for t, want in cases:
            with self.subTest(t=t):
                self.assertIs(bool(ap(t)), want)
                self.assertEqual(ap(t), want)

    # ------------------------------------------------------------ B2

    def check_approved(self, res, p, q, by, acked_p):
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(res.stdout.splitlines() and res.stdout.splitlines()[0].startswith("#1 approved"), res.stdout)
        t = self.snap().ticket("1")
        self.assertEqual(t.get("state"), "ready")
        self.assertEqual(t.get("round"), 0)
        self.assertIsNone(t.get("stuck"))
        self.assertIsNone(t.get("run"))
        a = t.get("approval")
        self.assertIsInstance(a, dict, t)
        self.assertEqual(sorted(a), sorted(["by", "at", "reason", "page", "fingerprint", "paths", "lines"]))
        self.assertRegex(a["at"], STAMP)
        self.assertEqual(a, {"by": by, "at": a["at"], "reason": "signals are its job", "page": p,
                             "fingerprint": FP, "paths": HB["paths"], "lines": HB["lines"]})
        last = t["history"][-1]
        self.assertRegex(last.get("at", ""), STAMP)
        self.assertEqual(last, {"at": last["at"], "event": "hard_block approved", "by": by,
                                "reason": "signals are its job", "page": p, "fingerprint": FP})
        self.assertNotIn("set ready by main", [h.get("event") for h in t["history"]])
        inbox = self.snap().inbox
        self.assertTrue(inbox[p].get("acked"))
        self.assertEqual(inbox[p].get("acked_by"), acked_p)
        self.assertTrue(inbox[q].get("acked"))
        self.assertEqual(inbox[q].get("acked_by"), "tickets approve")

    def test_b2_approve_records_and_requeues(self):
        p, q = self.b2_fixture()
        res = self.approve(1, p, "signals are its job", by="tester")
        self.check_approved(res, p, q, "tester", "tester")

    def test_b2_approve_without_by_is_user(self):
        p, q = self.b2_fixture()
        res = self.approve(1, p, "signals are its job")
        self.check_approved(res, p, q, "user", "user")

    # ------------------------------------------------------------ B3

    def refused(self, ident, page, reason="signals are its job", contains=None):
        before = self.pool_head()
        res = self.approve(ident, page, reason, by="tester")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        lines = res.stderr.splitlines()
        self.assertEqual(len(lines), 1, res.stderr)
        self.assertTrue(lines[0].startswith("tickets: "), res.stderr)
        if contains:
            self.assertIn(contains, lines[0])
        self.assertEqual(self.pool_head(), before)

    def test_b3a_page_without_hard_block(self):
        self.add(1, state="stuck", round=1, group=None,
                 stuck={"reason": "rounds", "text": "x", "at": "2026-11-28T00:00:00Z"})
        p = self.pool.report("decision", "1", "#1 is stuck (rounds): x")
        self.refused(1, p, contains="hard_block")

    def test_b3b_page_of_another_ticket(self):
        p, _ = self.b2_fixture()
        stuck = {"reason": "hard_block", "text": "the change touches hard_block: docs/DECISIONS.md",
                 "at": "2026-11-28T00:00:00Z", "hard_block": HB}
        self.add(2, state="stuck", round=1, group=None, stuck=stuck)
        self.refused(2, p)

    def test_b3c_page_not_in_inbox(self):
        self.b2_fixture()
        self.refused(1, "20261128T000000Z-decision-1-nosuch")

    def test_b3d_ticket_not_stuck(self):
        p, _ = self.b2_fixture(state="ready", stuck=None)
        self.refused(1, p)

    def test_b3e_fingerprint_differs(self):
        p, _ = self.b2_fixture(page_hb=dict(HB, fingerprint="cd" * 32))
        self.refused(1, p)

    def test_b3f_blank_reason(self):
        p, _ = self.b2_fixture()
        self.refused(1, p, reason="   ")

    def test_b3g_group_id(self):
        p, _ = self.b2_fixture()
        self.refused("g1", p)

    # ------------------------------------------------------------ B4

    def f1(self):
        import ticket_issuer
        from core.config import Config
        from core.gitx import Git
        self.assertIn("t", inspect.signature(ticket_issuer.Issuer.hard_block).parameters,
                      "Issuer.hard_block has no parameter t")
        self.set_patterns(["FORBIDDEN_MARK", "zz_forbidden"])
        self.write(self.proj, "keep.txt", "a\nb\n")
        self.commit_all(self.proj, "keep")
        git(self.proj, "branch", "int/g1", "main")
        wt = os.path.join(self.tmp, "t1")
        git(self.proj, "worktree", "add", "-q", wt, "-b", "t/1", "int/g1")
        self.write(wt, "keep.txt", "c\n", mode="a")
        self.write(wt, "impl.py", "y = 2\nz = 3  # FORBIDDEN_MARK zz_forbidden\n")
        self.write(wt, "docs/DECISIONS.md", "own\n")
        self.commit_all(wt, "own")
        issuer = ticket_issuer.Issuer(Config(self.proj), self.pool, "1", "int/g1")
        g = Git(wt)
        self.add(1, state="running")
        with self.assertRaises(ticket_issuer.Stuck) as cm:
            issuer.hard_block(g)
        self.assertEqual(cm.exception.reason, "hard_block")
        d = cm.exception.detail
        self.assertIsInstance(d, dict)
        return issuer, g, d

    def approval(self, d, **over):
        a = {"by": "u", "at": "2026-11-28T00:00:00Z", "reason": "r", "page": "p",
             "paths": d["paths"], "lines": d["lines"], "fingerprint": d["fingerprint"]}
        a.update(over)
        return a

    def test_b4_matching_approval_passes_changed_change_stops(self):
        import ticket_issuer
        issuer, g, d = self.f1()
        t = dict(self.snap().ticket("1"), approval=self.approval(d))
        issuer.hard_block(g, t)
        last = self.snap().ticket("1")["history"][-1]
        self.assertEqual(last.get("event"), "hard_block approval applied")
        self.assertEqual(last.get("by"), "u")
        self.assertEqual(last.get("approved_at"), "2026-11-28T00:00:00Z")
        self.assertEqual(last.get("fingerprint"), d["fingerprint"])
        self.write(g.cwd, "impl.py", "w = 4\n", mode="a")
        self.commit_all(g.cwd, "more")
        with self.assertRaises(ticket_issuer.Stuck) as cm:
            issuer.hard_block(g, t)
        exc = cm.exception
        self.assertEqual(exc.reason, "hard_block")
        self.assertEqual(exc.detail["lines"], d["lines"])
        self.assertNotEqual(exc.detail["fingerprint"], d["fingerprint"])
        self.assertEqual(exc.text.splitlines()[1],
                         "  the approval by u at 2026-11-28T00:00:00Z no longer matches this change "
                         "(approved fingerprint " + d["fingerprint"][:12] + ")")

    def test_b4_approval_with_other_lines_stops(self):
        import ticket_issuer
        issuer, g, d = self.f1()
        t = dict(self.snap().ticket("1"), approval=self.approval(d, lines=[]))
        with self.assertRaises(ticket_issuer.Stuck) as cm:
            issuer.hard_block(g, t)
        self.assertEqual(cm.exception.reason, "hard_block")
        self.assertNotIn("hard_block approval applied",
                         [h.get("event") for h in self.snap().ticket("1").get("history") or []])

    # ------------------------------------------------------------ B5 / B6 (real controller)

    def first_stop(self):
        self.set_patterns(["FORBIDDEN_MARK"])
        self.add(1)
        self.env["FAKE_WORKER_FILES"] = json.dumps({"impl.py": "z = 3  # FORBIDDEN_MARK\n"})
        self.plan(worker={"1": ["write_files"]})
        res = self.controller()
        t = self.snap().ticket("1")
        self.assertEqual(t.get("state"), "stuck", res.stdout + res.stderr)
        self.assertEqual(t["stuck"]["reason"], "hard_block")
        self.assertIsNone(t.get("group"))
        unread = [n for n, r in self.decision_pages().items() if not r.get("acked")]
        self.assertEqual(len(unread), 1, self.snap().inbox)
        return unread[0]

    def approve_and_move(self, p):
        res = self.approve(1, p, "ok", by="tester")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        res = self.run_py("dag_edit.py", "move", "1", "--to", "new", "--reason", "r")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def worker_count(self):
        with open(os.path.join(os.path.dirname(self.plan_path), "count-worker-1")) as fh:
            return fh.read().strip()

    def test_b5_approve_then_merge(self):
        p = self.first_stop()
        self.approve_and_move(p)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual(t.get("state"), "landed", (res.stdout + res.stderr, t))
        self.assertIn("z = 3  # FORBIDDEN_MARK", git(self.proj, "show", "main:impl.py"))
        self.assertEqual(self.worker_count(), "1")
        ev = [h.get("event") for h in t["history"]]
        applied = [i for i, e in enumerate(ev) if e == "hard_block approval applied"]
        self.assertEqual(len(applied), 1, ev)
        self.assertIn("hard_block approved", ev)
        self.assertGreater(applied[0], ev.index("hard_block approved"))
        row = t["history"][applied[0]]
        self.assertEqual(row.get("by"), "tester")
        self.assertEqual(row.get("fingerprint"), t["approval"]["fingerprint"])

    def test_b6_change_after_approval_stops_again(self):
        p = self.first_stop()
        wt = os.path.join(self.tmp, "wt", "t1")
        self.write(wt, "impl.py", "w = 4  # FORBIDDEN_MARK\n", mode="a")
        self.commit_all(wt, "after the stop")
        self.approve_and_move(p)
        approved = self.snap().ticket("1").get("approval")
        self.assertIsInstance(approved, dict)
        before = set(self.snap().inbox)
        self.controller()
        t = self.snap().ticket("1")
        self.assertEqual(t.get("state"), "stuck", t)
        self.assertEqual(t["stuck"]["reason"], "hard_block")
        self.assertEqual(sorted(x["text"] for x in t["stuck"]["hard_block"]["lines"]),
                         sorted(["z = 3  # FORBIDDEN_MARK", "w = 4  # FORBIDDEN_MARK"]))
        new = {n: r for n, r in self.decision_pages().items() if n not in before}
        self.assertEqual(len(new), 1, self.snap().inbox)
        self.assertIn("no longer matches", list(new.values())[0]["text"])
        self.assertEqual(t.get("approval"), approved)
        self.assertNotIn("hard_block approval applied", [h.get("event") for h in t["history"]])
        self.assertFalse(self.main_has("impl.py"))
        self.assertEqual(self.worker_count(), "1")
