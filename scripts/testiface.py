#!/usr/bin/env python3
"""The project test interface: five commands, one result file (D-071 C6, `docs/design/
project-runnable-entries.md` §4 "How A invokes a command").

    python3 scripts/testiface.py [--suspect-file F] preflight
    printf '%s\\n' scripts/event.py | python3 scripts/testiface.py select --mode branch
    python3 scripts/testiface.py select --mode base
    python3 scripts/testiface.py run test_event browser:safari:test_login
    python3 scripts/testiface.py full
    python3 scripts/testiface.py regression --tag example
    actl test <command> …                     # the same thing through the entry

**The only place A runs a project's tests.** `gate.sh` (and, after row 3c, verify-case) call this
and nothing else; A knows *when* to test and *what to do with the result*, the project knows
*which ids* and *how to run them*. The command for each name is `tests.<command>` in
`AC_ROOT/board/config.json` — a shell string — or, when that key is null or absent, the built-in:

    preflight   rc 0 (nothing to check)
    select      python3 "$AC_HOME/scripts/adapter-unittest.py" select
    run / full  python3 "$AC_HOME/scripts/adapter-unittest.py" run / full
    regression  python3 "$AC_HOME/scripts/verify.py" --result "$AC_TEST_OUT/result.json"

It runs as `sh -c "$cmd \\"\\$@\\"" ac-test <args…>` with cwd `AC_CODE_ROOT`, a fresh empty
`AC_TEST_OUT` (the caller's, which must be absent or empty, else `<reports_dir>/t<n>/<run_id>/
<command>/` or `<reports_dir>/gate/<run_id>/<command>/`) and env `AC_HOME AC_ROOT AC_CODE_ROOT
AC_TEST_OUT AC_TEST_MODE AC_TICKET AC_RUN_ID AC_TEST_TIMEOUT`. `AC_HOME` is the A this file lives
in: the built-in adapter is always the one next to it (the gate's companion rule, D-071 C3).

Stdout + stderr go to `$AC_TEST_OUT/<command>.log`; a command that writes that file itself (the
unittest adapter writes `run.log` / `full.log`) keeps it, and what it printed is appended after.
`select`'s stdout is also printed here (the ids); `select` / `preflight` stderr is echoed to this
stderr (the caller shows it). A reads decisions only from exit codes, `select`'s stdout and
`result.json` — never from the log.

## Exit codes (after validation; one meaning each)
0 green (result validated) · 1 red (result validated, ≥1 fail/error row) · 86 environment suspect
(rows copied to `--suspect-file` and `$AC_TEST_OUT/environment_suspect.json`) · 2 unmeasured, the
reason on stderr · 3 `select` / `regression` had nothing to select (the command's own rc 3).

**Every "could not answer" is rc 2, never 0 and never 1** (§5.5): a timeout
(`tests.timeout_seconds.<command>`, defaults 60/60/1800/3600/2700; a caller's `AC_TEST_TIMEOUT`
that is a smaller positive integer wins — verify-case passes `verify_case.timeout_seconds` that
way, #163) is `unmeasured: timeout`;
`select` rc 0 with empty stdout; `run`/`full`/`regression` whose `result.json` is missing,
invalid, `schema != 1`, has zero ids, ids other than the ones passed (`run`; a row id covers a
passed id it equals or is nested under, as the adapter reports methods for a module id), a
fail/error row under rc 0, no fail/error row under rc 1, rows under rc 0/1, or rc 86 with
`environment_suspect: []`; `preflight` rc 86 without a JSON list of rows on stdout, or any other
non-zero `preflight` ("preflight itself failed").
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import status  # noqa: E402  reports_dir, the D-019 row shape, ENV_SUSPECT_RC

event = status.event

COMMANDS = ("preflight", "select", "run", "full", "regression")
DEFAULT_TIMEOUTS = {"preflight": 60, "select": 60, "run": 1800, "full": 3600, "regression": 2700}
BUILTIN = {
    "preflight": None,
    "select": 'python3 "$AC_HOME/scripts/adapter-unittest.py" select',
    "run": 'python3 "$AC_HOME/scripts/adapter-unittest.py" run',
    "full": 'python3 "$AC_HOME/scripts/adapter-unittest.py" full',
    "regression": 'python3 "$AC_HOME/scripts/verify.py" --result "$AC_TEST_OUT/result.json"',
}
SCHEMA = 1
GREEN, RED, UNMEASURED, NOTHING = 0, 1, 2, 3
SUSPECT = status.ENV_SUSPECT_RC
USAGE = ("usage: testiface.py [--suspect-file <path>] <%s> [args…]" % "|".join(COMMANDS))


def say(text):
    sys.stderr.write("testiface: %s\n" % text)
    sys.stderr.flush()


# ------------------------------------------------------------ what to run, where


def tests_conf(root):
    tests = event.config(root).get("tests")
    return tests if isinstance(tests, dict) else {}


def command_string(conf, command):
    """`(shell string or None, declared?)` — `None` only for the built-in preflight."""
    value = conf.get(command)
    if isinstance(value, str) and value.strip():
        return value, True
    return BUILTIN[command], False


def timeout_of(conf, command):
    table = conf.get("timeout_seconds")
    value = table.get(command) if isinstance(table, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DEFAULT_TIMEOUTS[command]


def caller_timeout():
    """The caller's `AC_TEST_TIMEOUT` when it is a positive integer, else None. It can only
    shorten the configured limit: an inherited value never stretches a command's own timeout."""
    value = (os.environ.get("AC_TEST_TIMEOUT") or "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else None


def code_root():
    override = os.environ.get("AC_CODE_ROOT")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    done = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    top = done.stdout.strip()
    return top if done.returncode == 0 and top else None


def fresh_out(root, command):
    """`(dir, why)`: the caller's `AC_TEST_OUT` when it is absent or empty, else the default
    under reports (a suffix keeps it fresh). A non-empty caller dir is refused, never cleaned."""
    given = os.environ.get("AC_TEST_OUT")
    if given:
        path = os.path.abspath(os.path.expanduser(given))
        if os.path.isdir(path) and os.listdir(path):
            return None, "AC_TEST_OUT %s is not empty (one fresh directory per command)" % path
        os.makedirs(path, exist_ok=True)
        return path, ""
    ticket = os.environ.get("AC_TICKET") or ""
    run_id = os.environ.get("AC_RUN_ID") or "%s-%d" % (
        datetime.now().strftime("%Y%m%d-%H%M%S"), os.getpid())
    base = os.path.join(status.reports_dir(root), "t%s" % ticket if ticket else "gate", run_id)
    path, n = os.path.join(base, command), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(base, "%s-%d" % (command, n))
    os.makedirs(path)
    return path, ""


def mode_of(command, args):
    if os.environ.get("AC_TEST_MODE"):
        return os.environ["AC_TEST_MODE"]
    if command == "select" and "--mode" in args[:-1]:
        return args[args.index("--mode") + 1]
    return "full" if command == "full" else "branch"


# ------------------------------------------------------------ running it


def launch(cmd, args, env, cwd, out, command, timeout, stdin_text, split):
    """Run `cmd` once; return `(rc or None on timeout, stdout text, stderr text)`.

    Output goes to files, not pipes: a grandchild that outlives the timeout cannot hold a pipe
    open. The child gets its own session, so a timeout stops the whole group this call started.
    `split` keeps stdout and stderr apart (select / preflight read stdout); otherwise they share
    one file in the order they were written."""
    scratch = tempfile.mkdtemp(prefix="testiface-")   # outside AC_TEST_OUT: it stays empty
    capture_out = os.path.join(scratch, "stdout")
    capture_err = os.path.join(scratch, "stderr") if split else capture_out
    stdin_path = os.path.join(scratch, "stdin")
    with open(stdin_path, "w", encoding="utf-8") as handle:
        handle.write(stdin_text)
    rc = None
    with open(capture_out, "wb") as so, open(stdin_path, "rb") as si:
        se = open(capture_err, "wb") if split else so
        try:
            child = subprocess.Popen(["sh", "-c", '%s "$@"' % cmd, "ac-test", *args],
                                     cwd=cwd, env=env, stdin=si, stdout=so, stderr=se,
                                     start_new_session=True)
            try:
                rc = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except OSError:
                    pass
                child.wait()
        finally:
            if split:
                se.close()
    texts = []
    for path in (capture_out, capture_err) if split else (capture_out,):
        with open(path, encoding="utf-8", errors="replace") as handle:
            texts.append(handle.read())
    log = os.path.join(out, "%s.log" % command)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("".join(texts))
        if rc is None:
            handle.write("\ntestiface: unmeasured: timeout after %ds\n" % timeout)
    shutil.rmtree(scratch, ignore_errors=True)
    stdout = texts[0]
    stderr = texts[1] if split else ""
    return rc, stdout, stderr


# ------------------------------------------------------------ checking what came back


def covers(passed, got):
    return got == passed or got.startswith(passed + ".") or passed.startswith(got + ".")


def check_result(command, out, rc, passed):
    """`(rows of environment_suspect, why)`; `why` non-empty = unmeasured."""
    path = os.path.join(out, "result.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError:
        return None, "result.json missing (%s rc %d)" % (command, rc)
    except ValueError:
        return None, "result.json is not valid JSON"
    if not isinstance(data, dict):
        return None, "result.json is not a JSON object"
    if data.get("schema") != SCHEMA:
        return None, "result.json schema is %r, expected %d" % (data.get("schema"), SCHEMA)
    ids = data.get("ids")
    if not isinstance(ids, list) or not all(
            isinstance(row, dict) and isinstance(row.get("id"), str)
            and row.get("status") in ("pass", "fail", "error", "skip") for row in ids):
        return None, "result.json ids are not a list of {id, status}"
    suspects = data.get("environment_suspect")
    if not isinstance(suspects, list) or not all(isinstance(row, dict) for row in suspects):
        return None, "result.json environment_suspect is not a list of rows"
    if rc == SUSPECT:
        if not suspects:
            return None, "rc 86 but environment_suspect is [] in result.json"
        return suspects, ""
    if suspects:
        return None, "rc %d but result.json has %d environment_suspect row(s)" % (rc, len(suspects))
    if not ids:
        return None, "zero ids in result.json (%s rc %d)" % (command, rc)
    got = [row["id"] for row in ids]
    if command == "run":
        missing = [name for name in passed if not any(covers(name, one) for one in got)]
        extra = [one for one in got if not any(covers(name, one) for name in passed)]
        if missing or extra:
            return None, ("result.json ids do not match the ids passed (missing %s, not asked %s)"
                          % (missing, extra))
    reds = [row["id"] for row in ids if row["status"] in ("fail", "error")]
    if rc == GREEN and reds:
        return None, "rc 0 but result.json has fail/error rows: %s" % ", ".join(reds[:5])
    if rc == RED and not reds:
        return None, "rc 1 but result.json has no fail/error row"
    return [], ""


def write_rows(rows, out, suspect_file):
    rows = [status.suspect_row(**{key: row.get(key) for key in status.SUSPECT_KEYS})
            for row in rows]
    text = json.dumps(rows, ensure_ascii=False) + "\n"
    targets = [os.path.join(out, "environment_suspect.json")]
    if suspect_file:
        targets.append(os.path.abspath(suspect_file))
    for target in targets:
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
    return targets[-1]


def preflight_rows(stdout):
    try:
        rows = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        return None
    return rows


# ------------------------------------------------------------ main


def parse(argv):
    suspect_file = ""
    rest = list(argv)
    while rest and rest[0] == "--suspect-file":
        if len(rest) < 2:
            return None
        suspect_file, rest = rest[1], rest[2:]
    if not rest or rest[0] not in COMMANDS:
        return None
    command, args = rest[0], rest[1:]
    while args[:1] == ["--suspect-file"] and len(args) >= 2:
        suspect_file, args = args[1], args[2:]
    return command, args, suspect_file


def main(argv):
    parsed = parse(argv)
    if parsed is None:
        sys.stderr.write(USAGE + "\n")
        return UNMEASURED
    command, args, suspect_file = parsed
    if command == "run":
        seen = []
        for one in args:
            if one not in seen:
                seen.append(one)
        args = seen
        if not args:
            say("run: no ids given (run must never exit 0 with zero ids)")
            return UNMEASURED
    root = event.repo_root()
    conf = tests_conf(root)
    cmd, declared = command_string(conf, command)
    code = code_root()
    if not code:
        say("no code root: set AC_CODE_ROOT or run inside the tree under test")
        return UNMEASURED
    out, why = fresh_out(root, command)
    if out is None:
        say(why)
        return UNMEASURED
    timeout, limit = timeout_of(conf, command), "tests.timeout_seconds.%s" % command
    asked = caller_timeout()
    if asked is not None and asked < timeout:
        timeout, limit = asked, "the caller's AC_TEST_TIMEOUT"
    env = dict(os.environ)
    env.update({
        "AC_HOME": os.path.dirname(HERE), "AC_ROOT": root, "AC_CODE_ROOT": code,
        "AC_TEST_OUT": out, "AC_TEST_MODE": mode_of(command, args),
        "AC_TICKET": os.environ.get("AC_TICKET") or "",
        "AC_RUN_ID": os.environ.get("AC_RUN_ID") or "",
        "AC_TEST_TIMEOUT": str(timeout),
    })
    if cmd is None:
        with open(os.path.join(out, "preflight.log"), "w", encoding="utf-8") as handle:
            handle.write("built-in preflight: nothing to check\n")
        return GREEN
    stdin_text = sys.stdin.read() if command == "select" else ""
    started = time.monotonic()
    rc, stdout, stderr = launch(cmd, args, env, code, out, command, timeout, stdin_text,
                                split=command in ("select", "preflight"))
    if stderr:
        sys.stderr.write(stderr)
        sys.stderr.flush()
    source = "tests.%s" % command if declared else "built-in %s" % command
    if rc is None:
        say("unmeasured: timeout — %s ran longer than %ds (%s); "
            "log %s" % (source, timeout, limit, os.path.join(out, "%s.log" % command)))
        return UNMEASURED
    if command == "preflight":
        if rc == GREEN:
            return GREEN
        if rc == SUSPECT:
            rows = preflight_rows(stdout)
            if rows is None:
                say("preflight rc 86 but stdout is not a non-empty JSON list of "
                    "environment_suspect rows")
                return UNMEASURED
            where = write_rows(rows, out, suspect_file)
            say("preflight: environment suspect, %d row(s) → %s" % (len(rows), where))
            return SUSPECT
        say("preflight itself failed (%s rc %d); log %s"
            % (source, rc, os.path.join(out, "preflight.log")))
        return UNMEASURED
    if command == "select":
        if rc == GREEN and not stdout.strip():
            say("select rc 0 with empty stdout — nothing named, not even unmapped (§5.5)")
            return UNMEASURED
        if rc in (GREEN, NOTHING):
            sys.stdout.write(stdout)
            return rc
        say("select failed (%s rc %d)" % (source, rc))
        return UNMEASURED
    if command == "regression" and rc == NOTHING:
        return NOTHING
    if rc not in (GREEN, RED, SUSPECT):
        say("%s exited %d — could not run (%.1fs); log %s"
            % (source, rc, time.monotonic() - started, os.path.join(out, "%s.log" % command)))
        return UNMEASURED
    rows, why = check_result(command, out, rc, args)
    if why:
        say("unmeasured: %s; out %s" % (why, out))
        return UNMEASURED
    if rc == SUSPECT:
        where = write_rows(rows, out, suspect_file)
        say("%s: environment suspect, %d row(s) → %s" % (command, len(rows), where))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
