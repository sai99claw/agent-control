"""Start one LLM agent (worker, verifier, reviewer, fixer, creator) and collect its hand-in.

An agent gets a prompt on stdin, works in `cwd`, and must write its hand-in as JSON to the path
in $LOOP_RESULT before it exits. Nothing else is read from it. Its stdout/stderr go to a log
(never parsed for decisions); token usage is taken from that log when the CLI prints it.
"""

import json
import os
import string
import time

from . import osx

PROMPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


class Hand:
    """What came back from one agent run."""

    def __init__(self, role, result, rc, timed_out, log, cost):
        self.role, self.result, self.rc, self.timed_out = role, result, rc, timed_out
        self.log, self.cost = log, cost

    @property
    def kind(self):
        return (self.result or {}).get("kind")

    def problem(self):
        """Why this hand-in cannot be used, or None."""
        if self.timed_out:
            return "%s ran out of time and was stopped" % self.role
        if self.result is None:
            return "%s exited (rc %s) without writing result.json; log: %s" % (
                self.role, self.rc, self.log)
        return None


def render(name, values):
    """Fill a prompt template; unknown {names} are left as written."""
    path = os.path.join(PROMPTS, name)
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot read prompt %s" % path))

    class Keep(dict):
        def __missing__(self, key):
            return "{" + key + "}"
    return string.Formatter().vformat(text, (), Keep(values))


def start(cfg, role, prompt, cwd, run_dir, timeout, extra_env=None):
    """Start an agent without waiting. Returns a Pending."""
    osx.makedirs(run_dir)
    result_path = os.path.join(run_dir, "result.json")
    try:
        os.unlink(result_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot clear %s" % result_path))
    osx.atomic_write(os.path.join(run_dir, "prompt.md"), prompt)
    env = cfg.env(LOOP_ROLE=role, LOOP_RUN_DIR=run_dir, LOOP_RESULT=result_path,
                  **(extra_env or {}))
    log = os.path.join(run_dir, "agent.log")
    argv = cfg.agent(role)
    proc = osx.spawn(argv, cwd=cwd, env=env, log_path=log, stdin_text=prompt)
    return Pending(role, proc, result_path, log, timeout, argv)


class Pending:
    def __init__(self, role, proc, result_path, log, timeout, argv):
        self.role, self.proc, self.result_path, self.log = role, proc, result_path, log
        self.timeout, self.argv, self.started = timeout, argv, time.time()

    def stop(self):
        osx.kill_tree(self.proc.pid)

    def finish(self):
        left = max(1.0, self.timeout - (time.time() - self.started))
        rc, timed_out = osx.wait(self.proc, left)
        try:
            result = osx.read_json(self.result_path, None)
        except osx.OSFailure:
            result = None
        if result is not None and not isinstance(result, dict):
            result = None
        cost = {"role": self.role, "model": _model(self.argv),
                "seconds": round(time.time() - self.started, 1), "at": osx.now()}
        cost.update(_usage(self.log))
        return Hand(self.role, result, rc, timed_out, self.log, cost)


def run(cfg, role, prompt, cwd, run_dir, timeout, extra_env=None):
    return start(cfg, role, prompt, cwd, run_dir, timeout, extra_env).finish()


def _model(argv):
    if "--model" in argv:
        i = argv.index("--model")
        if i + 1 < len(argv):
            return argv[i + 1]
    return os.path.basename(argv[0]) if argv else "unknown"


def _usage(log):
    """Token counts from a `claude -p --output-format json` log; unknown stays None."""
    out = {"input_tokens": None, "output_tokens": None, "cache_read_tokens": None}
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
            out["input_tokens"] = usage.get("input_tokens")
            out["output_tokens"] = usage.get("output_tokens")
            out["cache_read_tokens"] = usage.get("cache_read_input_tokens")
            if data.get("total_cost_usd") is not None:
                out["usd"] = data.get("total_cost_usd")
            break
    return out
