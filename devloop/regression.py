#!/usr/bin/env python3
"""regression --branch int/gK --out result.json : the one big verification of an integration branch.

Started only by ticket_controller. Runs the project's `preflight`, then `full`, on a throwaway
worktree at the branch head. An unusable environment is not red: it is reported as "env" and
nobody is sent to fix code for it.

Holds one machine slot (devloop.machine_slots) from before preflight until full has ended, or with
devloop.regression_exclusive the whole machine (the gate and every slot); the wait counts against
regression_timeout_seconds.

Exit: 0 green · 1 red · 86 environment not usable · 2 could not measure. The same outcome is
written to --out as {"status", "sha", "summary", "failures"}.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import osx, slots, testrun  # noqa: E402
from core.config import Config  # noqa: E402
from core.gitx import Git  # noqa: E402

CODES = {testrun.GREEN: 0, testrun.RED: 1, testrun.ENV: 86, testrun.UNMEASURED: 2}


def verify(cfg, branch, out_path):
    repo = Git(cfg.root)
    sha = repo.sha(branch)
    if not sha:
        raise osx.OSFailure("branch %s does not exist" % branch)
    began = time.time()
    take, busy = ((slots.take_machine, slots.machine_busy) if cfg.regression_exclusive
                  else (slots.take, slots.busy))
    slot = take(cfg, began + cfg.regression_timeout_seconds)
    if slot is None:
        osx.write_json(out_path, {"status": testrun.UNMEASURED, "sha": sha,
                                  "summary": "could not measure: " + busy(cfg, time.time() - began),
                                  "failures": [], "at": osx.now()})
        return CODES[testrun.UNMEASURED]
    try:
        return _verify(cfg, repo, branch, sha, out_path, cfg.regression_timeout_seconds - (time.time() - began))
    finally:
        slot.release()


def _verify(cfg, repo, branch, sha, out_path, full_timeout):
    tree = os.path.join(cfg.worktree_root, "regression-" + branch.replace("/", "-"))
    repo.detached(tree, sha)
    logs = cfg.runs_dir("regression", branch.replace("/", "-"))
    try:
        result = testrun.run(cfg, tree, "preflight", timeout=600,
                             keep_dir=os.path.join(logs, "preflight"))
        if result.status == testrun.GREEN:
            result = testrun.run(cfg, tree, "full", timeout=max(1, full_timeout),
                                 keep_dir=os.path.join(logs, "full"))
        elif result.status == testrun.RED:   # preflight has no "red": treat as environment
            result = testrun.Outcome(testrun.ENV, detail="preflight failed", log_dir=result.log_dir)
    finally:
        repo.remove_worktree(tree)
    osx.write_json(out_path, {"status": result.status, "sha": sha, "summary": result.summary(),
                              "failures": result.failures, "logs": result.log_dir,
                              "at": osx.now()})
    return CODES[result.status]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--branch", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        return verify(Config(), args.branch, args.out)
    except osx.OSFailure as exc:
        print("regression: %s" % exc, file=sys.stderr)
        try:
            osx.write_json(args.out, {"status": testrun.UNMEASURED, "summary": str(exc), "at": osx.now()})
        except osx.OSFailure:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
