"""`scripts/status.py` 與 `gate.sh --ticket`:狀態檔與 flake 重跑(D-010)。

這一組釘的是使用者 2026-09-20 那三句話的可執行形狀:**「跑完了沒、錯了什麼、
去哪裡看錯的 case」**。在它之前,一個 agent 只有兩條路知道閘門怎麼了 —— 把整份
log 讀進上下文,或用 sleep 迴圈輪詢背景工作,兩條都是一次幾十萬 token。

所以這裡問的不是「JSON 長得對不對」,是那三句話**答不出來的時候會不會被發現**:
- 還在跑 vs 跑完了:`state` 與 `rc` 是兩格,少了 `finished` 的 `done` 是壞掉的檔。
- 錯了什麼:`failures` 逐條要有案例名、檔、行、log 路徑與 excerpt。
- 偶發的紅 vs 真的紅:單獨重跑綠的進 `flaky`,**不是從紅榜消失**。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

# 一份真的 unittest 輸出(從這個 repo 自己跑出來的形狀抄的,不是手捏的格式)。
LOG = """test_it_ran (test_ticket.T.test_it_ran) ... ok
test_it_is_red (test_zz_red.T.test_it_is_red) ... FAIL

======================================================================
FAIL: test_it_is_red (test_zz_red.T.test_it_is_red)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_zz_red.py", line 5, in test_it_is_red
    self.assertEqual(1, 2, "假的紅")
AssertionError: 1 != 2 : 假的紅

----------------------------------------------------------------------
Ran 2 tests in 0.003s

FAILED (failures=1)
"""

# 瀏覽器那一族:引擎寫在輸出裡,紅榜要把它帶出來 —— 「chrome 紅 firefox 綠」與
# 「兩個都紅」是兩件事,而少了 engine 那一格,它們在紅榜上長得一樣。
LOG_ENGINE = """======================================================================
ERROR: test_top_band (test_browsers.Band.test_top_band) [engine=firefox]
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_browsers.py", line 42, in test_top_band
    raise TimeoutError("頁面沒畫出來")
TimeoutError: 頁面沒畫出來

----------------------------------------------------------------------
Ran 1 test in 9.100s

FAILED (errors=1)
"""

# 第一次跑紅、第二次跑綠 —— flake 的最小可重現形狀。計數寫在檔案裡,所以
# 「同一個案例跑第二次」與「另一個案例」分得開。
FLAKY = """import os
import unittest


class T(unittest.TestCase):
    def test_flaky(self):
        mark = os.path.join(os.environ["AC_TEST_HOME"], "flaky.count")
        seen = os.path.exists(mark)
        open(mark, "a", encoding="utf-8").close()
        self.assertTrue(seen, "第一次跑一定紅")
"""

ALWAYS_RED = """import unittest


class T(unittest.TestCase):
    def test_always_red(self):
        self.assertEqual(1, 2, "真的紅")
"""


class StatusFile(Sandbox):

    def status(self, *args):
        return self.run_py("scripts/status.py", *args)

    def load(self, ticket="7"):
        return json.loads(self.read(os.path.join("reports", "t%s-status.json" % ticket)))

    # ------------------------------------------------------------ 跑完了沒

    def test_start_says_running_and_has_no_rc_yet(self):
        """**變異**:把 `cmd_start` 的 `"state": "running"` 改成 `"done"` → 這一條紅。

        理由:`{"state": "done", "rc": null}` 與「跑完了而且綠了」在讀的人眼裡長得
        一樣,而那正是要分開的兩件事 —— **還在跑要等,跑完了才有得修**。
        """
        done = self.status("start", "--ticket", "7", "--kind", "gate", "--sha", "abc1234")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = self.load()
        self.assertEqual(data["state"], "running")
        self.assertEqual(data["kind"], "gate")
        self.assertEqual(data["sha"], "abc1234")
        self.assertIsNone(data["rc"])
        self.assertIsNone(data["finished"])
        self.assertTrue(data["started"], "沒有開跑時間 = 答不出「跑多久了」")

    def test_done_overwrites_with_rc_and_keeps_the_start_time(self):
        self.status("start", "--ticket", "7", "--kind", "gate", "--sha", "abc1234")
        started = self.load()["started"]
        done = self.status("done", "--ticket", "7", "--rc", "0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = self.load()
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["rc"], 0)
        self.assertEqual(data["started"], started, "開跑時間被覆寫掉就算不出跑多久")
        self.assertTrue(data["finished"])
        self.assertEqual(data["kind"], "gate", "沒帶 --kind 時要沿用 start 寫的")

    def test_a_ticket_with_no_status_file_says_so_instead_of_printing_nothing(self):
        """「這一輪還沒開跑」與「跑完了而且沒事」在一片空白的輸出裡長得一樣。"""
        done = self.status("show", "--ticket", "7")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("還沒有狀態檔", done.stderr)

    # ------------------------------------------------------------ 錯了什麼

    def test_failures_carry_the_case_the_file_the_line_and_an_excerpt(self):
        """**變異**:把 `parse_failures` 的 `HEAD` 改成只認 `^FAIL:` → 這一條還綠,
        但下面那條(engine 那一條,它是 `ERROR:`)會紅。兩條一起看才釘得住。
        """
        log = self.write("gate.log", LOG)
        self.status("start", "--ticket", "7", "--kind", "gate")
        self.status("done", "--ticket", "7", "--rc", "1", "--log", log)
        data = self.load()
        self.assertEqual(len(data["failures"]), 1, data["failures"])
        row = data["failures"][0]
        self.assertEqual(row["case"], "test_zz_red.T.test_it_is_red",
                         "案例 id 要餵得回 `python3 -m unittest`")
        self.assertEqual(row["file"], "/sandbox/tests/test_zz_red.py")
        self.assertEqual(row["line"], 5)
        self.assertEqual(row["log"], log, "沒有 log 路徑就答不出「去哪裡看」")
        self.assertIn("AssertionError: 1 != 2", row["excerpt"])
        self.assertLessEqual(len(row["excerpt"].splitlines()), 20,
                             "excerpt 超過 20 行就是把整份 log 搬進來了")
        self.assertEqual(data["logs"], [log])

    def test_an_error_with_an_engine_keeps_the_engine(self):
        log = self.write("gate.log", LOG_ENGINE)
        self.status("done", "--ticket", "7", "--rc", "1", "--log", log)
        row = self.load()["failures"][0]
        self.assertEqual(row["kind"], "ERROR")
        self.assertEqual(row["engine"], "firefox",
                         "「chrome 紅 firefox 綠」與「兩個都紅」是兩件事")
        self.assertEqual(row["case"], "test_browsers.Band.test_top_band")

    def test_failures_prints_one_rerunnable_id_per_line(self):
        """呼叫它的是 shell 迴圈 —— 多印一個字,那個迴圈就會拿它當案例名去跑。"""
        log = self.write("gate.log", LOG)
        done = self.status("failures", "--log", log)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.split(), ["test_zz_red.T.test_it_is_red"])

    def test_a_green_log_has_no_failures(self):
        log = self.write("gate.log", "Ran 2 tests in 0.003s\n\nOK\n")
        self.status("done", "--ticket", "7", "--rc", "0", "--log", log)
        self.assertEqual(self.load()["failures"], [])

    # -------------------------------------------------------------- flaky

    def test_a_flaky_case_moves_out_of_failures_but_does_not_disappear(self):
        """**變異**:把 `cmd_done` 裡那一行改成 `flaky = []` → 這一條紅。

        單跑綠 **≠ 修好了**。一條經常 flaky 的案例要被看見,才有人會去修它的不穩定
        —— 從紅榜整個消失的那一條,誰都不會再想起它。
        """
        log = self.write("gate.log", LOG)
        self.status("done", "--ticket", "7", "--rc", "0", "--log", log,
                    "--flaky", "test_zz_red.T.test_it_is_red")
        data = self.load()
        self.assertEqual(data["failures"], [])
        self.assertEqual(len(data["flaky"]), 1)
        self.assertEqual(data["flaky"][0]["case"], "test_zz_red.T.test_it_is_red")


class GateWritesStatus(Sandbox):
    """`gate.sh --ticket <票號>`:同一支閘門,多一份給 agent 讀的狀態檔。"""

    def setUp(self):
        super().setUp()
        self.write("tests/test_land.py",
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_ok(self):\n        pass\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的替身測試模組")

    def gate(self, *args, **extra):
        return self.run_sh("scripts/gate.sh", *args,
                           env=self.env(AC_TEST_HOME=self.home, **extra))

    def load(self, ticket="7"):
        return json.loads(self.read(os.path.join("reports", "t%s-status.json" % ticket)))

    def test_without_a_ticket_nothing_is_written(self):
        """沒給票號就完全照舊 —— 一支新功能不該改變舊呼叫者看到的東西。"""
        done = self.gate("scripts/land.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(self.exists("reports"), "沒給票號卻寫了檔")

    def test_a_green_run_leaves_a_done_status_with_rc_zero(self):
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = self.load()
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["rc"], 0)
        self.assertEqual(data["kind"], "gate")
        self.assertEqual(data["failures"], [])

    def test_a_red_run_names_the_case_in_the_status_file(self):
        """**變異**:把 `status_done "$rc"` 從紅的那條路上拿掉 → 這一條紅
        (狀態檔會停在 `running`,而**停在 running 的檔與還在跑的檔長得一樣**)。
        """
        self.write("tests/test_land.py", ALWAYS_RED)
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        data = self.load()
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["rc"], 1)
        self.assertEqual([row["case"] for row in data["failures"]],
                         ["test_land.T.test_always_red"])
        self.assertIn("真的紅", data["failures"][0]["excerpt"])

    def test_a_file_nobody_guards_still_leaves_a_status_file(self):
        """「沒有人守著這幾個檔」是一個結果,不是一次沒跑 —— 讀狀態檔的人要看得到
        rc=3,而不是看到一個停在 `running` 的檔。"""
        self.write("assets/logo.png", "x")
        done = self.gate("assets/logo.png", "--ticket", "7")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual(self.load()["rc"], 3)

    def test_a_case_that_passes_on_its_own_is_flaky_and_the_gate_goes_green(self):
        """紅的案例單獨重跑一次:單跑綠的全部都是 flaky → 這一輪視為綠。

        **變異**:把 `flake_rerun` 裡的 `return 0` 改成 `return "$1"` → 這一條紅。
        理由:照著偶發的紅去派一輪修 bug,那一輪從頭到尾都是白跑的。
        """
        self.write("tests/test_land.py", FLAKY)
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("標成 flaky", done.stdout)
        data = self.load()
        self.assertEqual(data["rc"], 0)
        self.assertEqual(data["failures"], [], "全 flaky 時紅榜要是空的")
        self.assertEqual([row["case"] for row in data["flaky"]],
                         ["test_land.T.test_flaky"], "但它不准從狀態檔消失")

    def test_a_case_that_is_red_on_its_own_stays_red(self):
        """flake 重跑不是一張免死金牌:單跑還是紅的就是真紅。"""
        self.write("tests/test_land.py", ALWAYS_RED)
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("標成 flaky", done.stdout)
        self.assertEqual(self.load()["flaky"], [])

    def test_the_rerun_can_be_turned_off(self):
        self.write("tests/test_land.py", FLAKY)
        done = self.gate("scripts/land.sh", "--ticket", "7", AC_NO_FLAKE_RERUN="1")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("標成 flaky", done.stdout)

    def test_a_ticket_flag_with_no_value_is_a_usage_error(self):
        done = self.gate("--branch", "--ticket")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("要票號", done.stderr)


if __name__ == "__main__":
    unittest.main()
