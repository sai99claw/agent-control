#!/usr/bin/env python3
"""ticket_issuer --id N --target int/gK : take one ticket from "running" to "merged".

Started only by ticket_controller. Per round (at most devloop.max_rounds rounds that were not
free - a round that ended on a merge conflict with the group (at step 6, or replaying the
ticket's own commits at round start in step 0), on a revision that changed the round's work (the
ticket's brief or its cases, made while the round ran), or on a blocked worker whose failing ids
passed when devloop ran them is free - and at most 2 x max_rounds rounds in all; whole run at most
devloop.issuer_timeout_seconds (time waiting for a machine slot or for a usage limit to reset
not counted)):

  0. t/<N>-cases and t/<N> are put on the target's current head with only the ticket's own
     commits (uncommitted tracked changes committed first; branch_base and 'rebased', D-098);
     a replay that conflicts ends the round before any agent: the branch restarts at its start
     point, its old head kept under refs/devloop/before-rebase/ ('rebase conflict')
  1. worker (+ verifier while the ticket has no cases yet) work in their own worktrees; while
     the issuer waits for them (and for the reviewer in step 4) it reads the ticket every
     devloop.poll_seconds, and a revision that changes the round's work stops the round's agents
     and ends the round, free; the same check runs after the hand-ins and right before step 6
  2. the verifier's branch is merged into the ticket branch; from round 2 on (cases recorded
     when the round started, no revision since) the ticket branch must define the verifier's
     acceptance cases exactly as the cases branch does (case_definitions), else the round ends
  3. hard_block check on what the ticket changes
  4. reviewer, when the ticket asks for one
  5. the verifier's cases run on the ticket branch
  6. the ticket branch takes in the latest target and moves the target forward (compare-and-swap;
     if another ticket moved it first, take it in again)

A step that fails becomes feedback for the next round. A worker or verifier that thinks the
ticket is wrong hands in {"kind": "revise", "reason": R}; the issuer runs the opener's revision
itself (ticket_creator.revise, source worker | verifier, in the issuer's own environment, charged
to its deadline, no machine slot) - the worker's before the verifier is waited for, the
verifier's only when the worker's did not revise or ended UNCHANGED. REVISED ends the round as a
work-changing revision (free); UNCHANGED ends it unmerged and free with the opener's note as
feedback; ESCALATED leaves the ticket stuck (decision; the opener's page is the page); a failed
revision or a hand-in without "reason" is counted feedback. A revision the opener handed in but
nobody wrote into the pool (its ticket_creator.py was stopped meanwhile) is written in by the
issuer before the next round starts. Out of rounds, out of
time, hard_block, environment, or a question only the user can answer -> the ticket is "stuck"
and the main session gets one page. A worker's blocked hand-in gets one decision page, unless
a revise escalated in that round (its page is the page). When the blocked hand-in
names "failing" test ids, devloop runs them on the ticket branch first: when they pass, the block
was the agent's own environment, the round is free (agent_env) and the worker runs again next
round on the next command of its chain.

An agent whose last command hits the account's usage limit does not lose the round: the issuer
waits for the reset (agents.quota_wait), its ticket stays "running" with a "waiting" mark and the
run's cost row (outcome "quota"), and the agent runs again (D-091).

Exit: 0 merged · 1 stuck (reported) · 2 refused (wrong state / usage / another issuer of the
ticket holds its lock <worktrees>/locks/issuer-<N>.lock), or the ticket is no longer this issuer's
(not running under the stamp written at the claim: nothing more is written, no branch moves) ·
4 interrupted (left "ready" so the controller dispatches it again).
"""

import argparse
import ast
import json
import os
import re
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, chain, inflight, memory, osx, pages, slots, testrun, ticketlock, worktrees  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.gitx import Git, GitError  # noqa: E402
from core.pool import event  # noqa: E402
import ticket_creator  # noqa: E402

LOOP = os.path.dirname(os.path.abspath(__file__))


class Interrupted(Exception):
    pass


class NotOwned(Exception):
    """The ticket is no longer running under this issuer's claim stamp."""
    def __init__(self, ident, state):
        super().__init__("#%s is no longer this issuer's ticket (state %s)" % (ident, state))
        self.state = state


class Stuck(Exception):
    def __init__(self, reason, text, page=True):
        super().__init__(text)
        self.reason, self.text, self.page = reason, text, page


THROWN_OUT = ("the ticket was revised during this round and the revision threw out the "
              "verifier's cases (cases_wrong); the verifier writes them again next round and "
              "the worker works on the revised ticket")


REVISED_MID_ROUND = ("the ticket was revised during this round; the round was stopped and does "
                     "not count, and the next round works on the revised ticket")


def brief_revised(t):
    """True when a 'revised' event after the last 'round start' changed the round's work: its
    'fields' name a brief field (what _brief gives agents, id aside) or it has cases_wrong."""
    brief = set(BRIEF_KEYS) - {"id"}
    changed = False
    for h in t.get("history") or []:
        if h.get("event") == "round start":
            changed = False
        elif h.get("event") == "revised" and (h.get("cases_wrong") or brief & set(h.get("fields") or [])):
            changed = True
    return changed


def cases_thrown_out(t):
    """True when a 'revised' event with cases_wrong comes after the last 'round start'."""
    thrown = False
    for h in t.get("history") or []:
        if h.get("event") == "round start":
            thrown = False
        elif h.get("event") == "revised" and h.get("cases_wrong"):
            thrown = True
    return thrown


def revised_since_round_start(t):
    """True when any 'revised' event comes after the last 'round start'."""
    revised = False
    for h in t.get("history") or []:
        if h.get("event") == "round start":
            revised = False
        elif h.get("event") == "revised":
            revised = True
    return revised


def free_rounds(t):
    """{"merge": M, "revision": V, "agent_env": A}: the free 'feedback' events of the ticket's
    current run (after the last 'round start' with round 1) whose round is <= t['round']."""
    hist = t.get("history") or []
    starts = [i for i, h in enumerate(hist) if h.get("event") == "round start" and h.get("round") == 1]
    hist = hist[starts[-1]:] if starts else hist
    r = int(t.get("round") or 0)
    out = {"merge": 0, "revision": 0, "agent_env": 0}
    for h in hist:
        if (h.get("event") == "feedback" and h.get("free") in out
                and int(h.get("round") or 0) <= r):
            out[h["free"]] += 1
    return out


class Issuer:
    # Every Pending this issuer started (never cleared, unlike self.pending) and the seconds its
    # own test runs waited for a machine slot: neither counts against issuer_timeout_seconds.
    started_agents = ()
    test_waited = 0.0
    # Chain index the next worker starts at (set by a blocked hand-in whose ids passed; used once)
    next_worker = 0
    # The run stamp claim() wrote; from then on every write checks the ticket still carries it
    run_stamp = None

    def __init__(self, cfg, pool, ident, target):
        self.cfg, self.pool, self.id, self.target = cfg, pool, str(ident), target
        self.repo = Git(cfg.root)
        self.branch = "t/%s" % self.id
        self.cases_branch = "t/%s-cases" % self.id
        self.wt = os.path.join(cfg.worktree_root, "t%s" % self.id)
        self.cwt = os.path.join(cfg.worktree_root, "t%s-cases" % self.id)
        self.started = time.time()
        self.deadline = self.started + cfg.issuer_timeout_seconds   # before any slot wait
        self.pending = []
        self.started_agents = []
        # Cost rows of the agent runs this round collected so far; written with the round's
        # outcome (_merged/_fed_back) or, when the issuer is interrupted, by interrupted().
        self.round_costs = []
        # The worker's hand-in of an earlier round still stands when only the verifier's cases
        # were sent back: the next round starts the verifier alone.
        self.worker_ok = False

    # ------------------------------------------------------------ pool

    def ticket(self):
        return self.pool.snapshot().ticket(self.id)

    def owned(self, t):
        """Raise NotOwned unless `t` is running under this issuer's claim (no-op before it)."""
        if self.run_stamp is not None and (
                t.get("state") != "running" or t.get("run") != self.run_stamp):
            raise NotOwned(self.id, t.get("state"))

    def save(self, mutate, message):
        def change(tx):
            t = tx.ticket(self.id)
            self.owned(t)
            mutate(t)
            tx.put_ticket(t)
            return t
        return self.pool.update(change, "#%s: %s" % (self.id, message))

    def claim(self):
        stamp = osx.stamp()

        def mutate(t):
            if t.get("state") != "running" or t.get("target") != self.target:
                raise osx.OSFailure("#%s is %s for %s, not running for %s" % (
                    self.id, t.get("state"), t.get("target"), self.target))
            t["run"] = stamp
            event(t, "issuer started", target=self.target)
        t = self.save(mutate, "issuer started")
        self.run_stamp = stamp
        return t

    # ------------------------------------------------------------ main

    def live(self, t):
        """Write this issuer's record (core/inflight.py) for the ticket's current round."""
        self.live_stamp = getattr(self, "live_stamp", None) or osx.stamp()
        inflight.put(self.cfg, "issuer-%s" % self.id, {
            "kind": "issuer", "stamp": self.live_stamp, "ticket": self.id, "target": self.target,
            "round": t.get("round")})

    def go(self):
        t = self.claim()
        self.live(t)
        if not self.repo.sha(self.target):
            raise Stuck("target", "integration branch %s does not exist" % self.target)
        try:
            worktrees.branch_tree(self.cfg, self.repo, self.wt, self.branch, start=self.target)
        except worktrees.SetupFailed as exc:
            raise Stuck("env", str(exc))
        rounds = self.cfg.max_rounds
        while True:
            free = free_rounds(t)
            used = int(t.get("round") or 0)
            if not (used - sum(free.values()) < rounds and used < 2 * rounds):
                break
            # A revision the opener handed in while its ticket_creator.py was stopped (with the
            # worker that ran it) is written into the pool before the round reads the ticket.
            self.owned(self.ticket())
            ticket_creator.apply_unapplied(self.cfg, self.pool, self.id, self.started)
            def start_round(x):
                x["round"] = int(x.get("round") or 0) + 1
                event(x, "round start", round=x["round"])
            t = self.save(start_round, "round start")
            r = t["round"]
            self._free = None
            self.live(t)
            feedback, costs = self.one_round(t, r)
            if feedback is None:
                self.save(lambda x: self._merged(x, costs), "merged into %s" % self.target)
                self.round_costs = []
                self.cleanup()
                return 0
            self.save(lambda x: self._fed_back(x, r, feedback, costs), "round %d feedback" % r)
            self.round_costs = []
            t = self.ticket()
        raise Stuck("rounds", rounds_text(t))

    def _merged(self, t, costs):
        t["state"], t["run"], t["feedback"] = "merged", None, []
        t.setdefault("cost", []).extend(costs)
        event(t, "merged", target=self.target)

    def _fed_back(self, t, r, feedback, costs):
        t.setdefault("feedback", []).append("round %d: %s" % (r, feedback))
        del t["feedback"][:-5]
        t.setdefault("cost", []).extend(costs)
        free = getattr(self, "_free", None)
        event(t, "feedback", round=r, **({"free": free} if free else {}))

    # ------------------------------------------------------------ one round

    def one_round(self, t, r):
        """Returns (None, costs) when merged into the target, else (feedback text, costs)."""
        feedback, costs = self._round(t, r)
        if feedback is not None and not getattr(self, "_verifier_only", False):
            self.worker_ok = False
        self._verifier_only = False
        return feedback, costs

    def _round(self, t, r):
        costs = self.round_costs = []
        need_cases = t.get("needs_verifier", True) and not (t.get("cases") or {}).get("run")
        had_cases = t.get("needs_verifier", True) and bool((t.get("cases") or {}).get("run"))
        if need_cases:
            try:
                worktrees.branch_tree(self.cfg, self.repo, self.cwt, self.cases_branch,
                                      start=self.repo.sha(self.target))
            except worktrees.SetupFailed as exc:
                raise Stuck("env", str(exc))
        t, conflicts = self.rebase_round(t, r)
        if conflicts:
            self._free = "merge"
            return "\n".join(conflicts), costs
        work = None if self.worker_ok else self.start("worker", "worker.md", t, r, self.wt)
        ver = self.start("verifier", "verifier.md", t, r, self.cwt) if need_cases else None
        whand = vh = None
        problems = []
        wkept = False     # the worker's revise hand-in ended UNCHANGED
        for p in [work, ver]:
            if p:
                h = p.finish(self.revision_seen, self.cfg.poll_seconds)
                costs.append(h.cost)
                if h.stopped:
                    return self.revised_mid_round([work, ver], costs), costs
                memory.collect(self.pool, h, "#%s r%d %s" % (self.id, r, h.role))
                if p is ver:
                    vh = h
                    continue
                whand = h
                # A worker's revise hand-in is handled before the verifier is waited for, so a
                # revision or an escalation stops a verifier that still runs.
                if h.kind == "revise" and not h.problem():
                    end, text = self.revise_hand(h, "worker")
                    if end == "revised":
                        return self.revised_mid_round([work, ver], costs), costs
                    wkept = end == "unchanged"
                    problems.append(text)
        self.pending = []
        if whand and whand.kind == "blocked":
            escalated = False   # an 'escalated' event after the last 'round start': its page is the page
            for h in self.ticket().get("history") or []:
                if h.get("event") == "round start":
                    escalated = False
                elif h.get("event") == "escalated":
                    escalated = True
            text = whand.result.get("text") or "worker says the ticket needs your decision"
            if escalated:
                raise Stuck("decision", text, page=False)
            blocked = self.blocked_check(work, text, whand.result.get("failing"))
        else:
            blocked = None
        if brief_revised(self.ticket()):
            # This round's agents worked on the old ticket: do not red-check or save the
            # verifier's cases; the next round restarts the worker (and the verifier when the
            # ticket has no cases).
            return self.revised_mid_round([], costs), costs

        if blocked:
            problems.append(blocked)
        elif whand and whand.problem():
            problems.append(whand.problem())
        elif whand and whand.kind != "revise":
            self.worker_ok = True
        if ver:
            if vh.problem():
                problems.append(vh.problem())
            elif vh.kind == "revise" and whand is not None and whand.kind == "revise" and not wkept:
                problems.append("the verifier's revise hand-in was not acted on: the worker's "
                                "revise of this round came first and did not end unchanged")
            elif vh.kind == "revise":
                end, text = self.revise_hand(vh, "verifier")
                if end == "revised":
                    return self.revised_mid_round([], costs), costs
                problems.append(text)
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
        if r >= 2 and had_cases and cases.get("branch") and not revised_since_round_start(self.ticket()):
            changed = self.cases_changed(g, cases["branch"])
            if changed:
                return changed, costs

        self.hard_block(g)

        if complexity(t) == "complex":
            rh = self.start("reviewer", "reviewer.md", t, r, self.wt).finish(
                self.revision_seen, self.cfg.poll_seconds)
            costs.append(rh.cost)
            self.pending = []
            if rh.stopped:
                return self.revised_mid_round([], costs), costs
            memory.collect(self.pool, rh, "#%s r%d reviewer" % (self.id, r))
            if rh.problem():
                return rh.problem(), costs
            if rh.result.get("verdict") != "pass":
                return "review: " + str(rh.result.get("feedback") or "failed without a reason"), costs

        failed = self.run_cases(t)
        if failed:
            return failed, costs
        return self.merge_unless_revised(g, t, costs), costs

    def revise_hand(self, hand, role):
        """A kind "revise" hand-in of `role` (worker | verifier): the issuer runs the opener's
        revision itself (ticket_creator.revise, in this process and environment, its Pending in
        self.pending so a signal stops it). Returns ("revised", None) or ("unchanged" | "failed",
        feedback text); an UNCHANGED round is free ('revision'). An escalation -> Stuck decision
        (ticket_creator wrote its page)."""
        reason = hand.result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return "failed", "the %s's revise hand-in has no \"reason\"" % role
        self.check_time()
        out = {}
        ticket_creator.revise(self.cfg, self.pool, self.id, role, reason, cur=self.pending, outcome=out)
        kind = out.get("kind")
        if kind == "revised":
            return "revised", None
        if kind == "unchanged":
            self._free = "revision"
            return "unchanged", "the %s asked to revise the ticket; the opener kept it as written: %s" % (
                role, out.get("note") or "(no note)")
        if kind == "escalated":
            raise Stuck("decision", "the %s asked to revise the ticket and the opener asked the main "
                        "session: %s" % (role, out.get("note")), page=False)
        return "failed", ("the %s asked to revise the ticket; the revision failed: %s; the ticket "
                          "stands as written" % (role, out.get("why") or "the opener gave no outcome"))

    def merge_unless_revised(self, g, t, costs):
        """Right before the merge into the target: a work-changing revision ends the round
        (its feedback), else integrate()."""
        if brief_revised(self.ticket()):
            return self.revised_mid_round([], costs)
        return self.integrate(g, t)

    def revision_seen(self):
        """The watch finish() calls between slices: the ticket file in the local pool checkout
        has a work-changing revision this round (a snapshot syncs, too heavy every poll). An
        unreadable file counts as no revision yet."""
        try:
            t = osx.read_json(os.path.join(self.pool.path, "tickets", "%s.json" % self.id), None)
        except osx.OSFailure:
            return False
        return isinstance(t, dict) and brief_revised(t)

    def revised_mid_round(self, pendings, costs):
        """End the round on a work-changing revision: every agent of `pendings` not collected yet
        is collected when it already exited (its hand-in unused) and stopped when it still runs;
        their rows go into costs. The round is free ('revision'); returns its feedback text."""
        for p in pendings:
            if p is None or p.hand is not None:
                continue
            if p.proc is not None and p.proc.poll() is not None:
                costs.append(p.finish().cost)
            else:
                row = p.stop()
                if row is not None:
                    costs.append(row)
        self.pending = []
        self._free = "revision"
        if cases_thrown_out(self.ticket()):
            self.save(lambda x: x.__setitem__("cases", None), "cases thrown out")
            return THROWN_OUT
        return REVISED_MID_ROUND

    def rebase_round(self, t, r):
        """Before a round's agents (D-098): t/<N>-cases and t/<N> are put on the target's current
        head with only the ticket's own commits replayed. Uncommitted tracked changes in their
        worktrees become a devloop commit first. Records branch_base and a 'rebased' event per
        branch that moved. Returns (ticket, conflicts): a branch whose replay stopped on a conflict
        is kept under refs/devloop/before-rebase/ and set to its start point, with one 'rebase
        conflict' event (a cases conflict also throws the cases out); conflicts is the feedback
        text per such branch. Any other replay failure puts the branches back -> Stuck env."""
        self.owned(self.ticket())
        self.remake_tree(self.wt, self.branch)
        target = self.repo.sha(self.target)
        base = t.get("branch_base") or {}
        olds = [h["target"] for h in t.get("history") or []
                if h.get("event") == "issuer started" and h.get("target")]
        olds = [b for b in dict.fromkeys(olds) if self.repo.sha("refs/heads/" + b)]

        def exclude(branch, *more):
            recorded = [base[branch]] if base.get(branch) and self.repo.sha(base[branch]) else []
            return recorded + olds + [target] + list(more)

        has_cases = self.repo.sha("refs/heads/" + self.cases_branch) is not None
        throwaway = os.path.join(self.cfg.worktree_root, "rebase-t%s-cases" % self.id)
        cwt = None
        if has_cases:
            if worktrees._exists(self.cwt):
                cwt = self.cwt
            else:
                cwt = throwaway
                self.repo.remove_worktree(throwaway)
                self.repo.worktree(throwaway, self.cases_branch)
        trees = [(self.cases_branch, cwt)] if has_cases else []
        trees.append((self.branch, self.wt))
        heads, own, after, starts, conflicts = {}, {}, {}, {}, {}
        try:
            for branch, path in trees:
                g = Git(path)
                for op in ("merge", "rebase", "cherry-pick", "revert"):
                    g.run(op, "--abort", check=False)
                if g.dirty():
                    who = {"GIT_AUTHOR_NAME": "devloop", "GIT_AUTHOR_EMAIL": "devloop@localhost",
                           "GIT_COMMITTER_NAME": "devloop", "GIT_COMMITTER_EMAIL": "devloop@localhost"}
                    g.run("commit", "-q", "-a", "-m", "devloop: uncommitted changes in %s at the "
                          "start of round %d" % (branch, r), env=who)
                heads[branch] = g.sha("HEAD")
            failed, start = None, target
            starts[self.branch] = (self.target, target)
            if has_cases:
                g = Git(cwt)
                own[self.cases_branch] = g.own_commits(heads[self.cases_branch],
                                                       exclude(self.cases_branch))
                ok, why = g.replay(target, own[self.cases_branch])
                after[self.cases_branch] = g.sha("HEAD")
                starts[self.cases_branch] = (self.target, target)
                if not ok and g.conflict:
                    conflicts[self.cases_branch] = why
                elif not ok:
                    failed = why
                elif self.repo.is_ancestor(heads[self.cases_branch], heads[self.branch]):
                    start = after[self.cases_branch]
                    starts[self.branch] = (self.cases_branch, start)
            if failed is None:
                more = [heads[self.cases_branch]] if has_cases else []
                g = Git(self.wt)
                own[self.branch] = g.own_commits(heads[self.branch], exclude(self.branch, *more))
                ok, why = g.replay(start, own[self.branch])
                after[self.branch] = g.sha("HEAD")
                if not ok and g.conflict:
                    conflicts[self.branch] = why
                elif not ok:
                    failed = why
            if failed is not None:
                if has_cases and after.get(self.cases_branch) != heads[self.cases_branch]:
                    self.repo.move_ref(self.cases_branch, heads[self.cases_branch],
                                       after[self.cases_branch])
                    if cwt != throwaway:
                        Git(cwt).run("reset", "--hard", heads[self.cases_branch])
                raise Stuck("env", failed)
            kept = {}
            for branch, path in trees:
                if branch in conflicts:
                    kept[branch] = self.keep_head(branch, heads[branch], r)
                    Git(path).run("reset", "--hard", starts[branch][1])
                    after[branch] = starts[branch][1]
        finally:
            if cwt == throwaway:
                self.repo.remove_worktree(throwaway)

        def mutate(x):
            x["branch_base"] = dict(x.get("branch_base") or {}, **{b: target for b in heads})
            for b in heads:
                if b in conflicts:
                    event(x, "rebase conflict", branch=b, onto=self.target, kept=kept[b])
                elif after[b] != heads[b]:
                    event(x, "rebased", branch=b, onto=self.target, base=target,
                          replayed=len(own[b]))
            if self.cases_branch in conflicts:
                x["cases"] = None
        t = self.save(mutate, "rebased on %s" % self.target)
        texts = []
        if self.cases_branch in conflicts:
            texts.append("merging the latest %s into %s failed (%s) at the start of round %d: the "
                         "verifier's cases are kept at %s and the verifier writes them again "
                         "from %s" % (self.target, self.cases_branch, conflicts[self.cases_branch],
                                      r, kept[self.cases_branch], self.target))
        if self.branch in conflicts:
            name, sha = starts[self.branch]
            texts.append("merging the latest %s into %s failed (%s) at the start of round %d: %s "
                         "now starts at %s (%s) and its earlier commits are kept at %s; replay "
                         "them with `git cherry-pick %s` (oldest first) and resolve the conflicts"
                         % (self.target, self.branch, conflicts[self.branch], r, self.branch, name,
                            sha[:12], kept[self.branch], " ".join(c[:12] for c in own[self.branch])))
        return t, texts

    def keep_head(self, branch, sha, r):
        """Create refs/devloop/before-rebase/t<N>[-cases]-r<r> (first free '-2', '-3', ...) at
        `sha`; returns the ref."""
        name = "refs/devloop/before-rebase/t%s%s-r%d" % (
            self.id, "-cases" if branch == self.cases_branch else "", r)
        for n in range(1, 100):
            ref = name if n == 1 else "%s-%d" % (name, n)
            if self.repo.run("update-ref", ref, sha, "", check=False).ok:
                return ref
        raise Stuck("env", "could not create a ref under %s" % name)

    def blocked_check(self, work, text, ids):
        """A blocked worker named failing test ids: run them on the ticket branch. Green -> the
        round is free (agent_env), the next worker starts on the next command of the chain, and
        the feedback text is returned. Otherwise (no valid ids, or not green) -> Stuck."""
        if not (isinstance(ids, list) and ids and all(isinstance(x, str) for x in ids)):
            raise Stuck("decision", text)
        self.check_time()
        slot = self.test_slot()
        try:
            out = testrun.run(self.cfg, self.wt, "run", ids, timeout=self.left(),
                              keep_dir=os.path.join(self.cfg.runs_dir(self.id), "blocked-check"))
        finally:
            slot.release()
        if out.status != testrun.GREEN:
            raise Stuck("decision", text + "\ndevloop ran the ids the worker named: " + out.summary())
        self._free = "agent_env"
        size = len(self.cfg.agent_chain(work.chain_role or work.role))
        self.next_worker = min(work.index + 1, size - 1)
        return ("the worker handed in blocked (%s), but the ids it named (%s) passed when devloop "
                "ran them on %s: the block was the agent's own environment, not the ticket; "
                "continue the work" % (text, " ".join(ids), self.branch))

    def integrate(self, g, t):
        """Take in the latest target, re-test if it changed anything, move the target."""
        for _ in range(8):
            old = self.repo.sha(self.target)
            if not g.is_ancestor(old, "HEAD"):
                self.owned(self.ticket())
                ok, why = g.merge(self.target)
                if not ok:
                    self._free = "merge"
                    return ("merging the latest %s into %s failed (%s) - another ticket of "
                            "this group changed the same files; the next round starts on the "
                            "latest %s and replays this ticket's own commits on it" % (
                                self.target, self.branch, why, self.target))
                failed = self.run_cases(t)
                if failed:
                    return "after taking in %s: %s" % (self.target, failed)
            self.owned(self.ticket())
            if self.repo.move_ref(self.target, g.sha("HEAD"), old):
                return None
            time.sleep(1)
        return "%s kept moving while merging; try again" % self.target

    def cases_changed(self, g, cases_branch):
        """What the ticket branch changed in the verifier's acceptance cases, or None."""
        mark = case_mark(self.id)
        mine = case_definitions(g, g.sha("HEAD"), mark)
        theirs = case_definitions(self.repo, self.repo.sha(cases_branch), mark)
        keys = sorted((k for k in set(mine) | set(theirs) if mine.get(k) != theirs.get(k)),
                      key=lambda k: (k[0], k[1] or ""))
        if not keys:
            return None
        return ("this branch changes the verifier's acceptance cases (%s): %s; put them back as "
                "they are on %s, or hand in revise if the cases are wrong"
                % (mark, ", ".join("%s %s" % (p, n or "(does not parse)") for p, n in keys[:10]),
                   cases_branch))

    def run_cases(self, t):
        ids = (t.get("cases") or {}).get("run") or []
        if not ids:
            if t.get("needs_verifier", True):
                return ("the ticket needs a verifier and has no acceptance cases; it is not "
                        "merged untested")
            return None
        self.check_time()
        slot = self.test_slot()
        try:
            out = testrun.run(self.cfg, self.wt, "run", ids, timeout=self.left(),
                              keep_dir=os.path.join(self.cfg.runs_dir(self.id), "cases"))
        finally:
            slot.release()
        if out.status == testrun.GREEN:
            mark = case_mark(self.id)
            mine = [r for r in out.rows if mark in str(r.get("id"))]
            if not mine:
                return "verifier cases: no acceptance case named %s ran" % mark
            bad = ["%s (%s)" % (r.get("id"), r.get("status")) for r in mine if r.get("status") != "pass"]
            if bad:
                return "verifier cases: these cases did not pass: %s" % ", ".join(bad[:10])
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
        try:
            worktrees.detached_tree(self.cfg, self.repo, tree, self.repo.sha(self.cases_branch))
        except worktrees.SetupFailed as exc:
            raise Stuck("env", str(exc))
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
        other = ["%s (%s)" % (r.get("id"), r.get("status")) for r in mine if r.get("status") != "fail"]
        if other:
            return ("these cases did not fail on the code before the change (each must fail by its "
                    "assertion, not skip or be an expected failure): %s" % ", ".join(other[:10]))
        return None

    def hard_block(self, g):
        main = self.cfg.main_branch
        own = g.sha(main) is not None
        files = g.changed_files(self.target)
        if own:
            from_main = set(g.changed_files(main))
            files = [f for f in files if f in from_main]
        hits = [f for f in files if any(f == p or f.startswith(p.rstrip("/") + "/")
                                        for p in self.cfg.hard_paths)]
        if self.cfg.hard_patterns:
            added = g.added_lines(self.target)
            if own:
                from_main = set(g.added_lines(main))
                added = [line for line in added if line in from_main]
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
        values = {
            "ticket_id": self.id, "ticket_json": json.dumps(_brief(t), indent=2, ensure_ascii=False),
            "round": r, "max_rounds": self.cfg.max_rounds, "branch": self.branch,
            "cases_branch": self.cases_branch, "target": self.target, "worktree": cwd,
            "cases_run": " ".join((t.get("cases") or {}).get("run") or []) or "(not written yet)",
            "feedback": "\n".join(t.get("feedback") or []) or "(first round)",
            "result_path": os.path.join(run_dir, "result.json"),
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
            "memory": memory.for_prompt(self.cfg, role, agents.model_of(self.cfg.agent(chain))),
            "project_memory": memory.project_path(self.cfg),
        }
        timeout = min(self.cfg.agent_timeout_seconds, self.left())
        first = 0
        if role == "worker":
            first, self.next_worker = self.next_worker, 0
        p = agents.start(self.cfg, role, agents.render(template, values), cwd, run_dir, timeout,
                         extra_env={"DEVLOOP_TICKET": self.id, "AC_CODE_ROOT": cwd},
                         effort=t.get("effort") if role == "worker" else None, round_no=r,
                         chain_role=chain, on_quota=self.on_quota, first=first,
                         before_spawn=lambda: self.remake_tree(cwd, self.cases_branch if cwd == self.cwt
                                                               else self.branch))
        self.pending.append(p)
        self.started_agents.append(p)
        return p

    def remake_tree(self, path, branch):
        """Right before an agent command starts: a worktree that vanished is made again from its
        branch (uncommitted work in it is lost); when it cannot be, the ticket is stuck (env)."""
        if worktrees._exists(path):
            return
        if self.repo.sha("refs/heads/" + branch) is None:
            raise Stuck("env", "worktree missing: %s (branch %s does not exist)" % (path, branch))
        try:
            worktrees.branch_tree(self.cfg, self.repo, path, branch)
        except osx.OSFailure as exc:
            raise Stuck("env", "worktree missing: %s (making it again from %s failed: %s)"
                        % (path, branch, exc))
        self.save(lambda t: event(t, "worktree made again", path=path, branch=branch),
                  "worktree made again")

    def on_quota(self, phase, row=None, until=None, text=None):
        """An agent's last command hit the usage limit (phase "wait") or its wait is over
        ("over"). The run's row goes straight into the ticket's cost - not into round_costs, so
        a merge or an interrupt does not count it again."""
        if phase == "wait":
            mark = {"reason": "quota", "since": osx.now(),
                    "until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until)),
                    "text": (text or "")[:200]}

            def mutate(t):
                if row is not None:       # None: every command was cooling, nothing ran
                    t.setdefault("cost", []).append(row)
                t["waiting"] = mark
                event(t, "waiting for quota", until=mark["until"], **({"role": row.get("role")} if row else {}))
            self.save(mutate, "waiting for quota")
        else:
            self.save(lambda t: (t.pop("waiting", None), event(t, "quota wait over")),
                      "quota wait over")

    @property
    def deadline(self):
        """The deadline set (started + issuer_timeout_seconds) plus every second this issuer's
        agents and test runs spent waiting for a machine slot."""
        return self._deadline + self.test_waited + sum(p.waited for p in self.started_agents)

    @deadline.setter
    def deadline(self, value):
        self._deadline = value

    def left(self):
        return max(1.0, self.deadline - time.time())

    def test_slot(self):
        """A test run (core/slots.take_test_run: a machine slot, and with devloop.regression_exclusive
        no regression holding the machine), waiting as long as it takes; the wait is added to the
        issuer's deadline."""
        began = time.time()
        slot = slots.take_test_run(self.cfg, None)
        self.test_waited += time.time() - began
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

    def stuck(self, reason, text, page=True):
        def mutate(t):
            t["state"], t["run"] = "stuck", None
            t.pop("waiting", None)
            t["stuck"] = {"reason": reason, "text": text, "at": osx.now()}
            event(t, "stuck", reason=reason)
        self.save(mutate, "stuck (%s)" % reason)
        if page:   # not when the worker's revise escalated in this round: that page is the page
            page_text = "#%s is stuck (%s): %s" % (self.id, reason, text)
            snap = self.pool.snapshot()
            gid = (snap.ticket(self.id) or {}).get("group")
            if gid in snap.groups and snap.groups[gid].get("state") == "open" and chain.stuck_holds(snap, self.id, gid):
                page_text += "\n%s waits for this decision; groups after it in the chain: %s" % (
                    gid, ", ".join(chain.behind(snap, gid)) or "none")
            self.pool.report("env" if reason == "env" else "decision", self.id, page_text, **self.stuck_ask(reason))

    def stuck_ask(self, reason):
        i = self.id
        cancel = pages.option("cancel", "#%s is cancelled; tickets that depend on it keep waiting" % i)
        if reason == "hard_block":
            return pages.ask(
                "#%s's change touches hard_block: change the ticket, or cancel it?" % i,
                [pages.option("revise", "the opener changes #%s per your words so its change avoids hard_block; it goes back to ready at round 0" % i),
                 cancel,
                 pages.option("retry", "#%s runs again as written and stops at the same place unless hard_block changed" % i)],
                "the change the ticket asks for touches hard_block, so a retry stops at the same place")
        why = {"rounds": "a retry keeps the ticket as written; change it only when the last feedback shows the ticket itself is wrong",
               "timeout": "running out of time says nothing about the ticket's text",
               "target": "a retry puts #%s into a new integration branch" % i,
               "env": "an environment failure says nothing about the ticket; fix the environment first"}.get(
                   reason, "an unexpected failure says nothing about the ticket's text")
        return pages.ask(
            "#%s is stuck (%s): try again, change the ticket, or cancel it?" % (i, reason),
            [pages.option("retry", "#%s goes back to ready at round 0 as written" % i),
             pages.option("revise", "the opener changes #%s per your words; it goes back to ready at round 0" % i),
             cancel],
            why)

    def release(self, why, costs=None):
        def mutate(t):
            if costs:
                t.setdefault("cost", []).extend(costs)
            t.pop("waiting", None)
            if t.get("state") == "running":
                t["state"], t["run"] = "ready", None
                t["round"] = max(0, int(t.get("round") or 0) - 1)
                event(t, "interrupted", why=why)
        self.save(mutate, "interrupted")

    def interrupted_costs(self):
        """Stop the running agents and return every cost row of the interrupted round: the rows
        already collected plus one per run (stopped -> outcome "interrupted"). A run whose
        finish() returned just before the signal, before its row reached round_costs, still
        counts once."""
        rows = list(self.round_costs)
        for p in self.pending:
            row = p.stop()
            if row is None and p.hand is not None:
                row = p.hand.cost
                if any(c is row for c in rows):
                    continue
            if row is not None:
                rows.append(row)
        self.pending = []
        self.round_costs = []
        return rows


def rounds_text(t):
    """The 'rounds' stuck text: rounds used, split into real reds and free rounds, last feedback."""
    r, free = int(t.get("round") or 0), free_rounds(t)
    m, v, a = free["merge"], free["revision"], free["agent_env"]
    return ("%d rounds used and still not passing (%d real red, %d free: %d merge conflict, "
            "%d revision%s); last feedback:\n%s" % (
                r, r - m - v - a, m + v + a, m, v, ", %d agent environment" % a if a else "",
                "\n".join((t.get("feedback") or [])[-1:])))


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


def case_definitions(git, rev, mark):
    """{(path, name): [ast.dump, ...]} of every class / function whose name contains mark, in the
    .py files at rev (a sha) that contain mark; a file that does not parse is (path, None):
    ["SyntaxError ..."]. Line numbers, comments and order of definitions do not count."""
    res = git.run("grep", "-l", "-F", mark, rev, "--", "*.py", check=False)
    if res.rc == 1:
        return {}
    if not res.ok:
        raise GitError("git grep failed at %s: %s" % (rev, (res.err or res.out).strip()))
    found = {}
    for line in res.out.splitlines():
        path = line[len(rev) + 1:] if line.startswith(rev + ":") else line
        if not path:
            continue
        source = git.run("show", "%s:%s" % (rev, path)).out
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            found[(path, None)] = ["SyntaxError: %s" % exc]
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and mark in node.name:
                found.setdefault((path, node.name), []).append(ast.dump(node))
    return found


BRIEF_KEYS = ("id", "title", "objective", "acceptance", "outline", "test_plan", "needs_verifier",
              "complexity", "effort", "notes")


def _brief(t):
    return {k: t[k] for k in BRIEF_KEYS if k in t}


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
        lock = ticketlock.take(cfg, args.id)    # held until main returns; the kernel frees it on exit
        if lock is None:
            print("ticket_issuer: #%s already has a live issuer: lock %s is held; nothing done"
                  % (args.id, ticketlock.path(cfg, args.id)), file=sys.stderr)
            return 2
        issuer = Issuer(cfg, pool_for(cfg), args.id, args.target)
    except osx.OSFailure as exc:
        print("ticket_issuer: %s" % exc, file=sys.stderr)
        return 2
    try:
        return _go(issuer)
    finally:
        inflight.drop(cfg, "issuer-%s" % issuer.id)


def _go(issuer):
    try:
        return _outcome(issuer)
    except NotOwned as exc:
        _safe(issuer.stop_agents)
        print("ticket_issuer: %s; stopped without writing" % exc, file=sys.stderr)
        return 2


def _outcome(issuer):
    try:
        return issuer.go()
    except Stuck as exc:
        issuer.stop_agents()
        _safe(lambda: issuer.stuck(exc.reason, exc.text, exc.page))
        print("ticket_issuer: #%s stuck (%s): %s" % (issuer.id, exc.reason, exc.text), file=sys.stderr)
        return 1
    except Interrupted as exc:
        # A second signal must not break the stop and the pool write that records the costs.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        rows = []
        try:
            rows = issuer.interrupted_costs()
        except Exception as err:  # still stop the rest and release the ticket
            print("ticket_issuer: could not record every agent's cost: %s" % err, file=sys.stderr)
            _safe(issuer.stop_agents)
        _safe(lambda: issuer.release(str(exc), rows))
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
    except NotOwned:
        raise
    except Exception as exc:  # the pool itself may be what failed; say so and go on exiting
        print("ticket_issuer: could not record the outcome: %s" % exc, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
