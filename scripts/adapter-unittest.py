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
"""

import argparse
import contextlib
import fnmatch
import json
import os
import subprocess
import sys
import time
import traceback
import unittest
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
            "map": list(unit.get("map") or [])}


def threshold():
    return int(event.config(event.repo_root()).get("environment_fail_fast_threshold")
               or status.DEFAULT_ENV_FAIL_FAST_THRESHOLD)


def fail(rc, text):
    sys.stderr.write("adapter-unittest: %s\n" % text)
    return rc


# ------------------------------------------------------------ commands


def select_one(path, conf, root):
    """Ids for one changed file: map (first match wins) → `<root>/test_*.py` that exists → unmapped."""
    for row in conf["map"]:
        if fnmatch.fnmatchcase(path, row.get("match") or ""):
            return list(row.get("ids") or [])
    for test_root in conf["roots"]:
        prefix = test_root.rstrip("/") + "/"
        if not path.startswith(prefix) or not path.endswith(".py"):
            continue
        if not fnmatch.fnmatchcase(os.path.basename(path), conf["pattern"]):
            continue
        # A deleted test file selects nothing (#71 A3): unittest errors on a missing module.
        if not os.path.isfile(os.path.join(root, path)):
            return []
        return [path[len(prefix):-3].replace("/", ".")]
    return ["unmapped %s" % path]


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
    if args.command == "run":
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
    if not rows:
        return fail(2, "%s: no test ran" % args.command)
    doc = result_doc(args.command, started, round(time.monotonic() - clock, 3), rows,
                     suspects, log_name)
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
    run.set_defaults(run=cmd_run)
    full = subs.add_parser("full")
    full.set_defaults(run=cmd_run)
    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help(sys.stderr)
        return 2
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
