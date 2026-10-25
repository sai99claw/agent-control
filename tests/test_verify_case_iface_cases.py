"""#163 (D-071 C7): `verify-case.py red` classifies from the `result.json` rows that
`testiface.py regression --case` returns, not from a unittest log it spawned itself.

The sandbox is the committed fixture project (`fixture = True`, row 3b) with `tests.regression`
pointed at a fake command that writes the schema-1 rows given here and exits 1. The case files
themselves are green under plain unittest, so a tool that still reads its own unittest log sees
"no counted red" — only a tool that reads the rows sees what the rows say.

Expected values come from the ticket text and the design table (`docs/design/
project-runnable-entries.md` §4 result.json schema 1, C7): an `exception` ending in
`AssertionError` in a `verify.files` file counts (`frame_in`'s endswith rule for the file); the
red id and the row's message land in `red` / `red_lines`; any other fail outside verify.files is
"紅在別處(不算紅)".
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_verify_case import ALWAYS_GREEN, CaseSandbox  # noqa: E402


class RedReadsTheRegressionRows(CaseSandbox):
    """`red` on the fixture project, the rows coming from a declared `tests.regression`."""

    fixture = True

    ID_A = "verify.nav.test_ticket_1.Nothing.test_true"
    ID_B = "verify.nav.test_ticket_2.Nothing.test_true"
    MESSAGE = "MarkerAssertionError: 1 != 2 : row-message-163"

    def row(self, ident, status="pass", **fields):
        out = {"id": ident, "status": status, "message": None, "exception": None,
               "file": None, "line": None, "seconds": 0.01, "traceback": None}
        out.update(fields)
        return out

    def declare_regression(self, rows):
        """A fake `tests.regression`: writes these rows as `$AC_TEST_OUT/result.json`, rc 1."""
        script = os.path.join(self.home, "fake-regression.py")
        result = {"schema": 1, "command": "regression", "ac_test_mode": "verify-red",
                  "started": "2026-10-25T00:00:00+00:00", "seconds": 0.1, "ids": rows,
                  "environment_suspect": [], "log": None}
        red = any(one["status"] in ("fail", "error") for one in rows)
        self.write("fake-regression.py",
                   "import json, os, sys\n"
                   "with open(os.path.join(os.environ['AC_TEST_OUT'], 'result.json'), 'w') as h:\n"
                   "    h.write(%r)\n"
                   "sys.exit(%d)\n" % (json.dumps(result), 1 if red else 0),
                   where=self.home)
        conf = json.loads(self.read("board/config.json"))
        conf["tests"]["regression"] = "python3 %s" % script
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture: declared regression command")

    def two_case_ticket(self):
        for name in ("test_ticket_1.py", "test_ticket_2.py"):
            self.write(os.path.join("verify", "nav", name), ALWAYS_GREEN)
        self.make_ticket(1, verify={"files": ["verify/nav/test_ticket_1.py",
                                              "verify/nav/test_ticket_2.py"],
                                    "tags": ["example"], "run": "", "notes": ""})

    def test_an_assertion_error_subclass_under_another_prefix_counts_as_red(self):
        """Row exception `MarkerAssertionError` (a subclass name ending in AssertionError), row
        file an absolute path ending in `/verify/nav/test_ticket_1.py` -> counted red, id in
        `red`, the row's message in `red_lines`."""
        self.declare_regression([self.row(
            self.ID_A, "fail", message=self.MESSAGE, exception="MarkerAssertionError",
            file="/elsewhere/copy/base/verify/nav/test_ticket_1.py", line=7)])
        self.a_ticket(ALWAYS_GREEN)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("紅在別處", done.stdout)
        base = self.red_record(done)
        self.assertEqual(base["baseline"]["red"], [self.ID_A])
        self.assertEqual(len(base["baseline"]["red_lines"]), 1)
        self.assertIn("row-message-163", base["baseline"]["red_lines"][0])
        self.assertIn(self.ID_A, base["baseline"]["red_lines"][0])

    def test_two_verify_files_one_red_one_green(self):
        """Two verify.files: file 1's row is an AssertionError fail, file 2's row passes ->
        rc 0, `red` is exactly file 1's id, `cases` counts both ids, nothing else listed."""
        self.declare_regression([
            self.row(self.ID_A, "fail", message="AssertionError: 1 != 2 : row-message-163",
                     exception="AssertionError", file="verify/nav/test_ticket_1.py", line=7),
            self.row(self.ID_B)])
        self.two_case_ticket()
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.red_record(done)
        run = base["baseline"]
        self.assertEqual(run["red"], [self.ID_A])
        self.assertEqual(run["cases"], 2)
        self.assertEqual(run["import_failures"], [])
        self.assertEqual(run["missing_symbol"], [])
        self.assertEqual(run["elsewhere"], [])
        self.assertIn("row-message-163", run["red_lines"][0])
        self.assertEqual(base["files"], ["verify/nav/test_ticket_1.py",
                                         "verify/nav/test_ticket_2.py"])

    def test_an_assertion_row_whose_file_is_not_a_verify_file_is_elsewhere(self):
        """AssertionError row whose file is product code (`src/boom.py`) -> "紅在別處(不算紅)",
        rc 1, ticket untouched (mutation m1: classify on exception only -> this is red)."""
        self.declare_regression([self.row(
            self.ID_A, "fail", message="AssertionError: product-boom-163",
            exception="AssertionError", file="src/boom.py", line=2)])
        self.a_ticket(ALWAYS_GREEN)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("紅在別處(不算紅)", done.stdout)
        self.assertIn(self.ID_A, done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])


if __name__ == "__main__":
    unittest.main()
