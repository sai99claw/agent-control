"""#67 `status.py regression-red`:回歸紅在既有案例上 → 依案例 id 開(或沿用)修復票。

來源:T D-G130「以前過的 case 之後也要過,舊案例紅了就發 worker 修」。缺口是發版回歸紅了
沒有票可接 auto-fix,主線只能手開 stub。這一組釘的是:

- 每一條紅的案例**恰好一張**開著的票(查重認 `regression_case` 與 `flaky_case`,
  只認開著的);沿用時**不寫票** —— 覆核綁的 `state_version` 不能因為又紅一次而過期。
- 票的驗收是**固定五條樣板**,逐字比;期望值從旗標與 log 文字來,不從 status.py 算。
- 每一條案例一則 `regression.red`;`regression_auto_ticket: false` 只發事件不開票。
- `--dispatch` 只對**這次新開的**票背景起 auto-fix,不等它。
"""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox, write_executable  # noqa: E402
from test_status import LOG  # noqa: E402

CASE_A = "test_zz_red.T.test_it_is_red"
CASE_B = "test_zz_red.T.test_it_errors"

# 兩條不同 id 的紅:一條 FAIL、一條 ERROR(形狀比照 test_status.LOG)。
LOG_TWO = """test_it_ran (test_ticket.T.test_it_ran) ... ok
test_it_is_red (test_zz_red.T.test_it_is_red) ... FAIL
test_it_errors (test_zz_red.T.test_it_errors) ... ERROR

======================================================================
FAIL: test_it_is_red (test_zz_red.T.test_it_is_red)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_zz_red.py", line 5, in test_it_is_red
    self.assertEqual(1, 2, "假的紅")
AssertionError: 1 != 2 : 假的紅

======================================================================
ERROR: test_it_errors (test_zz_red.T.test_it_errors)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_zz_red.py", line 8, in test_it_errors
    raise RuntimeError("壞了")
RuntimeError: 壞了

----------------------------------------------------------------------
Ran 3 tests in 0.004s

FAILED (failures=1, errors=1)
"""

LOG_GREEN = """test_it_ran (test_ticket.T.test_it_ran) ... ok

----------------------------------------------------------------------
Ran 1 test in 0.001s

OK
"""

RERUN = "python3 -m unittest discover -s tests"


def template(case, rerun):
    """票面 A2 的五條,逐字抄自票面(不從 status.py 讀)。"""
    return [
        "%s 在全套原順序下綠:`%s` rc=0,且這一條不是 skip;紅的那個環境(發版靶)"
        "由主線發版時再跑一次證 —— 這張票不放行任何發版" % (case, rerun),
        "不准加 sleep 或猜測性等待(setTimeout/rAF/retry);不准放寬既有 timeout 或任何斷言;"
        "不准刪案例、不准 skip、不准收窄引擎或條件",
        "EVIDENCE 說得出紅的原因(測試等錯訊號 / 共用狀態 / 時序 / 產品行為改了),"
        "修的是原因不是症狀;產品行為改了而案例仍對 → 修產品",
        "案例明顯過時或不合理(D-G130 例外)→ 不改案例,OBJECTION category=ticket-wrong "
        "寫明為什麼過時,停下等使用者裁;worker 不准自己判過時",
        "變異:把修法還原 → 同一條案例在同樣條件下至少紅一次,輸出逐字貼進 EVIDENCE",
    ]


class RegressionRed(Sandbox):

    def status(self, *args, env=None):
        return self.run_py("scripts/status.py", *args, env=env)

    def red(self, *args, env=None):
        return self.status("regression-red", *args, env=env)

    def set_config(self, **fields):
        conf = json.loads(self.read("board/config.json"))
        conf.update(fields)
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def regression_tickets(self):
        return [row for row in self.tickets_on_disk() if row.get("regression_case")]

    def by_case(self):
        return {row["regression_case"]: row for row in self.regression_tickets()}

    def red_events(self):
        return [row for row in self.events() if row["kind"] == "regression.red"]

    def ticket_bytes(self):
        where = os.path.join(self.repo, "tickets")
        out = {}
        for name in sorted(os.listdir(where)):
            with open(os.path.join(where, name), "rb") as handle:
                out[name] = handle.read()
        return out

    # ------------------------------------------------------------ A1 / A2

    def test_a1_two_red_cases_open_two_ready_worker_tickets(self):
        """A1。**變異**:拿掉 KINDS 的 `regression.red` → rc≠0,這一條紅。"""
        log = self.write("release.log", LOG_TWO)
        done = self.red("--source", "release", "--log", log, "--rerun", RERUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        made = self.by_case()
        self.assertEqual(sorted(made), sorted([CASE_A, CASE_B]))
        for case, row in made.items():
            self.assertEqual(row["state"], "Ready")
            self.assertEqual(row["role"], "worker")
            self.assertIs(row["needs_verifier"], False)
            self.assertIs(row["interface_fixed"], False)
            self.assertEqual(row["regression_source"], "release")
            self.assertEqual(row["regression_log"], log)
            self.assertIn(case, row["subject"])
            self.assertEqual(row["in_scope"], [case])
            self.assertEqual(row["verify"]["run"], RERUN)
            self.assertTrue(row["base_sha"])

    def test_a2_acceptance_is_the_fixed_five_line_template(self):
        log = self.write("release.log", LOG_TWO)
        done = self.red("--source", "gate", "--log", log, "--rerun", RERUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for case, row in self.by_case().items():
            self.assertEqual(row["acceptance"], template(case, RERUN))

    # ------------------------------------------------------------ A3 查重

    def test_a3_the_same_log_twice_reuses_and_does_not_touch_the_tickets(self):
        """A3。**變異**:拿掉 `open_repair_ticket` 的查重迴圈 → 第二次多開兩張,這一條紅。"""
        log = self.write("release.log", LOG_TWO)
        first = self.red("--source", "release", "--log", log, "--rerun", RERUN)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = self.ticket_bytes()
        idents = sorted(row["id"] for row in self.regression_tickets())
        second = self.red("--source", "release", "--log", log, "--rerun", RERUN)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(len(self.regression_tickets()), 2)
        for ident in idents:
            self.assertIn("沿用 #%s" % ident, second.stdout)
        self.assertEqual(self.ticket_bytes(), before, "沿用時票檔被寫了 —— state_version 會過期")

    def test_a3_an_open_flaky_ticket_for_the_case_is_reused(self):
        self.make_ticket(50, flaky_case=CASE_A, state="Ready")
        done = self.red("--source", "release", "--case", CASE_A, "--rerun", RERUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("沿用 #50", done.stdout)
        self.assertEqual(self.regression_tickets(), [])
        self.assertEqual(len(self.tickets_on_disk()), 1)

    def test_a3_a_closed_ticket_for_the_case_does_not_count(self):
        self.make_ticket(50, regression_case=CASE_A, state="Done")
        done = self.red("--source", "release", "--case", CASE_A, "--rerun", RERUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        opened = [row for row in self.regression_tickets() if row["id"] != "50"]
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["regression_case"], CASE_A)
        self.assertEqual(opened[0]["state"], "Ready")

    # ------------------------------------------------------------ A4 來源與空集合

    def test_a4_cases_without_a_log_open_one_ticket_each(self):
        done = self.red("--source", "release", "--case", "a.B.c", "--case", "a.B.d",
                        "--rerun", RERUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(self.by_case()), ["a.B.c", "a.B.d"])
        for row in self.regression_tickets():
            self.assertEqual(row["regression_log"], "")

    def test_a4_the_same_id_from_log_and_case_opens_one(self):
        log = self.write("release.log", LOG_TWO)
        done = self.red("--source", "release", "--log", log, "--case", CASE_A,
                        "--rerun", RERUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(self.by_case()), sorted([CASE_A, CASE_B]))
        self.assertEqual(len(self.regression_tickets()), 2)

    def test_a4_a_green_log_and_no_case_opens_nothing(self):
        log = self.write("release.log", LOG_GREEN)
        before = self.ticket_bytes()
        done = self.red("--source", "release", "--log", log)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("沒有紅的案例", done.stdout)
        self.assertEqual(self.ticket_bytes(), before)

    def test_a4_a_missing_log_is_rc2_and_names_the_path(self):
        missing = os.path.join(self.home, "no-such.log")
        done = self.red("--source", "release", "--log", missing)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn(missing, done.stderr)

    def test_a4_source_missing_or_unknown_is_rc2_and_lists_the_known(self):
        for args in ([], ["--source", "deploy"]):
            done = self.red("--case", CASE_A, *args)
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn("release", done.stderr)
            self.assertIn("gate", done.stderr)
        self.assertEqual(self.tickets_on_disk(), [])

    # ------------------------------------------------------------ A5 取值順序

    def test_a5_model_flag_beats_config_beats_default(self):
        self.red("--source", "release", "--case", "a.B.c", "--model", "x", "--rerun", RERUN)
        self.set_config(routing={"implement": "sonnet"})
        self.red("--source", "release", "--case", "a.B.d", "--rerun", RERUN)
        self.set_config(routing={})
        self.red("--source", "release", "--case", "a.B.e", "--rerun", RERUN)
        made = self.by_case()
        self.assertEqual(made["a.B.c"]["model"], "x")
        self.assertEqual(made["a.B.d"]["model"], "sonnet")
        self.assertEqual(made["a.B.e"]["model"], "opus")

    def test_a5_rerun_flag_beats_rerun_cmd_beats_unit_cmd(self):
        self.set_config(regression={"rerun_cmd": "sh rerun.sh"}, unit_cmd="sh unit.sh")
        self.red("--source", "release", "--case", "a.B.c", "--rerun", "sh flag.sh")
        self.red("--source", "release", "--case", "a.B.d")
        self.set_config(regression={})
        self.red("--source", "release", "--case", "a.B.e")
        made = self.by_case()
        self.assertEqual(made["a.B.c"]["verify"]["run"], "sh flag.sh")
        self.assertEqual(made["a.B.d"]["verify"]["run"], "sh rerun.sh")
        self.assertEqual(made["a.B.e"]["verify"]["run"], "sh unit.sh")

    def test_a5_no_rerun_anywhere_still_opens_and_says_so(self):
        done = self.red("--source", "release", "--case", "a.B.c")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.by_case()["a.B.c"]["verify"]["run"], "")
        self.assertIn("沒有全套指令,verify.run 留空:設 board/config.json 的 "
                      "regression.rerun_cmd", done.stderr)

    def test_a5_write_paths_from_config_or_the_default(self):
        self.red("--source", "release", "--case", "a.B.c", "--rerun", RERUN)
        self.set_config(regression={"write_paths": ["demo/test_*.py", "demo/web/**"]})
        self.red("--source", "release", "--case", "a.B.d", "--rerun", RERUN)
        made = self.by_case()
        self.assertEqual(made["a.B.c"]["allowed_write_paths"], ["tests/*", "verify/*"])
        self.assertEqual(made["a.B.d"]["allowed_write_paths"],
                         ["demo/test_*.py", "demo/web/**"])

    # ------------------------------------------------------------ A6 開關與事件

    def test_a6_one_event_per_case_with_ticket_case_source_reused(self):
        self.make_ticket(50, regression_case=CASE_A, state="Ready")
        log = self.write("release.log", LOG_TWO)
        done = self.red("--source", "release", "--log", log, "--rerun", RERUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = {row["case"]: row for row in self.red_events()}
        self.assertEqual(len(self.red_events()), 2)
        self.assertEqual(sorted(rows), sorted([CASE_A, CASE_B]))
        self.assertEqual(rows[CASE_A]["ticket"], "50")
        self.assertIs(rows[CASE_A]["reused"], True)
        self.assertEqual(rows[CASE_B]["ticket"], self.by_case()[CASE_B]["id"])
        self.assertIs(rows[CASE_B]["reused"], False)
        for row in rows.values():
            self.assertEqual(row["source"], "release")

    def test_a6_auto_ticket_off_emits_but_opens_nothing(self):
        self.set_config(regression_auto_ticket=False)
        log = self.write("release.log", LOG_TWO)
        done = self.red("--source", "release", "--log", log, "--rerun", RERUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.tickets_on_disk(), [])
        rows = self.red_events()
        self.assertEqual(sorted(row["case"] for row in rows), sorted([CASE_A, CASE_B]))
        for row in rows:
            # `event.emit` 不寫空值的欄位:「空字串」在事件列上就是這一格不在。
            self.assertEqual(row.get("ticket", ""), "")
            self.assertIs(row["reused"], False)

    # ------------------------------------------------------------ A7 --dispatch

    def dispatch_env(self):
        self.calls = os.path.join(self.home, "dispatch.calls")
        stub = os.path.join(self.home, "dispatch-stub.sh")
        write_executable(stub, '#!/bin/sh\necho "$*" >> "%s"\n' % self.calls)
        return self.env(AC_REGRESSION_DISPATCH_CMD="sh %s" % stub)

    def dispatched(self, want):
        """替身是背景起的(不等它),所以等到它寫出第 `want` 行為止,有上限。"""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if os.path.exists(self.calls):
                with open(self.calls, encoding="utf-8") as handle:
                    lines = handle.read().splitlines()
                if len(lines) >= want:
                    return lines
            time.sleep(0.05)
        self.fail("替身 30 秒內沒被叫到 %d 次(%s)" % (want, self.calls))

    def test_a7_dispatch_starts_auto_fix_only_for_the_new_ticket(self):
        """A7。**變異**:拿掉 `subprocess.Popen` → 替身從沒被叫,這一條紅。"""
        env = self.dispatch_env()
        self.make_ticket(50, regression_case=CASE_A, state="Ready")
        log = self.write("release.log", LOG_TWO)
        done = self.red("--source", "release", "--log", log, "--rerun", RERUN,
                        "--dispatch", env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        new = self.by_case()[CASE_B]["id"]
        self.assertEqual(self.dispatched(1), [new])
        self.assertEqual(done.stdout.count("已起 auto-fix #"), 1)
        self.assertIn("已起 auto-fix #%s(背景,log " % new, done.stdout)
        self.assertIn(os.path.join("reports", "t%s" % new, "regression-dispatch.log"),
                      done.stdout)
        self.assertNotIn("已起 auto-fix #50", done.stdout)

    def test_a7_without_dispatch_nothing_is_started(self):
        env = self.dispatch_env()
        log = self.write("release.log", LOG_TWO)
        done = self.red("--source", "release", "--log", log, "--rerun", RERUN, env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("已起 auto-fix", done.stdout)
        # 下一趟帶 --dispatch 的新案例是會合點:它的那一行出現時,前一趟若有起替身,
        # 那一行早就寫進去了。
        again = self.red("--source", "release", "--case", "a.B.c", "--rerun", RERUN,
                         "--dispatch", env=env)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.dispatched(1), [self.by_case()["a.B.c"]["id"]])


class FlakyStillOpensVerifierTickets(Sandbox):
    """A8 的補強:flaky 票改走共用 helper 之後,仍是 role=verifier、驗收三條文字不變、
    沒有 regression_* 欄。既有三條(test_status.py)一個字不改,這裡只多釘文字。"""

    def test_the_flaky_ticket_keeps_its_role_and_acceptance(self):
        log = self.write("gate.log", LOG)
        for index in range(3):
            self.run_py("scripts/status.py", "done", "--ticket", "7", "--run-id", "r%d" % index, "--rc", "1",
                        "--log", log, "--suspected-flaky", CASE_A)
        made = [row for row in self.tickets_on_disk() if row.get("flaky_case") == CASE_A]
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0]["role"], "verifier")
        self.assertEqual(made[0]["acceptance"], [
            "原順序整組連跑 10 次,%s 沒有一次紅" % CASE_A,
            "說得出它不穩的原因(共用狀態 / 時序 / 外部資源),寫進 EVIDENCE",
            "不是靠放寬斷言或加 retry 讓它綠的",
        ])
        self.assertNotIn("regression_case", made[0])


if __name__ == "__main__":
    unittest.main()
