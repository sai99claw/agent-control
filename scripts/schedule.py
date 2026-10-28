#!/usr/bin/env python3
"""DAG scheduling and batch landing (#119; D-073 ②, D-067, D-049 ④).

    scripts/schedule.py plan    # read-only: batches, waiting reasons, file overlaps (no spawn / page / event)
    scripts/schedule.py run     # dispatch auto-fix.sh, land each all-green batch with land.sh, page main once

The LLM edits the order (`board/batches.json`), the system runs it: nobody types
`land.sh t162 t164 t165` by hand and nobody polls a status file — this script blocks on its own
children and writes exactly one inbox page per run at its terminal state.

## Facts, not a queue
Nodes are the tickets in tickets_dir, edges their `depends_on`. A dependency is satisfied iff the
dependency ticket is Done AND its `landed_sha` is an ancestor of main (`merge-base --is-ancestor`):
Done alone or a sha alone is not enough. `landed` is the only condition understood (legacy `落地…` /
`已落地…` on a Done dependency reads as landed); anything else, a missing ticket, a Cancelled /
Blocked dependency -> the dependant waits, reason printed. Ticket files and git are re-read at start
and after every child / land; `reports/schedule/state.json` only carries what they cannot answer
(run_id, which children this run spawned, pre-land branch heads, re-land counts).

## board/batches.json (schema 1)
    {"schema": 1, "batches": [{"id": "b1", "tickets": ["162", "164"]}, {"id": "b2", "tickets": ["163"]}]}
List order is landing order. rc 2 (naming ticket / dependency / batch): invalid JSON, schema != 1, an
unknown id, an id listed twice, a dependency in the same batch (gates are measured against main, not
against a batch-mate), a dependency in a later batch or in no batch and not landed. Unlisted open
tickets are untouched and printed under `unscheduled`. No file -> the frontier: every Ready (or
already green) ticket whose dependencies are landed; after it lands, recompute.

## run, per batch
Green already (InReview + gate rc 0; complex also `review_problems` empty) -> no dispatch. Ready ->
`sh $AC_HOME/scripts/auto-fix.sh <n>` (cwd AC_ROOT), at most `schedule.max_workers` (default 2) at
once. auto-fix rc 0 = green; any other rc = red: running children finish, nothing new starts, stop.
All green -> `sh $AC_HOME/scripts/land.sh <branch…>` in the foreground, split so that tickets whose
branch diffs (`git diff --name-only main...<branch>`) intersect never share a land call. A branch
that does not exist yet = files unknown (printed; it overlaps nothing it cannot be measured against).
land rc != 0 -> facts again: tickets now Done on main count as landed; the rest re-land only when each
got a new gated head (gate.sha == branch head != the head before the call; complex: review passes),
at most `schedule.reland_max` (default 2) times per batch.

## rc
    0 every batch landed (one `done` page)      1 stopped (one `decision` page, `actl schedule run` resumes)
    2 refused before start: cycle, batch file, lock held, a ticket another driver has (no page)
    3 nothing to dispatch or land (reasons on stdout, no page — "nothing to do" must not look like done)

## Never signals
The scheduler waits on its children and never signals any process: auto-fix and land own their
timeouts. Liveness of a lock holder or of a child from an interrupted run is read from /proc
(`/proc/<pid>`, `/proc/sys/kernel/random/boot_id`), never probed with a signal.
"""

import errno
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402  repo root and board/config.json, shared with every script
import inbox  # noqa: E402  the one page per terminal state
import liveness  # noqa: E402  pid + boot_id, shared with heartbeat (#117)
import ticket as tk  # noqa: E402  ticket files, review_problems, depends_cycles

BATCHES_REL = os.path.join("board", "batches.json")
LOCK_NAME = ".schedule.lock"
HOLDER = "holder"
STATE_SCHEMA = 1
DEFAULT_MAX_WORKERS = 2
DEFAULT_RELAND_MAX = 2
DEFAULT_MAIN = "main"
GIT_TIMEOUT = 60
LAND_CONFLICT_RC = 3
NOTHING = "nothing to dispatch or land"
RESUME_HINT = ("then `actl schedule run` resumes this run (facts first: what landed meanwhile counts); "
               "to change the order edit board/batches.json first")
USAGE = """usage: actl schedule plan | run
  plan   read-only: batches (board/batches.json, or the frontier), waiting reasons, file overlaps
  run    dispatch auto-fix.sh (schedule.max_workers at once), land each all-green batch, page main once
  rc: 0 all landed / 1 stopped (page) / 2 refused before start / 3 nothing to dispatch or land
"""


class Refused(Exception):
    """rc 2 before anything starts; `lines` go to stdout, one each."""

    def __init__(self, lines):
        super().__init__("; ".join(lines))
        self.lines = list(lines)


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def id_key(ident):
    return (0, int(ident), "") if str(ident).isdigit() else (1, 0, str(ident))


# Liveness is one implementation shared with heartbeat (#117 A2): scripts/liveness.py.
boot_id = liveness.boot_id
gone = liveness.gone


def pid_alive(pid):
    """/proc only — a signal-0 probe is a signal (hard_block). Kept by name for the scheduler's
    own readers; the rule lives in liveness.pid_alive."""
    return liveness.pid_alive(pid)


def ac_home():
    return os.environ.get("AC_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def schedule_dir(root):
    return os.path.join(inbox.reports_dir(root), "schedule")


def state_path(root):
    return os.path.join(schedule_dir(root), "state.json")


def land_lock(root):
    return os.environ.get("AC_LAND_LOCK") or os.path.join(root, ".land.lock")


def config_int(conf, key, default, low):
    value = (conf.get("schedule") or {}).get(key, default) if isinstance(conf.get("schedule"), dict) \
        else default
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise Refused(["board/config.json: schedule.%s must be an int >= %d (got %r)"
                       % (key, low, value)])
    return value


# ------------------------------------------------------------------------- facts


class Facts(object):
    """One read of the ticket files and git. Re-made after every child and every land call."""

    def __init__(self, root):
        self.root = root
        self.main = event.config(root).get("main_branch") or DEFAULT_MAIN
        self.tickets = {str(t["id"]): t for t in tk.load_all(event.tickets_dir(root))}
        self._ancestor = {}
        self._heads = {}
        self._files = {}

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True,
                              timeout=GIT_TIMEOUT)

    def on_main(self, sha):
        sha = str(sha or "").strip()
        if not sha:
            return False
        if sha not in self._ancestor:
            done = self.git("merge-base", "--is-ancestor", sha, self.main)
            self._ancestor[sha] = done.returncode == 0
        return self._ancestor[sha]

    def state(self, ident):
        return str((self.tickets.get(ident) or {}).get("state") or "")

    def landed(self, ident):
        row = self.tickets.get(ident) or {}
        return row.get("state") == "Done" and self.on_main(row.get("landed_sha"))

    def branch(self, ident):
        return str((self.tickets.get(ident) or {}).get("branch") or "") or "t%s" % ident

    def head(self, branch):
        if branch not in self._heads:
            done = self.git("rev-parse", "-q", "--verify", "refs/heads/%s^{commit}" % branch)
            self._heads[branch] = done.stdout.strip() if done.returncode == 0 else None
        return self._heads[branch]

    def files(self, ident):
        """Changed files of the ticket's branch against main (three-dot: since the merge base).
        None = unknown (no branch yet) — never read as "no overlap" in what is printed."""
        branch = self.branch(ident)
        if branch not in self._files:
            if not self.head(branch):
                self._files[branch] = None
            else:
                done = self.git("diff", "--name-only", "%s...%s" % (self.main, branch))
                self._files[branch] = (frozenset(line for line in done.stdout.splitlines() if line)
                                       if done.returncode == 0 else None)
        return self._files[branch]

    def deps(self, ident):
        out = []
        for dep in (self.tickets.get(ident) or {}).get("depends_on") or []:
            if isinstance(dep, dict):
                out.append((str(dep.get("id") or "").lstrip("#"), str(dep.get("condition") or "")))
            else:
                out.append((str(dep).lstrip("#"), ""))
        return out

    def dep_reasons(self, ident):
        """Why `ident` cannot start yet, one string per unsatisfied dependency; [] = all landed."""
        out = []
        for dep, condition in self.deps(ident):
            row = self.tickets.get(dep)
            if row is None:
                out.append("missing #%s" % dep)
            elif not tk.condition_understood(condition, row):
                out.append("unknown condition %r on #%s" % (condition, dep))
            elif row.get("state") in ("Cancelled", "Blocked"):
                out.append("dep #%s %s" % (dep, row.get("state")))
            elif row.get("state") != "Done":
                out.append("dep #%s not landed" % dep)
            elif not str(row.get("landed_sha") or "").strip():
                out.append("dep #%s Done without landed_sha" % dep)
            elif not self.on_main(row.get("landed_sha")):
                out.append("dep #%s landed_sha not on main" % dep)
        return out

    def gate_pass(self, ident):
        gate = (self.tickets.get(ident) or {}).get("gate")
        return isinstance(gate, dict) and gate.get("rc") == 0

    def green(self, ident):
        row = self.tickets.get(ident) or {}
        return (row.get("state") == "InReview" and self.gate_pass(ident)
                and not tk.review_problems(row))

    def awaiting_review(self, ident):
        row = self.tickets.get(ident) or {}
        return (row.get("state") == "InReview" and self.gate_pass(ident)
                and bool(tk.review_problems(row)))


def classify(facts, ident, mates, run=None):
    """(kind, text) for one ticket of a batch. kinds: landed / green / running / red / dispatch /
    waiting / driver. `mates` is the batch in landing order (overlap only looks at earlier ones)."""
    if facts.landed(ident):
        return "landed", "landed"
    if run is not None and ident in run.red:
        return "red", "red (auto-fix rc %s)" % run.red[ident]
    if run is not None and ident in run.running:
        return "running", "running (auto-fix pid %s)" % run.running[ident]
    if facts.green(ident) or (run is not None and ident in run.green):
        return "green", "green"
    state = facts.state(ident)
    if facts.awaiting_review(ident):
        return "waiting", "waiting(review)"
    reasons = facts.dep_reasons(ident)
    if reasons:
        return "waiting", "; ".join("waiting " + reason for reason in reasons)
    orphan = run is not None and state == "Running" and run.orphan(ident)
    if state == "Ready" or orphan:
        mine = facts.files(ident)
        if mine:
            for other in mates[:mates.index(ident)] if ident in mates else []:
                theirs = facts.files(other)
                if theirs and mine & theirs and not facts.landed(other):
                    return "waiting", "waiting(overlap #%s)" % other
        return "dispatch", "orphan of this run, auto-fix again" if orphan else "ready"
    if state == "Running":
        return "driver", "Running - another driver has #%s" % ident
    if state == "InReview":
        return "driver", "InReview without a gate pass - another driver has #%s" % ident
    return "waiting", "waiting(%s)" % (state or "no state")


def overlaps(facts, ids):
    """[(a, b, files)] for every pair in `ids` (landing order) whose known file sets intersect."""
    out = []
    for index, first in enumerate(ids):
        mine = facts.files(first)
        if not mine or facts.landed(first):
            continue
        for second in ids[index + 1:]:
            theirs = facts.files(second)
            if theirs and not facts.landed(second) and mine & theirs:
                out.append((first, second, sorted(mine & theirs)))
    return out


def compose(facts, greens):
    """Split the green tickets (batch order) into land calls: a ticket goes into the first call
    that holds no ticket it overlaps."""
    calls = []
    for ident in greens:
        mine = facts.files(ident) or frozenset()
        for call in calls:
            if not any(mine & (facts.files(other) or frozenset()) for other in call):
                call.append(ident)
                break
        else:
            calls.append([ident])
    return calls


# ---------------------------------------------------------------------- the plan


def load_batches(root, facts):
    """board/batches.json -> [{id, tickets}] in landing order, None when absent. Refused (rc 2)
    names every problem it finds."""
    path = os.path.join(root, BATCHES_REL)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise Refused(["%s: not valid JSON (%s)" % (BATCHES_REL, exc)])
    except OSError as exc:
        raise Refused(["%s: unreadable (%s)" % (BATCHES_REL, exc)])
    if not isinstance(data, dict) or data.get("schema") != 1:
        got = data.get("schema") if isinstance(data, dict) else type(data).__name__
        raise Refused(["%s: schema %r, expected schema 1" % (BATCHES_REL, got)])
    rows = data.get("batches")
    if not isinstance(rows, list):
        raise Refused(["%s: 'batches' must be a list of {id, tickets}" % BATCHES_REL])
    bad, out, where = [], [], {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not str(row.get("id") or "").strip() \
                or not isinstance(row.get("tickets"), list):
            bad.append("%s: batches[%d] needs an id and a tickets list" % (BATCHES_REL, index))
            continue
        bid = str(row["id"]).strip()
        if any(b["id"] == bid for b in out):
            bad.append("%s: batch id %s listed twice" % (BATCHES_REL, bid))
            continue
        ids = []
        for raw in row["tickets"]:
            ident = str(raw).strip().lstrip("#")
            if ident not in facts.tickets:
                bad.append("%s: %s lists #%s - no such ticket" % (BATCHES_REL, bid, ident))
            elif ident in where:
                bad.append("%s: #%s listed twice (%s, %s)" % (BATCHES_REL, ident, where[ident], bid))
            else:
                where[ident] = bid
                ids.append(ident)
        out.append({"id": bid, "tickets": ids})
    order = {b["id"]: index for index, b in enumerate(out)}
    for batch in out:
        for ident in batch["tickets"]:
            for dep, _ in facts.deps(ident):
                if facts.landed(dep):
                    continue
                home = where.get(dep)
                if dep not in facts.tickets:
                    bad.append("%s: #%s (%s) depends on #%s - no such ticket"
                               % (BATCHES_REL, ident, batch["id"], dep))
                elif home == batch["id"]:
                    bad.append("%s: #%s depends on #%s in the same batch %s - gates are measured "
                               "against main, not against a batch-mate; move #%s to a later batch"
                               % (BATCHES_REL, ident, dep, batch["id"], ident))
                elif home is None:
                    bad.append("%s: #%s (%s) depends on #%s, which is in no batch and not landed"
                               % (BATCHES_REL, ident, batch["id"], dep))
                elif order[home] > order[batch["id"]]:
                    bad.append("%s: #%s (%s) depends on #%s in a later batch %s"
                               % (BATCHES_REL, ident, batch["id"], dep, home))
    if bad:
        raise Refused(bad)
    return out


def check_cycles(facts):
    cycles = tk.depends_cycles(facts.tickets.values())
    if cycles:
        raise Refused(["depends_on cycle: %s" % " -> ".join("%s" % i for i in cycle)
                       for cycle in cycles] + ["nothing starts until the cycle is cut "
                                               "(edit depends_on, then ticket.py lint <id>)"])


def open_ids(facts):
    return sorted((i for i, t in facts.tickets.items() if t.get("state") not in tk.CLOSED_STATES),
                  key=id_key)


def frontier(facts, run=None):
    """The default batch: open tickets whose dependencies are landed and that can move —
    Ready, already green, or an orphan of this run. (ids ascending)"""
    out = []
    for ident in open_ids(facts):
        if facts.dep_reasons(ident) or facts.landed(ident):
            continue
        state = facts.state(ident)
        if state == "Ready" or facts.green(ident) \
                or (run is not None and state == "Running" and run.orphan(ident)):
            out.append(ident)
    return out


def describe(facts, batch_ids, run=None):
    """Plan lines for one batch: each ticket's class, files unknown, overlap pairs."""
    lines, kinds = [], {}
    for ident in batch_ids:
        kind, text = classify(facts, ident, batch_ids, run)
        kinds[ident] = kind
        extra = ""
        if kind != "landed" and facts.files(ident) is None:
            extra = "; files unknown (no branch %s)" % facts.branch(ident)
        lines.append("  #%s %s%s" % (ident, text, extra))
    for first, second, files in overlaps(facts, batch_ids):
        lines.append("overlap: #%s #%s (%s) -> #%s after #%s"
                     % (first, second, ", ".join(files), second, first))
    return lines, kinds


def plan(root):
    """(lines, model). Refused on a cycle or a bad batch file. Read-only."""
    facts = Facts(root)
    check_cycles(facts)
    batches = load_batches(root, facts)
    lines, model = [], {"main": facts.main, "batches": [], "unscheduled": [], "waiting": {}}
    if batches is not None:
        model["mode"] = "batches"
        lines.append("schedule: %s (%d batches, landing order)" % (BATCHES_REL, len(batches)))
        listed = set()
        for batch in batches:
            listed.update(batch["tickets"])
            lines.append("%s: %s" % (batch["id"], " ".join(batch["tickets"])))
            more, kinds = describe(facts, batch["tickets"])
            lines.extend(more)
            model["batches"].append({"id": batch["id"], "tickets": batch["tickets"], "kinds": kinds})
        rest = [i for i in open_ids(facts) if i not in listed]
        model["unscheduled"] = rest
        if rest:
            lines.append("unscheduled: %s" % " ".join(rest))
        return lines, model, facts, batches
    model["mode"] = "frontier"
    lines.append("schedule: no %s - the frontier (Ready or green tickets whose dependencies "
                 "are landed); recomputed after each batch lands" % BATCHES_REL)
    first = frontier(facts)
    lines.append("batch 1: %s" % (" ".join(first) if first else "(empty)"))
    more, kinds = describe(facts, first)
    lines.extend(more)
    model["batches"].append({"id": "f1", "tickets": first, "kinds": kinds})
    waiting = []
    for ident in open_ids(facts):
        if ident in first:
            continue
        _, text = classify(facts, ident, [ident])
        waiting.append("  #%s %s" % (ident, text))
        model["waiting"][ident] = text
    if waiting:
        lines.append("waiting:")
        lines.extend(waiting)
    return lines, model, facts, None


def cmd_plan(root):
    try:
        lines, _, _, _ = plan(root)
    except Refused as refused:
        for line in refused.lines:
            sys.stdout.write("schedule: %s\n" % line)
        return 2
    for line in lines:
        sys.stdout.write(line + "\n")
    return 0


# --------------------------------------------------------------------- the lock


class Lock(object):
    """`.schedule.lock` (mkdir, like land's). A holder whose pid is gone from /proc or that was
    written under another boot is stale and taken over; a live one -> rc 2 naming it."""

    def __init__(self, root, run_id):
        self.path = os.path.join(root, LOCK_NAME)
        self.run_id = run_id
        self.held = False

    def holder_line(self):
        return "pid=%d boot_id=%s run_id=%s\n" % (os.getpid(), boot_id(), self.run_id)

    def read_holder(self):
        try:
            with open(os.path.join(self.path, HOLDER), encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            return None, {}
        fields = dict(part.split("=", 1) for part in text.split() if "=" in part)
        return text.strip(), fields

    def take(self):
        try:
            os.mkdir(self.path)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            text, fields = self.read_holder()
            if text is None or "pid" not in fields:
                raise Refused(["%s is held (holder file unreadable) - another schedule run is "
                               "starting or died before writing it; rm -r %s once you are sure"
                               % (LOCK_NAME, self.path)])
            if not gone(fields.get("pid"), fields.get("boot_id")):
                raise Refused(["%s is held by pid %s (%s) - one schedule run at a time"
                               % (LOCK_NAME, fields.get("pid"), text)])
            aside = "%s.stale-%d" % (self.path, os.getpid())
            try:
                os.rename(self.path, aside)
                shutil.rmtree(aside, ignore_errors=True)
                os.mkdir(self.path)
            except OSError:
                raise Refused(["%s: taking over the stale holder pid %s raced with another run"
                               % (LOCK_NAME, fields.get("pid"))])
            sys.stdout.write("schedule: stale %s (pid %s) taken over\n"
                             % (LOCK_NAME, fields.get("pid")))
        self.held = True
        with open(os.path.join(self.path, HOLDER), "w", encoding="utf-8") as handle:
            handle.write(self.holder_line())

    def release(self):
        if self.held:
            shutil.rmtree(self.path, ignore_errors=True)
            self.held = False


# --------------------------------------------------------------------- the run


class Stop(Exception):
    """A terminal stop: one decision page attached to `ticket`."""

    def __init__(self, reason, ticket, what, next_step):
        super().__init__(what)
        self.reason, self.ticket, self.what, self.next_step = reason, ticket, what, next_step


class Run(object):

    def __init__(self, root):
        self.root = root
        conf = event.config(root)
        self.max_workers = config_int(conf, "max_workers", DEFAULT_MAX_WORKERS, 1)
        self.reland_max = config_int(conf, "reland_max", DEFAULT_RELAND_MAX, 0)
        self.run_id = "%s-%d" % (datetime.now().strftime("%Y%m%d-%H%M%S"), os.getpid())
        self.state = None
        self.running = {}        # ticket -> pid (children of this process)
        self.pids = {}           # pid -> ticket
        self.green = set()       # auto-fix rc 0 in this run
        self.red = {}            # ticket -> auto-fix rc
        self.landed_files = {}   # ticket -> files when it was put into a land call that landed
        self.landed_order = []   # (batch id, ticket) in the order they landed
        self.land_count = 0
        self.prior_children = {}
        self.home = ac_home()

    # ---------------------------------------------------------------- bookkeeping

    def run_dir(self):
        return os.path.join(schedule_dir(self.root), self.run_id)

    def log(self, line):
        stamped = "%s %s" % (now(), line)
        sys.stdout.write(("%s\n" if line.startswith("schedule: ") else "schedule: %s\n") % line)
        os.makedirs(self.run_dir(), exist_ok=True)
        with open(os.path.join(self.run_dir(), "schedule.log"), "a", encoding="utf-8") as handle:
            handle.write(stamped + "\n")

    def save(self):
        """Atomic rewrite (temp file + os.replace): a reader never sees half a file."""
        path = state_path(self.root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.tmp%d" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)

    def emit(self, kind, **fields):
        try:
            event.emit(kind, self.root, **fields)
        except (OSError, ValueError) as exc:
            sys.stderr.write("schedule: event %s not written (%s)\n" % (kind, exc))

    def entry(self, batch_id, tickets=None):
        for row in self.state["batches"]:
            if row["id"] == batch_id:
                if tickets is not None:
                    row["tickets"] = list(tickets)
                return row
        row = {"id": batch_id, "tickets": list(tickets or []), "status": "pending",
               "pre_land_heads": {}, "relands": 0}
        self.state["batches"].append(row)
        return row

    def orphan(self, ident):
        """A Running ticket this run (an interrupted earlier process of it) spawned whose child is
        gone: pid absent from /proc or recorded under another boot."""
        child = self.prior_children.get(ident)
        return isinstance(child, dict) and gone(child.get("pid"), child.get("boot_id"))

    # ---------------------------------------------------------------- start

    def load_state(self):
        """(previous state or None). A state whose stop is null is resumed (same run_id)."""
        path = state_path(self.root)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise Refused(["%s unreadable (%s) - move it aside to start a new run"
                           % (os.path.relpath(path, self.root), exc)])
        return data if isinstance(data, dict) else None

    def archive(self, old):
        """A finished run's state moves under its own run directory; the next run starts fresh."""
        where = os.path.join(schedule_dir(self.root), str(old.get("run_id") or "unknown"))
        os.makedirs(where, exist_ok=True)
        os.replace(state_path(self.root), os.path.join(where, "state.json"))

    def start(self):
        lock = Lock(self.root, self.run_id)
        old = self.load_state()
        resuming = isinstance(old, dict) and old.get("stop") is None and old.get("run_id")
        if resuming:
            self.run_id = str(old["run_id"])
            lock.run_id = self.run_id
        lock.take()
        try:
            return self.go(old if resuming else None, old)
        finally:
            lock.release()

    def go(self, resume, old):
        lines, model, facts, batches = plan(self.root)
        if resume:
            self.prior_children = dict(resume.get("children") or {})
        # Another driver: a listed ticket Running (not an orphan of this run) or InReview without
        # a gate pass -> rc 2 before anything starts. The frontier never holds such a ticket (it
        # is not Ready), so without a batch file they are only printed as waiting.
        scheduled = [i for b in batches for i in b["tickets"]] if batches is not None else []
        drivers = []
        for ident in scheduled:
            kind, text = classify(facts, ident, [ident], self)
            if kind == "driver":
                drivers.append("#%s %s" % (ident, text))
        if drivers:
            raise Refused(drivers + ["another driver is working on it - wait for its page, "
                                     "or take it out of %s" % BATCHES_REL])
        if resume:
            self.state = resume
            self.state.update({"pid": os.getpid(), "boot_id": boot_id(), "resumed": now()})
            self.state.setdefault("batches", [])
            self.state["children"] = {}
            for row in self.state["batches"]:
                if row.get("tickets") and all(facts.landed(i) for i in row["tickets"]):
                    row["status"] = "landed"
        else:
            self.state = {"schema": STATE_SCHEMA, "run_id": self.run_id, "pid": os.getpid(),
                          "boot_id": boot_id(), "started": now(), "batches": [],
                          "children": {}, "stop": None}
        queue = self.batches_to_run(facts, batches)
        if not queue and not resume:
            for line in lines:
                sys.stdout.write(line + "\n")
            sys.stdout.write("schedule: %s\n" % NOTHING)
            return 3
        if not resume and not self.can_move(facts, queue[0]):
            for line in lines:
                sys.stdout.write(line + "\n")
            sys.stdout.write("schedule: %s - batch %s: every ticket waits (reasons above)\n"
                             % (NOTHING, queue[0]["id"]))
            return 3
        if old is not None and not resume:
            self.archive(old)
        os.makedirs(self.run_dir(), exist_ok=True)
        with open(os.path.join(self.run_dir(), "plan.json"), "w", encoding="utf-8") as handle:
            json.dump(model, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        for line in lines:
            self.log(line)
        if resume:
            self.emit("schedule.resume", run_id=self.run_id)
            self.log("resume run %s" % self.run_id)
        else:
            self.emit("schedule.start", run_id=self.run_id,
                      batches=" ".join(b["id"] for b in queue) if batches is not None else "frontier",
                      max_workers=str(self.max_workers))
            self.log("start run %s (max_workers %d, reland_max %d)"
                     % (self.run_id, self.max_workers, self.reland_max))
        for batch in queue:
            self.entry(batch["id"], batch["tickets"])
        self.save()
        try:
            self.drive(batches, queue)
        except Stop as stop:
            return self.finish_stop(stop)
        return self.finish_done()

    def batches_to_run(self, facts, batches):
        """The batches with at least one ticket not landed, landing order. Frontier mode: an
        unfinished batch of the resumed state first, else the current frontier."""
        if batches is not None:
            return [b for b in batches if not all(facts.landed(i) for i in b["tickets"])]
        for row in (self.state or {}).get("batches") or []:
            if row.get("status") in ("dispatching", "landing", "pending") \
                    and not all(facts.landed(i) for i in row.get("tickets") or []):
                return [{"id": row["id"], "tickets": list(row["tickets"])}]
        first = frontier(facts, self)
        return [{"id": "f%d" % (len(self.state["batches"]) + 1), "tickets": first}] if first else []

    def can_move(self, facts, batch):
        kinds = [kind for kind, _ in self.kinds_of(facts, batch).values()]
        return "dispatch" in kinds or self.land_ready(kinds)

    @staticmethod
    def land_ready(kinds):
        """Every ticket not landed is green, or waits only on an overlap (it moves after that land)."""
        live = [k for k in kinds if k != "landed"]
        return bool(live) and "green" in live and all(k in ("green", "overlap") for k in live)

    # ---------------------------------------------------------------- drive

    def drive(self, batches, queue):
        while queue:
            batch = queue.pop(0)
            self.run_batch(batch)
            if batches is None:
                facts = Facts(self.root)
                queue = self.batches_to_run(facts, None)
                for nxt in queue:
                    self.entry(nxt["id"], nxt["tickets"])
                    self.log("next frontier %s: %s" % (nxt["id"], " ".join(nxt["tickets"])))
                self.save()

    def kinds_of(self, facts, batch):
        out = {}
        for ident in batch["tickets"]:
            kind, text = classify(facts, ident, batch["tickets"], self)
            out[ident] = ("overlap" if text.startswith("waiting(overlap") else kind, text)
        return out

    def run_batch(self, batch):
        entry = self.entry(batch["id"], batch["tickets"])
        reland = None
        if entry.get("status") == "landing":
            reland = self.resume_landing(batch, entry)
            if reland is None:
                return
        entry["status"] = "dispatching"
        self.save()
        while True:
            facts = Facts(self.root)
            kinds = self.kinds_of(facts, batch)
            if all(kind == "landed" for kind, _ in kinds.values()):
                entry["status"] = "landed"
                self.save()
                self.log("batch %s landed" % batch["id"])
                return
            if not self.red:
                for ident in batch["tickets"]:
                    if len(self.running) >= self.max_workers:
                        break
                    if kinds[ident][0] == "dispatch":
                        self.spawn(batch, entry, ident)
            if self.running:
                self.wait_one()
                continue
            if self.red:
                ident = next(i for i in batch["tickets"] if i in self.red)
                entry["status"] = "stopped"
                raise Stop("red", ident,
                           "#%s: auto-fix.sh stopped with rc %s in batch %s (it wrote its own page "
                           "for #%s); nothing else was dispatched in this batch and nothing landed"
                           % (ident, self.red[ident], batch["id"], ident),
                           "handle #%s's own page (or take #%s out of %s)"
                           % (ident, ident, BATCHES_REL))
            plain = [kind for kind, _ in kinds.values()]
            if self.land_ready(plain):
                self.land_batch(batch, entry, facts, kinds, reland)
                reland = None
                continue
            self.stuck(batch, entry, kinds)

    def stuck(self, batch, entry, kinds):
        entry["status"] = "stopped"
        waiting = [(i, text) for i, (kind, text) in kinds.items() if kind not in ("landed", "green")]
        review = [i for i, text in waiting if text == "waiting(review)"]
        lines = "; ".join("#%s %s" % (i, text) for i, text in waiting)
        if review:
            raise Stop("review", review[0],
                       "batch %s cannot land: %s" % (batch["id"], lines),
                       "complex ticket(s) %s wait for a review: run `actl review %s`"
                       % (" ".join("#" + i for i in review), review[0]))
        drivers = [i for i, (kind, _) in kinds.items() if kind == "driver"]
        first = (drivers or [i for i, _ in waiting] or batch["tickets"])[0]
        raise Stop("another-driver" if drivers else "waiting", first,
                   "batch %s cannot land: %s" % (batch["id"], lines),
                   "make #%s movable (its state / dependencies), or take it out of %s"
                   % (first, BATCHES_REL))

    # ---------------------------------------------------------------- children

    def spawn(self, batch, entry, ident):
        """fork, record the pid in state.json, then let the child exec: whoever reads the state
        while the child runs sees it under `children` (written before it starts, not after)."""
        argv = ["sh", os.path.join(self.home, "scripts", "auto-fix.sh"), ident]
        env = dict(os.environ, AC_ROOT=self.root, AC_HOME=self.home)
        out_path = os.path.join(self.run_dir(), "auto-fix-%s.log" % ident)
        out = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        gate_r, gate_w = os.pipe()
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:                                   # child
            try:
                os.close(gate_w)
                if os.read(gate_r, 1) != b"g":         # the parent died before recording us
                    os._exit(1)
                os.close(gate_r)
                null = os.open(os.devnull, os.O_RDONLY)
                os.dup2(null, 0)
                os.dup2(out, 1)
                os.dup2(out, 2)
                os.chdir(self.root)
                os.execvpe(argv[0], argv, env)
            finally:
                os._exit(127)
        os.close(gate_r)
        os.close(out)
        self.running[ident] = pid
        self.pids[pid] = ident
        self.state["children"][ident] = {"pid": pid, "boot_id": boot_id(), "started": now()}
        entry["status"] = "dispatching"
        self.save()
        self.emit("schedule.dispatch", ticket=ident, run_id=self.run_id, batch=batch["id"],
                  pid=str(pid))
        self.log("dispatch #%s (batch %s): sh %s %s -> pid %d, log %s"
                 % (ident, batch["id"], argv[1], ident, pid, os.path.relpath(out_path, self.root)))
        os.write(gate_w, b"g")
        os.close(gate_w)

    def wait_one(self):
        """Block until one child of this run exits (no polling, no timeout: auto-fix owns its own)."""
        while True:
            pid, status = os.waitpid(-1, 0)
            ident = self.pids.pop(pid, None)
            if ident is not None:
                break
        rc = os.waitstatus_to_exitcode(status)
        self.running.pop(ident, None)
        self.state["children"].pop(ident, None)
        self.prior_children.pop(ident, None)
        if rc == 0:
            self.green.add(ident)
        else:
            self.red[ident] = rc
        self.save()
        self.log("auto-fix #%s exited rc %d (%s)" % (ident, rc, "green" if rc == 0 else "red"))

    # ---------------------------------------------------------------- land

    def resume_landing(self, batch, entry):
        """A batch the interrupted run left in `landing`. Returns the re-land number to carry on
        the next land call, or None when the batch is already landed."""
        facts = Facts(self.root)
        if all(facts.landed(i) for i in batch["tickets"]):
            entry["status"] = "landed"
            self.save()
            self.log("batch %s: landed while the run was interrupted" % batch["id"])
            return None
        lock = land_lock(self.root)
        if os.path.exists(lock):
            entry["status"] = "stopped"
            raise Stop("land-lock", batch["tickets"][0],
                       "batch %s was being landed when the run was interrupted and %s is present "
                       "(a land is running, or died holding it); the scheduler never removes "
                       "land's lock" % (batch["id"], os.path.relpath(lock, self.root)),
                       "wait for that land's own page; if it is dead (heartbeat.sh says so) "
                       "remove .land.lock yourself")
        if entry.get("relands", 0) >= self.reland_max:
            entry["status"] = "stopped"
            raise Stop("reland_max", batch["tickets"][0],
                       "batch %s was being landed when the run was interrupted; landing it again "
                       "would exceed schedule.reland_max %d" % (batch["id"], self.reland_max),
                       "look at the land pages of batch %s" % batch["id"])
        entry["relands"] = entry.get("relands", 0) + 1
        self.log("batch %s: interrupted while landing - land again (re-land %d)"
                 % (batch["id"], entry["relands"]))
        return entry["relands"]

    def land_batch(self, batch, entry, facts, kinds, reland):
        greens = [i for i in batch["tickets"] if kinds[i][0] == "green"]
        calls = compose(facts, greens)
        if len(calls) > 1:
            self.log("batch %s: overlapping tickets land in separate calls: %s"
                     % (batch["id"], " | ".join(" ".join(c) for c in calls)))
        files = {i: facts.files(i) for i in calls[0]}
        call = calls[0]
        while True:
            rc = self.land_once(batch, entry, call, reland)
            after = Facts(self.root)
            if rc == 0:
                missing = [i for i in call if not after.landed(i)]
                if missing:
                    entry["status"] = "stopped"
                    raise Stop("close", missing[0],
                               "land.sh rc 0 for %s but %s not Done with landed_sha on main "
                               "(land merged it; ticket.py close refused - land's own page has why)"
                               % (" ".join(after.branch(i) for i in call),
                                  " ".join("#" + i for i in missing)),
                               "close #%s (land's page says what is missing)" % missing[0])
                self.mark_landed(batch, call, files)
                return
            if rc == LAND_CONFLICT_RC:
                self.conflict(batch, entry, call, files)
            landed = [i for i in call if after.landed(i)]
            self.mark_landed(batch, landed, files)
            rest = [i for i in call if i not in landed]
            if not rest:
                self.log("land rc %d, but every ticket of the call is landed (land's own fix "
                         "re-landed them)" % rc)
                return
            problems = self.not_fixed(after, entry, rest)
            if problems:
                entry["status"] = "stopped"
                review = all(why.startswith("review") for _, why in problems)
                text = "; ".join("#%s %s" % (i, why) for i, why in problems)
                raise Stop("review" if review else "land", problems[0][0],
                           "batch %s: land.sh %s exited rc %d and the batch is not fixed: %s"
                           % (batch["id"], " ".join(after.branch(i) for i in call), rc, text),
                           ("run `actl review %s`" % problems[0][0]) if review else
                           "handle land's own page for batch %s (land rc %d)" % (batch["id"], rc))
            if entry.get("relands", 0) >= self.reland_max:
                entry["status"] = "stopped"
                raise Stop("reland_max", rest[0],
                           "batch %s: land.sh exited rc %d again; %s fixed but schedule.reland_max "
                           "%d re-lands are used up" % (batch["id"], rc,
                                                        " ".join("#" + i for i in rest),
                                                        self.reland_max),
                           "look at land's pages for batch %s (reland_max reached)" % batch["id"])
            entry["relands"] = entry.get("relands", 0) + 1
            reland = entry["relands"]
            call = rest
            files = {i: after.files(i) for i in call}
            self.log("batch %s: land's own fix made %s green on a new head - re-land %d"
                     % (batch["id"], " ".join("#" + i for i in rest), reland))

    def not_fixed(self, facts, entry, idents):
        """[(ticket, why)] for tickets of a failed land call that are not fixed (rule 10)."""
        out = []
        for ident in idents:
            row = facts.tickets.get(ident) or {}
            branch = facts.branch(ident)
            head = facts.head(branch)
            gate = row.get("gate") if isinstance(row.get("gate"), dict) else {}
            if row.get("state") != "InReview":
                out.append((ident, "is %s, not InReview" % (row.get("state") or "?")))
            elif gate.get("rc") != 0:
                out.append((ident, "gate rc %r, not green" % gate.get("rc")))
            elif not head or gate.get("sha") != head:
                out.append((ident, "gate.sha %s is not the head of %s (%s)"
                            % (str(gate.get("sha"))[:12], branch, (head or "missing")[:12])))
            elif head == (entry.get("pre_land_heads") or {}).get(branch):
                out.append((ident, "no new commit on %s since the land call (same tree)" % branch))
            elif tk.review_problems(row):
                out.append((ident, "review missing on the new head - complex ticket needs "
                                   "review.sh"))
        return out

    def land_once(self, batch, entry, call, reland):
        facts = Facts(self.root)
        branches = [facts.branch(i) for i in call]
        entry["pre_land_heads"] = {b: facts.head(b) for b in branches}
        entry["status"] = "landing"
        self.save()
        fields = {"run_id": self.run_id, "batch": batch["id"], "branches": " ".join(branches)}
        if reland:
            fields["reland"] = str(reland)
        self.emit("schedule.land", **fields)
        self.land_count += 1
        out_path = os.path.join(self.run_dir(), "land-%d.log" % self.land_count)
        self.log("land batch %s: sh %s %s%s (log %s)"
                 % (batch["id"], os.path.join(self.home, "scripts", "land.sh"), " ".join(branches),
                    " (re-land %d)" % reland if reland else "", os.path.relpath(out_path, self.root)))
        with open(out_path, "a", encoding="utf-8") as out:
            done = subprocess.run(["sh", os.path.join(self.home, "scripts", "land.sh"), *branches],
                                  cwd=self.root, stdout=out, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL,
                                  env=dict(os.environ, AC_ROOT=self.root, AC_HOME=self.home))
        entry["status"] = "dispatching"
        self.save()
        self.log("land %s exited rc %d" % (" ".join(branches), done.returncode))
        return done.returncode

    def mark_landed(self, batch, idents, files):
        for ident in idents:
            if ident not in self.landed_files:
                self.landed_files[ident] = files.get(ident)
                self.landed_order.append((batch["id"], ident))

    def conflict(self, batch, entry, call, files):
        entry["status"] = "stopped"
        mates = [i for b, i in self.landed_order if b == batch["id"]]
        pairs = []
        for ident in call:
            mine = files.get(ident) or frozenset()
            for other in mates:
                shared = mine & (self.landed_files.get(other) or frozenset())
                if shared:
                    pairs.append("#%s overlaps #%s (landed earlier in this batch) on %s"
                                 % (ident, other, ", ".join(sorted(shared))))
        raise Stop("conflict", call[0],
                   "batch %s: land.sh for %s merge-conflicted (rc %d)%s"
                   % (batch["id"], " ".join("#" + i for i in call), LAND_CONFLICT_RC,
                      (": " + "; ".join(pairs)) if pairs else ""),
                   "resolve the conflict on #%s's branch against main (land left its worktree), "
                   "it is re-gated, then resume" % call[0])

    # ---------------------------------------------------------------- terminal

    def page(self, ticket, kind, state, what, where):
        return inbox.publish(self.root, ticket, self.run_id, kind, state, what, where,
                             lambda: inbox.PAGE % {
                                 "ticket": ticket, "subject": inbox.subject_of(self.root, ticket),
                                 "state": state, "run_id": self.run_id, "kind": kind,
                                 "at": inbox.now(), "what": what, "where": where, "note": ""})

    def where(self):
        return "%s/schedule.log (land / auto-fix logs beside it), %s" % (
            os.path.relpath(self.run_dir(), self.root),
            os.path.relpath(state_path(self.root), self.root))

    def finish_stop(self, stop):
        self.state["stop"] = {"reason": stop.reason, "at": now()}
        self.save()
        self.emit("schedule.stop", ticket=stop.ticket, run_id=self.run_id, reason=stop.reason)
        self.log("stop (%s): %s" % (stop.reason, stop.what))
        what = "%s. Next: %s; %s." % (stop.what, stop.next_step, RESUME_HINT)
        self.page(stop.ticket, "decision", "schedule stopped (%s)" % stop.reason, what, self.where())
        return 1

    def finish_done(self):
        facts = Facts(self.root)
        rows = []
        for row in self.state["batches"]:
            if row.get("status") != "landed":
                continue
            rows.append("%s: %s" % (row["id"], ", ".join(
                "#%s landed_sha %s" % (i, (facts.tickets.get(i) or {}).get("landed_sha") or "?")
                for i in row["tickets"])))
        waiting = [("#%s %s" % (i, classify(facts, i, [i], self)[1])) for i in open_ids(facts)
                   if not facts.landed(i)] if not os.path.exists(
                       os.path.join(self.root, BATCHES_REL)) else []
        listed = [i for row in self.state["batches"] for i in row.get("tickets") or []]
        last = self.landed_order[-1][1] if self.landed_order else (listed or ["?"])[-1]
        self.state["stop"] = {"reason": "done", "at": now()}
        self.save()
        self.emit("schedule.stop", ticket=last, run_id=self.run_id, reason="done")
        self.log("done: %s" % " | ".join(rows))
        what = "every batch landed - %s.%s Nothing to do; ack this page." % (
            " | ".join(rows),
            (" Still waiting (frontier empty): %s." % "; ".join(waiting)) if waiting else "")
        self.page(last, "done", "schedule done", what, self.where())
        return 0


def cmd_run(root):
    try:
        return Run(root).start()
    except Refused as refused:
        for line in refused.lines:
            sys.stdout.write("schedule: %s\n" % line)
        return 2


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        (sys.stdout if argv else sys.stderr).write(USAGE)
        return 0 if argv else 2
    verb = argv[0]
    if verb not in ("plan", "run") or len(argv) != 1:
        sys.stderr.write("schedule: unknown %r\n%s" % (" ".join(argv), USAGE))
        return 2
    root = event.repo_root()
    return cmd_plan(root) if verb == "plan" else cmd_run(root)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
