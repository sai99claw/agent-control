"""Machine slots: at most devloop.machine_slots heavy runs (agents, devloop-started test runs) at once.

A slot is a BSD flock (LOCK_EX) on <worktrees>/slots/slot-<i>, i = 0..N-1, held by the devloop
process that started the run. The files are created when missing and never deleted or replaced
(a lock on a replaced file would stop counting). flock locks belong to the open file
description: a second open of the same file in the same process is blocked by the first, the
lock fd is not inherited by children (Python fds are close-on-exec), and the kernel frees the lock
when its holder dies.

A process whose environment has DEVLOOP_SLOT set already runs under a slot (an agent, or a script
an agent started): it takes none of its own.
"""

import fcntl
import os
import threading
import time

from . import osx

POLL = 0.5
ENV = "DEVLOOP_SLOT"


class Slot:
    """One held slot (path set) or a free pass (path None: no slot needed). release() is idempotent
    and safe from any thread."""

    def __init__(self, path=None, fd=None):
        self.path, self._fd = path, fd
        self._lock = threading.Lock()

    @property
    def held(self):
        return self._fd is not None

    def release(self):
        with self._lock:
            fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)        # closing the only fd of the description drops the flock
            except OSError:
                pass

    def release_when_done(self, proc):
        """Free the slot as soon as `proc` exits, whether or not anyone collects it yet."""
        if not self.held:
            return

        def watch():
            try:
                proc.wait()
            except Exception:   # noqa: BLE001 - whatever happens, the slot must not leak
                pass
            self.release()
        threading.Thread(target=watch, name="slot-watch-%s" % proc.pid, daemon=True).start()


def needed(cfg):
    """True when a run in this process must take a slot."""
    return cfg.machine_slots is not None and not os.environ.get(ENV)


def slot_dir(cfg):
    return os.path.join(cfg.worktree_root, "slots")


def take(cfg, deadline):
    """A held Slot, a free pass when none is needed, or None when no slot came free before
    `deadline` (a time.time() value). Polls about every POLL seconds."""
    if not needed(cfg):
        return Slot()
    folder = osx.makedirs(slot_dir(cfg))
    paths = [os.path.join(folder, "slot-%d" % i) for i in range(cfg.machine_slots)]
    while True:
        for path in paths:
            slot = _try(path)
            if slot:
                return slot
        left = deadline - time.time()
        if left <= 0:
            return None
        time.sleep(min(POLL, left))


def _try(path):
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot open machine slot %s" % path))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError as exc:
        os.close(fd)
        raise osx.OSFailure(osx.explain(exc, "cannot lock machine slot %s" % path))
    return Slot(path, fd)


def busy(cfg, waited):
    """The sentence for a run that got no slot."""
    return "no machine slot came free within %d s (devloop.machine_slots %s, all busy)" % (
        round(waited), cfg.machine_slots)
