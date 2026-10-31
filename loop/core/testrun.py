"""Run the project's tests through the existing test interface (`scripts/testiface.py`).

The interface owns how a project runs tests (board/config.json `tests.*`); this module only
calls it on a given tree and reads its one result file. Exit codes of the interface:
0 green · 1 red · 86 environment not usable · 2 could not measure · 3 nothing selected.
"""

import os
import shutil
import tempfile

from . import osx

GREEN, RED, ENV, UNMEASURED = "green", "red", "env", "unmeasured"


class Outcome:
    def __init__(self, status, failures=None, env_rows=None, detail="", log_dir=None):
        self.status, self.failures, self.env_rows = status, failures or [], env_rows or []
        self.detail, self.log_dir = detail, log_dir

    def summary(self, limit=30):
        if self.status == GREEN:
            return "green"
        if self.status == ENV:
            return "environment not usable: " + "; ".join(
                str(r.get("why") or r) for r in self.env_rows[:5]) if self.env_rows else self.detail
        if self.status == UNMEASURED:
            return "could not measure: " + self.detail
        lines = ["%d failing:" % len(self.failures)]
        for row in self.failures[:limit]:
            lines.append("- %s %s %s" % (row.get("id"), row.get("exception") or "",
                                         (row.get("excerpt") or "").strip().splitlines()[-1:] or ""))
        return "\n".join(lines)


def run(cfg, code_root, command, ids=(), timeout=None, keep_dir=None):
    """Run `preflight`, `run <ids>` or `full` on `code_root`."""
    out = keep_dir or tempfile.mkdtemp(prefix="loop-test-")
    if keep_dir:
        shutil.rmtree(out, ignore_errors=True)
    osx.makedirs(os.path.dirname(out) if keep_dir else out)
    iface = os.path.join(cfg.home, "scripts", "testiface.py")
    env = cfg.env(AC_CODE_ROOT=code_root, AC_TEST_OUT=out,
                  AC_TEST_TIMEOUT=int(timeout) if timeout else None)
    res = osx.run(["python3", iface, command] + list(ids), cwd=code_root, env=env,
                  timeout=(timeout + 120) if timeout else None, what="tests " + command)
    if res.timed_out:
        return Outcome(UNMEASURED, detail="%s timed out" % command, log_dir=out)
    result = None
    try:
        result = osx.read_json(os.path.join(out, "result.json"), None)
    except osx.OSFailure:
        result = None
    rows = (result or {}).get("ids") or []
    failures = [r for r in rows if isinstance(r, dict) and r.get("status") in ("fail", "error")]
    if res.rc == 0:
        return Outcome(GREEN, log_dir=out)
    if res.rc == 1:
        return Outcome(RED, failures=failures, log_dir=out)
    if res.rc == 86:
        env_rows = (result or {}).get("environment_suspect") or []
        if not env_rows:
            try:
                env_rows = osx.read_json(os.path.join(out, "environment_suspect.json"), []) or []
            except osx.OSFailure:
                env_rows = []
        return Outcome(ENV, env_rows=env_rows, detail=_tail(res), log_dir=out)
    return Outcome(UNMEASURED, detail=_tail(res) or "rc %s" % res.rc, log_dir=out)


def _tail(res):
    lines = (res.err or res.out or "").strip().splitlines()
    return lines[-1] if lines else ""
