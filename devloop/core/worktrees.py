"""Worktrees devloop creates, set up by the project's devloop.worktree_setup (D-091 item 10).

A worktree that did not exist before is set up right after it is created: the setup command
runs with cwd = the worktree and AC_ROOT = the project root, its output kept in
<worktrees>/runs/setup/<folder name>.log. A setup that fails (or runs past SETUP_TIMEOUT) raises
SetupFailed and the worktree is removed again (a branch it was on stays). A reused worktree is
not set up again.
"""

import os

from . import osx

SETUP_TIMEOUT = 600   # seconds; tests lower it


class SetupFailed(osx.OSFailure):
    """devloop.worktree_setup failed in a worktree; an environment problem, not a red."""


def log_path(cfg, path):
    return os.path.join(cfg.runs_dir("setup"), os.path.basename(path.rstrip(os.sep)) + ".log")


def _tail(log, n=5):
    try:
        with open(log, errors="replace") as fh:
            lines = [ln.rstrip("\n") for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return ""
    return " | ".join(lines[-n:])


def setup(cfg, path):
    """Run devloop.worktree_setup in `path` (nothing when it is null). Raises SetupFailed."""
    cmd = cfg.values.get("worktree_setup")
    if cmd is None:
        return
    log = log_path(cfg, path)
    try:
        os.unlink(log)
    except OSError:
        pass
    proc = osx.spawn(["sh", "-c", cmd], cwd=path, env=cfg.env(), log_path=log)
    rc, timed_out = osx.wait(proc, SETUP_TIMEOUT)
    if rc == 0 and not timed_out:
        return
    raise SetupFailed("devloop.worktree_setup failed in %s (rc %s%s): %s" % (
        path, rc, ", timeout after %d s" % SETUP_TIMEOUT if timed_out else "",
        _tail(log) or "no output; log %s" % log))


def _exists(path):
    return os.path.exists(os.path.join(path, ".git"))


def _set_up_or_remove(cfg, repo, path):
    try:
        setup(cfg, path)
    except SetupFailed:
        try:
            repo.remove_worktree(path)
        except osx.OSFailure:
            pass
        raise


def branch_tree(cfg, repo, path, branch, start=None):
    """Git.worktree, then the setup when the worktree is new (removed again if it fails)."""
    existed = _exists(path)
    repo.worktree(path, branch, start=start)
    if not existed:
        _set_up_or_remove(cfg, repo, path)
    return path


def detached_tree(cfg, repo, path, ref):
    """Git.detached (always a fresh worktree), then the setup (removed again if it fails)."""
    repo.detached(path, ref)
    _set_up_or_remove(cfg, repo, path)
    return path
