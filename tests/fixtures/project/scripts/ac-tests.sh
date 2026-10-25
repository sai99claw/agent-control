#!/bin/sh
# The fixture project's command adapter (D-071 §4 shape): every tests.* key in board/config.json
# that is not null points here. A's tests/test_testiface.py drives it through test-only knobs:
#
#   AC_TEST_FAKE_PREFLIGHT  unset → rc 0 · `broken` → rc 1 (the probe itself crashed)
#                           · anything else → rc 86 + one declared row whose why names the value
#   AC_TEST_FAKE_SELECT     normal (default) · empty (rc 0, no output) · rc3 · unmapped
#   AC_TEST_FAKE_RUN        what the fake browser runner does with browser:* ids (scripts/fake_browser.py)
#   AC_TEST_FAKE_REGRESSION unset → A's built-in (verify.py --result) · `null-exception` → one fail
#                           row whose exception is null, rc 1 · `no-result` → rc 1, no result.json
#                           · `sleep` → sleeps 120 s (the caller's timeout stops it)
#   AC_TEST_CALLS           every `run` / `regression` invocation appends one line here: argv,
#                           AC_TEST_MODE, AC_CODE_ROOT, AC_TEST_OUT (tab separated)
#
# `regression` is not wired in this board/config.json (it stays the built-in); a case that wants
# it sets `tests.regression` to `sh scripts/ac-tests.sh regression` (#163).
#
# `select` normal: browser/<name>.py → browser:safari:<name>; everything else goes to A's
# adapter (tests.unittest roots / map). `run`: unittest ids → A's adapter, browser:* ids → the
# fake runner, which merges its rows into the same result.json.
set -u
cmd=$1
shift
case "$cmd" in
    preflight)
        case "${AC_TEST_FAKE_PREFLIGHT:-}" in
            "") exit 0 ;;
            broken) echo "preflight: the probe itself crashed (fixture)" >&2; exit 1 ;;
        esac
        python3 - "$AC_TEST_FAKE_PREFLIGHT" <<'PY'
import json, sys
print(json.dumps([{"source": "declared", "engine": "host",
                   "why": "screen %s (fixture preflight)" % sys.argv[1], "count": 1,
                   "threshold": None, "log": None, "line": None}]))
PY
        exit 86 ;;
    select)
        case "${AC_TEST_FAKE_SELECT:-normal}" in
            empty) exit 0 ;;
            rc3) echo "select: no changed files" >&2; exit 3 ;;
            unmapped) echo test_ok; echo "unmapped src/x.py"; exit 0 ;;
        esac
        [ "${2:-}" = branch ] || exec python3 "$AC_HOME/scripts/adapter-unittest.py" select "$@"
        changed=$(cat)
        printf '%s\n' "$changed" | sed -n 's#^browser/\(.*\)\.py$#browser:safari:\1#p'
        rest=$(printf '%s\n' "$changed" | grep -v '^browser/' || true)
        [ -n "$rest" ] || exit 0
        printf '%s\n' "$rest" | python3 "$AC_HOME/scripts/adapter-unittest.py" select "$@"
        exit $? ;;
    run)
        if [ -n "${AC_TEST_CALLS:-}" ]; then
            printf 'run %s\tmode=%s\tcode_root=%s\tout=%s\n' "$*" "${AC_TEST_MODE:-}" \
                "${AC_CODE_ROOT:-}" "${AC_TEST_OUT:-}" >> "$AC_TEST_CALLS"
        fi
        unit=""
        browser=""
        for id in "$@"; do
            case "$id" in
                browser:*) browser="$browser $id" ;;
                *) unit="$unit $id" ;;
            esac
        done
        rc=0
        # shellcheck disable=SC2086
        [ -z "$unit" ] || python3 "$AC_HOME/scripts/adapter-unittest.py" run $unit || rc=$?
        if [ -n "$browser" ]; then
            # shellcheck disable=SC2086
            python3 scripts/fake_browser.py $browser
            brc=$?
            [ "$brc" -eq 0 ] || rc=$brc
        fi
        exit $rc ;;
    regression)
        if [ -n "${AC_TEST_CALLS:-}" ]; then
            printf 'regression %s\tmode=%s\tcode_root=%s\tout=%s\n' "$*" "${AC_TEST_MODE:-}" \
                "${AC_CODE_ROOT:-}" "${AC_TEST_OUT:-}" >> "$AC_TEST_CALLS"
        fi
        case "${AC_TEST_FAKE_REGRESSION:-}" in
            null-exception)
                python3 - "$AC_TEST_OUT/result.json" <<'PY'
import json, sys
row = {"id": "verify.fixture.test_fixture_case.T.test_fixture", "status": "fail",
       "message": "fixture: a fail row with no exception", "exception": None,
       "file": "verify/fixture/test_fixture_case.py", "line": 23, "seconds": 0.0,
       "traceback": None}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"schema": 1, "command": "regression", "ac_test_mode": None, "started": None,
               "seconds": 0.0, "ids": [row], "environment_suspect": [], "log": None}, handle)
PY
                exit 1 ;;
            no-result) echo "regression: fixture wrote no result.json"; exit 1 ;;
            sleep) sleep 120; exit 0 ;;
        esac
        exec python3 "$AC_HOME/scripts/verify.py" --result "$AC_TEST_OUT/result.json" "$@" ;;
    *)
        echo "ac-tests: unknown command $cmd" >&2
        exit 2 ;;
esac
