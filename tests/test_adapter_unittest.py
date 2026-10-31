"""#161 `scripts/adapter-unittest.py`: A's built-in unittest adapter (D-071 C6, design §4).

What is pinned here is the seam A's gate will read in row 3b — `select`'s stdout, the exit code and
`result.json` — never the log text:
- A1 `select`: every row of `tests.unittest.map` in `board/config.json`, read at test time (one
  fact, one place - #173, D-054 Q12), selects exactly its own ids for a representative path;
  first match wins; `ids: []` is "no test, not unmapped"; empty stdin is rc 3, not an empty green.
  The table's content is not pinned here (structure is, in `test_verify_runner`); behaviour is
  pinned by the synthetic configs below.
- A2 `run` / `full` write schema 1: one row per id with status / message / exception / file / line;
  a skip without a reason is an error; zero ids and no `AC_TEST_OUT` are rc 2.
- A3 environment suspect: a declared row makes rc 86, and rc 86 never comes with `[]`.
- #169 `full` in parallel: one shard per module, merged result / log, `tests.unittest.serial`, the
  rc merge, `tests.parallel` as a cap, and the run-time process count from fake cgroup / meminfo
  files (expected N is arithmetic here from the fixture numbers, never the adapter's output).
"""

import fnmatch
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, SCRIPTS, TIMEOUT  # noqa: E402

ADAPTER = os.path.join(SCRIPTS, "adapter-unittest.py")

CONFIG = os.path.join(ROOT, "board", "config.json")


def unit_config():
    """`tests.unittest` of the real config, read at test time: the oracle is what the adapter reads."""
    with open(CONFIG, encoding="utf-8") as handle:
        return json.load(handle)["tests"]["unittest"]


def representative(match):
    """One path a `match` glob accepts: each `*` -> `zzz` (no row uses `?` or `[`, #173)."""
    return match.replace("*", "zzz")

# A2's fixture: one of each status. `FAIL_LINE` is found in this text, not hard-coded.
FIXTURE = '''import unittest


class FX(unittest.TestCase):
    def test_pass(self):
        pass

    def test_fail(self):
        self.assertEqual(1, 2)

    @unittest.skip("fx reason")
    def test_skip(self):
        pass

    @unittest.skip("")
    def test_skip_empty(self):
        pass


class Broken(unittest.TestCase):
    def setUp(self):
        raise RuntimeError("setUp broke")

    def test_x(self):
        pass
'''
FIVE = ["test_fx.FX.test_pass", "test_fx.FX.test_fail", "test_fx.Broken.test_x",
        "test_fx.FX.test_skip", "test_fx.FX.test_skip_empty"]
FAIL_LINE = FIXTURE.splitlines().index("        self.assertEqual(1, 2)") + 1

# Same shape as tests/test_status.py's DECLARES_THEN_SKIPS: the case prints the line, then skips.
DECLARES_THEN_SKIPS = '''import unittest


class T(unittest.TestCase):
    def test_it_declares_and_skips(self):
        print("ENVIRONMENT-SUSPECT: firefox fake")
        self.skipTest("environment is not right, recorded as skip")
'''
# Same shape as tests/test_gate.py's ENVIRONMENT_WAVE: one engine, one message shape, 12 times.
WAVE = '''import unittest


class T(unittest.TestCase):
    def test_wave(self):
        for index in range(12):
            with self.subTest(engine="safari", id=index):
                self.fail("localStorage id=%d empty after %d seconds" % (index, index + 100))
'''
SUSPECT_KEYS = ["source", "engine", "why", "count", "threshold", "log", "line"]


def clean_env(**extra):
    """No outer `AC_*` (land's full run sets them) — each call states the ones it means."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("AC_")}
    env.update(extra)
    return env


def adapter(*args, env, stdin="", cwd=ROOT):
    return subprocess.run([sys.executable, ADAPTER, *args], input=stdin, cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=TIMEOUT)


class Select(unittest.TestCase):
    """A1 (V12): `select --mode branch|base` with A's own config, cwd = A."""

    def env(self, **extra):
        return clean_env(AC_ROOT=ROOT, AC_CODE_ROOT=ROOT, **extra)

    def select(self, *files, env=None):
        stdin = "".join(path + "\n" for path in files)
        return adapter("select", "--mode", "branch", env=env or self.env(), stdin=stdin)

    def test_every_config_map_row_selects_the_ids_that_row_adds(self):
        """Each row's representative path is fed alone, so a row is not covered by its neighbour's
        ids. Rows whose path falls in the built-in `tests/test_*.py` arm are skipped by name.

        **Mutation m4**: `select_one` ignores the map → every path prints `unmapped`, red here."""
        rows = unit_config()["map"]
        self.assertGreater(len(rows), 10, "precondition: the map was read")
        for row in rows:
            path, ids = representative(row["match"]), row["ids"]
            if fnmatch.fnmatchcase(path, "tests/test_*.py"):
                continue  # built-in arm, asserted in test_unknown_file_is_unmapped_...
            with self.subTest(match=row["match"], path=path):
                done = self.select(path)
                self.assertEqual(done.returncode, 0, done.stderr)
                lines = done.stdout.splitlines()
                self.assertNotIn("unmapped", done.stdout)
                self.assertEqual(set(lines), set(ids), done.stdout)
                self.assertEqual(len(lines), len(set(lines)), "deduped: %r" % lines)

    def test_unknown_file_is_unmapped_and_test_files_are_built_in(self):
        self.assertEqual(self.select("docs/zzz.unknownext").stdout.splitlines(),
                         ["unmapped docs/zzz.unknownext"])
        self.assertEqual(self.select("tests/test_project_config.py").stdout.splitlines(),
                         ["test_project_config"])
        gone = self.select("tests/test_gone_zzz.py")
        self.assertEqual(gone.returncode, 0, gone.stderr)
        self.assertEqual(gone.stdout, "", "a deleted test file selects nothing, not unmapped")

    def test_several_files_are_deduped_in_order(self):
        done = self.select("scripts/project_config.py", "scripts/adapter-unittest.py", "docs/zzz.unknownext")
        self.assertEqual(done.stdout.splitlines(),
                         ["test_project_config", "test_project_config_cases", "test_adapter_unittest",
                          "test_adapter_cases", "test_testiface", "unmapped docs/zzz.unknownext"])

    def test_first_match_wins_and_empty_ids_is_not_unmapped(self):
        """**Mutation m3** (#173): let a later matching row add its ids too → the first assert is red."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "board"))
            with open(os.path.join(tmp, "board", "config.json"), "w", encoding="utf-8") as h:
                json.dump({"tests": {"unittest": {"map": [
                    {"match": "scripts/*", "ids": ["test_first"]},
                    {"match": "scripts/x.py", "ids": ["test_second"]},
                    {"match": "docs/*.txt", "ids": []}]}}}, h)
            env = clean_env(AC_ROOT=tmp, AC_CODE_ROOT=ROOT)
            first = self.select("scripts/x.py", env=env)
            self.assertEqual(first.stdout.splitlines(), ["test_first"], first.stderr)
            # `*` crosses `/` (sh `case`), so `scripts/a/b.sh` is still the first row.
            self.assertEqual(self.select("scripts/a/b.sh", env=env).stdout.splitlines(),
                             ["test_first"])
            empty = self.select("docs/a/note.txt", env=env)
            self.assertEqual((empty.returncode, empty.stdout), (0, ""), empty.stderr)

    def test_a_map_id_whose_module_this_diff_deletes_is_dropped_out_loud(self):
        """#166: the map comes from `AC_ROOT` (main), the tree is the branch — a branch deleting
        `tests/test_gone_zzz.py` still meets main's row naming `test_gone_zzz`. Only an id whose
        file is in the same diff and missing is dropped; one that never had a file stays.

        **Mutation**: drop the `gone` filter in `cmd_select` → the first assert is red."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "board"))
            with open(os.path.join(tmp, "board", "config.json"), "w", encoding="utf-8") as h:
                json.dump({"tests": {"unittest": {"map": [
                    {"match": "scripts/old_zzz.sh", "ids": ["test_gone_zzz", "test_event"]}]}}}, h)
            env = clean_env(AC_ROOT=tmp, AC_CODE_ROOT=ROOT)
            done = self.select("scripts/old_zzz.sh", "tests/test_gone_zzz.py", env=env)
            self.assertEqual((done.returncode, done.stdout.splitlines()), (0, ["test_event"]),
                             done.stderr)
            self.assertIn("test_gone_zzz", done.stderr, "dropped silently")
            kept = self.select("scripts/old_zzz.sh", env=env)
            self.assertEqual(kept.stdout.splitlines(), ["test_gone_zzz", "test_event"],
                             "an id not deleted by this diff is not ours to drop")

    def test_empty_stdin_is_rc_3_with_a_reason(self):
        done = self.select()
        self.assertEqual(done.returncode, 3, done.stdout)
        self.assertEqual(done.stdout, "")
        self.assertTrue(done.stderr.strip(), "rc 3 must say why")

    def test_base_prints_the_config_base_ids(self):
        base = unit_config()["base"]
        self.assertTrue(base, "precondition: tests.unittest.base was read")
        done = adapter("select", "--mode", "base", env=self.env())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), base)


class Fixture(unittest.TestCase):
    """A tmp code root with `tests/<name>.py`; `AC_ROOT` = the same tmp (no config → defaults)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.code = os.path.join(self.tmp, "code")
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(os.path.join(self.code, "tests"))

    def put(self, name, text):
        with open(os.path.join(self.code, "tests", name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def env(self, **extra):
        return clean_env(AC_ROOT=self.code, AC_CODE_ROOT=self.code, AC_TEST_OUT=self.out,
                         AC_TEST_MODE="branch", **extra)

    def run_adapter(self, *args, env=None):
        return adapter(*args, env=env or self.env(), cwd=self.code)

    def result(self):
        with open(os.path.join(self.out, "result.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def has_result(self):
        return os.path.exists(os.path.join(self.out, "result.json"))


class RunWritesSchemaOne(Fixture):
    """A2: `run` / `full` write `result.json` schema 1."""

    def setUp(self):
        super().setUp()
        self.put("test_fx.py", FIXTURE)

    def test_run_of_the_five_ids(self):
        """**Mutations**: m1 (file/line from the outermost frame) → the `file` / `line` asserts;
        m5 (empty-reason skip reported as skip) → the `skip_empty` status assert."""
        done = self.run_adapter("run", *FIVE)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        data = self.result()
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["command"], "run")
        self.assertEqual(data["ac_test_mode"], "branch")
        rows = {row["id"]: row for row in data["ids"]}
        self.assertEqual(set(rows), set(FIVE))
        self.assertEqual(len(data["ids"]), 5, "one row per id")
        self.assertEqual([rows[i]["status"] for i in FIVE],
                         ["pass", "fail", "error", "skip", "error"])
        fail = rows["test_fx.FX.test_fail"]
        self.assertEqual(fail["exception"], "AssertionError")
        self.assertEqual(fail["file"], "tests/test_fx.py")
        self.assertEqual(fail["line"], FAIL_LINE)
        self.assertTrue(fail["message"].startswith("AssertionError"), fail["message"])
        self.assertIsNotNone(fail["traceback"])
        broken = rows["test_fx.Broken.test_x"]
        self.assertEqual(broken["exception"], "RuntimeError")
        self.assertEqual(broken["file"], "tests/test_fx.py")
        self.assertEqual(rows["test_fx.FX.test_skip"]["message"], "fx reason")
        self.assertIn("without reason", rows["test_fx.FX.test_skip_empty"]["message"])
        self.assertIsNone(rows["test_fx.FX.test_pass"]["message"])
        self.assertEqual(data["environment_suspect"], [])
        self.assertEqual(data["log"], "run.log")
        with open(os.path.join(self.out, "run.log"), encoding="utf-8") as handle:
            self.assertIn("Ran 5 tests", handle.read())
        for key in ("started", "seconds"):
            self.assertIsNotNone(data[key], key)

    def test_full_finds_the_same_ids(self):
        done = self.run_adapter("full")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        data = self.result()
        self.assertEqual(data["command"], "full")
        self.assertEqual(data["log"], "full.log")
        self.assertEqual({row["id"] for row in data["ids"]}, set(FIVE))
        self.assertTrue(os.path.exists(os.path.join(self.out, "full.log")))

    def test_all_green_is_rc_0(self):
        done = self.run_adapter("run", "test_fx.FX.test_pass", "test_fx.FX.test_skip")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([row["status"] for row in self.result()["ids"]], ["pass", "skip"])

    def test_zero_ids_is_rc_2_without_a_result(self):
        """**Mutation m3**: `run` with zero ids returns 0 → red."""
        done = self.run_adapter("run")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertFalse(self.has_result())

    def test_no_test_out_is_rc_2(self):
        env = self.env()
        env.pop("AC_TEST_OUT")
        for args in (("run", FIVE[0]), ("full",)):
            with self.subTest(args=args):
                done = self.run_adapter(*args, env=env)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertTrue(done.stderr.strip())
        self.assertFalse(self.has_result())


class EnvironmentSuspect(Fixture):
    """A3: rc 86 ⇔ `environment_suspect` non-empty (D-019 seven keys)."""

    def assert_rows(self, rows):
        self.assertIsInstance(rows, list)
        self.assertTrue(rows, "rc 86 never comes with environment_suspect []")
        for row in rows:
            self.assertEqual(sorted(row), sorted(SUSPECT_KEYS))

    def test_a_declared_line_is_rc_86_with_one_declared_row(self):
        self.put("test_declared.py", DECLARES_THEN_SKIPS)
        done = self.run_adapter("run", "test_declared")
        self.assertEqual(done.returncode, 86, done.stdout + done.stderr)
        data = self.result()
        rows = data["environment_suspect"]
        self.assert_rows(rows)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source"], "declared")
        self.assertEqual(rows[0]["engine"], "firefox")
        self.assertEqual(rows[0]["why"], "fake")
        self.assertEqual(rows[0]["line"], "ENVIRONMENT-SUSPECT: firefox fake")
        self.assertEqual([row["status"] for row in data["ids"]], ["skip"])

    def test_a_statistical_wave_is_rc_86_with_one_statistical_row(self):
        self.put("test_wave.py", WAVE)
        done = self.run_adapter("run", "test_wave")
        self.assertEqual(done.returncode, 86, done.stdout + done.stderr)
        data = self.result()
        rows = data["environment_suspect"]
        self.assert_rows(rows)
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual((row["source"], row["engine"], row["count"], row["threshold"]),
                         ("statistical", "safari", 8, 8))
        self.assertEqual(row["log"], os.path.join(self.out, "run.log"))
        self.assertEqual([r["id"] for r in data["ids"]], ["test_wave.T.test_wave"])
        self.assertEqual(data["ids"][0]["status"], "fail")
        with open(os.path.join(self.out, "run.log"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().count("AssertionError: localStorage id="), 8,
                             "fail-fast stops at the threshold (8), not after all 12")


def load_adapter():
    """The adapter module, read only for `PER_PROCESS_PEAK_MIB` (an input that sizes the fixture
    numbers — the expected N is still computed here)."""
    spec = importlib.util.spec_from_file_location("adapter_unittest_under_test", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PEAK_MIB = load_adapter().PER_PROCESS_PEAK_MIB
MIB = 1048576
CPUS = os.cpu_count() or 1
LINE = re.compile(r"^parallel: (\d+) processes \(cores (\d+), available memory (\S+) MiB, "
                  r"per-process (\d+) MiB\)(.*)$")
HEADER = re.compile(r"^==== shard \d+/\d+ (\S+): ")

# A module that marks its start / end (setUpModule / tearDownModule) in `$FX_MARKS`, sleeps in one
# test and holds two more; `STATUS` below is what it declares — the oracle for its rows.
TIMED = '''import os, time, unittest


def _mark(what):
    with open(os.environ["FX_MARKS"], "a", encoding="utf-8") as handle:
        handle.write("%s {name} %r\\n" % (what, time.time()))


def setUpModule():
    _mark("start")


def tearDownModule():
    _mark("end")


class T(unittest.TestCase):
    def test_sleep(self):
        time.sleep({sleep})

    def test_one(self):
        pass

    @unittest.skip("declared skip")
    def test_two(self):
        pass
'''
TIMED_STATUS = {"T.test_sleep": "pass", "T.test_one": "pass", "T.test_two": "skip"}
TWO_GREEN = '''import unittest


class T(unittest.TestCase):
    def test_one(self):
        pass

    def test_two(self):
        pass
'''
ONE_RED = '''import unittest


class T(unittest.TestCase):
    def test_green(self):
        pass

    def test_red(self):
        self.assertEqual(1, 2)
'''
# Exits 0, then removes its own AC_TEST_OUT at interpreter exit — after result.json was written.
LEAVES_NO_RESULT = '''import atexit, os, shutil, unittest


def tearDownModule():
    atexit.register(shutil.rmtree, os.environ["AC_TEST_OUT"], True)


class T(unittest.TestCase):
    def test_green(self):
        pass
'''


def fake_cgroup(where, cpu_max=None, free_mib=None, meminfo_kb=None):
    """A cgroup v2 dir (`cpu.max`, `memory.max` / `memory.current` in bytes) and a meminfo file
    under `where`; a value left None leaves that file out. Returns the env that points at them."""
    cgroup = os.path.join(where, "cg")
    os.makedirs(cgroup, exist_ok=True)
    if cpu_max is not None:
        with open(os.path.join(cgroup, "cpu.max"), "w", encoding="utf-8") as handle:
            handle.write(cpu_max + "\n")
    if free_mib is not None:
        used = 123 * MIB
        with open(os.path.join(cgroup, "memory.max"), "w", encoding="utf-8") as handle:
            handle.write("%d\n" % (used + int(free_mib * MIB)))
        with open(os.path.join(cgroup, "memory.current"), "w", encoding="utf-8") as handle:
            handle.write("%d\n" % used)
    elif cpu_max is not None and cpu_max.startswith("max"):
        with open(os.path.join(cgroup, "memory.max"), "w", encoding="utf-8") as handle:
            handle.write("max\n")
    meminfo = os.path.join(where, "meminfo")
    with open(meminfo, "w", encoding="utf-8") as handle:
        handle.write("MemTotal: 99999999 kB\n")
        if meminfo_kb is not None:
            handle.write("MemAvailable: %d kB\n" % meminfo_kb)
    return {"AC_CGROUP_ROOT": cgroup, "AC_MEMINFO": meminfo}


def expected_n(cores, free_mib, cap=None):
    """The D-074 rule, from the fixture's own numbers."""
    count = min(max(1, cores - 1), math.floor(free_mib / PEAK_MIB))
    count = max(1, count)
    return min(count, cap) if cap else count


class ParallelFixture(Fixture):
    """Helpers for the #169 cases: a fake cgroup with room for N 3 on 4 CPUs, marks, config."""

    def setUp(self):
        super().setUp()
        self.marks = os.path.join(self.tmp, "marks")
        self.cgroup = fake_cgroup(self.tmp, cpu_max="400000 100000", free_mib=100 * PEAK_MIB)

    def config(self, **tests):
        os.makedirs(os.path.join(self.code, "board"), exist_ok=True)
        with open(os.path.join(self.code, "board", "config.json"), "w", encoding="utf-8") as h:
            json.dump({"tests": tests}, h)

    def timed(self, *names, sleep=2):
        for name in names:
            self.put("%s.py" % name, TIMED.replace("{name}", name).replace("{sleep}", str(sleep)))

    def full(self, env=None):
        began = time.monotonic()
        done = self.run_adapter("full", env=env or self.env(FX_MARKS=self.marks, **self.cgroup))
        return done, time.monotonic() - began

    def rows(self):
        return {row["id"]: row["status"] for row in self.result()["ids"]}

    def shards(self):
        return sorted(n for n in os.listdir(self.out) if n.startswith("shard-"))

    def merged_log(self):
        with open(os.path.join(self.out, "full.log"), encoding="utf-8") as handle:
            return handle.read()

    def parallel_lines(self, done):
        return [line for line in done.stdout.splitlines() if line.startswith("parallel: ")]

    def read_marks(self):
        out = {}
        with open(self.marks, encoding="utf-8") as handle:
            for line in handle:
                what, name, stamp = line.split()
                out.setdefault(name, {})[what] = float(stamp)
        return out

    def declared(self, *names):
        return {"%s.%s" % (name, test): state
                for name in names for test, state in TIMED_STATUS.items()}


class Parallel(ParallelFixture):
    """#169: `full` sharded across processes, merged back into one schema-1 result."""

    @unittest.skipIf(CPUS < 4, "needs 4 CPUs for N 3 (os.cpu_count() = %d)" % CPUS)
    def test_three_modules_in_three_shards_merge_into_one_result(self):
        """A1. **Mutation**: shards run one after another (pool of 1) → the wall assert is red."""
        self.timed("test_a", "test_b", "test_c")
        self.config(parallel=3)
        os.makedirs(os.path.join(self.code, "reports"))
        with open(os.path.join(self.code, "reports", "test-times.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"test_a": 10, "test_b": 30, "test_c": 20}, handle)
        done, wall = self.full()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertLess(wall, 4.5, done.stdout)
        data = self.result()
        self.assertEqual(len(data["ids"]), 9, data["ids"])
        self.assertEqual(self.rows(), self.declared("test_a", "test_b", "test_c"))
        self.assertEqual(len(self.shards()), 3)
        for shard in self.shards():
            self.assertTrue(os.path.isfile(os.path.join(self.out, shard, "result.json")), shard)
        log = self.merged_log()
        self.assertEqual(len([line for line in log.splitlines() if line.startswith("Ran ")]), 3)
        self.assertEqual([HEADER.match(line).group(1) for line in log.splitlines()
                          if HEADER.match(line)], ["test_b", "test_c", "test_a"])
        self.assertEqual(len(self.parallel_lines(done)), 1, done.stdout)
        with open(os.path.join(self.code, "reports", "test-times.json"), encoding="utf-8") as h:
            times = json.load(h)
        self.assertEqual(sorted(times), ["test_a", "test_b", "test_c"])
        self.assertGreaterEqual(times["test_a"], 2, "the measured wall replaced the hand-written 10")
        self.assertLess(times["test_a"], 10)

    def test_parallel_1_is_the_single_in_process_run(self):
        """A1: tests.parallel=1 → same id / status set, serial wall, no shard dir."""
        self.timed("test_a", "test_b", "test_c")
        self.config(parallel=1)
        done, wall = self.full()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertGreaterEqual(wall, 6)
        self.assertEqual(self.rows(), self.declared("test_a", "test_b", "test_c"))
        self.assertEqual(self.shards(), [])
        self.assertEqual(self.result()["log"], "full.log")
        self.assertEqual(self.merged_log().count("\nRan 9 tests"), 1)

    def test_serial_module_starts_after_the_parallel_ones_end(self):
        """A1. **Mutation m2**: `run_shards` ignores the serial list → c starts with a and b, red."""
        self.timed("test_a", "test_b", "test_c", sleep=1)
        self.config(parallel=3, unittest={"serial": ["test_c", "test_zzz_absent"]})
        done, _ = self.full()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        marks = self.read_marks()
        self.assertGreater(marks["test_c"]["start"],
                           max(marks["test_a"]["end"], marks["test_b"]["end"]), marks)
        named = [line for line in done.stdout.splitlines()
                 if "serial" in line and "test_zzz_absent" in line]
        self.assertEqual(len(named), 1, done.stdout)
        self.assertEqual(self.rows(), self.declared("test_a", "test_b", "test_c"))

    def test_one_failing_module_makes_rc_1_with_its_row_only(self):
        """A2. **Mutation m1**: merge takes the first shard's rc (test_a's 0) → red."""
        self.put("test_a.py", TWO_GREEN)
        self.put("test_b.py", ONE_RED)
        self.put("test_c.py", TWO_GREEN)
        self.config(parallel=3)
        done, _ = self.full()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.rows(), {
            "test_a.T.test_one": "pass", "test_a.T.test_two": "pass",
            "test_b.T.test_green": "pass", "test_b.T.test_red": "fail",
            "test_c.T.test_one": "pass", "test_c.T.test_two": "pass"})
        self.assertEqual(len(self.shards()), 3, "this arm must have run sharded")

    def test_a_declared_suspect_in_one_shard_is_rc_86_with_that_row(self):
        self.put("test_a.py", TWO_GREEN)
        self.put("test_declared.py", DECLARES_THEN_SKIPS)
        self.config(parallel=2)
        done, _ = self.full()
        self.assertEqual(done.returncode, 86, done.stdout + done.stderr)
        data = self.result()
        self.assertEqual(len(data["environment_suspect"]), 1, data["environment_suspect"])
        row = data["environment_suspect"][0]
        self.assertEqual(sorted(row), sorted(SUSPECT_KEYS))
        self.assertEqual((row["source"], row["line"]),
                         ("declared", "ENVIRONMENT-SUSPECT: firefox fake"))
        self.assertEqual(self.rows(), {"test_a.T.test_one": "pass", "test_a.T.test_two": "pass",
                                       "test_declared.T.test_it_declares_and_skips": "skip"})

    def test_a_syntax_error_module_is_an_error_row_under_its_id(self):
        self.put("test_a.py", TWO_GREEN)
        self.put("test_bad.py", "import unittest\n\ndef broken(:\n    pass\n")
        self.config(parallel=2)
        done, _ = self.full()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        rows = self.rows()
        self.assertEqual(rows.get("test_bad"), "error", rows)
        self.assertEqual(rows["test_a.T.test_one"], "pass")

    def test_a_shard_without_result_json_is_rc_2_naming_it(self):
        self.put("test_a.py", TWO_GREEN)
        self.put("test_gone.py", LEAVES_NO_RESULT)
        self.config(parallel=2)
        done, _ = self.full()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        named = [line for line in done.stdout.splitlines()
                 if "test_gone" in line and "result.json" in line]
        self.assertTrue(named, done.stdout)
        self.assertIn("exit 0", named[0], "the shard itself exited 0")
        self.assertFalse(self.has_result(), "no merged result.json under rc 2")

    def test_tests_parallel_that_is_not_a_positive_int_is_rc_2(self):
        self.put("test_a.py", TWO_GREEN)
        for value in (0, "x", True):
            with self.subTest(value=value):
                self.config(parallel=value)
                done, _ = self.full()
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertIn("tests.parallel", done.stdout + done.stderr)
                self.assertFalse(self.has_result())

    def test_project_config_check_knows_both_keys(self):
        with open(os.path.join(ROOT, "board", "config.json"), encoding="utf-8") as handle:
            conf = json.load(handle)
        conf["tests"]["parallel"] = 3
        conf["tests"]["unittest"]["serial"] = ["test_auto_fix"]
        os.makedirs(os.path.join(self.code, "board"))
        with open(os.path.join(self.code, "board", "config.json"), "w", encoding="utf-8") as h:
            json.dump(conf, h)
        done = subprocess.run([sys.executable, os.path.join(SCRIPTS, "project_config.py"),
                               "check", self.code], capture_output=True, text=True,
                              timeout=TIMEOUT, env=clean_env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("unknown", done.stdout + done.stderr)
        conf["tests"]["parallel"] = "x"
        with open(os.path.join(self.code, "board", "config.json"), "w", encoding="utf-8") as h:
            json.dump(conf, h)
        done = subprocess.run([sys.executable, os.path.join(SCRIPTS, "project_config.py"),
                               "check", self.code], capture_output=True, text=True,
                              timeout=TIMEOUT, env=clean_env())
        self.assertEqual(done.returncode, 2, "typed: a string cap is refused by check too")
        self.assertIn("tests.parallel", done.stderr)


class ProcessCount(ParallelFixture):
    """A4 (D-074 item 2): N from fake cgroup / meminfo files; nothing under /sys is touched and no
    memory is allocated. Each case runs a real `full` over two trivial modules and reads N back
    from the one `parallel: ` line."""

    def setUp(self):
        super().setUp()
        self.put("test_a.py", TWO_GREEN)
        self.put("test_b.py", TWO_GREEN)

    def line_of(self, cgroup, **tests):
        self.config(**tests)
        done, _ = self.full(env=self.env(FX_MARKS=self.marks, **cgroup))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = self.parallel_lines(done)
        self.assertEqual(len(lines), 1, done.stdout)
        found = LINE.match(lines[0])
        self.assertTrue(found, lines[0])
        return int(found.group(1)), int(found.group(2)), found.group(5), lines[0]

    def max_overlap(self):
        marks = self.read_marks()
        events = sorted([(m["start"], 1) for m in marks.values()]
                        + [(m["end"], -1) for m in marks.values()], key=lambda e: (e[0], e[1]))
        now = peak = 0
        for _, step in events:
            now += step
            peak = max(peak, now)
        return peak

    @unittest.skipIf(CPUS < 4, "case (a) needs os.cpu_count() >= 4 (it is %d)" % CPUS)
    def test_a_quota_of_4_cores_leaves_one_for_the_system(self):
        """(a). **Mutation m6**: no core left for the system → N 4, red."""
        for name in ("test_a", "test_b"):
            os.remove(os.path.join(self.code, "tests", name + ".py"))
        self.timed("test_p1", "test_p2", "test_p3", "test_p4", "test_p5", sleep=1.5)
        cgroup = fake_cgroup(os.path.join(self.tmp, "a"), cpu_max="400000 100000",
                             free_mib=10 * PEAK_MIB)
        count, cores, tail, line = self.line_of(cgroup)
        self.assertEqual(count, expected_n(4, 10 * PEAK_MIB))
        self.assertEqual(count, 3)
        self.assertEqual(cores, 4, line)
        self.assertNotIn("capped", tail)
        overlap = self.max_overlap()
        self.assertLessEqual(overlap, count)
        self.assertGreater(overlap, 1, "five sleeping modules under N 3 never overlapped")

    def test_b_no_quota_no_memory_limit_reads_meminfo(self):
        """(b) cpu.max 'max', memory.max 'max', MemAvailable 2.5 P."""
        cgroup = fake_cgroup(os.path.join(self.tmp, "b"), cpu_max="max 100000",
                             meminfo_kb=int(2.5 * PEAK_MIB * 1024))
        count, cores, _, line = self.line_of(cgroup)
        self.assertEqual(count, min(max(1, CPUS - 1), 2), line)
        self.assertEqual(cores, CPUS)

    def test_c_memory_short_falls_back_to_1(self):
        """(c). **Mutation m5**: N ignores memory → N = cores - 1, red."""
        cgroup = fake_cgroup(os.path.join(self.tmp, "c"), cpu_max="400000 100000",
                             free_mib=0.5 * PEAK_MIB)
        count, _, tail, line = self.line_of(cgroup)
        self.assertEqual(count, 1, line)
        self.assertIn("memory short, fallback 1", tail)
        self.assertEqual(self.shards(), [], "N 1 is the in-process run")

    def test_d_empty_cgroup_falls_back_to_cpu_count_and_meminfo(self):
        cgroup = fake_cgroup(os.path.join(self.tmp, "d"), meminfo_kb=100 * PEAK_MIB * 1024)
        count, cores, _, line = self.line_of(cgroup)
        self.assertEqual(cores, CPUS, line)
        self.assertEqual(count, expected_n(CPUS, 100 * PEAK_MIB), line)
        self.assertIn("available memory %d MiB" % (100 * PEAK_MIB), line)

    @unittest.skipIf(CPUS < 4, "case (e) needs os.cpu_count() >= 4 (it is %d)" % CPUS)
    def test_e_tests_parallel_caps_and_never_raises(self):
        """(e). **Mutation m4**: the cap is ignored → N 3 under cap 2, red; **m7**: the cap
        raises N → N 8 under cap 8, red."""
        for name in ("test_a", "test_b"):
            os.remove(os.path.join(self.code, "tests", name + ".py"))
        self.timed("test_p1", "test_p2", "test_p3", "test_p4", "test_p5", sleep=1.5)
        cgroup = fake_cgroup(os.path.join(self.tmp, "e"), cpu_max="400000 100000",
                             free_mib=10 * PEAK_MIB)
        count, _, tail, line = self.line_of(cgroup, parallel=2)
        self.assertEqual(count, expected_n(4, 10 * PEAK_MIB, cap=2))
        self.assertEqual(count, 2)
        self.assertIn("capped by tests.parallel=2", tail)
        self.assertLessEqual(self.max_overlap(), 2)
        os.remove(self.marks)
        count, _, tail, line = self.line_of(cgroup, parallel=8)
        self.assertEqual(count, expected_n(4, 10 * PEAK_MIB))
        self.assertEqual(count, 3, line)
        self.assertNotIn("capped", tail)


if __name__ == "__main__":
    unittest.main()
