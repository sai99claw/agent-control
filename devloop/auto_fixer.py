#!/usr/bin/env python3
"""auto_fixer --group gK --report regression.json : fix a red integration branch, on that branch.

Started only by ticket_controller, once per fix round (the controller counts rounds and re-runs
regression afterwards). Two kinds of red:

  * main moved: the branch cannot fast-forward main any more. First a plain `git merge main`;
    only a conflict needs an agent.
  * tests red: a fixer agent gets the red list, the group's tickets and what each changed, and
    commits fixes on the integration branch. It fixes by the tickets' current content: a merged
    ticket is frozen and cannot be revised. A verifier's case T<id>Acceptance may be changed only
    to match that ticket's current acceptance text, never looser than the ticket; every changed
    case is listed in the hand-in's `cases_changed` (ticket, case, the acceptance item it now
    matches) and appended, with the fix round, to the group's `cases_changed`. When two tickets of
    the group contradict each other the fixer hands in kind `decision` (where they contradict,
    which new ticket it proposes); that becomes a decision page and the group stops (exit 3).
    Kind `blocked` is handled the same way.

Exit: 0 something was committed (verify again) · 1 nothing usable came back · 3 the fixer handed
in `decision` / `blocked`: only the user can decide (already reported as a decision page) · 2 refused · 4 interrupted by SIGTERM/SIGINT (the
running fixer is stopped and its cost recorded with outcome "interrupted").
"""

import argparse
import json
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, memory, osx  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.gitx import Git  # noqa: E402

LOOP = os.path.dirname(os.path.abspath(__file__))


def fix(cfg, pool, gid, report, live=None):
    live = [] if live is None else live
    snap = pool.snapshot()
    group = snap.groups.get(gid)
    if not group:
        raise osx.OSFailure("no group %s" % gid)
    branch = group["branch"]
    repo = Git(cfg.root)
    tree = os.path.join(cfg.worktree_root, "fix-" + gid)
    repo.worktree(tree, branch)
    g = Git(tree)
    before = g.sha("HEAD")
    main = cfg.main_branch
    main_moved = not g.is_ancestor(main, "HEAD")
    if main_moved and g.merge(main)[0] and report.get("status") != "red":
        return 0                       # main taken in cleanly; regression decides the rest

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
        "tickets_json": json.dumps(members, indent=2, ensure_ascii=False),
        "diffstat": g.out("diff", "--stat", "%s...HEAD" % main) or "(none)",
        "result_path": os.path.join(run_dir, "result.json"),
        "index": os.path.join(tree, cfg.index_path) if cfg.index_path else "(no code index)",
        "test_cmd": "python3 %s <ids>" % os.path.join(LOOP, "run_tests.py"),
        "memory": memory.for_prompt(cfg, "fixer", agents._model(cfg.agent("fixer"))),
        "project_memory": memory.project_path(cfg),
    }
    pending = agents.start(cfg, "fixer", agents.render("fixer.md", values), tree, run_dir,
                           cfg.agent_timeout_seconds, extra_env={"AC_CODE_ROOT": tree, "DEVLOOP_GROUP": gid})
    live.append(pending)
    hand = pending.finish()
    live.clear()
    _record_cost(pool, gid, hand.cost)
    memory.collect(pool, hand, "group %s fix r%d" % (gid, rnd))
    if hand.kind in ("decision", "blocked"):
        pool.report("decision", gid, "group %s: the fixer needs your decision: %s" % (
            gid, (hand.result or {}).get("text")))
        return 3
    if hand.kind == "done":
        _record_cases(pool, gid, rnd, (hand.result or {}).get("cases_changed"))
    if g.dirty():
        return 1
    return 0 if g.sha("HEAD") != before else 1


def case_mark(ident):
    """The verifier's case mark of ticket `ident` (ticket_issuer.case_mark's convention)."""
    return "T%sAcceptance" % ident


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
    try:
        cfg = Config()
        report = osx.read_json(args.report, {}) or {}
        pool = pool_for(cfg)
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
        return 4
    except osx.OSFailure as exc:
        print("auto_fixer: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
