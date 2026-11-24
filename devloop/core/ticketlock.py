"""One issuer per ticket: a BSD flock (LOCK_EX) on <worktrees>/locks/issuer-<N>.lock.

ticket_issuer holds it from before its claim until it exits; the controller treats a held lock as
a live issuer whatever the ticket's stamp says. The file is created when missing and never deleted
or replaced (a lock on a replaced file would stop counting). The kernel frees the lock only when
its holder dies; the fd is close-on-exec, so agents the issuer starts do not inherit it. The
pattern is slots._try.
"""

import fcntl
import os

from . import osx


def path(cfg, ident):
    return os.path.join(cfg.worktree_root, "locks", "issuer-%s.lock" % ident)


def _open(cfg, ident):
    p = path(cfg, ident)
    osx.makedirs(os.path.dirname(p))
    try:
        return os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot open issuer lock %s" % p))


def take(cfg, ident):
    """The held lock's fd (keep it open for as long as the lock must hold), or None when another
    open file description holds it."""
    fd = _open(cfg, ident)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError as exc:
        os.close(fd)
        raise osx.OSFailure(osx.explain(exc, "cannot lock issuer lock %s" % path(cfg, ident)))
    return fd


def held(cfg, ident):
    """True when someone else holds the lock. Takes it for a moment when free (and drops it at
    once), so never call it for a ticket whose issuer is being started right now."""
    fd = take(cfg, ident)
    if fd is None:
        return True
    os.close(fd)
    return False
