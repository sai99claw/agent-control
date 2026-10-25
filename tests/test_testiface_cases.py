"""#162 verifier cases: the test interface beyond the worker's A1-A5 (`tests/test_testiface.py`).

Three shapes the ticket's test_plan names for the verifier:

1. **T's step-2 shape** — a second copy of the fixture project whose `tests.select` is declared
   and `tests.run` is null: the unittest ids the project's select prints run through A's
   built-in adapter (`$AC_HOME/scripts/adapter-unittest.py`), the project's own `run` command is
   never called, and no `browser:*` id appears.
2. **The group rerun count** — `flake_auto_group_runs` decides how many `run <all ids>` group
   reruns happen after the single reruns (ticket objective (2): "group rerun via run <all ids>
   flake_auto_group_runs times").
3. **`actl test preflight` by hand** with the fixture's fake env: rc and the suspect file.

**Expected values come from the fixture and the ticket, not from the scripts under test**:
the declared row is the one `tests/fixtures/project/scripts/ac-tests.sh` prints (its `why` is
`screen <value> (fixture preflight)`), the ids are the fixture's `browser/<name>.py` →
`browser:safari:<name>` rule, and the unittest id is unittest's own `<module>.<Class>.<method>`.
"What ran" is read from the fixture's `run` call log (`AC_TEST_CALLS`), not from the gate's words.
"""

import glob
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

# What the fixture's preflight prints for `AC_TEST_FAKE_PREFLIGHT=locked` (ac-tests.sh, literal).
LOCKED_ROW = {"source": "declared", "engine": "host", "why": "screen locked (fixture preflight)",
              "count": 1, "threshold": None, "log": None, "line": None}
X_ID = "browser:safari:test_x"
Y_ID = "browser:safari:test_y"

RED_TEST_OK = ('import unittest\n\n\nclass Red(unittest.TestCase):\n'
               '    def test_red(self):\n        self.assertEqual(1 + 1, 3)\n')


class FixtureCase(Sandbox):
    fixture = True

    def setUp(self):
        super().setUp()
        self.calls = os.path.join(self.home, "run-calls.log")
        self.make_ticket(7)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "ticket #7")

    def conf(self):
        return json.loads(self.read("board/config.json"))

    def save_conf(self, conf):
        # Committed: an uncommitted config file is a changed file and select would pick it.
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.git("add", "board/config.json")
        self.git("commit", "-q", "-m", "sandbox config")

    def gate(self, *extra, **env):
        if os.path.exists(self.calls):
            os.remove(self.calls)
        return self.run_sh("scripts/gate.sh", "--branch", "--ticket", "7", *extra,
                           env=self.env(AC_TEST_CALLS=self.calls, **env))

    def run_calls(self):
        """One row per fixture `run` call: argv / mode / code_root / out (ac-tests.sh format)."""
        if not os.path.exists(self.calls):
            return []
        rows = []
        with open(self.calls, encoding="utf-8") as handle:
            for line in handle.read().splitlines():
                head, *rest = line.split("\t")
                row = dict(part.split("=", 1) for part in rest if "=" in part)
                row["argv"] = head
                rows.append(row)
        return rows

    def gate_status(self):
        where = os.path.join(self.repo, "reports", "t7")
        self.assertTrue(os.path.isdir(where), "no reports/t7 — the gate wrote no status")
        return self.status_of(7, kind="gate")


class StepTwoShapeRunNull(FixtureCase):
    """Second fixture copy: `tests.select` declared, `tests.run` null → A's adapter runs the ids."""

    def setUp(self):
        super().setUp()
        conf = self.conf()
        self.assertIn("tests", conf, "sandbox config has no tests block")
        self.assertIn("sh scripts/ac-tests.sh select", str(conf["tests"].get("select")),
                      "the fixture's select is not the declared command")
        conf["tests"]["run"] = None
        self.save_conf(conf)

    def test_green_unittest_id_runs_through_the_builtin_adapter(self):
        self.write(os.path.join("tests", "test_ok.py"),
                   self.read(os.path.join("tests", "test_ok.py")) + "# this round's change\n")
        done = self.gate("--no-auto-fix")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("gate: run test_ok", done.stdout.splitlines())
        self.assertEqual(self.run_calls(), [], "tests.run is null but the project's run ran")
        self.assertNotIn("browser:", done.stdout)
        results = glob.glob(os.path.join(self.repo, "reports", "t7", "*", "run*", "result.json"))
        self.assertEqual(len(results), 1, results)
        with open(results[0], encoding="utf-8") as handle:
            doc = json.load(handle)
        self.assertEqual(doc.get("schema"), 1)
        self.assertEqual([(row["id"], row["status"]) for row in doc["ids"]],
                         [("test_ok.T.test_ok", "pass")])
        self.assertEqual(self.gate_status().get("rc"), 0)

    def test_red_unittest_id_is_rc_1_with_unittests_own_id(self):
        self.write(os.path.join("tests", "test_ok.py"), RED_TEST_OK)
        done = self.gate("--no-auto-fix", AC_NO_FLAKE_RERUN="1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("gate: run test_ok", done.stdout.splitlines())
        self.assertEqual(self.run_calls(), [], "tests.run is null but the project's run ran")
        status = self.gate_status()
        self.assertEqual(status.get("rc"), 1)
        self.assertEqual([row.get("case") for row in status.get("failures") or []],
                         ["test_ok.Red.test_red"])


class GroupRerunCount(FixtureCase):
    """`flake_auto_group_runs` = 3, `flake_auto_single_runs` = 2, two flaky ids."""

    def setUp(self):
        super().setUp()
        conf = self.conf()
        conf["flake_auto_single_runs"] = 2
        conf["flake_auto_group_runs"] = 3
        self.save_conf(conf)
        for name in ("test_x", "test_y"):
            self.write(os.path.join("browser", "%s.py" % name), "# changed engine case\n")

    def test_group_reruns_are_run_with_all_ids_group_runs_times(self):
        done = self.gate("--no-auto-fix", AC_TEST_FAKE_RUN="flaky-then-pass")
        calls = self.run_calls()
        argv = [row["argv"] for row in calls]
        both = "run %s %s" % (X_ID, Y_ID)
        # 1 first run + 2 ids x 2 single reruns + 3 group reruns.
        self.assertEqual(len(calls), 1 + 2 * 2 + 3, argv)
        self.assertEqual(argv[0], both)
        self.assertEqual(calls[0].get("mode"), "branch")
        singles = argv[1:5]
        self.assertEqual(sorted(singles), ["run %s" % X_ID] * 2 + ["run %s" % Y_ID] * 2, argv)
        groups = calls[5:]
        self.assertEqual([row["argv"] for row in groups], [both] * 3,
                         "group rerun is not `run <all ids>` in the original order")
        self.assertEqual([os.path.basename(row.get("out", "")) for row in groups],
                         ["rerun-group-1", "rerun-group-2", "rerun-group-3"])
        self.assertEqual(len({row.get("out") for row in calls}), len(calls),
                         "two runs shared one AC_TEST_OUT")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        status = self.gate_status()
        self.assertEqual(status.get("flaky"), "auto")
        self.assertEqual(sorted(row.get("case") for row in status.get("auto_flaky") or []),
                         [X_ID, Y_ID])


class ActlTestPreflightByHand(FixtureCase):
    """`actl test --suspect-file F preflight` from the project dir, AC_ROOT unset."""

    def actl(self, *args, **env):
        full = self.env(AC_TEST_OUT=tempfile.mkdtemp(dir=self.home), **env)
        full.pop("AC_ROOT", None)
        return subprocess.run(["sh", os.path.join(self.ac_home, "bin", "actl"), *args],
                              cwd=self.repo, env=full, capture_output=True, text=True,
                              timeout=120)

    def test_locked_is_86_and_the_declared_row_lands_in_the_suspect_file(self):
        suspect = os.path.join(self.home, "suspect.json")
        done = self.actl("test", "--suspect-file", suspect, "preflight",
                         AC_TEST_FAKE_PREFLIGHT="locked")
        self.assertEqual(done.returncode, 86, done.stdout + done.stderr)
        self.assertTrue(os.path.exists(suspect), "rc 86 but no suspect file")
        with open(suspect, encoding="utf-8") as handle:
            rows = json.load(handle)
        self.assertEqual(rows, [LOCKED_ROW])

    def test_broken_preflight_is_2_and_writes_no_suspect_file(self):
        suspect = os.path.join(self.home, "suspect.json")
        done = self.actl("test", "--suspect-file", suspect, "preflight",
                         AC_TEST_FAKE_PREFLIGHT="broken")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("preflight", done.stderr)
        self.assertFalse(os.path.exists(suspect), "a crashed preflight wrote suspect rows")

    def test_clean_preflight_is_0_and_writes_no_suspect_file(self):
        suspect = os.path.join(self.home, "suspect.json")
        done = self.actl("test", "--suspect-file", suspect, "preflight")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(suspect), "a clean preflight wrote suspect rows")


if __name__ == "__main__":
    unittest.main()
