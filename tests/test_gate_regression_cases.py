"""`gate.sh --branch --ticket <n>`: the regression layer runs **whole**, not the ticket's tags.

#159's branch gate was green and its land went red with 84 regression cases, all under tags
the ticket never declared: the branch gate only ran `regression --tag <ticket tags>`. These
cases pin the shape the ticket asks for (A1-A5): unit layer, then the whole regression layer,
the `verify.tags` gap check kept (one `--list` call, no second run), `--full` untouched.

Oracles come from the sandbox fixture itself (its case files and their `def test_` count),
from the ticket text (wording, rc table) and from reading base `regression()`; never from
gate output parsed back into expectations.

The regression command is `tests.regression` in the sandbox config: a wrapper that writes
its arguments to a call log and `exec`s the built-in (`verify.py --result …`), so every case
really runs through verify.py; the call log is only there to count calls.
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import VERIFY_CASE, Sandbox, write_executable  # noqa: E402


class BranchGateRunsTheWholeRegressionLayer(Sandbox):
    """A1-A3 + the #159 shape on one sandbox: `verify/example` (1 case, tag `example`)
    plus `verify/second` (3 cases, tag `second`), both green on main; `ghost` registered
    with no case. The branch changes `scripts/land.sh` (mapped to `test_land`)."""

    UNIT_PASSING = ("import unittest\n\n\nclass T(unittest.TestCase):\n"
                    "    def test_ok(self):\n        pass\n")
    # Each case appends its own name to `<home>/case-runs.log` (from `AC_TEST_LOG`'s
    # directory): how many times a case really executed, independent of the gate's log.
    SECOND_CASE = '''"""Sandbox regression case under the tag `second`."""
import os
import unittest

TAGS = ["second"]


def note(name):
    where = os.path.join(os.path.dirname(os.environ["AC_TEST_LOG"]), "case-runs.log")
    with open(where, "a", encoding="utf-8") as handle:
        handle.write(name + "\\n")


class Second(unittest.TestCase):
    def test_s1(self):
        note("s1")

    def test_s2(self):
        note("s2")
        # S2_BODY

    def test_s3(self):
        note("s3")
'''
    S2_RED = 'self.fail("second-red on the branch")'
    S2_FLAKY = ('mark = os.path.join(os.path.dirname(os.environ["AC_TEST_LOG"]), "s2-was-red")\n'
                '        if not os.path.exists(mark):\n'
                '            open(mark, "w").close()\n'
                '            self.fail("s2 red on its first run only")')
    S2_ID = "verify.second.test_second.Second.test_s2"
    TICKET = "7"

    def setUp(self):
        super().setUp()
        # Stand-ins for every unit module the branch diff can map to (the ticket file, the
        # mapped script, verify/*): this class asks about the regression layer only.
        for name in ("test_ticket", "test_event", "test_memory", "test_no_project_names",
                     "test_heartbeat", "test_land", "test_verify_runner"):
            self.write(os.path.join("tests", "%s.py" % name), self.UNIT_PASSING)
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- sandbox model: follow the dispatch, do not guess.\n")
        self.write(os.path.join("verify", "TAGS.md"), self.read(os.path.join("verify", "TAGS.md"))
                   + "- `second` — sandbox second tag\n- `ghost` — registered, no case\n")
        self.write(os.path.join("verify", "second", "__init__.py"), "")
        self.write(os.path.join("verify", "second", "test_second.py"), self.SECOND_CASE)
        self.calls = os.path.join(self.home, "regression-calls.log")
        fake = os.path.join(self.home, "fake-regression.sh")
        write_executable(fake, "#!/bin/sh\n"
                               "printf '%%s\\n' \"regression $*\" >> '%s'\n"
                               "exec python3 \"$AC_HOME/scripts/verify.py\" "
                               "--result \"$AC_TEST_OUT/result.json\" \"$@\"\n" % self.calls)
        config = json.loads(self.read("board/config.json"))
        config["tests"]["regression"] = "sh '%s'" % fake
        self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "sandbox: two tags, a regression wrapper, unit stand-ins")
        # The fake auto-fix: a marker file, nothing else.
        self.autofix_marker = os.path.join(self.home, "auto-fix-ran")
        self.stub_script("auto-fix.sh", "#!/bin/sh\necho \"$1\" >> '%s'\n" % self.autofix_marker)
        self.verify_log = os.path.join(self.home, "verify-branch.log")

    # ------------------------------------------------------------ helpers (own class only)

    def fixture_case_count(self):
        """The oracle: `def test_` lines in the sandbox's own regression case files."""
        total = 0
        for rel in (os.path.join("verify", "example", "test_example.py"),
                    os.path.join("verify", "second", "test_second.py")):
            total += len(re.findall(r"^    def test_", self.read(rel), re.M))
        return total

    def ticket_with(self, tags=None, **fields):
        if tags is not None:
            fields["verify"] = {"files": [], "tags": tags, "run": "", "notes": ""}
        fields.setdefault("needs_verifier", False)
        return self.make_ticket(self.TICKET, **fields)

    def branch_change(self, s2_body=None):
        self.write(os.path.join("scripts", "land.sh"), "# the branch touched it\n")
        if s2_body is not None:
            self.write(os.path.join("verify", "second", "test_second.py"),
                       self.SECOND_CASE.replace("# S2_BODY", s2_body))

    def branch_gate(self, *extra, **env):
        env.setdefault("AC_VERIFY_LOG", self.verify_log)
        return self.run_sh("scripts/gate.sh", "--branch", "--ticket", self.TICKET, *extra,
                           env=self.env(**env))

    @staticmethod
    def ran_count(text):
        return sum(int(n) for n in re.findall(r"^Ran (\d+) tests? in ", text, re.M))

    def logged(self, path):
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line.strip()]

    def regression_calls(self):
        return self.logged(self.calls)

    def case_runs(self, name):
        return self.logged(os.path.join(self.home, "case-runs.log")).count(name)

    def say(self, done):
        return "rc=%s\n%s\n%s\ncalls=%r" % (done.returncode, done.stdout, done.stderr,
                                           self.regression_calls())

    # ------------------------------------------------------------ A1

    def test_a1_no_declared_tags_runs_the_whole_regression_layer(self):
        """A1 verify.tags [] -> 'gate: 回歸全部' and Ran == both files' case count."""
        self.ticket_with(tags=[])
        self.branch_change()
        done = self.branch_gate("--no-auto-fix")
        self.assertEqual(done.returncode, 0, self.say(done))
        self.assertIn("gate: 回歸全部", done.stdout, self.say(done))
        with open(self.verify_log, encoding="utf-8") as handle:
            self.assertEqual(self.ran_count(handle.read()), self.fixture_case_count(),
                             self.say(done))

    def test_a1_a_declared_tag_still_runs_every_case_not_only_its_own(self):
        """A1 verify.tags ['example'] -> the same count as the whole layer (1 + 3, not 1)."""
        self.ticket_with(tags=["example"])
        self.branch_change()
        done = self.branch_gate("--no-auto-fix")
        self.assertEqual(done.returncode, 0, self.say(done))
        self.assertIn("gate: 回歸全部", done.stdout, self.say(done))
        with open(self.verify_log, encoding="utf-8") as handle:
            self.assertEqual(self.ran_count(handle.read()), self.fixture_case_count(),
                             self.say(done))
        self.assertEqual(self.case_runs("s1"), 1, "the `second` cases did not run")

    def test_a1_a_tag_that_selects_nothing_is_still_the_gap_rc_3_with_one_list_call(self):
        """A1 verify.tags ['ghost'] -> rc 3, today's gap message, exactly one '--list' call
        and at most one run call."""
        self.ticket_with(tags=["ghost"])
        self.branch_change()
        done = self.branch_gate("--no-auto-fix")
        self.assertEqual(done.returncode, 3, self.say(done))
        self.assertIn("gate: 票宣告了 tags,但一個案例都選不到 —— 那是缺口,不是綠。",
                      done.stdout, self.say(done))
        calls = self.regression_calls()
        listing = [c for c in calls if "--list" in c.split()]
        running = [c for c in calls if "--list" not in c.split()]
        self.assertEqual(len(listing), 1, self.say(done))
        self.assertIn("ghost", listing[0], "the gap check must list the declared tag")
        self.assertLessEqual(len(running), 1, self.say(done))

    def test_a1_a_ticket_without_a_verify_key_runs_the_whole_layer(self):
        """A1 a ticket with no 'verify' key at all -> regression still runs all."""
        row = self.ticket_with()
        self.assertNotIn("verify", row, "precondition: the ticket has no verify key")
        self.branch_change()
        done = self.branch_gate("--no-auto-fix")
        self.assertEqual(done.returncode, 0, self.say(done))
        self.assertIn("gate: 回歸全部", done.stdout, self.say(done))
        with open(self.verify_log, encoding="utf-8") as handle:
            self.assertEqual(self.ran_count(handle.read()), self.fixture_case_count(),
                             self.say(done))

    # ------------------------------------------------------------ A2 / #159 shape

    def test_a2_a_red_case_under_an_undeclared_tag_is_the_gate_red(self):
        """A2 (#159 shape) a `second` case red on the branch, ticket declares ['example'] ->
        rc 1, failures[0].case is that regression id, state 'done', fake auto-fix ran."""
        self.ticket_with(tags=["example"])
        self.branch_change(self.S2_RED)
        done = self.branch_gate()
        self.assertEqual(done.returncode, 1, self.say(done))
        status = self.status_of(self.TICKET, kind="gate")
        self.assertTrue(status["failures"], "rc 1 with an empty failures[]: %r" % status)
        self.assertEqual(status["failures"][0]["case"], self.S2_ID, status["failures"])
        self.assertEqual(status["state"], "done", status)
        self.assertTrue(os.path.exists(self.autofix_marker),
                        "the fake auto-fix was not called on a regression red")
        self.assertEqual(self.logged(self.autofix_marker), [self.TICKET])

    def test_a2_a_flaky_regression_case_is_rerun_by_id_and_marked_suspected_flaky(self):
        """A2 flaky-then-pass regression case -> rerun flake_auto_single_runs times,
        status.json marks it suspected_flaky. Group rerun off so the count is exact."""
        config = json.loads(self.read("board/config.json"))
        config["flake_auto_single_runs"] = 2
        self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "sandbox: two single reruns")
        self.ticket_with(tags=[])
        self.branch_change(self.S2_FLAKY)
        done = self.branch_gate("--no-auto-fix", AC_FLAKE_RERUN_GROUP="0")
        # 1 original run (red) + 2 single reruns (green).
        self.assertEqual(self.case_runs("s2"), 1 + 2, self.say(done))
        status = self.status_of(self.TICKET, kind="gate")
        rows = [row for row in status["failures"] if row["case"] == self.S2_ID]
        self.assertEqual(len(rows), 1, status["failures"])
        self.assertTrue(rows[0]["suspected_flaky"], rows[0])

    # ------------------------------------------------------------ A3

    def test_a3_two_gates_in_one_run_id_run_the_regression_once(self):
        """A3 two branch gates under one run id -> the second regression is served from
        verify.py's cache (one real execution of each case)."""
        self.ticket_with(tags=[])
        self.branch_change()
        for _ in range(2):
            done = self.branch_gate("--no-auto-fix", AC_GATE_RUN_ID="20261026-000000-1")
            self.assertEqual(done.returncode, 0, self.say(done))
        self.assertEqual(self.case_runs("s1"), 1, self.say(done))

    def test_a3_no_cache_runs_the_regression_twice(self):
        """A3 '--no-cache' -> two real runs under the same run id."""
        self.ticket_with(tags=[])
        self.branch_change()
        for _ in range(2):
            done = self.branch_gate("--no-auto-fix", "--no-cache",
                                    AC_GATE_RUN_ID="20261026-000000-2")
            self.assertEqual(done.returncode, 0, self.say(done))
        self.assertEqual(self.case_runs("s1"), 2, self.say(done))

    # ------------------------------------------------------------ --full vs --branch

    def test_full_and_branch_report_the_same_regression_ran_count(self):
        """Test plan: '--full' and '--branch --ticket' produce the same regression 'Ran '
        count on the same tree (and it is the fixture's count)."""
        self.ticket_with(tags=["example"])
        self.branch_change()
        branch = self.branch_gate("--no-auto-fix")
        self.assertEqual(branch.returncode, 0, self.say(branch))
        with open(self.verify_log, encoding="utf-8") as handle:
            branch_ran = self.ran_count(handle.read())
        full_log = os.path.join(self.home, "verify-full.log")
        full = self.run_sh("scripts/gate.sh", "--full", "--ticket", self.TICKET, "--no-auto-fix",
                           env=self.env(AC_VERIFY_LOG=full_log))
        self.assertEqual(full.returncode, 0, self.say(full))
        with open(full_log, encoding="utf-8") as handle:
            full_ran = self.ran_count(handle.read())
        self.assertEqual(full_ran, self.fixture_case_count())
        self.assertEqual(branch_ran, full_ran, self.say(branch))


class FullRegressionSectionIsUnchanged(Sandbox):
    """A4 `gate.sh --full`'s regression section is the same bytes as base `regression full`
    printed. One case file only (harness `verify/example`), so verify.py does not shard and
    the block order is fixed. Normalised: the `parallel:` line's numbers (cores / memory of
    this machine) and the `in <seconds>s` timing; nothing else."""

    # From base `regression()` (`full` arm): the header echo, the `^parallel: ` grep, then the
    # `^Ran |^OK|^FAILED|^verify: ` grep of the verify log. 1 test = harness VERIFY_CASE.
    EXPECTED = ("gate: 回歸全部 —— regression\n"
                "parallel: N processes (cores N, available memory N MiB, per-process N MiB)\n"
                "Ran 1 test in Ts\n"
                "OK\n")

    def setUp(self):
        super().setUp()
        self.write(os.path.join("tests", "test_land.py"),
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_ok(self):\n        pass\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "sandbox: one unit module")

    @staticmethod
    def section(stdout):
        """Lines from 'gate: 回歸全部' to the next 'gate: ' line (or status.py's 'status: '
        line, which `status_done` prints after `regression()` returns)."""
        lines = stdout.splitlines(True)
        start = next(i for i, line in enumerate(lines) if line.startswith("gate: 回歸全部"))
        out = [lines[start]]
        for line in lines[start + 1:]:
            if line.startswith("gate: ") or line.startswith("status: "):
                break
            if line.startswith("parallel: "):
                line = re.sub(r"\d+", "N", line)
            out.append(re.sub(r" in \d+(\.\d+)?s$", " in Ts", line.rstrip("\n")) + "\n")
        return "".join(out)

    def test_a4_full_regression_section_is_byte_identical(self):
        """A4 the regression section of 'gate.sh --full' stdout is unchanged."""
        self.assertEqual(len(re.findall(r"^    def test_", VERIFY_CASE, re.M)), 1,
                         "precondition: the harness example has exactly one case")
        self.make_ticket(7)
        done = self.run_sh("scripts/gate.sh", "--full", "--ticket", "7", "--no-auto-fix")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("gate: 回歸全部", done.stdout)
        self.assertEqual(self.section(done.stdout), self.EXPECTED, done.stdout)


class WorkflowDocSaysTheBranchGateRunsTheWholeLayer(unittest.TestCase):
    """A5 (static half) docs/WORKFLOW.md §閘門 says the branch gate runs '回歸層全部'."""

    def test_a5_workflow_doc_names_the_whole_regression_layer(self):
        """A5 grep -c '回歸層全部' docs/WORKFLOW.md >= 1."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "docs", "WORKFLOW.md"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertGreaterEqual(text.count("回歸層全部"), 1)


if __name__ == "__main__":
    unittest.main()
