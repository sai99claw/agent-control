"""Built-in unittest adapter (`scripts/adapter-unittest.py`): result.json schema 1 cases.

Oracle: docs/design/project-runnable-entries.md section 4 ("The five commands" table,
"result.json (schema 1)" field table, "Built-in unittest adapter") and ticket #161's
objective. Expected values are computed from fixture sources written here (marker
comments locate the line numbers), never from a previous adapter run.

Each case builds a throwaway project under a tempdir (AC_ROOT == AC_CODE_ROOT) and runs
the adapter as a child process with `subprocess.run(timeout=...)`.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import AC_HOME, SCRIPTS  # noqa: E402


class AdapterResultCases(unittest.TestCase):
    """Two roots (T's shape), helper-frame file/line, and ids set == ids passed."""

    TIMEOUT = 120
    ROW_KEYS = {"id", "status", "message", "exception", "file", "line", "seconds", "traceback"}

    FX = textwrap.dedent('''\
        import os
        import sys
        import unittest

        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
        import fxhelp


        def check_local(x):
            assert x == 2, "local helper"  # MARK-LOCAL-ASSERT


        class T(unittest.TestCase):
            def test_pass(self):
                self.assertTrue(True)

            def test_local_helper(self):
                check_local(1)  # MARK-LOCAL-CALL

            def test_lib_helper(self):
                fxhelp.check(1)  # MARK-LIB-CALL

            def test_multiline(self):
                self.fail("line one\\nline two")
        ''')

    LIB = textwrap.dedent('''\
        def check(x):
            assert x == 2, "lib helper"  # MARK-LIB-ASSERT
        ''')

    DEMO = textwrap.dedent('''\
        import unittest


        class T(unittest.TestCase):
            def test_x(self):
                self.assertEqual(1, 1)
        ''')

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="adapter-cases-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self._write("board/config.json", json.dumps(
            {"tests": {"adapter": "unittest",
                       "unittest": {"roots": ["tests", "demo/tests"]}}}))
        self._write("tests/test_fx.py", self.FX)
        self._write("lib/fxhelp.py", self.LIB)
        self._write("demo/tests/test_demo.py", self.DEMO)
        # Same content, name outside the pattern: reachable by `run <id>`, not by `full`.
        self._write("demo/tests/demo_mod.py", self.DEMO)

    def _write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def _line(self, source, marker):
        hits = [n for n, text in enumerate(source.splitlines(), 1) if marker in text]
        self.assertEqual(len(hits), 1, marker)
        return hits[0]

    def _script(self):
        script = os.path.join(SCRIPTS, "adapter-unittest.py")
        # python3 on a missing file exits 2 too: without this, the rc-2 cases are green on nothing
        self.assertTrue(os.path.isfile(script), script)
        return script

    def _adapter(self, *argv, out="out"):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AC_")}
        out_dir = os.path.join(self.root, out)
        os.makedirs(out_dir, exist_ok=True)
        env.update({"AC_HOME": AC_HOME, "AC_ROOT": self.root, "AC_CODE_ROOT": self.root,
                    "AC_TEST_OUT": out_dir, "AC_TEST_MODE": "branch",
                    "AC_TICKET": "", "AC_RUN_ID": "", "AC_TEST_TIMEOUT": "100"})
        proc = subprocess.run(
            [sys.executable, self._script()] + list(argv),
            cwd=self.root, env=env, capture_output=True, text=True, timeout=self.TIMEOUT)
        path = os.path.join(out_dir, "result.json")
        result = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                result = json.load(handle)
        return proc, result, out_dir

    def _rows(self, proc, result):
        detail = "rc=%s\nstdout=%s\nstderr=%s" % (proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
        self.assertIsNotNone(result, "no result.json\n" + detail)
        self.assertEqual(result.get("schema"), 1, detail)
        rows = {}
        for row in result["ids"]:
            self.assertEqual(set(row) & self.ROW_KEYS, self.ROW_KEYS, row)
            self.assertNotIn(row["id"], rows, "duplicate id row")
            rows[row["id"]] = row
        return rows

    def test_full_discovers_both_roots(self):
        proc, result, out_dir = self._adapter("full")
        rows = self._rows(proc, result)
        self.assertEqual(result["command"], "full")
        expected = {"test_fx.T.test_pass", "test_fx.T.test_local_helper",
                    "test_fx.T.test_lib_helper", "test_fx.T.test_multiline",
                    "test_demo.T.test_x"}
        self.assertEqual(set(rows), expected)
        self.assertEqual(rows["test_demo.T.test_x"]["status"], "pass")
        self.assertEqual(proc.returncode, 1)  # fixture has fails
        self.assertEqual(result["environment_suspect"], [])
        self.assertIsInstance(result["log"], str)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, result["log"])), result["log"])

    def test_run_id_in_second_root(self):
        proc, result, _ = self._adapter("run", "demo_mod.T.test_x")
        rows = self._rows(proc, result)
        self.assertEqual(result["command"], "run")
        self.assertEqual(set(rows), {"demo_mod.T.test_x"})
        self.assertEqual(rows["demo_mod.T.test_x"]["status"], "pass")
        self.assertEqual(proc.returncode, 0)

    def test_fail_in_helper_reports_test_file_frame(self):
        ids = ["test_fx.T.test_local_helper", "test_fx.T.test_lib_helper"]
        proc, result, _ = self._adapter("run", *ids)
        rows = self._rows(proc, result)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(set(rows), set(ids))
        local = rows["test_fx.T.test_local_helper"]
        self.assertEqual(local["status"], "fail")
        self.assertEqual(local["exception"], "AssertionError")
        self.assertEqual(local["file"], "tests/test_fx.py")
        # innermost frame inside the test file: the helper's assert, not the call site
        self.assertEqual(local["line"], self._line(self.FX, "MARK-LOCAL-ASSERT"))
        self.assertTrue(local["traceback"])
        lib = rows["test_fx.T.test_lib_helper"]
        self.assertEqual(lib["status"], "fail")
        # lib/ is outside the roots: the innermost test-file frame is the call in the test
        self.assertEqual(lib["file"], "tests/test_fx.py")
        self.assertEqual(lib["line"], self._line(self.FX, "MARK-LIB-CALL"))
        self.assertIn("lib/fxhelp.py", lib["traceback"])

    def test_message_is_first_line_only(self):
        proc, result, _ = self._adapter("run", "test_fx.T.test_multiline")
        row = self._rows(proc, result)["test_fx.T.test_multiline"]
        self.assertEqual(row["status"], "fail")
        self.assertNotIn("\n", row["message"])
        self.assertIn("line one", row["message"])
        self.assertNotIn("line two", row["message"])
        self.assertIn("line two", row["traceback"])

    def test_unknown_ids_get_error_rows(self):
        ids = ["test_fx.T.test_pass", "test_fx.T.test_does_not_exist", "test_nomod_zzz.T.test_x"]
        proc, result, _ = self._adapter("run", *ids)
        rows = self._rows(proc, result)
        self.assertEqual(set(rows), set(ids), "result ids must equal the ids passed")
        self.assertEqual(rows["test_fx.T.test_pass"]["status"], "pass")
        for bad in ids[1:]:
            self.assertEqual(rows[bad]["status"], "error", bad)
            self.assertTrue(rows[bad]["message"], bad)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(result["environment_suspect"], [])


class AdapterParallelCases(unittest.TestCase):
    """#169 A1 / A2 / A4: `full` shards one subprocess per module, merges into one schema-1 result.

    Oracle: the ticket's acceptance text. Statuses come from what each fixture module declares
    (`DECLARED` below); the expected process count N is arithmetic on the fixture numbers
    (cgroup v2 files and meminfo written under the tempdir, AC_CGROUP_ROOT / AC_MEMINFO point at
    them); the adapter's PER_PROCESS_PEAK_MIB is read only to size those numbers. No memory is
    allocated, nothing under /sys is written.
    """

    TIMEOUT = 120
    MIB = 1024 * 1024
    LINE = (r"^parallel: (\d+) processes \(cores (\d+), available memory ([\d.]+) MiB, "
            r"per-process ([\d.]+) MiB\)(.*)$")
    # test_two of each fixture module: body -> the status that module declares for it
    DECLARED = {"pass": ("pass", "self.assertTrue(True)"),
                "skip": ("skip", 'self.skipTest("declared skip")'),
                "fail": ("fail", 'self.fail("declared fail")'),
                "suspect": ("pass", 'print("ENVIRONMENT-SUSPECT: chromium screen locked mid run")')}

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="adapter-par-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.stamps = os.path.join(self.root, "stamps.txt")
        self.expected = {}

    # ---------------------------------------------------------------- helpers

    def _write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def _peak(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "adapter_unittest_t169", os.path.join(SCRIPTS, "adapter-unittest.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "PER_PROCESS_PEAK_MIB"),
                        "adapter-unittest.py has no PER_PROCESS_PEAK_MIB")
        peak = module.PER_PROCESS_PEAK_MIB
        self.assertTrue(isinstance(peak, int) and peak > 0, peak)
        return peak

    def _config(self, parallel=None, serial=None):
        tests = {"adapter": "unittest", "unittest": {"roots": ["tests"]}}
        if parallel is not None:
            tests["parallel"] = parallel
        if serial is not None:
            tests["unittest"]["serial"] = serial
        self._write("board/config.json", json.dumps({"tests": tests}))

    def _cgroup(self, cpu="400000 100000", free_mib=None, mem_max=None, avail_kb=None):
        """Fake cgroup v2 dir + meminfo. `free_mib` -> memory.max - memory.current == free_mib MiB."""
        cg = os.path.join(self.root, "cg")
        os.makedirs(cg, exist_ok=True)
        if cpu is not None:
            self._write("cg/cpu.max", cpu + "\n")
        if free_mib is not None:
            current = 64 * self.MIB
            self._write("cg/memory.current", "%d\n" % current)
            self._write("cg/memory.max", "%d\n" % (current + int(free_mib * self.MIB)))
        elif mem_max is not None:
            self._write("cg/memory.max", mem_max + "\n")
            self._write("cg/memory.current", "%d\n" % (64 * self.MIB))
        if avail_kb is None:
            avail_kb = 1000 * 1024 * 1024        # 1000 GiB: never the limiter unless asked
        self._write("meminfo", "MemTotal:       %d kB\nMemFree:        %d kB\n"
                    "MemAvailable:   %d kB\n" % (avail_kb + 1024, avail_kb, avail_kb))
        return cg

    def _module(self, name, sleep=0.0, two="pass", extra=""):
        status, body = self.DECLARED[two]
        source = textwrap.dedent('''\
            import os
            import shutil
            import time
            import unittest

            NAME = %r
            STAMPS = %r


            def _stamp(kind):
                with open(STAMPS, "a", encoding="utf-8") as handle:
                    handle.write("%%s %%s %%.6f\\n" %% (kind, NAME, time.time()))


            def setUpModule():
                _stamp("start")


            def tearDownModule():
                _stamp("end")
            %s

            class T(unittest.TestCase):
                def test_sleep(self):
                    time.sleep(%r)

                def test_one(self):
                    self.assertEqual(1, 1)

                def test_two(self):
                    %s
            ''') % (name, self.stamps, extra, sleep, body)
        self._write("tests/%s.py" % name, source)
        self.expected.update({"%s.T.test_sleep" % name: "pass", "%s.T.test_one" % name: "pass",
                              "%s.T.test_two" % name: status})

    def _full(self, cg=None):
        import time
        env = {k: v for k, v in os.environ.items() if not k.startswith("AC_")}
        out_dir = os.path.join(self.root, "out")
        shutil.rmtree(out_dir, True)
        os.makedirs(out_dir)
        if os.path.exists(self.stamps):
            os.remove(self.stamps)
        env.update({"AC_HOME": AC_HOME, "AC_ROOT": self.root, "AC_CODE_ROOT": self.root,
                    "AC_TEST_OUT": out_dir, "AC_TEST_MODE": "full",
                    "AC_TICKET": "", "AC_RUN_ID": "", "AC_TEST_TIMEOUT": "100",
                    "AC_CGROUP_ROOT": cg or self._cgroup(free_mib=100 * self._peak()),
                    "AC_MEMINFO": os.path.join(self.root, "meminfo")})
        script = os.path.join(SCRIPTS, "adapter-unittest.py")
        self.assertTrue(os.path.isfile(script), script)
        clock = time.monotonic()
        proc = subprocess.run([sys.executable, script, "full"], cwd=self.root, env=env,
                              capture_output=True, text=True, timeout=self.TIMEOUT)
        wall = time.monotonic() - clock
        result = None
        path = os.path.join(out_dir, "result.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                result = json.load(handle)
        return proc, result, out_dir, wall

    def _detail(self, proc):
        return "rc=%s\nstdout=%s\nstderr=%s" % (proc.returncode, proc.stdout[-3000:],
                                                proc.stderr[-3000:])

    def _rows(self, proc, result):
        self.assertIsNotNone(result, "no result.json\n" + self._detail(proc))
        self.assertEqual(result.get("schema"), 1)
        rows = {}
        for row in result["ids"]:
            self.assertNotIn(row["id"], rows, "duplicate id row %s" % row["id"])
            rows[row["id"]] = row
        return rows

    def _shards(self, out_dir):
        import glob
        return sorted(glob.glob(os.path.join(out_dir, "shard-*", "result.json")))

    def _log(self, out_dir, result):
        self.assertIsInstance(result.get("log"), str)
        path = os.path.join(out_dir, result["log"])
        self.assertTrue(os.path.isfile(path), path)
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()

    def _parallel_line(self, proc):
        import re
        lines = [line for line in proc.stdout.splitlines() if line.startswith("parallel: ")]
        self.assertEqual(len(lines), 1, "want exactly one 'parallel: ' line\n" + self._detail(proc))
        found = re.match(self.LINE, lines[0])
        self.assertIsNotNone(found, lines[0])
        return lines[0], found

    def _intervals(self):
        self.assertTrue(os.path.exists(self.stamps), "no module wrote a timestamp")
        spans = {}
        with open(self.stamps, encoding="utf-8") as handle:
            for line in handle.read().splitlines():
                kind, name, when = line.split()
                spans.setdefault(name, {})[kind] = float(when)
        return spans

    def _overlap(self, spans):
        events = sorted([(s["start"], 1) for s in spans.values()]
                        + [(s["end"], -1) for s in spans.values()], key=lambda e: (e[0], e[1]))
        now = most = 0
        for _, step in events:
            now += step
            most = max(most, now)
        return most

    @staticmethod
    def _expected_n(cores, free_mib, peak, cap=None):
        n = min(max(1, cores - 1), int(free_mib // peak))
        n = max(n, 1)
        return min(n, cap) if cap else n

    def _need_four(self):
        if (os.cpu_count() or 1) < 4:
            self.skipTest("needs os.cpu_count() >= 4 (have %s)" % os.cpu_count())

    # ---------------------------------------------------------------- A1

    def test_a1_parallel_three_shards_merge_and_order(self):
        self._need_four()
        self._config(parallel=3)
        self._module("test_a", 2, "pass")
        self._module("test_b", 2, "skip")
        self._module("test_c", 2, "pass")
        self._write("reports/test-times.json", json.dumps({"test_a": 1.0, "test_b": 30.0,
                                                           "test_c": 10.0}))
        proc, result, out_dir, wall = self._full()
        rows = self._rows(proc, result)
        self.assertEqual(proc.returncode, 0, self._detail(proc))
        self.assertEqual(len(result["ids"]), 9)
        self.assertEqual({k: r["status"] for k, r in rows.items()}, self.expected)
        self.assertEqual(len(self._shards(out_dir)), 3, os.listdir(out_dir))
        log = self._log(out_dir, result)
        self.assertEqual(len([l for l in log.splitlines() if l.startswith("Ran ")]), 3, log[-3000:])
        first = [log.find(name) for name in ("test_b", "test_c", "test_a")]
        self.assertNotIn(-1, first, "a module name is missing from the merged log")
        self.assertEqual(first, sorted(first), "log order must follow test-times.json "
                         "(test_b 30 > test_c 10 > test_a 1)")
        self.assertLess(wall, 4.5, "three 2 s modules under parallel=3")

    def test_a1_parallel_one_is_todays_single_run(self):
        self._config(parallel=1)
        self._module("test_a", 2, "pass")
        self._module("test_b", 2, "skip")
        self._module("test_c", 2, "pass")
        proc, result, out_dir, wall = self._full()
        rows = self._rows(proc, result)
        self.assertEqual(proc.returncode, 0, self._detail(proc))
        self.assertEqual({k: r["status"] for k, r in rows.items()}, self.expected)
        self.assertGreaterEqual(wall, 6.0)
        self.assertEqual([n for n in os.listdir(out_dir) if n.startswith("shard-")], [])
        self._parallel_line(proc)

    def test_a1_serial_module_runs_after_the_parallel_phase(self):
        self._need_four()
        self._config(parallel=3, serial=["test_c"])
        for name in ("test_a", "test_b", "test_c"):
            self._module(name, 2, "pass")
        proc, result, out_dir, _ = self._full()
        rows = self._rows(proc, result)
        self.assertEqual(proc.returncode, 0, self._detail(proc))
        self.assertEqual({k: r["status"] for k, r in rows.items()}, self.expected)
        self.assertEqual(len(self._shards(out_dir)), 3, "every module is a shard")
        spans = self._intervals()
        self.assertEqual(set(spans), {"test_a", "test_b", "test_c"}, spans)
        a, b, c = spans["test_a"], spans["test_b"], spans["test_c"]
        self.assertTrue(a["start"] < b["end"] and b["start"] < a["end"],
                        "test_a and test_b ran in parallel: %s" % spans)
        self.assertGreater(c["start"], max(a["end"], b["end"]), spans)

    def test_a1_serial_name_not_discovered_is_a_warning(self):
        self._config(parallel=3, serial=["test_zz_nowhere"])
        for name in ("test_a", "test_b"):
            self._module(name)
        proc, result, _, _ = self._full()
        rows = self._rows(proc, result)
        self.assertEqual(proc.returncode, 0, self._detail(proc))
        self.assertEqual({k: r["status"] for k, r in rows.items()}, self.expected)
        hits = [l for l in proc.stdout.splitlines() if "serial" in l and "test_zz_nowhere" in l]
        self.assertEqual(len(hits), 1, self._detail(proc))

    # ---------------------------------------------------------------- A2

    def test_a2_worst_rc_wins_a_failing_module_not_first(self):
        self._config(parallel=3)
        self._module("test_a")
        self._module("test_b")
        self._module("test_c", two="fail")
        proc, result, out_dir, _ = self._full()
        rows = self._rows(proc, result)
        self.assertEqual(len(self._shards(out_dir)), 3, os.listdir(out_dir))
        self.assertEqual(proc.returncode, 1, self._detail(proc))
        self.assertEqual({k: r["status"] for k, r in rows.items()}, self.expected)
        self.assertEqual(rows["test_c.T.test_two"]["status"], "fail")

    def test_a2_declared_environment_suspect_is_rc_86(self):
        self._config(parallel=3)
        self._module("test_a")
        self._module("test_b", two="suspect")
        self._module("test_c")
        proc, result, out_dir, _ = self._full()
        rows = self._rows(proc, result)
        self.assertEqual(len(self._shards(out_dir)), 3, os.listdir(out_dir))
        self.assertEqual(proc.returncode, 86, self._detail(proc))
        self.assertEqual({k: r["status"] for k, r in rows.items()}, self.expected)
        suspects = result["environment_suspect"]
        self.assertEqual(len(suspects), 1, suspects)
        row = suspects[0]
        self.assertEqual(set(row), {"source", "engine", "why", "count", "threshold", "log", "line"})
        self.assertEqual((row["source"], row["engine"], row["why"]),
                         ("declared", "chromium", "screen locked mid run"))

    def test_a2_syntax_error_module_is_an_error_row(self):
        self._config(parallel=3)
        self._module("test_a")
        self._module("test_b")
        self._write("tests/test_bad.py", "import unittest\n\ndef broken(:\n    pass\n")
        proc, result, out_dir, _ = self._full()
        rows = self._rows(proc, result)
        self.assertEqual(len(self._shards(out_dir)), 3, os.listdir(out_dir))
        self.assertEqual(proc.returncode, 1, self._detail(proc))
        bad = [r for k, r in rows.items() if k == "test_bad" or k.startswith("test_bad.")]
        self.assertTrue(bad, sorted(rows))
        self.assertEqual({r["status"] for r in bad}, {"error"})
        for ident, status in self.expected.items():
            self.assertEqual(rows[ident]["status"], status, ident)

    def test_a2_shard_without_result_json_is_rc_2(self):
        self._config(parallel=3)
        self._module("test_a")
        self._module("test_b")
        # exits 0 after removing its own AC_TEST_OUT: no result.json can come from this shard
        self._module("test_gone", extra=textwrap.dedent('''
            def tearDownModule():  # noqa: F811
                shutil.rmtree(os.environ["AC_TEST_OUT"], True)
                os._exit(0)
            '''))
        proc, result, out_dir, _ = self._full()
        self.assertEqual(proc.returncode, 2, self._detail(proc))
        named = [l for l in proc.stdout.splitlines() if "test_gone" in l and "result.json" in l]
        self.assertTrue(named, self._detail(proc))

    def test_a2_bad_parallel_value_is_rc_2(self):
        self._module("test_a")
        for value in (0, "x"):
            with self.subTest(parallel=value):
                self._config(parallel=value)
                proc, _, _, _ = self._full()
                self.assertEqual(proc.returncode, 2, self._detail(proc))
                self.assertIn("tests.parallel", proc.stdout + proc.stderr)

    def test_a2_project_config_knows_both_keys(self):
        self._write("board/config.json", json.dumps(
            {"config_version": 2,
             "tests": {"adapter": "unittest", "parallel": 2,
                       "unittest": {"roots": ["tests"], "serial": ["test_a"]}}}))
        tool = os.path.join(SCRIPTS, "project_config.py")
        env = {k: v for k, v in os.environ.items() if not k.startswith("AC_")}
        done = subprocess.run([sys.executable, tool, "check", self.root], cwd=self.root, env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("unknown", done.stdout + done.stderr)
        import importlib.util
        spec = importlib.util.spec_from_file_location("project_config_t169", tool)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        keys = {row[0]: row[1] for row in module.SCHEMA}
        self.assertEqual(keys.get("tests.parallel"), "int", "schema table lacks tests.parallel")
        self.assertEqual(keys.get("tests.unittest.serial"), "list",
                         "schema table lacks tests.unittest.serial")

    # ---------------------------------------------------------------- A4

    def _five(self):
        for name in ("test_p1", "test_p2", "test_p3", "test_p4", "test_p5"):
            self._module(name, 0.5)

    def _check_n(self, proc, result, want):
        rows = self._rows(proc, result)
        self.assertEqual({k: r["status"] for k, r in rows.items()}, self.expected,
                         self._detail(proc))
        line, found = self._parallel_line(proc)
        self.assertEqual(int(found.group(1)), want, line)
        spans = self._intervals()
        self.assertEqual(len(spans), 5, spans)
        self.assertLessEqual(self._overlap(spans), want, spans)
        return line, found

    def test_a4_a_quota_four_cores_leaves_one(self):
        self._need_four()
        peak = self._peak()
        self._config()
        self._five()
        proc, result, _, _ = self._full(self._cgroup("400000 100000", free_mib=10 * peak))
        want = self._expected_n(min(4, os.cpu_count()), 10 * peak, peak)
        self.assertEqual(want, 3)
        line, found = self._check_n(proc, result, want)
        self.assertEqual(int(found.group(2)), 4, line)
        self.assertIn("cores 4", line)
        self.assertEqual(int(float(found.group(4))), peak, line)

    def test_a4_b_no_quota_no_limit_uses_cpu_count_and_memavailable(self):
        peak = self._peak()
        self._config()
        self._five()
        cg = self._cgroup("max 100000", mem_max="max", avail_kb=int(2.5 * peak * 1024))
        proc, result, _, _ = self._full(cg)
        want = min(max(1, os.cpu_count() - 1), 2)
        self.assertEqual(want, self._expected_n(os.cpu_count(), 2.5 * peak, peak))
        line, found = self._check_n(proc, result, want)
        self.assertEqual(int(found.group(2)), os.cpu_count(), line)

    def test_a4_c_memory_short_falls_back_to_one(self):
        peak = self._peak()
        self._config()
        self._five()
        proc, result, _, _ = self._full(self._cgroup("400000 100000", free_mib=0.5 * peak))
        line, _ = self._check_n(proc, result, 1)
        self.assertIn("memory short, fallback 1", line)

    def test_a4_d_empty_cgroup_dir_falls_back(self):
        peak = self._peak()
        self._config()
        self._five()
        cg = self._cgroup(cpu=None, avail_kb=100 * peak * 1024)
        self.assertEqual(os.listdir(cg), [])
        proc, result, _, _ = self._full(cg)
        want = self._expected_n(os.cpu_count(), 100 * peak, peak)
        line, found = self._check_n(proc, result, want)
        self.assertEqual(int(found.group(2)), os.cpu_count(), line)
        self.assertEqual(int(float(found.group(3))), 100 * peak, line)

    def test_a4_e_tests_parallel_caps_but_never_raises(self):
        self._need_four()
        peak = self._peak()
        self._five()
        for cap, want, capped in ((2, 2, True), (8, 3, False)):
            with self.subTest(cap=cap):
                self._config(parallel=cap)
                proc, result, _, _ = self._full(
                    self._cgroup("400000 100000", free_mib=10 * peak))
                self.assertEqual(want, self._expected_n(4, 10 * peak, peak, cap))
                line, _ = self._check_n(proc, result, want)
                if capped:
                    self.assertIn("capped by tests.parallel=2", line)
                else:
                    self.assertNotIn("capped", line)

    # ---------------------------------------------------------------- test plan: shared state

    def test_shared_state_red_is_not_hidden_under_parallel(self):
        """Two modules write one fixed path under the temp root; parallel=2 must show the clash."""
        self._config(parallel=2)
        shared = os.path.join(self.root, "shared.txt")
        for name in ("test_s1", "test_s2"):
            self._write("tests/%s.py" % name, textwrap.dedent('''\
                import time
                import unittest


                class T(unittest.TestCase):
                    def test_clash(self):
                        with open(%r, "w", encoding="utf-8") as handle:
                            handle.write(%r)
                        time.sleep(1.5)
                        with open(%r, encoding="utf-8") as handle:
                            self.assertEqual(handle.read(), %r)
                ''') % (shared, name, shared, name))
        proc, result, out_dir, _ = self._full()
        rows = self._rows(proc, result)
        self.assertEqual(set(rows), {"test_s1.T.test_clash", "test_s2.T.test_clash"})
        self.assertTrue({r["status"] for r in rows.values()} & {"fail", "error"},
                        "shared-state red hidden: %s" % rows)
        self.assertEqual(proc.returncode, 1, self._detail(proc))
        log = self._log(out_dir, result)
        self.assertEqual(len([l for l in log.splitlines() if l.startswith("Ran ")]), 2, log[-3000:])


if __name__ == "__main__":
    unittest.main()
