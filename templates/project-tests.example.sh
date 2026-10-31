#!/bin/sh
# <project>/scripts/ac-tests.sh — every tests.* key in board/config.json points here.
# devloop calls three commands: preflight, run and full. `full` is the regression and runs every
# layer; `run` accepts every id `full` reports.
# Template of the minimal command adapter (D-071 §4, docs/design/project-runnable-entries.md):
# copy it into the project, replace the project knowledge (preflight probe, browser runner), keep the
# contract. A runs each command as `sh -c "$cmd \"\$@\"" ac-test <args…>`
# with cwd = AC_CODE_ROOT and a fresh AC_TEST_OUT; it reads only exit codes and
# $AC_TEST_OUT/result.json (schema 1). Exit codes: 0 ok · 1 red · 86 environment suspect ·
# 2 could not run. Rehearse a command with `actl test <command> …`.
set -u
cmd=$1; shift
case "$cmd" in
  preflight)
    # lock screen / leftover Safari process / free disk: project knows, A never learns what these are
    if sh scripts/preflight-mac.sh >"$AC_TEST_OUT/preflight.txt" 2>&1; then exit 0; fi
    python3 - "$AC_TEST_OUT/preflight.txt" <<'PY'
import json, sys
why = open(sys.argv[1], encoding="utf-8").read().strip() or "preflight failed"
print(json.dumps([{"source": "declared", "engine": "host", "why": why, "count": 1,
                   "threshold": None, "log": None, "line": None}]))
PY
    exit 86 ;;
  run)
    # unittest ids → A's adapter (reuse, do not rewrite); browser ids → the project's engine runner
    unit=""; browser=""
    for id in "$@"; do case "$id" in browser:*) browser="$browser $id" ;; *) unit="$unit $id" ;; esac; done
    rc=0
    [ -z "$unit" ] || { python3 "$AC_HOME/scripts/adapter-unittest.py" run $unit || rc=$?; }
    [ -z "$browser" ] || { python3 scripts/browser-tests.py --result "$AC_TEST_OUT/result.json" $browser || rc=$?; }
    exit $rc ;;        # browser-tests.py merges its rows into result.json; both write schema 1
  full)
    # every layer: unittest adapter, then every browser engine; rows merged into result.json
    rc=0
    python3 "$AC_HOME/scripts/adapter-unittest.py" full || rc=$?
    python3 scripts/browser-tests.py --result "$AC_TEST_OUT/result.json" --all-engines || rc=$?
    exit $rc ;;        # browser-tests.py merges its rows into the unittest rows; ids: browser:<engine>:<test>
  *) echo "ac-tests: unknown $cmd" >&2; exit 2 ;;
esac
