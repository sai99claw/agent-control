"""Quota cooldowns: one machine-wide file of commands that are out of quota until a known reset.

When an agent run ends out of quota and the CLI's message names a reset, the command's record
goes here; every agent start on the machine (any role, any project) skips that command until then.

The file is $AC_COOLDOWN_FILE, else ~/.agent-control/cooldown.json:
{"version": 1, "commands": {<key>: {"state": "quota", "until", "since", "text", "project", "role"}}}.
A command's key is the program's basename plus its --model value ("claude opus"): one program and
model share one record across roles, efforts and projects.

A program whose login is missing or expired has the record {"state": "needs_login", "since",
"text", "project", "role"} (no until) under its basename alone (one login per program, D-102): it
parks every --model of that program until the record is removed (`actl tickets login-ok`). It
replaces a quota record of the same key; a quota hit never replaces it.

The lock is a separate file, <path>.lock (created when missing, never deleted or replaced): the
data file is rewritten whole by replacing it (osx.write_json), and a flock on a replaced file would
stop counting. LOCK_EX on it is held only while the data file is read and rewritten. A missing,
unreadable or broken data file counts as no record; a failed write prints one line on stderr.
"""

import calendar
import fcntl
import os
import sys
import time

from . import osx

ENV = "AC_COOLDOWN_FILE"
FMT = "%Y-%m-%dT%H:%M:%SZ"


def path():
    p = os.environ.get(ENV)
    if p:
        return os.path.abspath(p)
    return os.path.join(os.path.expanduser("~"), ".agent-control", "cooldown.json")


def key(argv):
    """Basename of argv[0], plus " <model>" when argv has a --model value."""
    out = os.path.basename(argv[0])
    if "--model" in argv:
        i = argv.index("--model")
        if i + 1 < len(argv):
            out += " " + argv[i + 1]
    return out


def epoch(ts):
    """The epoch of a FMT stamp, or None."""
    try:
        return calendar.timegm(time.strptime(ts, FMT))
    except (TypeError, ValueError):
        return None


def records():
    """{key: record} from the file; {} when it is missing, unreadable or broken."""
    try:
        data = osx.read_json(path(), None)
    except osx.OSFailure:
        return {}
    commands = data.get("commands") if isinstance(data, dict) else None
    if not isinstance(commands, dict):
        return {}
    return {k: v for k, v in commands.items() if isinstance(v, dict)}


def _cooling(rec, now):
    when = epoch(rec.get("until"))
    return rec.get("state") == "quota" and when is not None and when > now


def _kept(rec, now):
    return _cooling(rec, now) or rec.get("state") == "needs_login"


def blocking(argv, now):
    """The record that keeps `argv` from starting at `now` (its program's needs_login record, else
    its own cooling record), or None."""
    recs = records()
    login = recs.get(os.path.basename(argv[0]))
    if login is not None and login.get("state") == "needs_login":
        return login
    rec = recs.get(key(argv))
    return rec if rec is not None and _cooling(rec, now) else None


def _rewrite(name, change):
    """Under the lock: change(commands, now) on the kept records (expired ones dropped); a True
    return writes them back. Returns what change returned, or None when the file cannot be written."""
    target = path()
    try:
        osx.makedirs(os.path.dirname(target))
        fd = os.open(target + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            now = time.time()
            commands = {k: v for k, v in records().items() if _kept(v, now)}
            out = change(commands, now)
            if out:
                osx.write_json(target, {"version": 1, "commands": commands})
            return out
        finally:
            os.close(fd)            # closing the only fd of the description drops the flock
    except (OSError, osx.OSFailure) as exc:
        print("cooldown: cannot record %s in %s (%s)" % (name, target, exc), file=sys.stderr)
        return None


def record_quota(argv, until, text, project, role):
    """Set the record of argv's key (quota until epoch `until`), dropping expired records; a
    needs_login record of that key stays as it is."""
    def change(commands, now):
        old = commands.get(key(argv))
        if old is not None and old.get("state") == "needs_login":
            return False
        commands[key(argv)] = {"state": "quota", "until": time.strftime(FMT, time.gmtime(until)),
                               "since": time.strftime(FMT, time.gmtime(now)), "text": text[:200],
                               "project": project, "role": role}
        return True
    _rewrite(key(argv), change)


def record_login(argv, text, project, role):
    """Park argv's program (its basename) as needs_login. True when this call made the
    transition (the program had no needs_login record before, read under the lock)."""
    program = os.path.basename(argv[0])

    def change(commands, now):
        old = commands.get(program)
        if old is not None and old.get("state") == "needs_login":
            return False
        commands[program] = {"state": "needs_login", "since": time.strftime(FMT, time.gmtime(now)),
                             "text": text[:200], "project": project, "role": role}
        return True
    return bool(_rewrite(program, change))


def login_names():
    """The sorted names with a needs_login record."""
    return sorted(k for k, v in records().items() if v.get("state") == "needs_login")


def clear_login(name):
    """Remove the needs_login record of `name` under the lock. True when it removed one."""
    def change(commands, now):
        rec = commands.get(name)
        if rec is None or rec.get("state") != "needs_login":
            return False
        del commands[name]
        return True
    return bool(_rewrite(name, change))
