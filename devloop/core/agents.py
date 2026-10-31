"""Start one LLM agent (worker, verifier, reviewer, fixer, creator, consolidator) and collect it.

An agent gets a prompt on stdin, works in `cwd`, and must write its hand-in as JSON to the path
in $DEVLOOP_RESULT before it exits. Nothing else is read from it.

Each role has a chain of commands (D-049 ②, D-084): the first one that exists is started; when a
run ends without a hand-in and its log says the account is out of quota, the next command is
tried with the same prompt. A ticket may ask for a reasoning effort (D-047); it replaces the
command's own `--effort`.

Cost (D-045, D-084): every run records role, model, effort, seconds, tokens (input, output, cache
read, cache write), dollars, session id, outcome and run dir. A run that was stopped (timeout)
prints no usage; its tokens are then read from the CLI's transcript (D-076 #124).
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
          chain_role=None):
    """Start an agent without waiting. Returns a Pending. `chain_role` picks the commands when
    they differ from the role's (a simple ticket's worker uses "worker_simple")."""
    osx.makedirs(run_dir)
    result_path = os.path.join(run_dir, "result.json")
    osx.atomic_write(os.path.join(run_dir, "prompt.md"), prompt)
    env = cfg.env(DEVLOOP_ROLE=role, DEVLOOP_RUN_DIR=run_dir, DEVLOOP_RESULT=result_path,
                  **(extra_env or {}))
    chain = [with_effort(argv, effort) for argv in cfg.agent_chain(chain_role or role)]
    p = Pending(role, chain, prompt, cwd, env, run_dir, result_path, timeout, round_no, cfg)
    p.launch()
    return p


def run(cfg, role, prompt, cwd, run_dir, timeout, extra_env=None, effort=None, round_no=None):
    return start(cfg, role, prompt, cwd, run_dir, timeout, extra_env, effort, round_no).finish()


class Pending:
    def __init__(self, role, chain, prompt, cwd, env, run_dir, result_path, timeout, round_no, cfg=None):
        self.role, self.chain, self.prompt, self.cwd, self.env = role, chain, prompt, cwd, env
        self.run_dir, self.result_path, self.timeout, self.round_no = run_dir, result_path, timeout, round_no
        self.cfg = cfg
        self.started = time.time()
        self.proc = self.argv = self.log = self.slot = None
        self.no_slot = None      # the sentence when no machine slot came free in time
        self.tried = []

    def launch(self):
        """Start the next command of the chain that exists, under a machine slot (the wait counts
        against this run's timeout; the slot is freed the moment the command's process exits)."""
        self._release()
        if self.cfg is not None:
            self.slot = slots.take(self.cfg, self.started + self.timeout)
            if self.slot is None:
                self.proc = None
                self.no_slot = "%s was not started: %s" % (
                    self.role, slots.busy(self.cfg, time.time() - self.started))
                return
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
            self.log = os.path.join(self.run_dir, "agent-%d.log" % (len(self.tried) + 1))
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
        if self.proc is not None:
            osx.kill_tree(self.proc.pid)
        self._release()

    def finish(self):
        while True:
            if self.no_slot:
                return self._unstarted()
            left = max(1.0, self.timeout - (time.time() - self.started))
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
            self._release()
            cost = self._cost(result, rc, timed_out, out_of_quota)
            return Hand(self.role, result, rc, timed_out, self.log, cost)

    def _unstarted(self):
        """The hand of a run that never got a machine slot: nothing ran, tokens unknown."""
        argv = self.chain[0] if self.chain else self.argv
        cost = {"role": self.role, "model": model_of(argv), "effort": effort_of(argv),
                "command": os.path.basename(argv[0]) if argv else None, "round": self.round_no,
                "seconds": round(time.time() - self.started, 1), "at": osx.now(),
                "outcome": "no slot", "run_dir": self.run_dir}
        if self.tried:
            cost["fell_back_from"] = self.tried
        cost.update(EMPTY)
        return Hand(self.role, None, None, False, self.log, cost, why=self.no_slot)

    def _cost(self, result, rc, timed_out, out_of_quota):
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
                "seconds": round(time.time() - self.started, 1), "at": osx.now(),
                "outcome": outcome, "run_dir": self.run_dir}
        if self.tried:
            cost["fell_back_from"] = self.tried
        usage = usage_from_log(self.log)
        if usage.get("input_tokens") is None and os.path.basename(self.argv[0]) == "claude":
            usage = usage_from_transcript(self.cwd, self.attempt_started) or usage
        cost.update(usage)
        return cost


def _says_quota(log):
    try:
        with open(log, errors="replace") as fh:
            text = fh.read()[-8000:].lower()
    except OSError:
        return False
    return any(sign in text for sign in QUOTA_SIGNS)


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
