#!/usr/bin/env python3
"""auto_fixer --group gK --report regression.json : fix a red integration branch, on that branch.

Started only by ticket_controller, once per fix round (the controller counts rounds and re-runs
regression afterwards). Two kinds of red:

  * main moved: the branch cannot fast-forward main any more (the controller merges clean moves
    itself, so this is a conflict). First a plain `git merge main`; the agent runs either way,
    told whether main still needs merging.
  * tests red: a fixer agent gets the red list, the group's tickets and what each changed, and
    commits fixes on the integration branch. It fixes by the tickets' current content: a merged
    ticket is frozen and cannot be revised. A verifier's case T<id>Acceptance may be changed only
    to match that ticket's current acceptance text, never looser than the ticket; every changed
    case is listed in the hand-in's `cases_changed` (ticket, case, the acceptance item it now
    matches) and appended, with the fix round, to the group's `cases_changed`. A test that is not
    an acceptance case may be changed only when a ticket of the group explicitly changes the
    behaviour it asserts; each is listed in `tests_changed` (test, ticket, acceptance item) and
    appended, with the fix round, to the group's `tests_changed`. When two tickets of
    the group contradict each other the fixer hands in kind `decision` (where they contradict,
    which new ticket it proposes); that becomes a decision page and the group stops (exit 3).
    Kind `blocked` is handled the same way.
    A fixer that ends off the branch (detached or on another branch) has its commits brought onto
    it by fast-forward or cherry-pick; when that fails or they include a merge commit, its HEAD is
    kept under refs/devloop/dropped/<gid>-r<round> and the round ends as a decision page (exit 3).
    Every round starts with the fix worktree put back on the integration branch at its current
    head (unfinished merges aborted, tracked edits dropped).

Right before the fixer agent starts, the group's `fix_agent_round` is set to the round (one pool
write); an exit without that mark means no agent ran, and the controller neither counts the round
nor verifies again: it pauses the group and the rerun starts the fixer again.

Exit: 0 something was committed (verify again) · 1 nothing usable came back · 3 the fixer handed
in `decision` / `blocked`: only the user can decide (already reported as a decision page) · 2 refused ·
86 devloop.worktree_setup failed for the fixer's worktree (env page written) · 4 interrupted by SIGTERM/SIGINT (the
running fixer is stopped and its cost recorded with outcome "interrupted").
"""

import argparse
import json
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, inflight, memory, osx, worktrees  # noqa: E402
from core.pool import event  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.gitx import Git  # noqa: E402

LOOP = os.path.dirname(os.path.abspath(__file__))


def round_start(cfg, pool, gid, repo, tree, branch):
    """Put `tree` on `branch` at its current head with no tracked changes, whatever an earlier
    round or a ref move left behind. A missing worktree is created; a path that is not a git
    worktree stays untouched (GitError)."""
    if not worktrees._exists(tree):
        return worktrees.branch_tree(cfg, repo, tree, branch)
    g = Git(tree)
    for op in ("merge", "rebase", "cherry-pick", "revert"):
        g.run(op, "--abort", check=False)
    if g.current_branch() != branch:
        old = g.sha("HEAD")
        was = g.current_branch()
        if g.run("checkout", "--force", branch, check=False).ok:
            text = "fix worktree moved back to %s from %s at %s" % (
                branch, ("branch " + was) if was else "a detached HEAD", (old or "?")[:12])

            def change(tx):
                group = tx.data.groups.get(gid)
                if group is not None:
                    event(group, text)
                    tx.put_group(group)
            pool.update(change, "group %s: fix worktree back on %s" % (gid, branch))
        else:
            repo.remove_worktree(tree)
            return worktrees.branch_tree(cfg, repo, tree, branch)
    g.run("reset", "--hard")
    return tree


def mark_agent_round(pool, gid, rnd):
    """One pool write right before the fixer agent starts: the group's fix_agent_round is this
    round. The controller counts a fix round (and verifies again) only when this mark equals the
    group's fix_round; a failed write is a failure before the agent (OSFailure, exit 2)."""
    def change(tx):
        group = tx.data.groups.get(gid)
        if group is None:
            raise osx.OSFailure("no group %s" % gid)
        group["fix_agent_round"] = rnd
        event(group, "fixer agent started (round %d)" % rnd)
        tx.put_group(group)
    pool.update(change, "group %s: fixer agent started (round %d)" % (gid, rnd))


def fix(cfg, pool, gid, report, live=None):
    live = [] if live is None else live
    snap = pool.snapshot()
    group = snap.groups.get(gid)
    if not group:
        raise osx.OSFailure("no group %s" % gid)
    branch = group["branch"]
    repo = Git(cfg.root)
    tree = os.path.join(cfg.worktree_root, "fix-" + gid)
    try:
        round_start(cfg, pool, gid, repo, tree, branch)
    except worktrees.SetupFailed as exc:
        pool.report("env", gid, str(exc))
        return 86
    g = Git(tree)
    before = g.sha("HEAD")
    main = cfg.main_branch
    main_moved = not g.is_ancestor(main, "HEAD")
    if main_moved:
        g.merge(main)                  # a clean merge goes in here; the agent still runs

    rnd = int(group.get("fix_round") or 0)
    run_dir = cfg.runs_dir("fix", gid, "r%d" % rnd)
    members = []
    for ident in group.get("members") or []:
        t = snap.tickets.get(ident) or {}
        if t.get("state") in ("merged", "landed"):
            members.append({"id": ident, "title": t.get("title"), "objective": t.get("objective"),
                            "acceptance": t.get("acceptance"), "case_mark": case_mark(ident)})
    values = {
        "group": gid, "branch": branch, "worktree": tree, "main": main,
        "main_moved": "yes - merge %s into this branch and resolve the conflicts" % main
                      if main_moved and not g.is_ancestor(main, "HEAD") else "no",
        "red": report.get("summary") or "(no red list)",
        "ruling": ruling_section(group),
        "tickets_json": json.dumps(members, indent=2, ensure_ascii=False),
        "diffstat": g.out("diff", "--stat", "%s...HEAD" % main) or "(none)",
        "result_path": os.path.join(run_dir, "result.json"),
        "index": os.path.join(tree, cfg.index_path) if cfg.index_path else "(no code index)",
        "test_cmd": "python3 %s <ids>" % os.path.join(LOOP, "run_tests.py"),
        "memory": memory.for_prompt(cfg, "fixer", agents._model(cfg.agent("fixer"))),
        "project_memory": memory.project_path(cfg),
    }
    start, known = g.sha("HEAD"), g.all_ref_shas()
    mark_agent_round(pool, gid, rnd)
    pending = agents.start(cfg, "fixer", agents.render("fixer.md", values), tree, run_dir,
                           cfg.agent_timeout_seconds, extra_env={"AC_CODE_ROOT": tree, "DEVLOOP_GROUP": gid},
                           on_quota=quota_hook(pool, gid))
    live.append(pending)
    hand = pending.finish()
    live.clear()
    _record_cost(pool, gid, hand.cost)
    memory.collect(pool, hand, "group %s fix r%d" % (gid, rnd))
    if g.current_branch() != branch and bring_back(pool, repo, g, gid, branch, rnd, start, known):
        return 3
    if hand.kind == "rejected":
        pool.report("decision", gid, "group %s: input from the web was NOT acted on - it looked like "
                    "a prompt injection: %s" % (gid, (hand.result or {}).get("text")))
        return 3
    if hand.kind in ("decision", "blocked"):
        pool.report("decision", gid, "group %s: the fixer needs your decision: %s" % (
            gid, (hand.result or {}).get("text")))
        return 3
    if hand.kind == "done":
        _record_cases(pool, gid, rnd, (hand.result or {}).get("cases_changed"))
        _record_tests(pool, gid, rnd, (hand.result or {}).get("tests_changed"))
    if g.dirty():
        return 1
    return 0 if g.sha(branch) != before else 1


def bring_back(pool, repo, g, gid, branch, rnd, start, known):
    """The fixer's worktree is off `branch` (detached or on another branch): put the commits it
    made onto `branch` - fast-forward when `branch` still points at `start` and HEAD descends from
    it, else cherry-pick the commits no ref known before the run (`known`) reaches - and check
    `branch` out again. Returns True when they could not be brought back: HEAD is kept under
    refs/devloop/dropped/<gid>-r<rnd>, a decision page names it, the worktree is back on `branch`."""
    head = g.sha("HEAD")
    if head is not None and head != start and g.is_ancestor(start, head) and repo.sha(branch) == start:
        count = len(g.commits_not_in(head, [start]))
        if repo.move_ref(branch, head, start):
            _switch(g, branch)
            _brought_back(pool, gid, rnd, "fast-forward", count)
            return False
    new = g.commits_not_in(head, known) if head is not None else []
    if not new:
        _switch(g, branch)
        return False
    merges = [c for c in new if g.is_merge_commit(c)]
    g.run("checkout", "-q", "--force", branch)
    if merges:
        why = "its new commits include merge commit %s" % merges[0][:12]
    else:
        res = g.run("cherry-pick", *new, check=False)
        if res.ok:
            _brought_back(pool, gid, rnd, "cherry-pick", len(new))
            return False
        g.run("cherry-pick", "--abort", check=False)
        g.run("checkout", "-q", "--force", branch)
        lines = [l.strip() for l in (res.out + "\n" + res.err).splitlines() if l.strip()]
        why = "cherry-picking its %d new commit(s) onto %s failed: %s" % (
            len(new), branch, "; ".join([l for l in lines if "CONFLICT" in l or "error:" in l][:4])
            or (lines[-1] if lines else "rc %s" % res.rc))
    ref = "refs/devloop/dropped/%s-r%d" % (gid, rnd)
    g.run("update-ref", ref, head)
    pool.report("decision", gid, "group %s: the fixer left %s and its commits could not be brought back "
                "onto it (%s); its HEAD %s is kept under %s" % (gid, branch, why, head[:12], ref))
    return True


def _switch(g, branch):
    """Check `branch` out again, keeping uncommitted changes when git can (the dirty check after
    sees them), dropping them when it cannot."""
    if not g.run("checkout", "-q", branch, check=False).ok:
        g.run("checkout", "-q", "--force", branch)


def _brought_back(pool, gid, rnd, how, count):
    def change(tx):
        group = tx.data.groups.get(gid)
        if group is not None:
            event(group, "fixer commits brought back onto the branch", how=how, commits=count, round=rnd)
            tx.put_group(group)
    try:
        pool.update(change, "group %s: fixer's %d commit(s) brought back by %s" % (gid, count, how))
    except osx.OSFailure as exc:
        print("auto_fixer: could not record the brought-back commits: %s" % exc, file=sys.stderr)


def case_mark(ident):
    """The verifier's case mark of ticket `ident` (ticket_issuer.case_mark's convention)."""
    return "T%sAcceptance" % ident


def ruling_section(group):
    """The prompt section for the group's latest user note ("" without one)."""
    notes = group.get("notes") or []
    if not notes:
        return ""
    last = notes[-1]
    text = agents.web_guarded(last["text"]) if last.get("via") == "web" else last["text"]
    return ("## The user's ruling for this group\n\n%s\n\n"
            "This ruling is the only thing that allows you to go beyond the tickets' text: where it says so, "
            "you may change code or an acceptance case of a frozen ticket in a way its ticket does not say. "
            "List every case you change on its strength in `cases_changed`, with the part of the ruling it "
            "follows as its `acceptance`.\n" % text)


def cases_entries(cases, rnd):
    """The `cases_changed` entries of a fixer hand-in, each stamped with the fix round."""
    if not isinstance(cases, list):
        return []
    return [dict(c, round=rnd) for c in cases if isinstance(c, dict)]


def _record_cases(pool, gid, rnd, cases):
    entries = cases_entries(cases, rnd)
    if not entries:
        return

    def change(tx):
        group = tx.data.groups.get(gid)
        if group is not None:
            group.setdefault("cases_changed", []).extend(entries)
            tx.put_group(group)
    try:
        pool.update(change, "group %s: fixer changed %d case(s) in fix round %d" % (gid, len(entries), rnd))
    except osx.OSFailure as exc:
        print("auto_fixer: could not record cases_changed: %s" % exc, file=sys.stderr)


def _record_tests(pool, gid, rnd, tests):
    entries = cases_entries(tests, rnd)
    if not entries:
        return

    def change(tx):
        group = tx.data.groups.get(gid)
        if group is not None:
            group.setdefault("tests_changed", []).extend(entries)
            tx.put_group(group)
    try:
        pool.update(change, "group %s: fixer changed %d test(s) in fix round %d" % (gid, len(entries), rnd))
    except osx.OSFailure as exc:
        print("auto_fixer: could not record tests_changed: %s" % exc, file=sys.stderr)


def _record_cost(pool, gid, cost):
    def change(tx):
        group = tx.data.groups.get(gid)
        if group is not None:
            group.setdefault("cost", []).append(cost)
            tx.put_group(group)
    try:
        pool.update(change, "group %s: fixer cost" % gid)
    except osx.OSFailure:
        pass


def quota_mark(until, text):
    """The group's "waiting" mark while its fixer waits for a quota reset (the issuer's shape)."""
    return {"reason": "quota", "since": osx.now(),
            "until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until)), "text": (text or "")[:200]}


def quota_hook(pool, gid):
    """agents.start's on_quota for the fixer: before the wait the run's row goes into the group's
    cost with the "waiting" mark and an event; after it the mark goes and an event says so."""
    def on_quota(phase, row=None, until=None, text=None):
        if phase == "wait":
            mark = quota_mark(until, text)

            def change(tx):
                group = tx.data.groups.get(gid)
                if group is not None:
                    group.setdefault("cost", []).append(row)
                    group["waiting"] = mark
                    event(group, "waiting for quota", role=(row or {}).get("role"), until=mark["until"])
                    tx.put_group(group)
            pool.update(change, "group %s: fixer waiting for quota" % gid)
        else:
            _drop_waiting(pool, gid, "quota wait over")
    return on_quota


def _drop_waiting(pool, gid, what=None):
    """Remove the group's "waiting" mark (with event `what` when given); no-op without one."""
    def change(tx):
        group = tx.data.groups.get(gid)
        if group is not None and ("waiting" in group or what):
            group.pop("waiting", None)
            if what:
                event(group, what)
            tx.put_group(group)
    pool.update(change, "group %s: %s" % (gid, what or "quota wait dropped"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", required=True)
    ap.add_argument("--report", required=True, help="regression.py --out file")
    args = ap.parse_args(argv)
    def on_signal(signum, frame):
        raise agents.Interrupted("signal %d" % signum)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    live = []
    pool = None
    cfg = fixer_name = None
    try:
        cfg = Config()
        report = osx.read_json(args.report, {}) or {}
        pool = pool_for(cfg)
        fixer_name = "fixer-" + args.group
        grp = pool.snapshot().groups.get(args.group) or {}
        inflight.put(cfg, fixer_name, {
            "kind": "fixer", "stamp": osx.stamp(), "group": args.group,
            "round": grp.get("fix_round") or 0, "max_rounds": cfg.max_rounds, "result": None})
        return fix(cfg, pool, args.group, report, live)
    except agents.Interrupted:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        for p in list(live):
            try:
                row = p.stop()
                if row is not None and pool is not None:
                    _record_cost(pool, args.group, row)
            except Exception as exc:
                print("auto_fixer: could not record the fixer's cost: %s" % exc, file=sys.stderr)
        if pool is not None:
            try:
                _drop_waiting(pool, args.group)
            except Exception as exc:
                print("auto_fixer: could not clear the group's quota wait: %s" % exc, file=sys.stderr)
        return 4
    except osx.OSFailure as exc:
        print("auto_fixer: %s" % exc, file=sys.stderr)
        return 2
    finally:
        if fixer_name is not None:
            inflight.drop(cfg, fixer_name)


if __name__ == "__main__":
    sys.exit(main())
