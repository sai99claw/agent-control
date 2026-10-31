#!/usr/bin/env python3
"""auto_fixer --group gK --report regression.json : fix a red integration branch, on that branch.

Started only by ticket_controller, once per fix round (the controller counts rounds and re-runs
regression afterwards). Two kinds of red:

  * main moved: the branch cannot fast-forward main any more. First a plain `git merge main`;
    only a conflict needs an agent.
  * tests red: a fixer agent gets the red list, the group's tickets and what each changed, and
    commits fixes on the integration branch. If two tickets contradict each other it calls
    ticket_creator.py --revise (in its prompt), like a worker would.

Exit: 0 something was committed (verify again) · 1 nothing usable came back · 3 the fixer says
only the user can decide (already reported) · 2 refused.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, memory, osx  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.gitx import Git  # noqa: E402

LOOP = os.path.dirname(os.path.abspath(__file__))


def fix(cfg, pool, gid, report):
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
                            "acceptance": t.get("acceptance")})
    values = {
        "group": gid, "branch": branch, "worktree": tree, "main": main,
        "main_moved": "yes - merge %s into this branch and resolve the conflicts" % main
                      if main_moved and not g.is_ancestor(main, "HEAD") else "no",
        "red": report.get("summary") or "(no red list)",
        "tickets_json": json.dumps(members, indent=2, ensure_ascii=False),
        "diffstat": g.out("diff", "--stat", "%s...HEAD" % main) or "(none)",
        "result_path": os.path.join(run_dir, "result.json"),
        "revise_cmd": "python3 %s --revise <ticket id> --from fixer --reason '<why>'" % (
            os.path.join(LOOP, "ticket_creator.py")),
        "test_cmd": "python3 %s <ids>" % os.path.join(LOOP, "run_tests.py"),
        "memory": memory.for_prompt(snap, "fixer", agents._model(cfg.agent("fixer"))),
        "project_memory": os.path.join(pool.path, memory.rel("project", "project")),
    }
    hand = agents.run(cfg, "fixer", agents.render("fixer.md", values), tree, run_dir,
                      cfg.agent_timeout_seconds, extra_env={"AC_CODE_ROOT": tree, "DEVLOOP_GROUP": gid})
    _record_cost(pool, gid, hand.cost)
    memory.collect(pool, hand, "group %s fix r%d" % (gid, rnd))
    if hand.kind == "blocked":
        pool.report("decision", gid, "group %s: the fixer needs your decision: %s" % (
            gid, (hand.result or {}).get("text")))
        return 3
    if g.dirty():
        return 1
    return 0 if g.sha("HEAD") != before else 1


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
    try:
        cfg = Config()
        report = osx.read_json(args.report, {}) or {}
        return fix(cfg, pool_for(cfg), args.group, report)
    except osx.OSFailure as exc:
        print("auto_fixer: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
