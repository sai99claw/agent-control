#!/bin/sh
# <project>/scripts/ac-tests.sh — every tests.* key in board/config.json points here.
# Template of the minimal command adapter (D-071 §4, docs/design/project-runnable-entries.md):
# copy it into the project, replace the project knowledge (preflight probe, select table, browser
# runner), keep the contract. A runs each command as `sh -c "$cmd \"\$@\"" ac-test <args…>`
# with cwd = AC_CODE_ROOT and a fresh AC_TEST_OUT; it reads only exit codes, select's stdout and
# $AC_TEST_OUT/result.json (schema 1). Exit codes: 0 ok · 1 red · 86 environment suspect ·
# 2 could not run · 3 nothing to select. Rehearse a command with `actl test <command> …`.
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
  select)
    mode=$2
    if [ "$mode" = base ]; then echo test_ledger; echo test_invariants; exit 0; fi
    n=0
    while IFS= read -r f; do
      n=$((n+1))
      case "$f" in
        demo/*.py)       echo demo.tests.test_demo ;;
        browser/*.py)    echo browser:safari:test_engines; echo browser:chrome:test_engines ;;
        tests/test_*.py) echo "$(basename "$f" .py)" ;;
        *.md)            ;;                         # docs: no test, not unmapped (project's call)
        *)               echo "unmapped $f" ;;
      esac
    done
    [ "$n" -gt 0 ] || { echo "select: no changed files" >&2; exit 3; } ;;
  run)
    # unittest ids → A's adapter (reuse, do not rewrite); browser ids → the project's engine runner
    unit=""; browser=""
    for id in "$@"; do case "$id" in browser:*) browser="$browser $id" ;; *) unit="$unit $id" ;; esac; done
    rc=0
    [ -z "$unit" ] || { python3 "$AC_HOME/scripts/adapter-unittest.py" run $unit || rc=$?; }
    [ -z "$browser" ] || { python3 scripts/browser-tests.py --result "$AC_TEST_OUT/result.json" $browser || rc=$?; }
    exit $rc ;;        # browser-tests.py merges its rows into result.json; both write schema 1
  full)        exec python3 "$AC_HOME/scripts/adapter-unittest.py" full ;;
  regression)  exec python3 "$AC_HOME/scripts/verify.py" --result "$AC_TEST_OUT/result.json" "$@" ;;
  *) echo "ac-tests: unknown $cmd" >&2; exit 2 ;;
esac
