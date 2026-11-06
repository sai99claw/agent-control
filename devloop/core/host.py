"""Host samples (#44): while the controller runs, one JSON line every devloop.host_sample_seconds
in <worktrees>/runs/host/<hostname>.jsonl with the machine's load, memory, disk, running devloop
agents and machine slots in use. Lines older than 7 days are pruned (at most once a day).

Memory comes from scripts/hostinfo.py (shared with the unittest adapter), loaded by file path:
scripts/ is not put on sys.path because it holds modules named status and event.
"""

import calendar
import errno
import fcntl
import importlib.util
import json
import os
import re
import shutil
import socket
import sys
import threading
import time

from . import config, osx, slots

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"      # the pool's format (osx.now)
KEEP_SECONDS = 7 * 86400                 # lines kept
PRUNE_AFTER_SECONDS = 8 * 86400          # the first line this old (or unreadable) triggers a rewrite
KEYS = ("at", "host", "load1", "load5", "cpus", "mem_available", "disk_free", "agents",
        "slots_used", "slots")

_hostinfo = None


def hostinfo():
    """scripts/hostinfo.py of this agent-control clone, loaded once by path."""
    global _hostinfo
    if _hostinfo is None:
        path = os.path.join(config.AC_HOME, "scripts", "hostinfo.py")
        spec = importlib.util.spec_from_file_location("ac_hostinfo", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _hostinfo = module
    return _hostinfo


def _epoch(when):
    """`when` (None = now, an epoch number, or a pool-format string) as epoch seconds."""
    if when is None:
        return time.time()
    if isinstance(when, str):
        return float(calendar.timegm(time.strptime(when, TIME_FORMAT)))
    return float(when)


def stamp(when=None):
    return time.strftime(TIME_FORMAT, time.gmtime(_epoch(when)))


def host_dir(cfg):
    return os.path.join(cfg.worktree_root, "runs", "host")


def host_file(cfg):
    return os.path.join(host_dir(cfg), socket.gethostname().replace(os.sep, "_") + ".jsonl")


def memory_available():
    """Available memory in bytes (None = unknown), with the adapter's environment overrides."""
    return hostinfo().available_bytes(
        os.environ.get("AC_CGROUP_ROOT") or "/sys/fs/cgroup",
        os.environ.get("AC_MEMINFO") or "/proc/meminfo",
        os.environ.get("AC_VM_STAT") or "vm_stat",
        os.environ.get("AC_SYSCTL") or "sysctl")


def agents_running(proc_root=None, ps=None):
    """How many distinct DEVLOOP_RUN_DIR values the processes with DEVLOOP_ROLE in their environment
    have (one agent run may be several processes). From <proc_root>/<pid>/environ when proc_root is
    a folder, else from `ps -A -E -ww -o command=` (macOS prints the environment after the command).
    None = unknown."""
    proc_root = proc_root or os.environ.get("AC_PROC_ROOT") or "/proc"
    ps = ps or os.environ.get("AC_PS") or "ps"
    runs = set()
    if os.path.isdir(proc_root):
        try:
            names = os.listdir(proc_root)
        except OSError:
            return None
        for name in names:
            if not name.isdigit():
                continue
            try:
                with open(os.path.join(proc_root, name, "environ"), "rb") as handle:
                    raw = handle.read()
            except OSError:
                continue
            env = {}
            for item in raw.split(b"\0"):
                key, sep, value = item.partition(b"=")
                if sep:
                    env[key] = value
            if b"DEVLOOP_ROLE" in env and b"DEVLOOP_RUN_DIR" in env:
                runs.add(env[b"DEVLOOP_RUN_DIR"])
        return len(runs)
    out = hostinfo().run_tool([ps, "-A", "-E", "-ww", "-o", "command="])
    if out is None:
        return None
    for line in out.splitlines():
        if "DEVLOOP_ROLE=" not in line:
            continue
        found = re.search(r"(?:^|\s)DEVLOOP_RUN_DIR=(\S*)", line)
        if found:
            runs.add(found.group(1))
    return len(runs)


def slots_in_use(cfg):
    """How many of the devloop.machine_slots slot files are locked now (None = no slots set). Each is
    probed with a non-blocking LOCK_EX on a fresh fd that is closed at once: the probe may hold a
    free slot for a moment, and a taker that hits that moment tries the next slot or polls again."""
    count = cfg.machine_slots
    if count is None:
        return None
    used = 0
    for i in range(count):
        try:
            fd = os.open(os.path.join(slots.slot_dir(cfg), "slot-%d" % i), os.O_RDONLY)
        except OSError:
            continue                    # never created: nobody holds it
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                used += 1
        finally:
            os.close(fd)                # closing the only fd of the description drops our lock
    return used


def disk_free(path):
    """Bytes free on the filesystem that holds `path`. A path not created yet (a fresh worktree root)
    is measured at its nearest existing ancestor: the filesystem it will be created on."""
    path = os.path.abspath(path)
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return shutil.disk_usage(path).free


def sample(cfg, now=None):
    """One host sample: the object with exactly KEYS."""
    try:
        load1, load5 = os.getloadavg()[:2]
    except (OSError, AttributeError):
        load1 = load5 = None
    slot_count = cfg.machine_slots
    return {
        "at": stamp(now),
        "host": socket.gethostname(),
        "load1": load1,
        "load5": load5,
        "cpus": os.cpu_count(),
        "mem_available": memory_available(),
        "disk_free": disk_free(cfg.worktree_root),
        "agents": agents_running(),
        "slots_used": slots_in_use(cfg) if slot_count is not None else None,
        "slots": slot_count,
    }


def _line_epoch(line):
    """Epoch of a line's 'at'; None when the line or its 'at' cannot be read."""
    try:
        return _epoch(json.loads(line)["at"])
    except (ValueError, TypeError, KeyError):
        return None


def append(cfg, row, now=None):
    """Append `row` as one line. When the file's first line is more than 8 days old (or unreadable),
    first rewrite the file keeping only lines within 7 days of `now`: so a rewrite happens at most
    about once a day."""
    path = host_file(cfg)
    osx.makedirs(host_dir(cfg))
    current = int(_epoch(now))           # the lines' 'at' has whole seconds
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        lines = []
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot read %s" % path))
    if lines:
        first = _line_epoch(lines[0])
        if first is None or current - first > PRUNE_AFTER_SECONDS:
            kept = []
            for line in lines:
                at = _line_epoch(line)
                if at is not None and current - at <= KEEP_SECONDS:
                    kept.append(line)
            osx.atomic_write(path, "".join(line + "\n" for line in kept))
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot write %s" % path))


class Sampler:
    """A daemon thread that samples at start, then every devloop.host_sample_seconds, until stop().
    A failed sample prints one line to stderr and the next one is tried as usual."""

    def __init__(self, cfg, out=None):
        self.cfg = cfg
        self.every = cfg.host_sample_seconds
        self.out = out
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self.every is None or self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._loop, name="host-sampler", daemon=True)
        self._thread.start()
        return self

    def once(self):
        try:
            osx.makedirs(host_dir(self.cfg))     # also the worktree root disk_free measures
            append(self.cfg, sample(self.cfg))
        except Exception as exc:        # noqa: BLE001 - a sample never stops the controller
            text = " ".join(str(exc).split()) or exc.__class__.__name__
            print("ticket_controller: host sample failed: %s" % text, file=sys.stderr if self.out is None else self.out,
                  flush=True)

    def _loop(self):
        while True:
            self.once()
            if self._stop.wait(self.every):
                return

    def stop(self, timeout=5.0):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
