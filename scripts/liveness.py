#!/usr/bin/env python3
"""Is a recorded driver still running? One answer, shared by schedule.py and heartbeat (#117).

    python3 scripts/liveness.py boot              # this boot's id
    python3 scripts/liveness.py driver <pid>      # the pid to record as the driver of <pid>
    python3 scripts/liveness.py gone <pid> [boot] # rc 0 = gone, 1 = still running

A process recorded as (pid, boot_id) is **gone** when its pid is absent from /proc or it was
recorded under another boot (a container restart reuses pids from 1). No boot recorded -> judged
by pid only. Liveness is read from /proc only: a signal-0 probe is a signal, and hard_block forbids
signals in tests; a probe that can kill is the wrong tool for a question.
"""

import os
import sys

BOOT_ID = "/proc/sys/kernel/random/boot_id"
# A hook / `$(...)` runs us through `sh -c`: that shell is gone the moment the command returns, so
# it is not the driver. `driver` walks past these to the process that keeps running.
SHELLS = ("sh", "bash", "dash", "zsh", "ash", "busybox")
WALK_LIMIT = 4


def boot_id():
    try:
        with open(BOOT_ID, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def pid_alive(pid):
    """/proc only — a signal-0 probe is a signal (hard_block)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    return pid > 0 and os.path.isdir("/proc/%d" % pid)


def gone(pid, boot):
    """A recorded process (lock holder, child of an interrupted run, the driver of a start event)
    is gone when its pid is absent from /proc or it was recorded under another boot. No boot
    recorded -> judged by pid only."""
    current = boot_id()
    if boot and current and boot != current:
        return True
    return not pid_alive(pid)


def driver_fields(pid=None):
    """The kv a start event carries (#117 A1): the driver's pid and this boot."""
    return {"driver_pid": int(pid if pid is not None else os.getpid()), "boot_id": boot_id()}


def _stat(pid):
    """(comm, ppid) from /proc/<pid>/stat; (None, None) when it cannot be read."""
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None, None
    left, right = text.find("("), text.rfind(")")
    if left < 0 or right < left:
        return None, None
    fields = text[right + 2:].split()
    try:
        return text[left + 1:right], int(fields[1])
    except (IndexError, ValueError):
        return text[left + 1:right], None


def _is_dash_c(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as handle:
            argv = handle.read().split(b"\0")
    except OSError:
        return False
    return b"-c" in argv[1:3]


def driver_of(pid):
    """The process to record as driver when `pid` is the parent of a short-lived hook: walk up
    past `sh -c` wrappers (at most WALK_LIMIT). Best effort — a wrong answer reads `dead` sooner,
    and the session row is then only closed, nothing removed."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    for _ in range(WALK_LIMIT):
        comm, ppid = _stat(pid)
        if comm is None or comm not in SHELLS or not _is_dash_c(pid) or not ppid or ppid <= 1:
            break
        pid = ppid
    return pid if pid_alive(pid) else None


def main(argv):
    if argv[:1] == ["boot"]:
        print(boot_id())
        return 0
    if argv[:1] == ["gone"] and len(argv) in (2, 3):
        return 0 if gone(argv[1], argv[2] if len(argv) == 3 else "") else 1
    if argv[:1] == ["driver"] and len(argv) == 2:
        pid = driver_of(argv[1])
        print("" if pid is None else pid)
        return 0
    sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
