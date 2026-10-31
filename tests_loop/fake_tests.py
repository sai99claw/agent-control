#!/usr/bin/env python3
"""Stand-in project tests for the loop tests, behind the real test interface.
case_<n> passes when impl_<n>.txt says ok; `full` also fails while red.txt exists.
preflight reports an unusable environment while $AC_ROOT/.env-bad exists."""

import glob
import json
import os
import sys

cmd, ids = sys.argv[1], sys.argv[2:]
out = os.environ.get("AC_TEST_OUT", ".")


def write(rows):
    json.dump({"schema": 1, "ids": rows, "environment_suspect": []}, open(os.path.join(out, "result.json"), "w"))
    sys.exit(1 if any(r["status"] == "fail" for r in rows) else 0)


def ok(n):
    path = "impl_%s.txt" % n
    return os.path.exists(path) and open(path).read() == "ok"


if cmd == "preflight":
    if os.path.exists(os.path.join(os.environ["AC_ROOT"], ".env-bad")):
        print(json.dumps([{"engine": "screen", "why": "screen locked"}]))
        sys.exit(86)
    sys.exit(0)
if cmd == "run":
    write([{"id": i, "status": "pass" if ok(i.split("_", 1)[1]) else "fail"} for i in ids])
if cmd == "full":
    rows = [{"id": "suite.base", "status": "pass"}]
    rows += [{"id": "impl." + os.path.basename(p), "status": "pass" if open(p).read() == "ok" else "fail"}
             for p in sorted(glob.glob("impl_*.txt"))]
    if os.path.exists("red.txt"):
        rows.append({"id": "suite.red", "status": "fail"})
    write(rows)
sys.exit(2)
