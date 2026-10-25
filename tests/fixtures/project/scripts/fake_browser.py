"""The fixture project's fake engine runner: one schema-1 row per `browser:*` id, as
`AC_TEST_FAKE_RUN` says, merged into `$AC_TEST_OUT/result.json` (A's adapter may already have
written the unittest rows there). Exit code follows the §4 table for that arm.

    pass (default)        every id passes, rc 0
    fail                  every id fails with an AssertionError row, rc 1
    fail-rc0              the same fail rows, but rc 0 (a runner that lies about green)
    error-null-exception  status error, exception null, rc 1
    zero-ids              result.json with ids [], rc 0
    no-result             no result.json at all, rc 0
    rc86-no-rows          rc 86 with environment_suspect []
    flaky-then-pass       fails unless AC_TEST_MODE is `rerun`
    sleep                 sleeps 60 s (A's timeout stops it), then passes
"""

import json
import os
import sys
import time

ids = sys.argv[1:]
arm = os.environ.get("AC_TEST_FAKE_RUN") or "pass"
out = os.environ["AC_TEST_OUT"]
path = os.path.join(out, "result.json")


def row(ident, state, message=None, exception=None, line=None):
    name = ident.rsplit(":", 1)[-1]
    return {"id": ident, "status": state, "message": message, "exception": exception,
            "file": "browser/%s.py" % name if line else None, "line": line,
            "seconds": 0.01, "traceback": None}


if arm == "sleep":
    time.sleep(60)
red = arm in ("fail", "fail-rc0") or (arm == "flaky-then-pass"
                                       and os.environ.get("AC_TEST_MODE") != "rerun")
if red:
    rows = [row(one, "fail", "AssertionError: fixture browser red", "AssertionError", 1)
            for one in ids]
elif arm == "error-null-exception":
    rows = [row(one, "error", "the engine crashed before any assertion") for one in ids]
elif arm == "zero-ids":
    rows = []
else:
    rows = [row(one, "pass") for one in ids]

if arm == "no-result":
    if os.path.exists(path):
        os.remove(path)
    sys.exit(0)
try:
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
except (OSError, ValueError):
    doc = {"schema": 1, "command": "run", "ac_test_mode": os.environ.get("AC_TEST_MODE"),
           "started": None, "seconds": 0.0, "ids": [], "environment_suspect": [], "log": None}
doc["ids"] = (doc.get("ids") or []) + rows if arm != "zero-ids" else []
with open(path, "w", encoding="utf-8") as handle:
    json.dump(doc, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
print("fake-browser: %s %s" % (arm, " ".join(ids)))
if arm == "rc86-no-rows":
    sys.exit(86)
if arm == "fail-rc0":
    sys.exit(0)
sys.exit(1 if red or arm == "error-null-exception" else 0)
