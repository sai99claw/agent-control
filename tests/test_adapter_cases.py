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


if __name__ == "__main__":
    unittest.main()
