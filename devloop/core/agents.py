"""Start one LLM agent (worker, verifier, reviewer, fixer, creator, consolidator) and collect it.

An agent gets a prompt on stdin, works in `cwd`, and must write its hand-in as JSON to the path
in $DEVLOOP_RESULT before it exits. Nothing else is read from it.

Each role has a chain of commands (D-049 ②, D-084): the first one that exists is started; when a
run ends without a hand-in and its log says the account is out of quota, the next command is
tried with the same prompt. When the last command of the chain says quota and the caller
passed `on_quota` (the issuer does, the opener does not), the run waits for the reset its message
names (quota_reset) or devloop.quota_retry_seconds, then starts the chain again from its first
command (D-091). A ticket may ask for a reasoning effort (D-047); it replaces the
command's own `--effort`.

Cost (D-045, D-084): every run records role, model, effort, seconds, tokens (input, output, cache
read, cache write), dollars, session id, outcome and run dir. A run that was stopped (timeout)
prints no usage; its tokens are then read from the CLI's transcript (D-076 #124). A run its
caller stopped before collecting it (Pending.stop) gets the same row with outcome "interrupted".
"""

import glob
import json
import os
import re
import string
import time

from . import osx, slots

PROMPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
QUOTA_SIGNS = ("usage limit", "rate limit", "quota", "credit balance", "limit reached",
               "out of credits", "too many requests")


class Hand:
    """What came back from one agent run."""

    def __init__(self, role, result, rc, timed_out, log, cost, why=None):
        self.role, self.result, self.rc, self.timed_out = role, result, rc, timed_out
        self.log, self.cost, self.why = log, cost, why

    @property
    def kind(self):
        return (self.result or {}).get("kind")

    def problem(self):
        """Why this hand-in cannot be used, or None."""
        if self.why:
            return self.why
        if self.timed_out:
            return "%s ran out of time and was stopped" % self.role
        if self.result is None:
            return "%s exited (rc %s) without writing result.json; log: %s" % (
                self.role, self.rc, self.log)
        return None


# ---------------------------------------------------------------- prompts

def raw(name):
    """A prompt file with no placeholders, as written."""
    path = os.path.join(PROMPTS, name)
    try:
        with open(path) as fh:
            return fh.read()
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot read prompt %s" % path))


WEB_LIMIT = 8000              # characters of web input passed on
MARK_OPEN, MARK_CLOSE = "<<<WEB INPUT", "WEB INPUT>>>"


def web_guarded(text):
    """Web text cleaned (control characters, our own markers, length) and wrapped behind the
    injection check in prompts/web-guard.md."""
    clean = "".join(c for c in text if c in "\n\t" or ord(c) >= 32)
    clean = clean.replace(MARK_OPEN, "").replace(MARK_CLOSE, "")[:WEB_LIMIT]
    return "%s\n\n%s\n%s\n%s" % (raw("web-guard.md"), MARK_OPEN, clean, MARK_CLOSE)


def render(name, values):
    """Fill a prompt template; unknown {names} are left as written."""
    class Keep(dict):
        def __missing__(self, key):
            return "{" + key + "}"
    return string.Formatter().vformat(raw(name), (), Keep(values))


# ---------------------------------------------------------------- commands

def with_effort(argv, effort):
    """The command with its reasoning effort set to `effort` (when the command takes one)."""
    argv = list(argv)
    if not effort:
        return argv
    if effort not in EFFORTS:
        raise osx.OSFailure("effort %r is not one of %s" % (effort, ", ".join(EFFORTS)))
    if "--effort" in argv:
        i = argv.index("--effort")
        if i + 1 < len(argv):
            argv[i + 1] = effort
            return argv
    if os.path.basename(argv[0]) == "claude":
        return argv + ["--effort", effort]
    return argv


def model_of(argv):
    argv = argv or []
    if "--model" in argv:
        i = argv.index("--model")
        if i + 1 < len(argv):
            return argv[i + 1]
    return os.path.basename(argv[0]) if argv else "unknown"


def effort_of(argv):
    argv = argv or []
    if "--effort" in argv:
        i = argv.index("--effort")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


_model = model_of   # older name, still imported in places


# ---------------------------------------------------------------- running

def start(cfg, role, prompt, cwd, run_dir, timeout, extra_env=None, effort=None, round_no=None,
          chain_role=None, on_quota=None):
    """Start an agent without waiting. Returns a Pending. `chain_role` picks the commands when
    they differ from the role's (a simple ticket's worker uses "worker_simple").

    `on_quota(phase, row=None, until=None, text=None)`: when given, a run whose last command is
    out of quota waits for the reset instead of ending. Before the wait it is called with phase
    "wait", the run's cost row (outcome "quota"), the epoch the wait ends and an excerpt of the
    CLI's message; after the wait with phase "over". Without it the run ends as before."""
    osx.makedirs(run_dir)
    result_path = os.path.join(run_dir, "result.json")
    osx.atomic_write(os.path.join(run_dir, "prompt.md"), prompt)
    env = cfg.env(DEVLOOP_ROLE=role, DEVLOOP_RUN_DIR=run_dir, DEVLOOP_RESULT=result_path,
                  **(extra_env or {}))
    chain = [with_effort(argv, effort) for argv in cfg.agent_chain(chain_role or role)]
    p = Pending(role, chain, prompt, cwd, env, run_dir, result_path, timeout, round_no, cfg,
                on_quota=on_quota)
    p.launch()
    return p


def run(cfg, role, prompt, cwd, run_dir, timeout, extra_env=None, effort=None, round_no=None,
        on_quota=None):
    return start(cfg, role, prompt, cwd, run_dir, timeout, extra_env, effort, round_no,
                 on_quota=on_quota).finish()


class Interrupted(Exception):
    """Raised by a script's SIGTERM/SIGINT handler."""


class Pending:
    def __init__(self, role, chain, prompt, cwd, env, run_dir, result_path, timeout, round_no, cfg=None,
                 on_quota=None):
        self.role, self.chain, self.prompt, self.cwd, self.env = role, chain, prompt, cwd, env
        self.run_dir, self.result_path, self.timeout, self.round_no = run_dir, result_path, timeout, round_no
        self.cfg = cfg
        self.on_quota = on_quota
        self.full_chain = list(chain)
        self.started = time.time()
        self.proc = self.argv = self.log = self.slot = None
        # seconds spent waiting for a machine slot (every launch adds) or for a quota reset
        self.waited = 0.0
        self.waited_before = 0.0  # self.waited when this run's own clock (self.started) last started
        self.logs = 0            # agent-<n>.log files used so far (they keep counting across quota waits)
        self.tried = []
        self.sleeping = False    # waiting for a quota reset: the row is already with the caller
        self.hand = None         # set once finish() collected the run; its row is in the Hand

    def launch(self):
        """Start the next command of the chain that exists, under a machine slot. The wait has no
        limit and does not count against this run's timeout (added to self.waited); the slot is
        freed the moment the command's process exits."""
        self._release()
        if self.cfg is not None:
            began = time.time()
            self.slot = slots.take(self.cfg, None)
            self.waited += time.time() - began
        try:
            self._spawn_next()
        except BaseException:
            self._release()
            raise
        if self.slot is not None:
            self.slot.release_when_done(self.proc)

    def _release(self):
        if self.slot is not None:
            self.slot.release()

    def _spawn_next(self):
        last = None
        env = self.env
        if self.slot is not None and self.slot.held:
            env = dict(self.env, **{slots.ENV: self.slot.path})
        while self.chain:
            argv = self.chain.pop(0)
            try:
                os.unlink(self.result_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise osx.OSFailure(osx.explain(exc, "cannot clear %s" % self.result_path))
            self.logs += 1
            self.log = os.path.join(self.run_dir, "agent-%d.log" % self.logs)
            self.attempt_started = time.time()
            try:
                self.proc = osx.spawn(argv, cwd=self.cwd, env=env, log_path=self.log,
                                      stdin_text=self.prompt)
            except osx.OSFailure as exc:
                self.tried.append({"command": argv[0], "model": model_of(argv), "outcome": "not found"})
                last = exc
                continue
            self.argv = argv
            return
        raise osx.OSFailure("no command for %s could start (%s)" % (self.role, last))

    def stop(self):
        """Stop the run. Returns its cost row with outcome "interrupted", or None when finish()
        already collected it (that row is in its Hand) or it is waiting for a quota reset (that
        row went to on_quota)."""
        if self.proc is not None:
            osx.kill_tree(self.proc.pid)
        self._release()
        if self.hand is not None or self.sleeping:
            return None
        return self._cost(None, None, False, False, outcome="interrupted")

    def finish(self):
        self.hand = self._finish()
        return self.hand

    def _finish(self):
        while True:
            # only the time under a slot counts
            left = max(1.0, self.timeout - (time.time() - self.started - (self.waited - self.waited_before)))
            rc, timed_out = osx.wait(self.proc, left)
            try:
                result = osx.read_json(self.result_path, None)
            except osx.OSFailure:
                result = None
            if result is not None and not isinstance(result, dict):
                result = None
            out_of_quota = result is None and not timed_out and _says_quota(self.log)
            if out_of_quota and self.chain:
                self.tried.append({"command": self.argv[0], "model": model_of(self.argv),
                                   "outcome": "out of quota"})
                self.launch()
                continue
            if out_of_quota and self.on_quota is not None:
                self._wait_for_quota()
                continue
            self._release()
            cost = self._cost(result, rc, timed_out, out_of_quota)
            return Hand(self.role, result, rc, timed_out, self.log, cost)

    def _wait_for_quota(self):
        """The last command said quota: hand the row to the caller, sleep until the reset with no
        slot held, then start the whole chain again with a fresh clock (D-091)."""
        self._release()
        row = self._cost(None, None, False, True, outcome="quota")
        text, at = _log_tail(self.log)
        now = time.time()
        retry = getattr(self.cfg, "quota_retry_seconds", None) or 1800
        wait = quota_wait(text, at, now, retry)
        self.proc = None
        self.on_quota("wait", row=row, until=now + wait, text=_excerpt(text))
        self.sleeping = True     # from here on the row is the caller's: stop() returns None
        began = time.time()
        time.sleep(max(0.0, now + wait - began))
        self.waited += time.time() - began
        self.on_quota("over")
        self.chain = list(self.full_chain)
        self.tried = []
        self.started = time.time()
        self.waited_before = self.waited
        self.launch()
        self.sleeping = False

    def _seconds(self):
        """Wall time since start; a run stopped within a tenth of a second keeps more digits
        rather than reading 0."""
        spent = time.time() - self.started
        return round(spent, 1) or round(spent, 4)

    def _cost(self, result, rc, timed_out, out_of_quota, outcome=None):
        if outcome is None:
            if timed_out:
                outcome = "timeout"
            elif out_of_quota:
                outcome = "out of quota"
            elif result is None:
                outcome = "no hand-in"
            else:
                outcome = result.get("kind") or "hand-in"
        cost = {"role": self.role, "model": model_of(self.argv), "effort": effort_of(self.argv),
                "command": os.path.basename(self.argv[0]), "round": self.round_no,
                "seconds": self._seconds(), "at": osx.now(),
                "outcome": outcome, "run_dir": self.run_dir}
        if self.tried:
            cost["fell_back_from"] = self.tried
        usage = usage_from_log(self.log)
        if usage.get("input_tokens") is None and os.path.basename(self.argv[0]) == "claude":
            usage = usage_from_transcript(self.cwd, self.attempt_started) or usage
        cost.update(usage)
        return cost


def _log_tail(log):
    """(the log's last 8000 characters, the log's mtime); ("", now) when it cannot be read."""
    try:
        with open(log, errors="replace") as fh:
            text = fh.read()[-8000:]
        return text, os.path.getmtime(log)
    except OSError:
        return "", time.time()


def _says_quota(log):
    text, at = _log_tail(log)
    low = text.lower()
    return any(sign in low for sign in QUOTA_SIGNS) or quota_reset(text, at) is not None


def _excerpt(text, limit=200):
    """The part of the CLI's message that says quota, at most `limit` characters."""
    low = text.lower()
    hits = [low.rfind(sign) for sign in QUOTA_SIGNS + ("reset",)]
    hits = [h for h in hits if h >= 0]
    if not hits:
        return text.strip()[-limit:]
    i = max(hits)
    begin = text.rfind("\n", 0, i) + 1
    end = text.find("\n", i)
    line = text[begin:end if end >= 0 else len(text)].strip()
    return line[:limit]


# ---------------------------------------------------------------- quota reset

_UNITS = {"h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
          "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60}
_CLOCK = re.compile(r"\breset(?:s)?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?(?:\s*(am|pm)\b)?", re.I)
_SPAN = re.compile(r"\breset(?:s)?\s+in\s+((?:\d+\s*(?:hours?|hrs?|h|minutes?|mins?|m)\b[\s,]*(?:and\s+)?)+)",
                   re.I)
_SPAN_PART = re.compile(r"(\d+)\s*(hours?|hrs?|h|minutes?|mins?|m)\b", re.I)
_EPOCH = re.compile(r"limit reached\s*\|\s*(\d{9,11})\b", re.I)
GRACE = 600   # a time of day at most this many seconds before `at` is today's (the CLI's clock may run ahead)


def quota_reset(text, at):
    """The epoch a CLI's usage-limit message says the limit resets, or None (D-091 item 5).

    Read: a time of day ("resets 3pm", "resets 3:30pm", "resets at 15:00", "will reset at 5pm"),
    local time, the next one after `at` (one at most GRACE s before `at` is today's); a duration
    ("resets in 2h 30m", "resets in 1 hour"); or an epoch ("... limit reached|1762473600"). The
    last match in the text wins."""
    found = []
    for m in _CLOCK.finditer(text or ""):
        hour, minute, half = int(m.group(1)), m.group(2), (m.group(3) or "").lower()
        minute = int(minute) if minute is not None else None
        if half:
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if half == "pm" else 0)
        elif minute is None or hour > 23:
            continue
        if minute is not None and minute > 59:
            continue
        day = time.localtime(at)
        when = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, hour, minute or 0, 0, 0, 0, -1))
        if when < at - GRACE:
            when = time.mktime((day.tm_year, day.tm_mon, day.tm_mday + 1, hour, minute or 0, 0, 0, 0, -1))
        found.append((m.start(), when))
    for m in _SPAN.finditer(text or ""):
        seconds = sum(int(n) * _UNITS[u.lower()] for n, u in _SPAN_PART.findall(m.group(1)))
        found.append((m.start(), at + seconds))
    for m in _EPOCH.finditer(text or ""):
        found.append((m.start(), float(m.group(1))))
    if not found:
        return None
    return max(found, key=lambda f: f[0])[1]


def quota_wait(text, at, now, retry_seconds):
    """Seconds to wait before running again: until a minute after the reset the text names (at
    least 60), else retry_seconds."""
    reset = quota_reset(text, at)
    if reset is None:
        return retry_seconds
    return max(60, reset + 60 - now)


# ---------------------------------------------------------------- usage

EMPTY = {"input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
         "cache_write_tokens": None, "usd": None, "session_id": None}


def usage_from_log(log):
    """Token counts from a `claude -p --output-format json` log; unknown stays None."""
    out = dict(EMPTY)
    try:
        with open(log, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return out
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            out.update(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                       cache_read_tokens=usage.get("cache_read_input_tokens"),
                       cache_write_tokens=usage.get("cache_creation_input_tokens"),
                       usd=data.get("total_cost_usd"), session_id=data.get("session_id"))
            break
    return out


def usage_from_transcript(cwd, since):
    """Sum the usage of a stopped run from the CLI transcript written under ~/.claude/projects.

    The transcript folder is the run's cwd with every non-alphanumeric character turned into "-";
    the run's file is the newest one there that was written after it started."""
    folder = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                          re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(cwd)))
    try:
        files = [f for f in glob.glob(os.path.join(folder, "*.jsonl")) if os.path.getmtime(f) >= since - 5]
    except OSError:
        return None
    if not files:
        return None
    path = max(files, key=os.path.getmtime)
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    seen = set()
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                msg = row.get("message") if isinstance(row, dict) else None
                usage = msg.get("usage") if isinstance(msg, dict) else None
                if not isinstance(usage, dict) or msg.get("id") in seen:
                    continue
                seen.add(msg.get("id"))
                totals["input_tokens"] += usage.get("input_tokens") or 0
                totals["output_tokens"] += usage.get("output_tokens") or 0
                totals["cache_read_tokens"] += usage.get("cache_read_input_tokens") or 0
                totals["cache_write_tokens"] += usage.get("cache_creation_input_tokens") or 0
    except OSError:
        return None
    if not seen:
        return None
    out = dict(EMPTY)
    out.update(totals)
    out["session_id"] = os.path.basename(path)[:-6]
    out["from_transcript"] = True
    return out


def total(rows):
    """Sum of cost rows (D-045: the ticket's total when it lands). A field nobody recorded stays
    None - unknown is not zero."""
    keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "usd", "seconds")
    out = {k: None for k in keys}
    for row in rows or []:
        for k in keys:
            v = row.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = (out[k] or 0) + v
    out["runs"] = len(rows or [])
    out["unknown_runs"] = sum(1 for r in rows or [] if r.get("input_tokens") is None)
    return out
