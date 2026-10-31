"""OS layer: run commands, kill process trees, tell whether a pid is still ours, write files safely.

Every other module goes through here, so OS failures surface in one shape:
`OSFailure` (with a one-line reason a person can act on) instead of a raw traceback.
Works on Linux and macOS with the Python 3.8+ standard library.
"""

import errno
import json
import os
import platform
import signal
import socket
import subprocess
import tempfile
import time


class OSFailure(Exception):
    """An operating-system level failure, already explained in `str(self)`."""


def explain(exc, what):
    """Turn an OSError into a sentence that says what to do."""
    code = getattr(exc, "errno", None)
    if code == errno.ENOSPC:
        return "%s: disk full - free space and run again" % what
    if code in (errno.EACCES, errno.EPERM):
        return "%s: permission denied (%s)" % (what, exc)
    if code == errno.ENOENT:
        return "%s: not found (%s)" % (what, exc)
    if code == errno.EMFILE:
        return "%s: too many open files" % what
    return "%s: %s" % (what, exc)


# ---------------------------------------------------------------- commands

class Result:
    def __init__(self, rc, out, err, timed_out=False):
        self.rc, self.out, self.err, self.timed_out = rc, out, err, timed_out

    @property
    def ok(self):
        return self.rc == 0 and not self.timed_out


def run(argv, cwd=None, env=None, timeout=None, stdin_text=None, check=False, what=None):
    """Run a command to completion, capture text output, never hang past `timeout`.

    A missing program or a bad cwd raises OSFailure. A timeout kills the whole process
    group and returns `timed_out=True` with rc -9. `check=True` raises OSFailure on rc != 0.
    """
    what = what or os.path.basename(str(argv[0]))
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, text=True,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        raise OSFailure(explain(exc, "cannot start %s" % what))
    try:
        out, err = proc.communicate(stdin_text, timeout=timeout)
        res = Result(proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            out, err = "", ""
        res = Result(-9, out or "", err or "", timed_out=True)
    if check and not res.ok:
        tail = (res.err or res.out).strip().splitlines()[-1:] or [""]
        raise OSFailure("%s failed (rc %s%s): %s" % (
            what, res.rc, ", timeout" if res.timed_out else "", tail[0]))
    return res


def spawn(argv, cwd=None, env=None, log_path=None, stdin_text=None):
    """Start a detached child (own session) whose output goes to `log_path`. Returns Popen."""
    try:
        log = open(log_path, "a") if log_path else subprocess.DEVNULL
    except OSError as exc:
        raise OSFailure(explain(exc, "cannot open log %s" % log_path))
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, text=True,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        raise OSFailure(explain(exc, "cannot start %s" % os.path.basename(str(argv[0]))))
    finally:
        if log_path and log is not subprocess.DEVNULL:
            log.close()
    if stdin_text is not None:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    return proc


def wait(proc, timeout):
    """Wait for a spawned child; on timeout kill its tree. Returns (rc, timed_out)."""
    try:
        return proc.wait(timeout=timeout), False
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return -9, True


def kill_tree(pid, grace=5.0):
    """Stop a process group started with start_new_session: TERM, wait, then KILL."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            try:
                os.kill(pid, sig)
            except OSError:
                return
        deadline = time.time() + grace
        while time.time() < deadline:
            if not alive(pid):
                return
            time.sleep(0.1)


# ---------------------------------------------------------------- liveness

def boot_id():
    """An id that changes on every reboot (or container restart)."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as fh:
            return fh.read().strip()
    except OSError:
        pass
    if platform.system() == "Darwin":
        res = run(["sysctl", "-n", "kern.boottime"], timeout=10)
        if res.ok:
            return res.out.strip()
    return "unknown"


def host():
    return socket.gethostname()


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    try:  # a zombie child we have not reaped is not "running"
        wpid, _ = os.waitpid(int(pid), os.WNOHANG)
        if wpid == int(pid):
            return False
    except ChildProcessError:
        pass
    except OSError:
        pass
    return True


def stamp():
    """Who is running: recorded on a ticket so a later reader can tell a dead run."""
    return {"pid": os.getpid(), "host": host(), "boot": boot_id(), "started": now()}


def stamp_dead(st, grace_seconds=0):
    """True when the process a stamp describes is certainly gone.

    Same host and boot: ask the kernel. Another boot on this host: gone. Another host:
    we cannot ask, so it counts as gone only after `grace_seconds` past its start.
    """
    if not st:
        return True
    if st.get("host") == host():
        if st.get("boot") != boot_id():
            return True
        return not alive(st.get("pid"))
    return grace_seconds > 0 and age(st.get("started")) > grace_seconds


# ---------------------------------------------------------------- time

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def age(ts):
    """Seconds since an `now()` timestamp; a bad timestamp is infinitely old."""
    try:
        return time.time() - _epoch(ts)
    except (TypeError, ValueError):
        return float("inf")


def _epoch(ts):
    import calendar
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


# ---------------------------------------------------------------- files

def atomic_write(path, text):
    """Write a file so a reader never sees half of it, even if we die mid-way."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise OSFailure(explain(exc, "cannot write %s" % path))


def write_json(path, data):
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


def read_json(path, default=None):
    """Read JSON; a missing file gives `default`, a broken one raises OSFailure."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except ValueError as exc:
        raise OSFailure("%s is not valid JSON (%s)" % (path, exc))
    except OSError as exc:
        raise OSFailure(explain(exc, "cannot read %s" % path))


def makedirs(path):
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise OSFailure(explain(exc, "cannot create %s" % path))
    return path


class PidLock:
    """One instance per lock file. A lock left by a dead process is taken over."""

    def __init__(self, path):
        self.path = path
        self.held = False

    def acquire(self):
        makedirs(os.path.dirname(os.path.abspath(self.path)))
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    holder = read_json(self.path, {})
                except OSFailure:
                    holder = {}
                if holder and not stamp_dead(holder):
                    raise OSFailure("already running (pid %s on %s since %s); lock %s" % (
                        holder.get("pid"), holder.get("host"), holder.get("started"), self.path))
                try:
                    os.unlink(self.path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise OSFailure(explain(exc, "cannot clear stale lock %s" % self.path))
                continue
            except OSError as exc:
                raise OSFailure(explain(exc, "cannot create lock %s" % self.path))
            with os.fdopen(fd, "w") as fh:
                json.dump(stamp(), fh)
            self.held = True
            return self
        raise OSFailure("cannot take lock %s" % self.path)

    def release(self):
        if self.held:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
