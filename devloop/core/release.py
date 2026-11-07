"""release : run the project's release for a group at a sha, the one way devloop does it.

The controller's landing and `tickets.py release` both call run(): a throwaway detached worktree
at the sha under the worktree root, the controller's environment plus the DEVLOOP_* variables,
and devloop.release_timeout_seconds as the limit. The worktree is removed afterwards, so
anything the release produces goes to DEVLOOP_RELEASE_DIR (<worktrees>/runs/release/<group>/,
created before the command, never emptied or deleted by devloop). The whole output (stdout and
stderr interleaved, also what was printed before a timeout) is kept in
<worktrees>/runs/release/<group>.log; each run replaces it. log_path(cfg, gid) names it.
"""

import os

from . import inflight, osx, worktrees
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
    try:
        out_dir = os.path.abspath(cfg.runs_dir("release", gid))
        worktrees.detached_tree(cfg, repo, tree, sha)
        proc = osx.spawn(["sh", "-c", cmd], cwd=tree, env=env(cfg, group, sha, out_dir), log_path=log)
        inflight.put(cfg, live_name, {
            "kind": "release", "group": gid, "sha": sha, "log": log, "result": None,
            "stamp": {"pid": proc.pid, "host": osx.host(), "boot": osx.boot_id(),
                      "started": osx.now()}})
        rc, timed_out = osx.wait(proc, cfg.release_timeout_seconds)
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
