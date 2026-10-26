#!/usr/bin/env python3
"""A's built-in unittest adapter (D-071 C6/C7, `docs/design/project-runnable-entries.md` §4).

    printf '%s\\n' scripts/event.py | python3 scripts/adapter-unittest.py select --mode branch
    python3 scripts/adapter-unittest.py select --mode base
    AC_TEST_OUT=<dir> python3 scripts/adapter-unittest.py run test_event test_land.RefuseTest
    AC_TEST_OUT=<dir> python3 scripts/adapter-unittest.py full

**One runner in A**: `status.py run-tests` used to own the in-process unittest run; it moved here
and the shim is gone (#162: the gate calls `scripts/testiface.py`, whose built-in `select` / `run` /
`full` is this file). `EnvironmentResult` (statistical environment
suspects, `environment_fail_fast_threshold`) and `LogStream` moved here verbatim — the D-019 row shape
(`suspect_row`, `parse_environment_suspects`) stays in `status.py`, which `status.py done` needs even
in a copy that has no adapter next to it.

## Environment (§4)
`AC_ROOT` (whose `board/config.json` carries `tests.unittest`; unset = cwd's main repo root),
`AC_CODE_ROOT` (tree under test; unset = cwd's `git --show-toplevel`), `AC_TEST_OUT` (required for
`run` / `full`: missing → rc 2), `AC_TEST_MODE` (copied into `result.json`), `AC_TICKET`,
`AC_RUN_ID`, `AC_TEST_TIMEOUT` (A enforces it from outside; the adapter only reads its config).

## Exit codes (one meaning each)
0 every id pass/skip · 1 any fail/error · 86 `environment_suspect` non-empty (rows in the file) ·
2 could not run (no `AC_TEST_OUT`, zero ids, no code root, nothing discovered) · 3 `select` had
nothing to select (stderr says why).

## `result.json` (schema 1)
One row per unittest id (`module.Class.method`, rerunnable via `run <id>`). **An id that was passed
always comes back**: a name that does not load, loads zero tests, or never ran (setUpClass broke,
fail-fast stopped the run) becomes an `error` row for that id — a missing row and a green row look
the same to a reader that only counts reds (§5.5). A skip without a reason is an `error` with message
`skip without reason` for the same reason. `file` / `line` are the innermost traceback frame inside a
test file under the roots (`tests.unittest.pattern`), relative to `AC_CODE_ROOT` — what verify-case
classifies on (C7); a helper module's frame is never the answer.

## `full` in parallel (#169, D-073 / D-074)
`full` runs one shard per discovered module — `adapter-unittest.py run <module>` with its own
`AC_TEST_OUT=<out>/shard-<k>/` (and `AC_TEST_SHARD=1`: cwd = code root, as `full` has it) — at most
N at once, longest-known module first (`<reports_dir>/test-times.json`, `{module: seconds}`, written
after every `full`; absent = discovery order), then `tests.unittest.serial` one at a time. The shards
merge into one `result.json` (ids in shard order, `seconds` = wall, suspects concatenated, rc from
the merged rows) and one `full.log` (each shard's log under a `==== shard` header). A shard that
times out, exits other than 0/1/86, or leaves no valid `result.json` → rc 2, the module named on
stdout, no merged `result.json`. N is decided here at run time (`process_count`), printed as one
`parallel: ` line; `tests.parallel` only caps it, and N == 1 is the old single in-process run.
`verify.py --result` shards its case files through the same `run_shards` / `merge_shards`.
"""

import argparse
import contextlib
import fnmatch
import json
import math
import os
import subprocess
import sys
import threading
import time
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status  # noqa: E402  D-019 row shape, the declared-row parser, event (config / roots)

event = status.event

SCHEMA = 1
DEFAULT_ROOTS = ["tests"]
DEFAULT_PATTERN = "test_*.py"
STATUSES = ("pass", "fail", "error", "skip")
SKIP_WITHOUT_REASON = "skip without reason"
# Worse wins when one id reports twice (a subTest failure, then a tearDown error).
RANK = {"pass": 0, "skip": 1, "fail": 2, "error": 3}
# Peak RSS of one shard process tree, MiB — the divisor of free memory in `process_count` (D-074).
# Measured #169 A5 (wait4 ru_maxrss per shard over one parallel `full` of this repo at N 3, 4 CPUs):
# max 49.9 MiB (test_status; regression max 38.7, verify ticket_136), rounded up to 128 — a ~2.5x
# margin because ru_maxrss is the largest single process of the shard's tree, not the sum of the
# subprocesses a test holds at once.
PER_PROCESS_PEAK_MIB = 128
DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"
DEFAULT_MEMINFO = "/proc/meminfo"
TIMES_FILE = "test-times.json"
DEFAULT_TIMEOUTS = {"full": 3600, "regression": 2700}       # testiface's, when no caller's limit


# ------------------------------------------------------------ the runner (moved from status.py)


class EnvironmentResult(unittest.TextTestResult):
    """同一引擎、同形訊息連紅達門檻就中止那一段。

    只算**連續**的:中間夾一條綠或一條別的形狀就歸零。「一路都在倒同一句」是環境的
    徵狀,「散落幾條同句」是程式的 bug,這兩件事不該走同一條路。
    """

    def __init__(self, *args, threshold=status.DEFAULT_ENV_FAIL_FAST_THRESHOLD, **kwargs):
        super().__init__(*args, **kwargs)
        self.threshold = threshold
        self.last_shape = None
        self.streak = 0
        # **空值只有 `[]` 一種寫法**(D-019)。`None` 與 `[]` 揉在一起的那一刻,
        # 「還沒有量到」與「量了而沒有」在真值上長得一樣。
        self.environment_suspect = []

    def _observe(self, test, err):
        if err is None:
            self.last_shape = None
            self.streak = 0
            return
        detail = "%s\n%s" % (test, err[1])
        found = status.ENGINE.search(detail)
        if not found:
            # 認不出引擎就不猜:這一條可能只是一個普通的紅。
            self.last_shape = None
            self.streak = 0
            return
        engine = found.group(1)
        message = status.normalized_failure_message(err[1])
        shape = (engine.lower(), message)
        self.streak = self.streak + 1 if shape == self.last_shape else 1
        self.last_shape = shape
        if self.streak >= self.threshold:
            # 形狀見 `docs/DESIGN-ENV-SUSPECT.md`:一個 list,每一筆標來源。
            # `why` 是**正規化過的**訊息形狀(原 `message_shape`,改名併進來),
            # `line` 是觸發那一條紅的**原始**訊息第一行 —— 形狀認得出「同一句話」,
            # 原文才答得出「那台機器當時到底說了什麼」,兩格都要留。
            # `log` 那一格由 `run_suite` 補:這裡看不到 log 是哪一份檔。
            # `line` 用 `format_exception_only` 的第一行:那正是 log 上的
            # `AssertionError: <原話>`,而形狀(`why`)已經把數字換成 `<n>` 了。
            raw = "".join(traceback.format_exception_only(err[0], err[1])).splitlines()
            self.environment_suspect = [status.suspect_row(
                "statistical", engine=engine, why=message, count=self.streak,
                threshold=self.threshold, log="",
                line=raw[0].strip() if raw else "")]
            # 兩個旗標都要設:`shouldStop` 停的是**下一條測試方法**,而 subTest 的
            # 迴圈跑在同一個方法裡面 —— 只設它,那 12 條 subTest 會整組跑完才停。
            # `failfast` 才是 subTest 自己看的那一格(實測見 EVIDENCE M1)。
            self.failfast = True
            self.shouldStop = True

    def addSuccess(self, test):
        self._observe(test, None)
        super().addSuccess(test)

    def addFailure(self, test, err):
        self._observe(test, err)
        super().addFailure(test, err)

    def addError(self, test, err):
        self._observe(test, err)
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        self._observe(subtest, err)
        super().addSubTest(test, subtest, err)


class LogStream:
    """同一份 log 收兩種輸出:runner 的,與案例自己 `print` 的。

    `verbosity=2` 的 runner 把 `test_x (…) ... ` 寫完**不換行**就去跑案例,所以案例
    `print` 出來的字會黏在那一行尾巴。實測(#23 第 2 輪)那一行長這樣:

        test_it_declares_and_skips (…) ... ENVIRONMENT-SUSPECT: firefox 假的宣告

    於是兩件事同時壞:`line` 那一格前面多帶了一截案例名(文件要的是**整行原樣**),
    而宣告行不在行首 —— 而行首是唯一分得開「印出那一行」與「在講那一行」的東西
    (`status.parse_environment_suspects`)。所以這裡追蹤這份 log 現在停在第幾欄,**換寫入者
    而且還停在半行**的時候補一個換行。
    """

    def __init__(self, handle):
        self.handle = handle
        self.column = 0
        self.who = None

    def side(self, who):
        return LogSide(self, who)

    def write(self, who, text):
        if not text:
            return 0
        if self.column and who != self.who:
            self.handle.write("\n")
            self.column = 0
        self.who = who
        self.handle.write(text)
        if "\n" in text:
            self.column = len(text.rsplit("\n", 1)[1])
        else:
            self.column += len(text)
        return len(text)


class LogSide:
    """`LogStream` 的一個寫入者;runner 與 `redirect_stdout` 各拿一個。"""

    def __init__(self, shared, who):
        self.shared = shared
        self.who = who

    def write(self, text):
        return self.shared.write(self.who, text)

    def __getattr__(self, name):
        return getattr(self.shared.handle, name)


# ------------------------------------------------------------ per-id rows


def walk(suite):
    """Flatten a suite into its test cases, in run order."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from walk(item)
        else:
            yield item


def under(path, dirs):
    path = os.path.realpath(path)
    return any(path == d or path.startswith(d + os.sep) for d in dirs)


class RecordingResult(EnvironmentResult):
    """`EnvironmentResult` plus one `result.json` row per id.

    `aliases` maps a test object (by `id()` of the object) to the id it must be reported under:
    a name that did not load comes back from the loader as `unittest.loader._FailedTest.<last part>`,
    which `run` cannot take back — the row carries the name that was passed instead.
    """

    def __init__(self, *args, code_root=".", test_dirs=(), pattern=DEFAULT_PATTERN,
                 aliases=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.code_root = code_root
        self.test_dirs = [os.path.realpath(d) for d in test_dirs]
        self.pattern = pattern
        self.aliases = aliases or {}
        self.rows = {}
        self.order = []
        self.started_at = {}

    def ident(self, test):
        alias = self.aliases.get(id(test))
        if alias:
            return alias
        if isinstance(test, unittest.loader._FailedTest):
            # discover's import failure: the method name is the module name, which `run` takes.
            return test._testMethodName
        if isinstance(test, unittest.case._SubTest):
            return self.ident(test.test_case)
        name = test.id()
        if isinstance(test, unittest.suite._ErrorHolder):
            # `setUpClass (test_mod.Case)` / `setUpModule (test_mod)` → the id inside the parens.
            inside = name[name.find("(") + 1:name.rfind(")")] if "(" in name else name
            return inside.strip() or name
        return name

    def where(self, tb):
        found = (None, None)
        for frame in traceback.extract_tb(tb):
            if not frame.filename or not under(frame.filename, self.test_dirs):
                continue
            if not fnmatch.fnmatchcase(os.path.basename(frame.filename), self.pattern):
                continue
            found = (os.path.relpath(os.path.realpath(frame.filename),
                                     os.path.realpath(self.code_root)).replace(os.sep, "/"),
                     frame.lineno)
        return found

    def record(self, test, state, err=None, message=None):
        key = self.ident(test)
        row = {"id": key, "status": state, "message": message, "exception": None,
               "file": None, "line": None, "seconds": None, "traceback": None}
        if err is not None:
            first = "".join(traceback.format_exception_only(err[0], err[1])).splitlines()
            row["message"] = first[0].strip() if first else err[0].__name__
            row["exception"] = err[0].__name__
            row["file"], row["line"] = self.where(err[2])
            row["traceback"] = self._exc_info_to_string(err, test)
        old = self.rows.get(key)
        if old is None:
            self.order.append(key)
        elif RANK[old["status"]] >= RANK[state]:
            return
        else:
            row["seconds"] = old["seconds"]
        self.rows[key] = row

    def startTest(self, test):
        self.started_at[self.ident(test)] = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        key = self.ident(test)
        began = self.started_at.pop(key, None)
        if key in self.rows and began is not None:
            self.rows[key]["seconds"] = round(time.monotonic() - began, 3)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, "fail", err)

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, "error", err)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if str(reason or "").strip():
            self.record(test, "skip", message=str(reason))
        else:
            self.record(test, "error", message=SKIP_WITHOUT_REASON)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.record(test, "pass", message="expected failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.record(test, "fail", message="unexpected success")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is None:
            return
        failed = issubclass(err[0], test.failureException)
        self.record(test, "fail" if failed else "error", err)

    def finished_rows(self, wanted=()):
        """Rows in run order, plus an `error` row for every wanted id nothing ran for."""
        rows = [self.rows[key] for key in self.order]
        for name in wanted:
            if any(row["id"] == name or row["id"].startswith(name + ".")
                   or name.startswith(row["id"] + ".") for row in rows):
                continue
            why = ("did not run: the run stopped early (environment suspect)"
                   if self.shouldStop else "did not run")
            rows.append({"id": name, "status": "error", "message": why, "exception": None,
                         "file": None, "line": None, "seconds": None, "traceback": None})
        return rows


def run_suite(suite, log_path, threshold, code_root, test_dirs, pattern=DEFAULT_PATTERN,
              aliases=None, wanted=()):
    """Run `suite` in-process into `log_path`; return `(result, rows, suspects)`.

    The log is written the way `status.py run-tests` always wrote it: runner output and the cases'
    own `print` through one `LogStream`, so a case's `ENVIRONMENT-SUSPECT:` line lands at the start
    of a line in the log (D-019). `suspects` = statistical rows (each `log` = `log_path`) then the
    declared rows parsed back from that log.
    """
    with open(log_path, "w", encoding="utf-8") as handle:
        shared = LogStream(handle)
        runner = unittest.TextTestRunner(
            stream=shared.side("runner"), verbosity=2,
            resultclass=lambda *items, **kw: RecordingResult(
                *items, threshold=threshold, code_root=code_root, test_dirs=test_dirs,
                pattern=pattern, aliases=aliases, **kw))
        with contextlib.redirect_stdout(shared.side("case")):
            result = runner.run(suite)
    for row in result.environment_suspect:
        # `log` 只有這裡知道:嫌疑是在跑的時候量到的,而檔名是呼叫者給的。
        row["log"] = log_path
    suspects = list(result.environment_suspect) + status.parse_environment_suspects(log_path)
    return result, result.finished_rows(wanted), suspects


def load_names(loader, names):
    """`(suite, aliases, wanted)` for `run <id>…`: one `loadTestsFromName` per id, so a name that
    does not load is reported under that name, and a name that loads zero tests gets a row."""
    suite = unittest.TestSuite()
    aliases = {}
    wanted = []
    for name in names:
        if name in wanted:
            continue
        wanted.append(name)
        try:
            part = loader.loadTestsFromName(name)
        except Exception as error:                             # noqa: BLE001
            # The loader reports most failures as a `_FailedTest`; the rest (a name that is
            # not a test) raise. Both become an `error` row under the name that was passed.
            part = unittest.TestSuite([unittest.loader._FailedTest(name, error)])
        for test in walk(part):
            if isinstance(test, unittest.loader._FailedTest):
                aliases[id(test)] = name
        suite.addTest(part)
    return suite, aliases, wanted


def result_doc(command, started, seconds, rows, suspects, log):
    return {"schema": SCHEMA, "command": command,
            "ac_test_mode": os.environ.get("AC_TEST_MODE") or None,
            "started": started, "seconds": seconds, "ids": rows,
            "environment_suspect": [status.suspect_row(**{k: row.get(k) for k in status.SUSPECT_KEYS})
                                    for row in suspects],
            "log": log}


def write_result(path, doc):
    """The single writer of `result.json` (the adapter and `verify.py --result` both use it)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def exit_code(rows, suspects):
    if suspects:
        return status.ENV_SUSPECT_RC
    return 1 if any(row["status"] in ("fail", "error") for row in rows) else 0


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------------ config and roots


def code_root():
    override = os.environ.get("AC_CODE_ROOT")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    try:
        done = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True)
    except OSError:
        return None
    top = done.stdout.strip()
    return top if done.returncode == 0 and top else None


def settings():
    """`tests.unittest` of `AC_ROOT/board/config.json`, defaults filled in."""
    tests = event.config(event.repo_root()).get("tests") or {}
    unit = tests.get("unittest") if isinstance(tests, dict) else None
    unit = unit if isinstance(unit, dict) else {}
    return {"roots": list(unit.get("roots") or DEFAULT_ROOTS),
            "pattern": unit.get("pattern") or DEFAULT_PATTERN,
            "base": list(unit.get("base") or []),
            "map": list(unit.get("map") or []),
            "serial": list(unit.get("serial") or [])}


def parallel_cap():
    """`tests.parallel`: `(cap or None, error or "")`. Absent = no cap; anything but an int >= 1
    is refused — a cap that does not parse must not quietly become "no cap"."""
    tests = event.config(event.repo_root()).get("tests") or {}
    if not isinstance(tests, dict) or "parallel" not in tests or tests["parallel"] is None:
        return None, ""
    value = tests["parallel"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None, "tests.parallel must be an integer >= 1, got %r" % (value,)
    return value, ""


def run_timeout(command):
    """The whole run's limit in seconds: the caller's `AC_TEST_TIMEOUT` (testiface always sets it),
    else `tests.timeout_seconds.<command>`, else testiface's default. A shard gets what is left."""
    value = (os.environ.get("AC_TEST_TIMEOUT") or "").strip()
    if value.isdigit() and int(value) > 0:
        return int(value)
    tests = event.config(event.repo_root()).get("tests") or {}
    table = tests.get("timeout_seconds") if isinstance(tests, dict) else None
    value = table.get(command) if isinstance(table, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DEFAULT_TIMEOUTS[command]


def threshold():
    return int(event.config(event.repo_root()).get("environment_fail_fast_threshold")
               or status.DEFAULT_ENV_FAIL_FAST_THRESHOLD)


def fail(rc, text):
    sys.stderr.write("adapter-unittest: %s\n" % text)
    return rc


# ------------------------------------------------------------ parallel: how many, in what order


def read_text(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def cgroup_cores(cgroup):
    """`floor(quota / period)` of a numeric `cpu.max`, never above `os.cpu_count()`; else that."""
    cpus = os.cpu_count() or 1
    parts = (read_text(os.path.join(cgroup, "cpu.max")) or "").split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) > 0:
        return min(int(parts[0]) // int(parts[1]), cpus)
    return cpus


def available_bytes(cgroup, meminfo):
    """`memory.max - memory.current` when both are numbers, else `MemAvailable`; None = neither."""
    limit = read_text(os.path.join(cgroup, "memory.max")) or ""
    used = read_text(os.path.join(cgroup, "memory.current")) or ""
    if limit.isdigit() and used.isdigit():
        return max(0, int(limit) - int(used))
    for line in (read_text(meminfo) or "").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "MemAvailable:" and fields[1].isdigit():
            return int(fields[1]) * 1024
    return None


def process_count(cap):
    """`(N, the 'parallel: ' line)` (D-074 item 2). One core is left for the system; memory gives
    `floor(free / PER_PROCESS_PEAK_MIB)`; either below 1 → 1. `cap` (`tests.parallel`) only lowers N.
    Memory that cannot be read at all is said and taken as 1 — not as "plenty"."""
    cgroup = os.environ.get("AC_CGROUP_ROOT") or DEFAULT_CGROUP_ROOT
    meminfo = os.environ.get("AC_MEMINFO") or DEFAULT_MEMINFO
    cores = cgroup_cores(cgroup)
    free = available_bytes(cgroup, meminfo)
    count = max(1, cores - 1)
    notes = []
    if free is None:
        count = 1
        notes.append("available memory unknown, fallback 1")
    elif free // (PER_PROCESS_PEAK_MIB * 1048576) < 1:
        count = 1
        notes.append("memory short, fallback 1")
    else:
        count = min(count, free // (PER_PROCESS_PEAK_MIB * 1048576))
    if cap is not None and cap < count:
        count = cap
        notes.append("capped by tests.parallel=%d" % cap)
    memory = "unknown" if free is None else "%d MiB" % (free // 1048576)
    line = "parallel: %d processes (cores %d, available memory %s, per-process %d MiB)%s" % (
        count, cores, memory, PER_PROCESS_PEAK_MIB, "".join("; " + note for note in notes))
    return count, line


def discover_modules(dirs, pattern):
    """The module ids `full`'s discover finds, in its order, without importing one: `pattern`
    files (valid module names) in sorted order, descending only into packages (`__init__.py`)."""
    out = []

    def visit(path, prefix):
        for name in sorted(os.listdir(path)):
            where = os.path.join(path, name)
            if os.path.isfile(where):
                stem = name[:-3]
                if name.endswith(".py") and stem.isidentifier() \
                        and fnmatch.fnmatch(name, pattern):
                    out.append(prefix + stem)
            elif os.path.isfile(os.path.join(where, "__init__.py")) and name.isidentifier():
                visit(where, prefix + name + ".")

    for where in dirs:
        if os.path.isdir(where):
            visit(where, "")
    return out


def times_path():
    return os.path.join(status.reports_dir(event.repo_root()), TIMES_FILE)


def read_times():
    try:
        with open(times_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, (int, float))} \
        if isinstance(data, dict) else {}


def write_times(seconds):
    """Merge `{module: seconds}` into `<reports_dir>/test-times.json` (other modules' rows kept)."""
    if not seconds:
        return
    data = read_times()
    data.update({key: round(value, 3) for key, value in seconds.items()})
    try:
        write_result(times_path(), dict(sorted(data.items())))
    except OSError as error:
        print("adapter-unittest: %s not written (%s) - next run keeps the old order"
              % (TIMES_FILE, error))


def schedule(modules, serial):
    """Parallel modules longest-known first (unknown ones first, in discovery order — they may be
    the long ones), then the serial ones in discovery order."""
    times = read_times()
    first = [m for m in modules if m not in serial]
    first.sort(key=lambda m: -times.get(m, math.inf))
    return first + [m for m in modules if m in serial]


# ------------------------------------------------------------ parallel: run and merge shards


def wait_child(child, seconds):
    """`(rc or None on timeout, peak RSS MiB of the shard's process tree or None)`.

    `os.wait4`, not `child.wait()`: each shard's own `ru_maxrss` comes back with it
    (`RUSAGE_CHILDREN` would be one number, the max over every shard)."""
    expired = threading.Event()

    def stop():
        expired.set()
        child.kill()

    timer = threading.Timer(max(0.0, seconds), stop)
    timer.start()
    peak = None
    try:
        _, word, usage = os.wait4(child.pid, 0)
        child.returncode = os.waitstatus_to_exitcode(word)
        peak = round(usage.ru_maxrss / 1024, 1)                  # Linux: kB
    except ChildProcessError:
        # `kill()` polls first and may reap the child itself; its returncode is then set.
        pass
    finally:
        timer.cancel()
    if expired.is_set() and child.returncode is not None and child.returncode < 0:
        return None, peak
    return child.returncode, peak


def run_shard(job, deadline):
    """Run one shard to completion; fills `rc` / `seconds` / `peak` / `start` / `end` in `job`."""
    os.makedirs(job["out"], exist_ok=True)
    job["start"] = time.monotonic()
    with open(os.path.join(job["out"], "console.txt"), "wb") as console:
        child = subprocess.Popen(job["argv"], cwd=job["cwd"], env=job["env"],
                                 stdin=subprocess.DEVNULL, stdout=console,
                                 stderr=subprocess.STDOUT)
        job["rc"], job["peak"] = wait_child(child, deadline - time.monotonic())
    job["end"] = time.monotonic()
    job["seconds"] = round(job["end"] - job["start"], 3)
    return job


def run_shards(jobs, count, serial, timeout):
    """`jobs` (dicts with module / argv / cwd / env / out) in their order: the non-serial ones at
    most `count` at once, then the `serial` modules one at a time. A shard gets what is left of
    `timeout`; the run stops nobody early — every shard's outcome is reported."""
    deadline = time.monotonic() + timeout
    first = [job for job in jobs if job["module"] not in serial]
    with ThreadPoolExecutor(max_workers=max(1, count)) as pool:
        list(pool.map(lambda job: run_shard(job, deadline), first))
    for job in jobs:
        if job["module"] in serial:
            run_shard(job, deadline)
    write_times({job["module"]: job["seconds"] for job in jobs})
    return jobs


def shard_result(job):
    """`(doc, "")` for a shard whose `result.json` is valid and agrees with its exit code,
    else `(None, why)`."""
    if job["rc"] is None:
        return None, "timeout after %ds (the run's limit: AC_TEST_TIMEOUT / tests.timeout_seconds)" \
            % job["timeout"]
    path = os.path.join(job["out"], "result.json")
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except OSError:
        return None, "no result.json (exit %d)" % job["rc"]
    except ValueError:
        return None, "result.json is not valid JSON (exit %d)" % job["rc"]
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA \
            or not isinstance(doc.get("ids"), list) \
            or not all(isinstance(row, dict) and row.get("status") in STATUSES
                       for row in doc["ids"]) \
            or not isinstance(doc.get("environment_suspect"), list):
        return None, "result.json is not schema %d (exit %d)" % (SCHEMA, job["rc"])
    expected = exit_code(doc["ids"], doc["environment_suspect"])
    if job["rc"] != expected:
        return None, "exit %d but its result.json says %d" % (job["rc"], expected)
    return doc, ""


def merge_shards(jobs, log_path):
    """`(rows, suspects, problems)` over every shard in `jobs` order; writes the merged log.

    Each shard's log follows one `==== shard k/K <module>: …` header. A shard with a problem also
    gets its console output (the shard's own stdout/stderr) in the log."""
    rows, suspects, problems = [], [], []
    with open(log_path, "w", encoding="utf-8") as log:
        for index, job in enumerate(jobs, 1):
            doc, why = shard_result(job)
            log.write("==== shard %d/%d %s: %s, %.2f s, peak %s MiB ====\n" % (
                index, len(jobs), job["module"],
                "timeout" if job["rc"] is None else "rc %d" % job["rc"], job["seconds"],
                "?" if job["peak"] is None else "%g" % job["peak"]))
            text = read_text(os.path.join(job["out"], job["log"]))
            if text:
                log.write(text + "\n")
            if why:
                problems.append("shard %s (%s): %s" % (job["module"],
                                                       os.path.basename(job["out"]), why))
                console = read_text(os.path.join(job["out"], "console.txt"))
                if console:
                    log.write(console + "\n")
                continue
            rows.extend(doc["ids"])
            suspects.extend(doc["environment_suspect"])
    return rows, suspects, problems


def shard_jobs(modules, out_dir, argv_of, cwd, log_name, timeout):
    return [{"module": module, "argv": argv_of(module, os.path.join(out_dir, "shard-%d" % index)),
             "cwd": cwd, "env": dict(os.environ, AC_TEST_OUT=os.path.join(out_dir,
                                                                          "shard-%d" % index)),
             "out": os.path.join(out_dir, "shard-%d" % index), "log": log_name,
             "timeout": timeout}
            for index, module in enumerate(modules, 1)]


def module_seconds(rows, modules):
    """`{module: summed row seconds}` for the in-process run (no shard wall to read)."""
    out = {}
    for row in rows:
        owner = max((m for m in modules if row["id"] == m or row["id"].startswith(m + ".")),
                    key=len, default=None)
        if owner is not None:
            out[owner] = out.get(owner, 0) + (row.get("seconds") or 0)
    return out


# ------------------------------------------------------------ commands


def test_id(path, conf):
    """The module id of a test file under one of the roots, or None."""
    for test_root in conf["roots"]:
        prefix = test_root.rstrip("/") + "/"
        if not path.startswith(prefix) or not path.endswith(".py"):
            continue
        if not fnmatch.fnmatchcase(os.path.basename(path), conf["pattern"]):
            continue
        return path[len(prefix):-3].replace("/", ".")
    return None


def select_one(path, conf, root):
    """Ids for one changed file: map (first match wins) → `<root>/test_*.py` that exists → unmapped."""
    for row in conf["map"]:
        if fnmatch.fnmatchcase(path, row.get("match") or ""):
            return list(row.get("ids") or [])
    ident = test_id(path, conf)
    if ident is None:
        return ["unmapped %s" % path]
    # A deleted test file selects nothing (#71 A3): unittest errors on a missing module.
    return [ident] if os.path.isfile(os.path.join(root, path)) else []


def cmd_select(args):
    conf = settings()
    if args.mode == "base":
        if not conf["base"]:
            return fail(3, "select --mode base: tests.unittest.base is empty")
        out = conf["base"]
    else:
        root = code_root()
        if not root:
            return fail(2, "no code root: set AC_CODE_ROOT or run inside the tree under test")
        files = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
        if not files:
            return fail(3, "select --mode branch: no changed files on stdin")
        out = []
        for path in files:
            out.extend(select_one(path, conf, root))
        # The map is read from `AC_ROOT` (main's config), the tree is the branch: a branch that
        # deletes a test module still meets main's rows naming it (#166). Only a module this
        # same diff deletes is dropped, out loud; an id that never had a file stays and fails.
        gone = {test_id(path, conf) for path in files
                if not os.path.isfile(os.path.join(root, path))} - {None}
        for ident in sorted(gone & set(out)):
            sys.stderr.write("adapter-unittest: map names %s, which this diff deletes "
                             "- not selected\n" % ident)
        out = [item for item in out if item not in gone]
    seen = []
    for item in out:
        if item not in seen:
            seen.append(item)
    for item in seen:
        print(item)
    return 0


def cmd_run(args):
    out_dir = os.environ.get("AC_TEST_OUT")
    if not out_dir:
        return fail(2, "AC_TEST_OUT is not set: no place for result.json")
    root = code_root()
    if not root:
        return fail(2, "no code root: set AC_CODE_ROOT or run inside the tree under test")
    if args.command == "run" and not args.ids:
        # Exit 0 with zero ids is the one thing `run` must never do (§4).
        return fail(2, "run: no ids given")
    conf = settings()
    dirs = [os.path.join(root, r) for r in conf["roots"]]
    os.makedirs(out_dir, exist_ok=True)
    log_name = "%s.log" % args.command
    for where in reversed(dirs):
        sys.path.insert(0, where)
    missing = [where for where in dirs if not os.path.isdir(where)]
    if args.command == "run" and missing == dirs:
        return fail(2, "run: no test root exists (%s)" % ", ".join(conf["roots"]))
    loader = unittest.defaultTestLoader
    aliases, wanted = None, ()
    modules = []
    if args.command == "full":
        cap, why = parallel_cap()
        if why:
            return fail(2, why)
        modules = discover_modules(dirs, conf["pattern"])
        for name in conf["serial"]:
            if name not in modules:
                print("adapter-unittest: warning: tests.unittest.serial names %s, which was not "
                      "discovered - ignored" % name)
        count, line = process_count(cap)
        print(line, flush=True)
        if min(count, len(modules)) > 1:
            return full_parallel(root, modules, conf["serial"], count, out_dir)
    if args.command == "run" and args.shard:
        # One shard of a parallel `full`: cwd as `full` has it, and a module that holds zero
        # tests has no row (discover gives it none either) rather than a "did not run" error.
        os.chdir(root)
        suite, aliases, wanted = load_names(loader, args.ids)
        wanted = ()
    elif args.command == "run":
        # cwd as `run-tests --mode names` always had it: the (first existing) test root.
        os.chdir(next(where for where in dirs if os.path.isdir(where)))
        suite, aliases, wanted = load_names(loader, args.ids)
    else:
        os.chdir(root)
        suite = unittest.TestSuite()
        for where in dirs:
            if os.path.isdir(where):
                suite.addTest(loader.discover(where, pattern=conf["pattern"], top_level_dir=where))
        if not suite.countTestCases():
            return fail(2, "full: nothing discovered under %s" % ", ".join(conf["roots"]))
    started, clock = now(), time.monotonic()
    log_path = os.path.join(os.path.abspath(out_dir), log_name)
    _, rows, suspects = run_suite(suite, log_path, threshold(), root, dirs, conf["pattern"],
                                  aliases, wanted)
    if not rows and not (args.command == "run" and args.shard):
        return fail(2, "%s: no test ran" % args.command)
    doc = result_doc(args.command, started, round(time.monotonic() - clock, 3), rows,
                     suspects, log_name)
    write_result(os.path.join(out_dir, "result.json"), doc)
    if args.command == "full":
        write_times(module_seconds(rows, modules))
    return exit_code(rows, doc["environment_suspect"])


def full_parallel(root, modules, serial, count, out_dir):
    """`full` as shards (`run --shard <module>`), merged into `full.log` + `result.json`."""
    order = schedule(modules, serial)
    timeout = run_timeout("full")
    here = os.path.abspath(__file__)
    jobs = shard_jobs(order, os.path.abspath(out_dir),
                      lambda module, _out: [sys.executable, here, "run", "--shard", module],
                      root, "run.log", timeout)
    started, clock = now(), time.monotonic()
    run_shards(jobs, count, serial, timeout)
    rows, suspects, problems = merge_shards(jobs, os.path.join(out_dir, "full.log"))
    if problems:
        for problem in problems:
            print("adapter-unittest: %s" % problem)
        print("adapter-unittest: full: %d shard(s) could not be read - no result.json "
              "(see full.log)" % len(problems))
        return 2
    if not rows:
        return fail(2, "full: no test ran")
    doc = result_doc("full", started, round(time.monotonic() - clock, 3), rows, suspects,
                     "full.log")
    write_result(os.path.join(out_dir, "result.json"), doc)
    return exit_code(rows, doc["environment_suspect"])


def main(argv):
    parser = argparse.ArgumentParser(prog="adapter-unittest.py")
    subs = parser.add_subparsers(dest="command")
    select = subs.add_parser("select")
    select.add_argument("--mode", choices=("branch", "base"), required=True)
    select.set_defaults(run=cmd_select)
    run = subs.add_parser("run")
    run.add_argument("ids", nargs="*")
    # Internal: one shard of a parallel `full` (a flag, not an env var — tests inherit env).
    run.add_argument("--shard", action="store_true", help=argparse.SUPPRESS)
    run.set_defaults(run=cmd_run)
    full = subs.add_parser("full")
    full.set_defaults(run=cmd_run, shard=False)
    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help(sys.stderr)
        return 2
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
