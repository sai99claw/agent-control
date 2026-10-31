#!/usr/bin/env python3
"""ticket_issuer --id N --target int/gK : take one ticket from "running" to "merged".

Started only by ticket_controller. Per round (at most devloop.max_rounds, whole run at most
devloop.issuer_timeout_seconds):

  1. worker (+ verifier while the ticket has no cases yet) work in their own worktrees
  2. the verifier's branch is merged into the ticket branch
  3. hard_block check on what the ticket changes
  4. reviewer, when the ticket asks for one
  5. the verifier's cases run on the ticket branch
  6. the ticket branch takes in the latest target and moves the target forward (compare-and-swap;
     if another ticket moved it first, take it in again)

A step that fails becomes feedback for the next round. A worker that thinks the ticket is wrong
calls ticket_creator.py --revise itself (it is in the worker's prompt). Out of rounds, out of
time, hard_block, environment, or a question only the user can answer -> the ticket is "stuck"
and the main session gets one page.

Exit: 0 merged · 1 stuck (reported) · 2 refused (wrong state / usage) · 4 interrupted (left
"ready" so the controller dispatches it again).
"""

import argparse
import json
import os
import re
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, memory, osx, slots, testrun  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.gitx import Git  # noqa: E402
from core.pool import event  # noqa: E402

LOOP = os.path.dirname(os.path.abspath(__file__))


class Interrupted(Exception):
    pass


class Stuck(Exception):
    def __init__(self, reason, text):
        super().__init__(text)
        self.reason, self.text = reason, text


class Issuer:
    def __init__(self, cfg, pool, ident, target):
        self.cfg, self.pool, self.id, self.target = cfg, pool, str(ident), target
        self.repo = Git(cfg.root)
        self.branch = "t/%s" % self.id
        self.cases_branch = "t/%s-cases" % self.id
        self.wt = os.path.join(cfg.worktree_root, "t%s" % self.id)
        self.cwt = os.path.join(cfg.worktree_root, "t%s-cases" % self.id)
        self.deadline = time.time() + cfg.issuer_timeout_seconds
        self.pending = []
        # The worker's hand-in of an earlier round still stands when only the verifier's cases
        # were sent back: the next round starts the verifier alone.
        self.worker_ok = False

    # ------------------------------------------------------------ pool

    def ticket(self):
        return self.pool.snapshot().ticket(self.id)

    def save(self, mutate, message):
        def change(tx):
            t = tx.ticket(self.id)
            mutate(t)
            tx.put_ticket(t)
            return t
        return self.pool.update(change, "#%s: %s" % (self.id, message))

    def claim(self):
        def mutate(t):
            if t.get("state") != "running" or t.get("target") != self.target:
                raise osx.OSFailure("#%s is %s for %s, not running for %s" % (
                    self.id, t.get("state"), t.get("target"), self.target))
            t["run"] = osx.stamp()
            event(t, "issuer started", target=self.target)
        return self.save(mutate, "issuer started")

    # ------------------------------------------------------------ main

    def go(self):
        t = self.claim()
        if not self.repo.sha(self.target):
            raise Stuck("target", "integration branch %s does not exist" % self.target)
        self.repo.worktree(self.wt, self.branch, start=self.target)
        rounds = self.cfg.max_rounds
        while int(t.get("round") or 0) < rounds:
            t = self.save(lambda x: (x.__setitem__("round", int(x.get("round") or 0) + 1),
                                     event(x, "round start")), "round start")
            r = t["round"]
            feedback, costs = self.one_round(t, r)
            if feedback is None:
                self.save(lambda x: self._merged(x, costs), "merged into %s" % self.target)
                self.cleanup()
                return 0
            self.save(lambda x: self._fed_back(x, r, feedback, costs), "round %d feedback" % r)
            t = self.ticket()
        raise Stuck("rounds", "%d rounds used and still not passing; last feedback:\n%s" % (
            rounds, "\n".join((t.get("feedback") or [])[-1:])))

    def _merged(self, t, costs):
        t["state"], t["run"], t["feedback"] = "merged", None, []
        t.setdefault("cost", []).extend(costs)
        event(t, "merged", target=self.target)

    def _fed_back(self, t, r, feedback, costs):
        t.setdefault("feedback", []).append("round %d: %s" % (r, feedback))
        del t["feedback"][:-5]
        t.setdefault("cost", []).extend(costs)
        event(t, "feedback", round=r)

    # ------------------------------------------------------------ one round

    def one_round(self, t, r):
        """Returns (None, costs) when merged into the target, else (feedback text, costs)."""
        feedback, costs = self._round(t, r)
        if feedback is not None and not getattr(self, "_verifier_only", False):
            self.worker_ok = False
        self._verifier_only = False
        return feedback, costs

    def _round(self, t, r):
        costs = []
        need_cases = t.get("needs_verifier", True) and not (t.get("cases") or {}).get("run")
        if need_cases:
            self.repo.worktree(self.cwt, self.cases_branch, start=self.repo.sha(self.target))
        work = None if self.worker_ok else self.start("worker", "worker.md", t, r, self.wt)
        ver = self.start("verifier", "verifier.md", t, r, self.cwt) if need_cases else None
        hands = [p.finish() for p in [work, ver] if p]
        self.pending = []
        for h in hands:
            memory.collect(self.pool, h, "#%s r%d %s" % (self.id, r, h.role))
        costs.extend(h.cost for h in hands)
        whand = hands[0] if work else None
        if whand and whand.kind == "blocked":
            raise Stuck("decision", whand.result.get("text") or "worker says the ticket needs your decision")

        problems = []
        if whand and whand.problem():
            problems.append(whand.problem())
        elif whand:
            self.worker_ok = True
        if ver:
            vh = hands[-1]
            if vh.problem():
                problems.append(vh.problem())
            elif vh.kind != "cases" or not vh.result.get("run"):
                problems.append("verifier hand-in has no \"run\" list of case ids")
            elif self.repo.out("rev-list", "--count",
                               "%s..%s" % (self.target, self.cases_branch)) == "0":
                problems.append("verifier committed nothing on %s" % self.cases_branch)
            else:
                ids = [str(x) for x in vh.result["run"]]
                wrong = self.red_check(ids)
                if wrong:
                    problems.append("verifier: " + wrong)
                else:
                    cases = {"branch": self.cases_branch, "run": ids}
                    self.save(lambda x: (x.__setitem__("cases", cases), event(x, "cases ready")),
                              "cases ready")
                    t = self.ticket()
        if problems:
            self._verifier_only = self.worker_ok
            return "\n".join(problems), costs

        t = self.ticket()          # the worker may have had the ticket revised
        g = Git(self.wt)
        if g.dirty():
            return "the worktree has uncommitted changes; commit everything before handing in", costs
        cases = t.get("cases") or {}
        if cases.get("branch") and not g.is_ancestor(cases["branch"], "HEAD"):
            ok, why = g.merge(cases["branch"])
            if not ok:
                return ("merging the verifier's branch %s into %s failed (%s); merge it "
                        "yourself and resolve" % (cases["branch"], self.branch, why)), costs

        self.hard_block(g)

        if complexity(t) == "complex":
            rh = self.start("reviewer", "reviewer.md", t, r, self.wt).finish()
            self.pending = []
            costs.append(rh.cost)
            memory.collect(self.pool, rh, "#%s r%d reviewer" % (self.id, r))
            if rh.problem():
                return rh.problem(), costs
            if rh.result.get("verdict") != "pass":
                return "review: " + str(rh.result.get("feedback") or "failed without a reason"), costs

        failed = self.run_cases(t)
        if failed:
            return failed, costs
        return self.integrate(g, t), costs

    def integrate(self, g, t):
        """Take in the latest target, re-test if it changed anything, move the target."""
        for _ in range(8):
            old = self.repo.sha(self.target)
            if not g.is_ancestor(old, "HEAD"):
                ok, why = g.merge(self.target)
                if not ok:
                    return ("merging the latest %s into %s failed (%s) - another ticket of "
                            "this group changed the same files; merge %s yourself and resolve" % (
                                self.target, self.branch, why, self.target))
                failed = self.run_cases(t)
                if failed:
                    return "after taking in %s: %s" % (self.target, failed)
            if self.repo.move_ref(self.target, g.sha("HEAD"), old):
                return None
            time.sleep(1)
        return "%s kept moving while merging; try again" % self.target

    def run_cases(self, t):
        ids = (t.get("cases") or {}).get("run") or []
        if not ids:
            return None
        self.check_time()
        slot = self.test_slot()
        try:
            out = testrun.run(self.cfg, self.wt, "run", ids, timeout=self.left(),
                              keep_dir=os.path.join(self.cfg.runs_dir(self.id), "cases"))
        finally:
            slot.release()
        if out.status == testrun.GREEN:
            return None
        if out.status == testrun.ENV:
            raise Stuck("env", out.summary())
        return "verifier cases: " + out.summary()

    def red_check(self, ids):
        """The verifier's cases must fail on the code before the ticket - by their assertions,
        not by an error (D-020; checked by running them, D-084 Q10). The verifier's branch is
        exactly that: the old code plus the cases. Returns what is wrong, or None."""
        mark = case_mark(self.id)
        tree = os.path.join(self.cfg.worktree_root, "red-t%s" % self.id)
        self.check_time()
        self.repo.detached(tree, self.repo.sha(self.cases_branch))
        try:
            slot = self.test_slot()
            try:
                out = testrun.run(self.cfg, tree, "run", ids, timeout=min(1800, self.left()),
                                  keep_dir=os.path.join(self.cfg.runs_dir(self.id), "red-check"))
            finally:
                slot.release()
        finally:
            self.repo.remove_worktree(tree)
        if out.status == testrun.ENV:
            raise Stuck("env", out.summary())
        if out.status == testrun.UNMEASURED:
            return "the cases could not be run on the code before the change (%s)" % out.detail
        mine = [r for r in out.rows if mark in str(r.get("id"))]
        if not mine:
            broken = [r for r in out.rows if r.get("status") == "error"]
            if broken:
                return ("the case modules did not load on the code before the change, so no %s case "
                        "ran: %s" % (mark, "; ".join("%s: %s" % (r.get("id"), _last_line(
                            r.get("message") or r.get("exception"))) for r in broken[:5])))
            return ("no acceptance case named %s ran (ids %s); name every acceptance case with %s "
                    "(the mark must appear in each case's id as the test interface reports it in result.json)"
                    % (mark, " ".join(ids), mark))
        passing = [r["id"] for r in mine if r.get("status") == "pass"]
        errors = [r["id"] for r in mine if r.get("status") == "error"]
        if passing:
            return ("these cases already pass before the change, so they do not test the ticket: %s"
                    % ", ".join(passing[:10]))
        if errors:
            return ("these cases fail with an error instead of their assertion (import error or "
                    "missing name - assert the symbol exists first): %s" % ", ".join(errors[:10]))
        return None

    def hard_block(self, g):
        files = g.changed_files(self.target)
        hits = [f for f in files if any(f == p or f.startswith(p.rstrip("/") + "/")
                                        for p in self.cfg.hard_paths)]
        if self.cfg.hard_patterns:
            added = g.added_lines(self.target)
            for pat in self.cfg.hard_patterns:
                try:
                    rx = re.compile(pat)
                except re.error:
                    continue
                if any(rx.search(line) for line in added):
                    hits.append("pattern %r" % pat)
        if hits:
            raise Stuck("hard_block", "the change touches hard_block: %s" % ", ".join(hits))

    # ------------------------------------------------------------ agents

    def start(self, role, template, t, r, cwd):
        self.check_time()
        run_dir = self.cfg.runs_dir(self.id, "r%d-%s" % (r, role))
        simple = role == "worker" and complexity(t) == "simple"
        chain = "worker_simple" if simple else role
        revise = "python3 %s --revise %s --from %s --reason '<why the ticket is wrong>'" % (
            os.path.join(LOOP, "ticket_creator.py"), self.id, role)
        values = {
            "ticket_id": self.id, "ticket_json": json.dumps(_brief(t), indent=2, ensure_ascii=False),
            "round": r, "max_rounds": self.cfg.max_rounds, "branch": self.branch,
            "cases_branch": self.cases_branch, "target": self.target, "worktree": cwd,
            "cases_run": " ".join((t.get("cases") or {}).get("run") or []) or "(not written yet)",
            "feedback": "\n".join(t.get("feedback") or []) or "(first round)",
            "revise_cmd": revise, "result_path": os.path.join(run_dir, "result.json"),
            "test_cmd": "python3 %s <ids>" % os.path.join(LOOP, "run_tests.py"),
            "case_mark": case_mark(self.id),
            "base_cmd": ("python3 %s %s" % (os.path.join(LOOP, "run_tests.py"), " ".join(self.cfg.base_test_ids))
                         if self.cfg.base_test_ids else "(this project has no base set)"),
            "index": (os.path.join(cwd, self.cfg.index_path) if self.cfg.index_path else "(no code index)"),
            "tests_rule": (
                "Write unit tests for the code you write (its helpers and internal pieces). The "
                "acceptance cases - one per acceptance item - belong to the verifier: do not write or "
                "change those, and never name a test of yours with %s, the verifier's mark."
                % case_mark(self.id)
                if t.get("needs_verifier", True) else
                "This ticket has no verifier: write unit tests for your code and the acceptance cases "
                "yourself, in the project's existing test modules."),
            "memory": memory.for_prompt(self.pool.snapshot(), role, agents.model_of(self.cfg.agent(chain))),
            "project_memory": os.path.join(self.pool.path, memory.rel("project", "project")),
        }
        timeout = min(self.cfg.agent_timeout_seconds, self.left())
        p = agents.start(self.cfg, role, agents.render(template, values), cwd, run_dir, timeout,
                         extra_env={"DEVLOOP_TICKET": self.id, "AC_CODE_ROOT": cwd},
                         effort=t.get("effort") if role == "worker" else None, round_no=r,
                         chain_role=chain)
        self.pending.append(p)
        return p

    def left(self):
        return max(1.0, self.deadline - time.time())

    def test_slot(self):
        """A machine slot for one test run, waiting at most until the issuer's deadline."""
        began = time.time()
        slot = slots.take(self.cfg, self.deadline)
        if slot is None:
            raise Stuck("timeout", "issuer ran longer than %d s waiting for a test run: %s" % (
                self.cfg.issuer_timeout_seconds, slots.busy(self.cfg, time.time() - began)))
        return slot

    def check_time(self):
        if time.time() >= self.deadline:
            raise Stuck("timeout", "issuer ran longer than %d s" % self.cfg.issuer_timeout_seconds)

    def stop_agents(self):
        for p in self.pending:
            p.stop()
        self.pending = []

    def cleanup(self):
        for path in (self.wt, self.cwt):
            try:
                self.repo.remove_worktree(path)
            except osx.OSFailure:
                pass

    # ------------------------------------------------------------ endings

    def stuck(self, reason, text):
        def mutate(t):
            t["state"], t["run"] = "stuck", None
            t["stuck"] = {"reason": reason, "text": text, "at": osx.now()}
            event(t, "stuck", reason=reason)
        self.save(mutate, "stuck (%s)" % reason)
        if reason != "decision":   # a revise that escalated already wrote its page
            self.pool.report("env" if reason == "env" else "decision", self.id,
                             "#%s is stuck (%s): %s" % (self.id, reason, text))

    def release(self, why):
        def mutate(t):
            if t.get("state") == "running":
                t["state"], t["run"] = "ready", None
                t["round"] = max(0, int(t.get("round") or 0) - 1)
                event(t, "interrupted", why=why)
        self.save(mutate, "interrupted")


def complexity(t):
    """simple | complex (D-075; the opener sets it). Older tickets: needs_review = complex."""
    value = t.get("complexity")
    if value in ("simple", "complex"):
        return value
    return "complex" if t.get("needs_review") else "simple"


def _last_line(text):
    lines = str(text or "error").strip().splitlines()
    return lines[-1] if lines else "error"


def case_mark(ident):
    """The name every acceptance case of ticket N carries, so the verifier's cases and the
    implementer's unit tests never collide (D-084 Q8) and the red check can find them."""
    return "T%sAcceptance" % ident


def _brief(t):
    keep = ("id", "title", "objective", "acceptance", "outline", "test_plan", "needs_verifier",
            "complexity", "effort", "notes")
    return {k: t[k] for k in keep if k in t}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--id", required=True)
    ap.add_argument("--target", required=True)
    args = ap.parse_args(argv)

    def on_signal(signum, frame):
        raise Interrupted("signal %d" % signum)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    try:
        cfg = Config()
        issuer = Issuer(cfg, pool_for(cfg), args.id, args.target)
    except osx.OSFailure as exc:
        print("ticket_issuer: %s" % exc, file=sys.stderr)
        return 2
    try:
        return issuer.go()
    except Stuck as exc:
        issuer.stop_agents()
        _safe(lambda: issuer.stuck(exc.reason, exc.text))
        print("ticket_issuer: #%s stuck (%s): %s" % (issuer.id, exc.reason, exc.text), file=sys.stderr)
        return 1
    except Interrupted as exc:
        issuer.stop_agents()
        _safe(lambda: issuer.release(str(exc)))
        return 4
    except osx.OSFailure as exc:
        issuer.stop_agents()
        if "not running for" in str(exc):
            print("ticket_issuer: %s" % exc, file=sys.stderr)
            return 2
        _safe(lambda: issuer.stuck("error", str(exc)))
        print("ticket_issuer: #%s stuck (error): %s" % (issuer.id, exc), file=sys.stderr)
        return 1


def _safe(fn):
    try:
        fn()
    except Exception as exc:  # the pool itself may be what failed; say so and go on exiting
        print("ticket_issuer: could not record the outcome: %s" % exc, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
