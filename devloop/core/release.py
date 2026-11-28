"""release : run the project's release for a group at a sha, the one way devloop does it.

The controller's release job (devloop/release_job.py) and `tickets.py release` both call run():
a throwaway detached worktree at the sha under the worktree root, the controller's environment plus the DEVLOOP_* variables,
and devloop.release_timeout_seconds as the limit. The worktree is removed afterwards, so
anything the release produces goes to DEVLOOP_RELEASE_DIR (<worktrees>/runs/release/<group>/,
created before the command, never emptied or deleted by devloop). With devloop.machine_slots set it
first takes what a regression takes (core/slots: one slot, or the whole machine with
devloop.regression_exclusive), waiting at most devloop.regression_timeout_seconds; the limit counts
from then and the hold is released when run() returns. The whole output (stdout and
stderr interleaved, also what was printed before a timeout) is kept in
<worktrees>/runs/release/<group>.log; each run replaces it. log_path(cfg, gid) names it.
"""

import os
import time

from . import inflight, logtail, osx, slots, worktrees
from .gitx import Git


def log_path(cfg, gid):
    """Absolute path of the kept output of the group's last release run."""
    return os.path.join(os.path.abspath(cfg.runs_dir("release")), gid + ".log")


def env(cfg, group, sha, out_dir=None):
    """The release command's environment."""
    return cfg.env(DEVLOOP_RELEASE_SHA=sha, DEVLOOP_GROUP=group["id"],
                   DEVLOOP_TICKETS=",".join(group.get("members") or []),
                   DEVLOOP_REMOTE=cfg.values.get("remote"),
                   DEVLOOP_RELEASE_DIR=out_dir)


def _write(path, text):
    try:
        with open(path, "w") as f:
            f.write(text)
    except OSError:
        pass


def _read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


FAILURE_TAIL_LINES = 15


def failure_text(log, first):
    """The block of a release-failed page: the first line of run()'s failure text, the last
    stage the release printed and the last lines of its log."""
    stage = logtail.stage(log)
    return "\n".join([first.split("\n")[0],
                      "last step: %s" % (stage if stage is not None else "(none printed)"),
                      "last %d lines of the release log:" % FAILURE_TAIL_LINES,
                      logtail.tail(log, FAILURE_TAIL_LINES)])


def product_failed(text):
    """True when run()'s failure text says the release exited 1 (the product failed a check):
    its first line is exactly 'rc 1' - not a timeout, not an OSFailure, not another code."""
    return (text or "").split("\n")[0] == "rc 1"


def run(cfg, group, sha):
    """Run devloop.release for `group` at `sha` once. Returns (ok, text): the output tail, or on
    failure 'rc N[ (timeout)]' followed by it. Never retries, never rolls back."""
    cmd = cfg.require_release()
    repo = Git(cfg.root)
    gid = group["id"]
    tree = os.path.join(cfg.worktree_root, "release-" + gid)
    log = log_path(cfg, gid)
    live_name = "release-" + gid
    _write(log, "")
    hold = None
    try:
        out_dir = os.path.abspath(cfg.runs_dir("release", gid))
        run_env = env(cfg, group, sha, out_dir)
        if slots.needed(cfg):
            hold, busy = _take(cfg, gid, sha, log, live_name)
            if hold is None:
                with open(log, "a") as f:
                    f.write(busy + "\n")
                return False, "%s\n%s" % (busy, _read(log).strip()[-2000:])
            if hold.path:
                run_env[slots.MACHINE_ENV if cfg.regression_exclusive else slots.ENV] = hold.path
        worktrees.detached_tree(cfg, repo, tree, sha)
        proc = osx.spawn(["sh", "-c", cmd], cwd=tree, env=run_env, log_path=log)
        inflight.put(cfg, live_name, {
            "kind": "release", "group": gid, "sha": sha, "log": log, "result": None,
            "stamp": {"pid": proc.pid, "host": osx.host(), "boot": osx.boot_id(),
                      "started": osx.now()}})
        try:
            rc, timed_out = osx.wait(proc, cfg.release_timeout_seconds)
        except BaseException:               # a signal (release_job.py) stops the command too
            osx.kill_tree(proc.pid)
            raise
        text = _read(log).strip()[-2000:]
        if rc == 0 and not timed_out:
            return True, text
        return False, "rc %s%s\n%s" % (rc, " (timeout)" if timed_out else "", text)
    except osx.OSFailure as exc:
        with open(log, "a") as f:
            f.write(str(exc) + "\n")
        return False, str(exc)
    finally:
        inflight.drop(cfg, live_name)
        try:
            repo.remove_worktree(tree)
        except osx.OSFailure:
            pass
        if hold is not None:
            hold.release()


def _take(cfg, gid, sha, log, live_name):
    """What a regression takes (the whole machine with devloop.regression_exclusive, else one slot),
    waiting at most devloop.regression_timeout_seconds; the live record says the release waits.
    Returns (hold, None) or (None, the busy sentence)."""
    stage = "machine" if cfg.regression_exclusive else "slot"
    inflight.put(cfg, live_name, {"kind": "release", "group": gid, "sha": sha, "log": log, "result": None,
                                  "stage": stage, "stamp": osx.stamp(), "since": osx.now()})
    take, busy = ((slots.take_machine, slots.machine_busy) if cfg.regression_exclusive
                  else (slots.take, slots.busy))
    began = time.time()
    hold = take(cfg, began + cfg.regression_timeout_seconds)
    return hold, (None if hold is not None else busy(cfg, time.time() - began))
