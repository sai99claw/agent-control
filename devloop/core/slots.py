"""Machine slots: at most devloop.machine_slots heavy runs (agents, devloop-started test runs) at once.

A slot is a BSD flock (LOCK_EX) on <worktrees>/slots/slot-<i>, i = 0..N-1, held by the devloop
process that started the run. The files are created when missing and never deleted or replaced
(a lock on a replaced file would stop counting). flock locks belong to the open file
description: a second open of the same file in the same process is blocked by the first, the
lock fd is not inherited by children (Python fds are close-on-exec), and the kernel frees the lock
when its holder dies.

A process whose environment has DEVLOOP_SLOT set already runs under a slot (an agent, or a script
an agent started): it takes none of its own.

devloop.regression_exclusive: a regression takes the whole machine. The gate is
<worktrees>/slots/exclusive (created when missing, never deleted or replaced). A taker holds a
LOCK_SH on it only while it tries the slots, not for its whole run; the regression holds a LOCK_EX
on it from the moment it starts waiting until it has released every slot, so no new slot is taken
meanwhile. A separate mark (instead of one shared lock held for the whole run) because Linux flock
gives a waiting LOCK_EX no priority over new LOCK_SH requests (inferred from flock semantics).
"""

import fcntl
import os
import threading
import time

from . import osx

POLL = 0.5
GATE_POLL = 0.05     # the gate is held only for moments: try it often so the regression is not starved
ENV = "DEVLOOP_SLOT"
GATE = "exclusive"


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


class Machine:
    """The whole machine held by an exclusive regression: every slot and the gate. release()
    frees the slots first, then the gate; idempotent."""

    def __init__(self, gate, held):
        self.gate, self.slots = gate, held

    @property
    def held(self):
        return self.gate.held

    def release(self):
        for slot in self.slots:
            slot.release()
        self.gate.release()


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
    paths = _paths(cfg, folder)
    gated = bool(cfg.regression_exclusive)
    while True:
        gate = _try(os.path.join(folder, GATE), fcntl.LOCK_SH) if gated else None
        if gate is not None or not gated:
            try:
                for path in paths:
                    slot = _try(path)
                    if slot:
                        return slot
            finally:
                if gate is not None:
                    gate.release()
        left = deadline - time.time()
        if left <= 0:
            return None
        time.sleep(min(POLL if not gated or gate is not None else GATE_POLL, left))


def take_machine(cfg, deadline):
    """For an exclusive regression: the gate (LOCK_EX) and then every slot, waiting for each to be
    freed; a free pass when no slot is needed; None when the machine was not free before
    `deadline` (whatever was taken is released)."""
    if not needed(cfg):
        return Slot()
    folder = osx.makedirs(slot_dir(cfg))
    gate = _wait(os.path.join(folder, GATE), deadline, fcntl.LOCK_EX, GATE_POLL)
    if gate is None:
        return None
    held = []
    try:
        for path in _paths(cfg, folder):
            slot = _wait(path, deadline, fcntl.LOCK_EX, POLL)
            if slot is None:
                break
            held.append(slot)
        else:
            return Machine(gate, held)
    except BaseException:
        Machine(gate, held).release()
        raise
    Machine(gate, held).release()
    return None


def _paths(cfg, folder):
    return [os.path.join(folder, "slot-%d" % i) for i in range(cfg.machine_slots)]


def _wait(path, deadline, mode, poll):
    while True:
        got = _try(path, mode)
        if got:
            return got
        left = deadline - time.time()
        if left <= 0:
            return None
        time.sleep(min(poll, left))


def _try(path, mode=fcntl.LOCK_EX):
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot open machine slot %s" % path))
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
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


def machine_busy(cfg, waited):
    """The sentence for an exclusive regression that did not get the whole machine."""
    return ("the whole machine (every machine slot, devloop.machine_slots %s, "
            "devloop.regression_exclusive) did not come free within %d s" % (cfg.machine_slots, round(waited)))
