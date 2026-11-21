#!/usr/bin/env python3
"""regression --branch int/gK --out result.json : the one big verification of an integration branch.

Started only by ticket_controller. Runs the project's `preflight`, then `quick` (optional
`tests.quick`; undeclared it is a built-in no-op), then `full`, on a throwaway worktree at the
branch head. A red, env or unmeasured quick ends the regression there (summary "quick: …") and
full does not run. An unusable environment is not red: it is reported as "env" and nobody is sent
to fix code for it.

Holds one machine slot (devloop.machine_slots) from before preflight until full has ended, or with
devloop.regression_exclusive the whole machine (the gate and every slot); the wait, quick and full
all count against regression_timeout_seconds.

A red quick or full with 1 to RERUN_AT_MOST (5) failing rows gets one second look: exactly those
ids run once more with the project's `run <ids>` (one call, same worktree, same slot, within the
time left; logs in <logs>/<stage>-rerun). A failing id whose rerun answered (rc 0 or 1) with every
row of it or nested under it ("<id>." prefix) passing, at least one, is flaky: it no longer counts
as red. All flaky: the stage is green (a flaky quick goes on to full). Some still red: red with only
those. 6 or more failing rows, or a rerun that cannot answer (rc 86, 2): no change. The rerun is the
gate's one second look, not a fix: no agent hides a flaky test with sleeps or retries in the test.

Exit: 0 green · 1 red · 86 environment not usable · 2 could not measure. The same outcome is
written to --out as {"status", "sha", "summary", "failures", "flaky"}: failures are the first run's
rows of the still-red cases (the summary names only those), flaky is [{"id", "message"}, ...] of the
cases that passed on a rerun (quick's first, then full's; message from the first run, null when it
had none), [] when none did.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import inflight, osx, slots, testrun, worktrees  # noqa: E402
from core.config import Config  # noqa: E402
from core.gitx import Git  # noqa: E402

RERUN_AT_MOST = 5
CODES = {testrun.GREEN: 0, testrun.RED: 1, testrun.ENV: 86, testrun.UNMEASURED: 2}


def live_name(branch):
    return "regression-" + branch.replace("/", "-")


def put_live(cfg, branch, sha, out_path, stage, stamp=None):
    """Write (or rewrite) this regression's live record; `since` is when `stage` began."""
    inflight.put(cfg, live_name(branch), {
        "kind": "regression", "stamp": stamp or osx.stamp(),
        "group": branch[len("int/"):] if branch.startswith("int/") else branch,
        "branch": branch, "sha": sha, "exclusive": bool(cfg.regression_exclusive),
        "result": out_path, "stage": stage, "since": osx.now()})


def verify(cfg, branch, out_path):
    repo = Git(cfg.root)
    sha = repo.sha(branch)
    if not sha:
        raise osx.OSFailure("branch %s does not exist" % branch)
    began = time.time()
    if slots.needed(cfg):
        stage = "machine" if cfg.regression_exclusive else "slot"
    else:
        stage = "preflight"
    put_live(cfg, branch, sha, out_path, stage)
    take, busy = ((slots.take_machine, slots.machine_busy) if cfg.regression_exclusive
                  else (slots.take, slots.busy))
    slot = take(cfg, began + cfg.regression_timeout_seconds)
    if slot is None:
        osx.write_json(out_path, {"status": testrun.UNMEASURED, "sha": sha,
                                  "summary": "could not measure: " + busy(cfg, time.time() - began),
                                  "failures": [], "flaky": [], "at": osx.now()})
        return CODES[testrun.UNMEASURED]
    try:
        return _verify(cfg, repo, branch, sha, out_path, began + cfg.regression_timeout_seconds)
    finally:
        slot.release()


def passed_again(ident, rows):
    """True when the rerun `rows` hold at least one row for `ident` or nested under it, all "pass"."""
    mine = [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)
            and (r["id"] == ident or r["id"].startswith(ident + "."))]
    return bool(mine) and all(r.get("status") == "pass" for r in mine)


def second_look(cfg, tree, logs, stage, outcome, deadline):
    """Rerun a red `stage`'s 1..RERUN_AT_MOST failing ids once; returns (outcome, flaky) where
    outcome keeps only the still-red first-run rows (GREEN when none) and flaky lists the rest."""
    ids = [r.get("id") for r in outcome.failures]
    if (outcome.status != testrun.RED or not 1 <= len(ids) <= RERUN_AT_MOST
            or not all(isinstance(i, str) and i for i in ids)):
        return outcome, []
    again = testrun.run(cfg, tree, "run", ids, timeout=max(1, deadline - time.time()),
                        keep_dir=os.path.join(logs, stage + "-rerun"))
    if again.status not in (testrun.GREEN, testrun.RED):   # rc 86 / 2 / timeout: no answer
        return outcome, []
    red = [r for r in outcome.failures if not passed_again(r["id"], again.rows)]
    flaky = [{"id": r["id"], "message": r.get("message")} for r in outcome.failures if r not in red]
    if not flaky:
        return outcome, []
    status = testrun.RED if red else testrun.GREEN
    return testrun.Outcome(status, failures=red, log_dir=outcome.log_dir, rows=outcome.rows), flaky


def _verify(cfg, repo, branch, sha, out_path, deadline):
    tree = os.path.join(cfg.worktree_root, "regression-" + branch.replace("/", "-"))
    try:
        worktrees.detached_tree(cfg, repo, tree, sha)
    except worktrees.SetupFailed as exc:
        osx.write_json(out_path, {"status": testrun.ENV, "sha": sha, "summary": str(exc),
                                  "failures": [], "flaky": [], "at": osx.now()})
        return CODES[testrun.ENV]
    logs = cfg.runs_dir("regression", branch.replace("/", "-"))
    summary, flaky = None, []
    try:
        put_live(cfg, branch, sha, out_path, "preflight")
        result = testrun.run(cfg, tree, "preflight", timeout=600,
                             keep_dir=os.path.join(logs, "preflight"))
        if result.status == testrun.RED:   # preflight has no "red": treat as environment
            result = testrun.Outcome(testrun.ENV, detail="preflight failed", log_dir=result.log_dir)
        if result.status == testrun.GREEN:
            put_live(cfg, branch, sha, out_path, "quick")
            result = testrun.run(cfg, tree, "quick", timeout=max(1, deadline - time.time()),
                                 keep_dir=os.path.join(logs, "quick"))
            result, flaky = second_look(cfg, tree, logs, "quick", result, deadline)
            if result.status != testrun.GREEN:   # a red quick ends it before the full suite
                summary = "quick: " + result.summary()
            else:
                put_live(cfg, branch, sha, out_path, "full")
                result = testrun.run(cfg, tree, "full", timeout=max(1, deadline - time.time()),
                                     keep_dir=os.path.join(logs, "full"))
                result, more = second_look(cfg, tree, logs, "full", result, deadline)
                flaky += more
    finally:
        repo.remove_worktree(tree)
    osx.write_json(out_path, {"status": result.status, "sha": sha,
                              "summary": result.summary() if summary is None else summary,
                              "failures": result.failures, "flaky": flaky, "logs": result.log_dir,
                              "at": osx.now()})
    return CODES[result.status]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--branch", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    cfg = None
    try:
        cfg = Config()
        return verify(cfg, args.branch, args.out)
    except osx.OSFailure as exc:
        print("regression: %s" % exc, file=sys.stderr)
        try:
            osx.write_json(args.out, {"status": testrun.UNMEASURED, "summary": str(exc), "at": osx.now()})
        except osx.OSFailure:
            pass
        return 2
    finally:
        if cfg is not None:
            inflight.drop(cfg, live_name(args.branch))


if __name__ == "__main__":
    sys.exit(main())
