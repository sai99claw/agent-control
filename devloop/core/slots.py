"""Machine slots: at most devloop.machine_slots heavy runs (agents, devloop-started test runs) at once.

A slot is a BSD flock (LOCK_EX) on <worktrees>/slots/slot-<i>, i = 0..N-1, held by the devloop
process that started the run. The files are created when missing and never deleted or replaced
(a lock on a replaced file would stop counting). flock locks belong to the open file
description: a second open of the same file in the same process is blocked by the first, the
lock fd is not inherited by children (Python fds are close-on-exec), and the kernel frees the lock
when its holder dies.

A process whose environment has DEVLOOP_SLOT set already runs under a slot (an agent, or a script
an agent started): it takes none of its own.

devloop.regression_exclusive: a regression takes the whole machine, which keeps every other test run
off it; agents still take their slot and run beside it. The mark is <worktrees>/slots/exclusive and
the test runs in flight are counted on <worktrees>/slots/tests (both created when missing, never
deleted or replaced). A test run (take_test_run) holds a LOCK_SH on the mark only while it takes its
slot (none when nested in an agent's slot) and a LOCK_SH on slots/tests for its whole run; the
regression holds a LOCK_EX on the mark from the moment it starts waiting, then a LOCK_EX on
slots/tests (every test run in flight has ended), so no new test run starts meanwhile. A separate
mark (instead of the one shared lock alone) because Linux flock gives a waiting LOCK_EX no priority
over new LOCK_SH requests (inferred from flock semantics). A process whose environment has
DEVLOOP_MACHINE set runs inside the holder of the machine: its test runs take nothing.
"""

import fcntl
import os
import threading
import time

from . import osx

POLL = 0.5
GATE_POLL = 0.05     # the gate is held only for moments: try it often so the regression is not starved
ENV = "DEVLOOP_SLOT"
MACHINE_ENV = "DEVLOOP_MACHINE"
GATE = "exclusive"
TESTS = "tests"


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
    """A held pair of locks, released in order (`parts[-1]` first) and idempotently: the whole machine
    held by an exclusive regression (the mark, then slots/tests; path = the mark), or a test run
    under devloop.regression_exclusive (its slot when it took one, then its slots/tests share;
    path = the slot or None)."""

    def __init__(self, path, parts):
        self.path, self.parts = path, parts

    @property
    def held(self):
        return any(p.held for p in self.parts)

    def release(self):
        for part in reversed(self.parts):
            part.release()


def needed(cfg):
    """True when a run in this process must take a slot."""
    return cfg.machine_slots is not None and not os.environ.get(ENV)


def slot_dir(cfg):
    return os.path.join(cfg.worktree_root, "slots")


def take(cfg, deadline):
    """An agent's slot: a held Slot, a free pass when none is needed, or None when no slot came free
    before `deadline` (a time.time() value; None = wait until one is free, never None). Polls about
    every POLL seconds. The exclusive mark does not keep it off."""
    if not needed(cfg):
        return Slot()
    paths = _paths(cfg, osx.makedirs(slot_dir(cfg)))
    while True:
        slot = _first(paths)
        if slot:
            return slot
        if not _pause(deadline, POLL):
            return None


def take_test_run(cfg, deadline):
    """A test run (run_tests.py, the issuer's red check and case runs): a free pass when
    devloop.machine_slots is null or DEVLOOP_MACHINE is set; without devloop.regression_exclusive
    take()'s answer (a free pass when nested in a slot). With it, waits until no regression holds the
    mark, then takes a slot (only when DEVLOOP_SLOT is unset) and a share of slots/tests held until
    release(). None when that did not happen before `deadline` (None = no limit)."""
    if cfg.machine_slots is None or os.environ.get(MACHINE_ENV):
        return Slot()
    if not cfg.regression_exclusive:
        return take(cfg, deadline)
    folder = osx.makedirs(slot_dir(cfg))
    paths = [] if os.environ.get(ENV) else _paths(cfg, folder)
    while True:
        gate = _try(os.path.join(folder, GATE), fcntl.LOCK_SH)
        if gate is not None:
            try:
                slot = _first(paths) if paths else Slot()
                if slot is not None:
                    tests = _try(os.path.join(folder, TESTS), fcntl.LOCK_SH)
                    if tests is not None:
                        return Machine(slot.path, [slot, tests])
                    slot.release()
            finally:
                gate.release()
        if not _pause(deadline, POLL if gate is not None else GATE_POLL):
            return None


def take_machine(cfg, deadline):
    """For an exclusive regression: the mark (LOCK_EX), then slots/tests (LOCK_EX: every test run in
    flight has ended); a free pass when no slot is needed; None when the machine was not free before
    `deadline` (whatever was taken is released). Agents' slots are not taken."""
    if not needed(cfg):
        return Slot()
    folder = osx.makedirs(slot_dir(cfg))
    gate = _wait(os.path.join(folder, GATE), deadline, fcntl.LOCK_EX, GATE_POLL)
    if gate is None:
        return None
    try:
        tests = _wait(os.path.join(folder, TESTS), deadline, fcntl.LOCK_EX, POLL)
    except BaseException:
        gate.release()
        raise
    if tests is None:
        gate.release()
        return None
    return Machine(gate.path, [gate, tests])


def _paths(cfg, folder):
    return [os.path.join(folder, "slot-%d" % i) for i in range(cfg.machine_slots)]


def _first(paths):
    for path in paths:
        slot = _try(path)
        if slot:
            return slot
    return None


def _pause(deadline, poll):
    """Sleep before the next try; False when `deadline` (None = never) has passed."""
    if deadline is None:
        time.sleep(poll)
        return True
    left = deadline - time.time()
    if left <= 0:
        return False
    time.sleep(min(poll, left))
    return True


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
    return ("the whole machine (devloop.regression_exclusive: no other test run) "
            "did not come free within %d s" % round(waited))
