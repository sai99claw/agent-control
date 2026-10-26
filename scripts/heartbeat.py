#!/usr/bin/env python3
"""heartbeat body (#117): who died half-way, who is still running, and what a dead one left behind.

    sh scripts/heartbeat.sh               # read-only report; rc 0 quiet / 1 something to handle
    sh scripts/heartbeat.sh --reconcile   # put the dead rounds back to the facts (main's own action)

Called by `scripts/heartbeat.sh` (`actl heartbeat`), which resolves the project root and the copy
root (`AC_WTBASE`, the same `wtbase` rule as auto-fix / land).

## Verdicts
Every start without its closer (`session.start`, `land.start`, `ticket.attempt.start`, and
`agent.start` rows that carry a driver) gets one verdict:
- `alive` - the driver pid is in /proc and was recorded under this boot (printed even past the
  lease; a slow live run is not a dead one);
- `dead awaiting-takeover` - pid gone or another boot, and nobody started the same work later;
- `dead taken-over by <attempt N | run R | stamp S>` - a later start for the same key (H2);
- `unknown (no driver recorded; lease rule)` - rows written before #117: judged by the lease only,
  never `alive`, never reconciled.
Liveness is `liveness.gone()` - the same function schedule.py uses; /proc only, no signal.

## --reconcile acts on `dead awaiting-takeover` only
worker / verifier round: wip diff saved and the ticket worktree cleaned, pid files removed,
sessions.json rows closed, `agent.failed` / `ticket.attempt.failed` reason=driver-died, a cost row
per dead agent, ticket Running -> Ready. Reviewer: `agent.failed` + `review.missing`. Land: lock,
`land.fail stage=died`, land/<stamp> worktree and branch. Main session: `session.end`. The scheduler
is only reported (`actl schedule run` resumes it and takes over its own lock). The closers carry the
dead start's `driver_pid`, so they close that start and not a later one with the same key.
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402
import inbox  # noqa: E402  reports_dir
import liveness  # noqa: E402  the one liveness rule (#117 A2), shared with schedule.py

DEFAULT_LEASE = 7200
CLOSERS = {
    "session.start": ("session.end",),
    "land.start": ("land.pass", "land.fail", "land.refused"),
    "ticket.attempt.start": ("ticket.attempt.done", "ticket.attempt.failed"),
    "agent.start": ("agent.done", "agent.failed"),
}
ENDER = {end: start for start, ends in CLOSERS.items() for end in ends}
LEASE_KEY = {"land.start": "land", "ticket.attempt.start": "worker"}
REASON = "driver-died"
ALIVE, DEAD, UNKNOWN = "alive", "dead", "unknown"
AWAITING = "awaiting-takeover"
UNKNOWN_WORDS = "unknown (no driver recorded; lease rule)"
RESUME = "actl schedule run"
# Roles whose round auto-fix runs (worktree / copy, cost row, ticket back to Ready).
ROUND_ROLES = ("worker", "verifier", "opener")


def say(line, log=None):
    print(line)
    if log is not None:
        log.append(line)


def key_of(row):
    """Start -> what counts as its coming back. Fine enough that two lines running at once do not
    cancel each other out."""
    kind = row.get("kind", "")
    if kind.startswith("land."):
        return ("land", str(row.get("stamp", "")))
    if kind.startswith("ticket.attempt."):
        return ("attempt", str(row.get("ticket", "")), str(row.get("attempt", "")))
    if kind.startswith("agent."):
        return ("agent", str(row.get("ticket", "")), str(row.get("role", "")),
                str(row.get("run_id", "")), str(row.get("round", "")))
    # session: the emitted `--session` id (#45 B3); old rows without it fall back to pid.
    if row.get("session"):
        return ("session", row.get("session"), str(row.get("role", "")))
    return ("session", str(row.get("pid", "")), str(row.get("role", "")))


def driver_of(row):
    """(pid, boot) recorded on a start, or None when the row predates #117."""
    try:
        pid = int(row.get("driver_pid"))
    except (TypeError, ValueError):
        return None
    return pid, str(row.get("boot_id") or "")


def lease_for(row, leases):
    kind = row.get("kind", "")
    if kind in LEASE_KEY:
        return int(leases.get(LEASE_KEY[kind]) or DEFAULT_LEASE)
    role = str(row.get("role") or "")
    return int(leases.get(role) or leases.get("worker") or DEFAULT_LEASE)


def open_starts(rows):
    """(index, row) of every start nobody closed. A closer closes the starts of its key written
    before it; one that carries `driver_pid` (reconcile's) closes only that driver's start."""
    still = []
    for index, row in enumerate(rows):
        kind = row.get("kind")
        if kind in CLOSERS:
            if kind == "agent.start" and driver_of(row) is None:
                continue        # never tracked before #117: no driver, no lease of its own
            still.append((index, row))
        elif kind in ENDER:
            key, who = key_of(row), str(row.get("driver_pid") or "")
            still = [(i, start) for i, start in still
                     if not (start.get("kind") == ENDER[kind] and key_of(start) == key
                             and (not who or str(start.get("driver_pid")) == who))]
    return still


def land_tickets(row):
    names = str(row.get("note") or "").split()
    ids = {match.group(1) for match in (re.match(r"t(\d+)(?:[-_]|$)", name) for name in names)
           if match}
    return ids or set(names)


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def taken_over(index, row, rows):
    """H2: who took this dead start over, decided from the event file alone; None = nobody."""
    kind, mine = row.get("kind"), driver_of(row)
    for later in rows[index + 1:]:
        if later.get("kind") != kind:
            continue
        if kind == "ticket.attempt.start":
            if str(later.get("ticket")) != str(row.get("ticket")):
                continue
            new, old = as_int(later.get("attempt")), as_int(row.get("attempt"))
            if new is None or old is None or new < old:
                continue
            if new > old or driver_of(later) != mine:
                return "attempt %s" % later.get("attempt")
        elif kind == "agent.start":
            if (str(later.get("ticket")) == str(row.get("ticket"))
                    and later.get("role") == row.get("role")
                    and str(later.get("run_id") or "") != str(row.get("run_id") or "")):
                return "run %s" % (later.get("run_id") or "?")
        elif kind == "land.start":
            if later.get("channel") == "docs" or row.get("channel") == "docs":
                continue
            if land_tickets(later) & land_tickets(row):
                return "stamp %s" % later.get("stamp")
    return None


def age_of(row, now):
    try:
        started = datetime.datetime.fromisoformat(row.get("ts", ""))
    except (TypeError, ValueError):
        return None
    if started.tzinfo is None:
        started = started.astimezone()
    return (now - started).total_seconds()


def judge(root):
    """Pure read: every open start with its verdict, plus the scheduler. Nothing is written."""
    rows = event.read_events(root)
    leases = event.config(root).get("lease_seconds") or {}
    now = datetime.datetime.now().astimezone()
    items = []
    for index, row in open_starts(rows):
        item = {"index": index, "row": row, "age": age_of(row, now),
                "lease": lease_for(row, leases), "driver": driver_of(row)}
        if item["driver"] is None:
            item["verdict"] = UNKNOWN
        elif liveness.gone(*item["driver"]):
            item["verdict"] = DEAD
            item["by"] = taken_over(index, row, rows)
        else:
            item["verdict"] = ALIVE
        items.append(item)
    # An agent row of a round whose attempt was taken over is taken over with it.
    attempts = {(str(i["row"].get("ticket")), str(i["row"].get("attempt")), i["driver"]): i
                for i in items if i["row"].get("kind") == "ticket.attempt.start" and i["driver"]}
    for item in items:
        if item["row"].get("kind") == "agent.start" and item["verdict"] == DEAD and not item["by"]:
            head = attempts.get((str(item["row"].get("ticket")), str(item["row"].get("round")),
                                 item["driver"]))
            if head and head["verdict"] == DEAD and head.get("by"):
                item["by"] = head["by"]
    return items, schedule_state(root)


def schedule_state(root):
    path = os.path.join(inbox.reports_dir(root), "schedule", "state.json")
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("stop") is not None or not state.get("run_id"):
        return None
    if as_int(state.get("pid")) is None:
        return dict(state, verdict=UNKNOWN)
    gone = liveness.gone(state.get("pid"), state.get("boot_id"))
    return dict(state, verdict=DEAD if gone else ALIVE)


def who(row):
    return " ".join(filter(None, [
        str(row.get("kind")),
        "#%s" % row["ticket"] if row.get("ticket") else "",
        "role=%s" % row["role"] if row.get("role") else "",
        "model=%s" % row["model"] if row.get("model") else "",
        "attempt=%s" % row["attempt"] if row.get("attempt") else "",
        "run_id=%s" % row["run_id"] if row.get("run_id") and row.get("kind") == "agent.start" else "",
        "round=%s" % row["round"] if row.get("round") else "",
        "pid=%s" % row["pid"] if row.get("pid") and driver_of(row) is None else "",
        "driver_pid=%s" % row["driver_pid"] if driver_of(row) else "",
        "stamp=%s" % row["stamp"] if row.get("stamp") else ""]))


def words(item):
    if item["verdict"] == DEAD:
        return "dead taken-over by %s" % item["by"] if item.get("by") else "dead " + AWAITING
    return item["verdict"]


def land_debris(root):
    done = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"],
                          capture_output=True, text=True)
    leftovers, path = [], ""
    for line in done.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/land/"):
            leftovers.append((line[len("branch refs/heads/"):], path))
    return leftovers


def report(root, items, sched):
    """Print the verdicts; return how many rows want a human (rc 1 when > 0)."""
    wanted = 0
    known = [item for item in items if item["verdict"] != UNKNOWN]
    if known or sched:
        print("heartbeat: start 還沒有結尾、有記 driver 的(讀 /proc 與 boot_id,不發訊號):")
        for item in known:
            print("heartbeat:   %s —— %s —— %s" % (item["row"].get("ts", "?"), who(item["row"]),
                                                  words(item)))
            wanted += item["verdict"] == DEAD and not item.get("by")
        if sched:
            line = "schedule run %s pid=%s" % (sched.get("run_id"), sched.get("pid") or "?")
            if sched["verdict"] == DEAD:
                print("heartbeat:   %s —— dead %s(接續:%s;--reconcile 不動它)"
                      % (line, AWAITING, RESUME))
                wanted += 1
            else:
                print("heartbeat:   %s —— %s" % (line, sched["verdict"]))
    stale = [item for item in items if item["verdict"] == UNKNOWN
             and item["age"] is not None and item["age"] > item["lease"]]
    if stale:
        print("heartbeat: 租約到期還沒回來的:")
        for item in stale:
            print("heartbeat:   %s —— %s,已經 %d 秒(租約 %d 秒)—— %s"
                  % (item["row"].get("ts", "?"), who(item["row"]), int(item["age"]),
                     item["lease"], UNKNOWN_WORDS))
        print("heartbeat: 處置:確認那個 session 真的沒了,再把票的狀態對回事實"
              "(scripts/ticket.py set … / verify)。unknown 的列 --reconcile 不動。")
        wanted += len(stale)
    else:
        print("heartbeat: 沒有到期的租約")
    if any(item["verdict"] == DEAD and not item.get("by") for item in items):
        print("heartbeat: 處置:sh scripts/heartbeat.sh --reconcile(只動 dead awaiting-takeover 的列)")
    leftovers = land_debris(root)
    if leftovers:
        print("heartbeat: land 的 worktree 還留著:")
        for branch, where in leftovers:
            print("heartbeat:   %s -> %s" % (branch, where))
        print("heartbeat: 處置:看完 gate.log 再 `git worktree remove` 並刪掉那條分支。")
        wanted += len(leftovers)
    else:
        print("heartbeat: 沒有 land 殘骸")
    return wanted


# ----------------------------------------------------------------------- reconcile


class Reconciler(object):

    def __init__(self, root):
        self.root = root
        self.reports = inbox.reports_dir(root)
        self.ac = os.path.dirname(os.path.abspath(__file__))
        self.lines = []
        self.held = []

    def emit(self, kind, **fields):
        event.emit(kind, root=self.root, **fields)

    def done(self, what, did):
        say("heartbeat: reconciled %s -> %s" % (what, did), self.lines)

    def left(self, what, why):
        """Not reconciled this time: printed, not logged - a second run says the same thing and
        writes nothing."""
        say("heartbeat: left alone %s —— %s" % (what, why))
        self.held.append(what)

    def tool(self, *args):
        return subprocess.run([sys.executable, os.path.join(self.ac, args[0])] + list(args[1:]),
                              capture_output=True, text=True, env=dict(os.environ, AC_ROOT=self.root))

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", cwd or self.root] + list(args), capture_output=True)

    # ------------------------------------------------------------ worker rounds

    def ticket_file(self, ident):
        return os.path.join(event.tickets_dir(self.root), "%s.json" % ident)

    def load_ticket(self, ident):
        try:
            with open(self.ticket_file(ident), encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def worktree_of(self, branch):
        done = self.git("worktree", "list", "--porcelain")
        path = ""
        for line in done.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree "):]
            elif line == "branch refs/heads/%s" % branch:
                return path
        return None

    def run_dir(self, ident, run_id):
        base = os.path.join(self.reports, "t%s" % ident)
        return os.path.join(base, run_id) if run_id else base

    def log_of(self, ident, run_id, role, rnd):
        where = self.run_dir(ident, run_id)
        exact = os.path.join(where, "%s-round%s.log" % (role, rnd))
        if os.path.exists(exact):
            return exact
        try:
            names = [name for name in os.listdir(where)
                     if name.startswith("%s-round%s-" % (role, rnd)) and name.endswith(".log")]
        except OSError:
            names = []
        if not names:
            return None
        return max((os.path.join(where, name) for name in names), key=os.path.getmtime)

    def pid_files(self, ident, run_id, rnd):
        wtbase = os.environ.get("AC_WTBASE") or ""
        paths = [os.path.join(self.run_dir(ident, run_id), name)
                 for name in ("worker.pid", "verifier.pid")]
        if wtbase:
            paths += [os.path.join(wtbase, "%s-t%s" % (kind, ident), "round%s" % rnd, "worker.pid")
                      for kind in ("fix", "verify")]
        return [path for path in paths if os.path.exists(path)]

    def save_wip(self, ident, run_id, rnd):
        """H3: `git add -N .` then `git diff HEAD` (untracked files included), saved before the
        worktree is cleaned; an empty diff writes no file. Commits made before death stay."""
        ticket = self.load_ticket(ident) or {}
        branch = ticket.get("branch") or "t%s" % ident
        wt = self.worktree_of(branch)
        if not wt or not os.path.isdir(wt):
            return "no worktree for %s" % branch
        head = self.git("rev-parse", "HEAD", cwd=wt).stdout.strip()
        self.git("add", "-N", ".", cwd=wt)
        diff = self.git("diff", "--no-color", "--no-ext-diff", "HEAD", cwd=wt)
        if diff.returncode != 0:
            return "git diff failed in %s: %s" % (wt, diff.stderr.decode("utf-8", "replace").strip())
        if not diff.stdout and not self.git("status", "--porcelain", cwd=wt).stdout:
            return "worktree %s clean, no wip file" % branch
        path = ""
        if diff.stdout:
            path = os.path.join(self.run_dir(ident, run_id), "wip-round%s-died.diff" % rnd)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(diff.stdout)
        self.git("reset", "-q", "--hard", "HEAD", cwd=wt)
        self.git("clean", "-fdq", cwd=wt)
        left = self.git("status", "--porcelain", cwd=wt).stdout
        after = self.git("rev-parse", "HEAD", cwd=wt).stdout.strip()
        if left or after != head:
            return "worktree %s NOT clean after saving %s" % (branch, path or "(nothing)")
        return "wip saved to %s, worktree %s clean" % (
            os.path.relpath(path, self.root) if path else "(no diff)", branch)

    def close_sessions(self, ident, roles, rnd):
        path = os.path.join(self.reports, "t%s" % ident, "sessions.json")
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return []
        hit = []
        at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        for role, row in data.items():
            if (role in roles and isinstance(row, dict) and row.get("state") == "open"
                    and str(row.get("round")) == str(rnd)):
                row.update({"state": "closed", "why": REASON, "closed_at": at})
                hit.append(role)
        if hit:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        return hit

    def agent_still_running(self, pids, driver):
        """The driver died but an agent it started may not have (own session, own group): a pid
        file of this boot whose pid is in /proc. Its worktree is not ours to clean."""
        if driver and driver[1] and driver[1] != liveness.boot_id():
            return None
        for path in pids:
            try:
                with open(path, encoding="utf-8") as handle:
                    pid = handle.read().strip()
            except OSError:
                continue
            if liveness.pid_alive(pid):
                return pid
        return None

    def round(self, ident, rnd, driver, attempt, agents):
        run_id = (attempt or {}).get("run_id") or next(
            (row.get("run_id") for row in agents if row.get("run_id")), "") or ""
        what = "#%s round %s%s" % (ident, rnd, " run %s" % run_id if run_id else "")
        pids = self.pid_files(ident, run_id, rnd)
        alive = self.agent_still_running(pids, driver)
        if alive:
            self.left(what, "the driver died but agent pid %s is still running; "
                            "wait for it, then run --reconcile again" % alive)
            return
        roles = sorted({row.get("role") for row in agents if row.get("role")}
                       | ({"worker"} if attempt else set()))
        did = []
        if attempt or "worker" in roles:
            did.append(self.save_wip(ident, run_id, rnd))
        for path in pids:
            os.remove(path)
            did.append("removed %s" % os.path.relpath(path, self.root))
        closed = self.close_sessions(ident, roles, rnd)
        if closed:
            did.append("sessions.json %s closed" % ",".join(closed))
        dead_pid = driver[0] if driver else ""
        for row in agents:
            self.emit("agent.failed", ticket=ident, role=row.get("role"), model=row.get("model"),
                      run_id=row.get("run_id"), round=row.get("round"), agent=row.get("agent"),
                      reason=REASON, driver_pid=dead_pid)
            did.append("agent.failed %s" % row.get("role"))
        if attempt:
            self.emit("ticket.attempt.failed", ticket=ident, attempt=attempt.get("attempt"),
                      run_id=run_id or None, reason=REASON, driver_pid=dead_pid)
            did.append("ticket.attempt.failed attempt=%s" % attempt.get("attempt"))
        for row in agents:
            role = row.get("role")
            if role not in ROUND_ROLES:
                continue
            args = ["ticket.py", "cost", str(ident), "--role", role, "--round", str(rnd),
                    "--outcome", "error", "--note", REASON, "--by", "heartbeat.sh"]
            if row.get("model"):
                args += ["--model", str(row["model"])]
            if run_id:
                args += ["--run-id", run_id]
            log = self.log_of(ident, run_id, role, rnd)
            if log:
                args += ["--from-envelope", log]
            got = self.tool(*args)
            did.append("cost row %s" % role if got.returncode == 0
                       else "cost row %s FAILED: %s" % (role, got.stderr.strip()))
        ticket = self.load_ticket(ident)
        if ticket and ticket.get("state") == "Running":
            got = self.tool("ticket.py", "set", str(ident), "state", "Ready",
                            "--expect-state-version", str(ticket.get("state_version")))
            did.append("ticket Running -> Ready" if got.returncode == 0
                       else "ticket state NOT changed: %s" % got.stderr.strip())
        elif ticket:
            did.append("ticket stays %s" % ticket.get("state"))
        self.done(what, "; ".join(did))

    def reviewer(self, row, driver):
        ident = row.get("ticket")
        self.emit("agent.failed", ticket=ident, role=row.get("role"), model=row.get("model"),
                  run_id=row.get("run_id"), round=row.get("round"), agent=row.get("agent"),
                  reason=REASON, driver_pid=driver[0])
        self.emit("review.missing", ticket=ident, run_id=row.get("run_id"), reason=REASON,
                  note="reviewer 的 driver 死了(heartbeat --reconcile)")
        self.done("#%s reviewer run %s" % (ident, row.get("run_id") or "?"),
                  "agent.failed + review.missing; ticket stays %s"
                  % ((self.load_ticket(ident) or {}).get("state") or "?"))

    def other_agent(self, row, driver):
        self.emit("agent.failed", ticket=row.get("ticket"), role=row.get("role"),
                  model=row.get("model"), run_id=row.get("run_id"), round=row.get("round"),
                  agent=row.get("agent"), reason=REASON, driver_pid=driver[0])
        self.done("%s %s" % (row.get("agent") or "agent", who(row)), "agent.failed")

    # ------------------------------------------------------------ land

    def land(self, row, driver):
        stamp = str(row.get("stamp") or "")
        what = "land %s" % stamp
        did = []
        lock = os.environ.get("AC_LAND_LOCK") or os.path.join(self.root, ".land.lock")
        holder = {}
        try:
            with open(os.path.join(lock, "holder"), encoding="utf-8") as handle:
                holder = dict(part.split("=", 1) for part in handle.read().split() if "=" in part)
        except OSError:
            pass
        if os.path.isdir(lock):
            mine = str(holder.get("pid")) == str(driver[0])
            if mine or (holder.get("pid") and liveness.gone(holder.get("pid"),
                                                            holder.get("boot_id"))):
                aside = "%s.stale-%d" % (lock, os.getpid())
                os.rename(lock, aside)
                shutil.rmtree(aside, ignore_errors=True)
                did.append("removed %s (holder pid %s)" % (os.path.basename(lock),
                                                          holder.get("pid") or "?"))
            else:
                did.append("lock kept: holder pid %s is alive or unreadable" % holder.get("pid"))
        self.emit("land.fail", stamp=stamp, stage="died",
                  main_before=row.get("main_before") or holder.get("main_before"),
                  note=REASON, driver_pid=driver[0])
        did.append("land.fail stage=died")
        branch = "land/%s" % stamp
        wt = self.worktree_of(branch)
        if wt:
            self.git("worktree", "remove", "--force", wt)
            did.append("worktree %s removed" % wt)
        if self.git("rev-parse", "-q", "--verify", "refs/heads/%s" % branch).returncode == 0:
            self.git("branch", "-D", branch)
            did.append("branch %s deleted" % branch)
        self.done(what, "; ".join(did))

    def session(self, row, driver):
        self.emit("session.end", role=row.get("role"), model=row.get("model"),
                  session=row.get("session"), reason=REASON, driver_pid=driver[0])
        self.done("session %s role=%s" % (row.get("session") or row.get("pid"), row.get("role")),
                  "session.end reason=%s" % REASON)

    def run(self, items):
        dead = [item for item in items if item["verdict"] == DEAD and not item.get("by")]
        rounds = {}
        for item in dead:
            row, driver = item["row"], item["driver"]
            kind = row.get("kind")
            if kind == "ticket.attempt.start":
                key = (str(row.get("ticket")), str(row.get("attempt")), driver)
                rounds.setdefault(key, {"attempt": None, "agents": []})["attempt"] = row
            elif kind == "agent.start" and row.get("role") in ROUND_ROLES \
                    and str(row.get("agent") or "").startswith("auto-fix"):
                key = (str(row.get("ticket")), str(row.get("round")), driver)
                rounds.setdefault(key, {"attempt": None, "agents": []})["agents"].append(row)
            elif kind == "agent.start" and row.get("agent") == "review":
                self.reviewer(row, driver)
            elif kind == "agent.start":
                self.other_agent(row, driver)
            elif kind == "land.start":
                self.land(row, driver)
            elif kind == "session.start":
                self.session(row, driver)
        for (ident, rnd, driver), unit in sorted(rounds.items(), key=lambda pair: pair[0][:2]):
            self.round(ident, rnd, driver, unit["attempt"], unit["agents"])
        return self.lines


def write_log(root, lines):
    where = os.path.join(inbox.reports_dir(root), "heartbeat")
    os.makedirs(where, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(where, "%s.log" % stamp)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("".join(line + "\n" for line in lines))
    return path


def main(argv):
    root = os.environ.get("AC_ROOT") or event.repo_root()
    fix = False
    for arg in argv:
        if arg == "--reconcile":
            fix = True
        else:
            sys.stderr.write("heartbeat: 不認得 %r —— 用法:actl heartbeat [--reconcile]\n" % arg)
            return 2
    items, sched = judge(root)
    wanted = report(root, items, sched)
    if not fix:
        return 1 if wanted else 0
    lines = Reconciler(root).run(items)
    if not lines:
        print("heartbeat: nothing to reconcile")
        return 1 if wanted else 0
    path = write_log(root, lines)
    print("heartbeat: 收拾紀錄 -> %s" % os.path.relpath(path, root))
    items, sched = judge(root)
    left = sum(item["verdict"] == DEAD and not item.get("by") for item in items)
    left += sched is not None and sched["verdict"] == DEAD
    left += sum(item["verdict"] == UNKNOWN and item["age"] is not None
                and item["age"] > item["lease"] for item in items)
    left += len(land_debris(root))
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
