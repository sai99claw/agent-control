"""release : run the project's release for a group at a sha, the one way devloop does it.

The controller's landing and `tickets.py release` both call run(): a throwaway detached worktree
at the sha under the worktree root, the controller's environment plus the DEVLOOP_* variables,
and devloop.release_timeout_seconds as the limit. The worktree is removed afterwards.
"""

import os

from . import osx
from .gitx import Git


def env(cfg, group, sha):
    """The release command's environment."""
    return cfg.env(DEVLOOP_RELEASE_SHA=sha, DEVLOOP_GROUP=group["id"],
                   DEVLOOP_TICKETS=",".join(group.get("members") or []),
                   DEVLOOP_REMOTE=cfg.values.get("remote"))


def run(cfg, group, sha):
    """Run devloop.release for `group` at `sha` once. Returns (ok, text): the output tail, or on
    failure 'rc N[ (timeout)]' followed by it. Never retries, never rolls back."""
    cmd = cfg.require_release()
    repo = Git(cfg.root)
    tree = os.path.join(cfg.worktree_root, "release-" + group["id"])
    try:
        repo.detached(tree, sha)
        res = osx.run(["sh", "-c", cmd], cwd=tree, timeout=cfg.release_timeout_seconds,
                      env=env(cfg, group, sha), what="release")
        text = (res.out + res.err).strip()[-2000:]
        return res.ok, text if res.ok else "rc %s%s\n%s" % (
            res.rc, " (timeout)" if res.timed_out else "", text)
    except osx.OSFailure as exc:
        return False, str(exc)
    finally:
        try:
            repo.remove_worktree(tree)
        except osx.OSFailure:
            pass
