"""`scripts/status.py` 與 `gate.sh --ticket`:狀態檔與 flake 重跑(D-010)。

這一組釘的是使用者 2026-09-20 那三句話的可執行形狀:**「跑完了沒、錯了什麼、
去哪裡看錯的 case」**。在它之前,一個 agent 只有兩條路知道閘門怎麼了 —— 把整份
log 讀進上下文,或用 sleep 迴圈輪詢背景工作,兩條都是一次幾十萬 token。

所以這裡問的不是「JSON 長得對不對」,是那三句話**答不出來的時候會不會被發現**:
- 還在跑 vs 跑完了:`state` 與 `rc` 是兩格,少了 `finished` 的 `done` 是壞掉的檔。
- 錯了什麼:`failures` 逐條要有案例名、檔、行、log 路徑與 excerpt。
- 偶發的紅 vs 真的紅:單獨重跑綠的只降級成 `suspected_flaky`,**紅榜與 rc 都不動**
  (2026-09-21 外部審查的順序依賴反例:整組必紅、單跑必綠,而舊版正是以「全部單跑
  都綠」為由回傳 0)。
"""

import ast
import json
import os
import re
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, Sandbox  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))
import status as status_module  # noqa: E402

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
        with open(mark, "a", encoding="utf-8") as handle:
            handle.write("x")
        self.assertTrue(seen, "第一次跑一定紅")
"""

# 原生 subTest 的輸出:**圓括號的參數**,不是自己組的方括號標籤。這一段是 2026-09-21
# 外部審查跑出來的原文,一個字都沒改 —— 舊版把它解析成
# `__main__.NativeSubtest.test_engine) (engine='firefox'.test_engine`,一個餵不回
# `python3 -m unittest` 的 id。
LOG_NATIVE_SUBTEST = """FAIL: test_engine (__main__.NativeSubtest.test_engine) (engine='firefox')
AssertionError: 1 != 2
Ran 1 test in 0.000s
FAILED (failures=1)
"""

# 順序依賴的最小反例(同一份審查):第一條污染共用狀態,第二條檢查乾淨狀態。
# **整組必紅,單跑第二條必綠** —— 舊版的 flake 判定正是放它過去的那一條。
ORDER_DEPENDENT = """import unittest

LEAKED = []


class T(unittest.TestCase):
    def test_a_leaks(self):
        LEAKED.append("leak")

    def test_b_wants_it_clean(self):
        self.assertEqual(LEAKED, [], "Lists differ")
"""

ALWAYS_RED = """import unittest


class T(unittest.TestCase):
    def test_always_red(self):
        self.assertEqual(1, 2, "真的紅")
"""


class StatusFile(Sandbox):

    def status(self, *args, env=None):
        return self.run_py("scripts/status.py", *args, env=env)

    def load(self, ticket="7"):
        return self.status_of(ticket)

    def test_failure_shape_removes_numbers_and_ids(self):
        """**變異**:拿掉數字正規化 → 這一條紅。

        理由:同一個環境故障的每一條只差流水號(`... after 101 seconds` /
        `... after 202 seconds`)。逐字比就是 26 種不同的紅,而「環境壞了」與「26 個
        真 bug」因此長得一樣。"""
        first = status_module.normalized_failure_message(
            AssertionError("localStorage id=abc-123 empty after 101 seconds"))
        second = status_module.normalized_failure_message(
            AssertionError("localStorage id=xyz-987 empty after 202 seconds"))
        self.assertEqual(first, second)
        self.assertNotIn("abc-123", first)
        self.assertNotIn("101", first)

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

    def test_a_suspected_flaky_case_stays_in_the_red_list(self):
        """**變異**:把 `cmd_done` 裡那一行改回「從 failures 搬走」→ 這一條紅。

        單跑綠 **≠ 修好了**,也 **≠ 那條紅是假的**:順序依賴的失敗單跑一定綠。所以
        它只是被標記,不是被搬走 —— 從紅榜消失的那一條,誰都不會再想起它。
        """
        log = self.write("gate.log", LOG)
        self.status("done", "--ticket", "7", "--rc", "1", "--log", log,
                    "--suspected-flaky", "test_zz_red.T.test_it_is_red")
        data = self.load()
        self.assertEqual(len(data["failures"]), 1, "疑似 flaky 不准離開紅榜")
        self.assertTrue(data["failures"][0]["suspected_flaky"])
        self.assertEqual([row["case"] for row in data["suspected_flaky"]],
                         ["test_zz_red.T.test_it_is_red"])

    def test_a_native_subtest_parses_into_a_rerunnable_id(self):
        """**變異**:把 `parse_head` 換回舊的那個正規表示式 → 這一條紅。

        審查實測:舊版把整行尾巴吃進 qualifier,case 變成
        `__main__.NativeSubtest.test_engine) (engine='firefox'.test_engine`,
        subtest 與 engine 都是空字串 —— 而**餵不回 unittest 的紅榜與沒有紅榜一樣**。
        """
        log = self.write("gate.log", LOG_NATIVE_SUBTEST)
        self.status("done", "--ticket", "7", "--rc", "1", "--log", log)
        row = self.load()["failures"][0]
        self.assertEqual(row["case"], "__main__.NativeSubtest.test_engine")
        self.assertEqual(row["subtest"], "engine='firefox'")
        self.assertEqual(row["engine"], "firefox")

    def test_each_round_gets_its_own_directory_and_the_old_one_survives(self):
        """**變異**:把 run 目錄改回單一覆寫檔 → 這一條紅。

        舊版每票一份覆寫檔:上一輪的結果被這一輪蓋掉,而接手的人分不出手上這份是
        誰的(審查:status 的生命週期會誤導接手者)。
        """
        self.status("start", "--ticket", "7", "--run-id", "r1")
        self.status("done", "--ticket", "7", "--run-id", "r1", "--rc", "1")
        self.status("start", "--ticket", "7", "--run-id", "r2")
        self.status("done", "--ticket", "7", "--run-id", "r2", "--rc", "0")
        done = self.status("show", "--ticket", "7", "--runs")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("r1", done.stdout)
        self.assertIn("r2", done.stdout)
        first = json.loads(self.read(os.path.join("reports", "t7", "r1", "status.json")))
        self.assertEqual(first["rc"], 1, "上一輪被這一輪蓋掉了")
        self.assertEqual(self.load()["rc"], 0, "show 預設要給最新那一輪")

    def test_the_repair_context_carries_what_a_new_worker_needs(self):
        """下一輪換的是**新的** worker,它手上只有這一份檔(審查:status 不是交接包)。"""
        patch = self.write("patch.diff", "--- base/x\n+++ work/x\n")
        self.make_ticket(7, attempt=2)
        self.status("start", "--ticket", "7", "--run-id", "r1", "--base-sha", "abc123",
                    "--worktree", "/tmp/wt", "--patch", patch, "--round", "2",
                    "--prev-evidence", "EVIDENCE.md", "--repro", "sh scripts/gate.sh --branch",
                    "--cwd", "/tmp/wt", "--env", "AC_GATE_LOG=gate.log")
        ctx = json.loads(self.read(os.path.join("reports", "t7", "r1", "status.json")))["repair_context"]
        self.assertEqual(ctx["base_sha"], "abc123")
        self.assertEqual(ctx["worktree"], "/tmp/wt")
        self.assertEqual(ctx["round"], 2)
        self.assertEqual(ctx["prev_evidence"], "EVIDENCE.md")
        self.assertEqual(ctx["repro"], {"cmd": "sh scripts/gate.sh --branch", "cwd": "/tmp/wt"})
        self.assertEqual(ctx["env"], {"AC_GATE_LOG": "gate.log"})
        self.assertEqual(len(ctx["patch"]["sha256"]), 64, "一個路徑答不出是不是同一份 patch")
        self.assertEqual(ctx["ticket"]["attempt"], 2, "票面快照沒帶進來")
        self.assertEqual(ctx["ticket"]["state_version"], 1)

    def test_the_log_is_copied_so_it_survives_the_worktree_going_away(self):
        """land 成功後會把 worktree 收掉,而 log 就住在那裡面 —— 只留路徑等於留了一個
        明天不存在的路徑。"""
        log = self.write("gate.log", LOG)
        self.status("done", "--ticket", "7", "--run-id", "r1", "--rc", "1", "--log", log)
        kept = self.load()["kept_logs"][0]["kept"]
        self.assertTrue(self.exists(kept), kept)
        self.assertIn("AssertionError", self.read(kept))

    def test_phases_are_recorded_separately(self):
        """gate / merge / push 混成一格的 `done` 會說謊 —— 舊版在 merge 與 push 之前
        就寫 `done, rc=0`。"""
        self.status("start", "--ticket", "7", "--run-id", "r1", "--kind", "land")
        self.status("phase", "--ticket", "7", "--run-id", "r1", "--phase", "gate", "--rc", "0")
        self.status("phase", "--ticket", "7", "--run-id", "r1", "--phase", "merge", "--rc", "1")
        self.status("done", "--ticket", "7", "--run-id", "r1", "--rc", "1")
        phases = self.load()["phases"]
        self.assertEqual([(row["phase"], row["rc"]) for row in phases],
                         [("gate", 0), ("merge", 1)])

    # ------------------------------------------------------------ 花了多久

    def test_done_writes_the_duration_and_the_round(self):
        """**變異**:把 `duration_seconds` 那一格拿掉 → 這一條紅。

        期望值從**檔上的兩個時間戳**自己減一次來,不從被測程式算 —— 拿被測程式的
        輸出當期望值,是「把期望值改成程式現在印什麼」的那一種假綠。
        """
        self.status("start", "--ticket", "7", "--run-id", "r1", "--round", "3")
        self.status("done", "--ticket", "7", "--run-id", "r1", "--rc", "0",
                    "--round", "3")
        data = self.load()
        started = datetime.fromisoformat(data["started"])
        finished = datetime.fromisoformat(data["finished"])
        self.assertEqual(data["duration_seconds"],
                         int((finished - started).total_seconds()))
        self.assertIsInstance(data["duration_seconds"], int)
        self.assertGreaterEqual(data["duration_seconds"], 0)
        self.assertEqual(data["round"], 3, "頂層的 round 要等於 repair_context 那一格")
        self.assertEqual(data["round"], data["repair_context"]["round"])

    def test_a_missing_start_time_makes_the_duration_null_not_zero(self):
        """**變異**:算不出來時回 0 → 這一條紅。

        0 秒是「跑得很快」,而「算不出來」不是一個秒數 —— 揉成同一個 0 的那一刻,
        一份缺了 `started` 的壞檔與一趟真的在同一秒內跑完的閘門長得一樣。
        """
        broken = {"state": "running", "run_id": "r1", "kind": "gate",
                  "ticket": "7", "rc": None, "finished": None, "phases": []}
        self.write(os.path.join("reports", "t7", "r1", "status.json"),
                   json.dumps(broken, ensure_ascii=False))
        self.status("done", "--ticket", "7", "--run-id", "r1", "--rc", "0")
        text = self.read(os.path.join("reports", "t7", "r1", "status.json"))
        self.assertIsNone(json.loads(text)["duration_seconds"])
        self.assertNotIn("\"duration_seconds\": 0", text,
                         "算不出來被寫成 0 了")

    def test_each_phase_carries_the_seconds_since_the_last_mark(self):
        """**變異**:把「沒有上一筆就減 `started`」那一支拿掉 → 這一條紅
        (第一筆的 `seconds` 會變成 null)。

        一段的牆鐘秒數是「從上一個記號到這個記號」,而第一段的上一個記號是開跑
        那一刻 —— 少了它,land 的第一段(閘門,最久的那一段)永遠沒有秒數。
        """
        self.status("start", "--ticket", "7", "--run-id", "r1", "--kind", "land")
        for phase in ("gate", "merge", "push"):
            self.status("phase", "--ticket", "7", "--run-id", "r1",
                        "--phase", phase, "--rc", "0")
        self.status("done", "--ticket", "7", "--run-id", "r1", "--rc", "0")
        data = self.load()
        phases = data["phases"]
        self.assertEqual([row["phase"] for row in phases], ["gate", "merge", "push"],
                         "land 現有的三筆要照樣寫得出來")
        marks = [data["started"]] + [row["at"] for row in phases]
        for index, row in enumerate(phases):
            self.assertEqual(
                row["seconds"],
                int((datetime.fromisoformat(row["at"])
                     - datetime.fromisoformat(marks[index])).total_seconds()),
                "第 %d 筆對到的不是上一個記號" % (index + 1))

    def test_phase_and_done_force_the_environment_run_id(self):
        self.status("start", "--ticket", "7", "--run-id", "from-env")
        self.status("start", "--ticket", "7", "--run-id", "from-flag")
        env = self.env(AC_RUN_ID="from-env")
        phase = self.status("phase", "--ticket", "7", "--run-id", "from-flag",
                            "--phase", "gate", "--rc", "0", env=env)
        done = self.status("done", "--ticket", "7", "--run-id", "from-flag",
                           "--rc", "1", env=env)
        self.assertEqual((phase.returncode, done.returncode), (0, 0))
        env_data = json.loads(self.read("reports/t7/from-env/status.json"))
        flag_data = json.loads(self.read("reports/t7/from-flag/status.json"))
        self.assertEqual(env_data["rc"], 1)
        self.assertEqual(env_data["phases"][0]["phase"], "gate")
        self.assertEqual(flag_data["state"], "running")

    def test_falling_back_to_latest_run_prints_a_warning(self):
        self.status("start", "--ticket", "7", "--run-id", "r1")
        phase = self.status("phase", "--ticket", "7", "--phase", "gate", "--rc", "0")
        done = self.status("done", "--ticket", "7", "--rc", "0")
        self.assertEqual((phase.returncode, done.returncode), (0, 0))
        self.assertIn("警告", phase.stderr)
        self.assertIn("警告", done.stderr)

    def test_flaky_rows_are_appended_with_one_write(self):
        rows = [
            {"case": "test_x.T.test_a", "kind": "FAIL", "subtest": "", "log": "a.log"},
            {"case": "test_x.T.test_b", "kind": "ERROR", "subtest": "", "log": "b.log"},
        ]
        original_write = os.write
        with mock.patch.object(status_module.os, "write", wraps=original_write) as write_call:
            status_module.record_flakes(self.repo, "7", "r1", rows)
        self.assertEqual(write_call.call_count, 1)
        saved = [json.loads(line) for line in
                 self.read("reports/flaky.jsonl").splitlines()]
        self.assertEqual([row["case"] for row in saved],
                         ["test_x.T.test_a", "test_x.T.test_b"])

    def test_suspected_flakes_go_into_a_persistent_ledger(self):
        """**變異**:把 `record_flakes` 拿掉 → 這一條紅。

        舊版只把 flake 留在當前 status,下一次 start 就清空 —— 同一條案例每天疑似
        一次,累計次數永遠是 1,沒有人會去修它的不穩定。
        """
        log = self.write("gate.log", LOG)
        for index in range(3):
            self.status("done", "--ticket", "7", "--run-id", "r%d" % index, "--rc", "1",
                        "--log", log, "--suspected-flaky", "test_zz_red.T.test_it_is_red")
        rows = [json.loads(line) for line
                in self.read(os.path.join("reports", "flaky.jsonl")).splitlines() if line.strip()]
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["case"] for row in rows}, {"test_zz_red.T.test_it_is_red"})
        self.assertIn("decision.asked", self.kinds(),
                      "達門檻要發 NeedsDecision,不是只寫進一份沒有人讀的檔")

    def test_reaching_the_threshold_opens_a_repair_ticket(self):
        """達門檻**自動開一張修復票**(D-015)。

        以前只發 `decision.asked` 就停在那裡,理由是「自動開票會生出沒人認領的票」。
        實際相反:事件沒有 owner、沒有驗收,而**一則沒人認領的事件比一張沒人認領的
        票更容易被滑過去** —— 票至少每個 session 開場都看得到。

        **變異**:把 `open_flaky_ticket` 拿掉 → 這一條紅。
        """
        log = self.write("gate.log", LOG)
        for index in range(3):
            self.status("done", "--ticket", "7", "--run-id", "r%d" % index, "--rc", "1",
                        "--log", log, "--suspected-flaky", "test_zz_red.T.test_it_is_red")
        made = [row for row in self.tickets_on_disk()
                if row.get("flaky_case") == "test_zz_red.T.test_it_is_red"]
        self.assertEqual(len(made), 1, "同一條案例只開一張,不是每輪一張")
        self.assertEqual(made[0]["role"], "verifier")
        self.assertEqual(made[0]["state"], "Ready")
        self.assertIn("test_zz_red.T.test_it_is_red", made[0]["subject"])
        self.assertTrue(made[0]["acceptance"], "沒有驗收的票跟沒有票一樣")

    def test_a_fourth_flake_does_not_open_a_second_ticket(self):
        """誤判那一半用「同一條案例只開一張」擋住 —— 開著的還在就不再開。"""
        log = self.write("gate.log", LOG)
        for index in range(5):
            self.status("done", "--ticket", "7", "--run-id", "r%d" % index, "--rc", "1",
                        "--log", log, "--suspected-flaky", "test_zz_red.T.test_it_is_red")
        made = [row for row in self.tickets_on_disk()
                if row.get("flaky_case") == "test_zz_red.T.test_it_is_red"]
        self.assertEqual(len(made), 1)

    def test_the_auto_ticket_can_be_turned_off_in_config(self):
        import json as _json
        conf = _json.loads(self.read("board/config.json"))
        conf["flaky_auto_ticket"] = False
        self.write("board/config.json", _json.dumps(conf, ensure_ascii=False))
        log = self.write("gate.log", LOG)
        for index in range(3):
            self.status("done", "--ticket", "7", "--run-id", "r%d" % index, "--rc", "1",
                        "--log", log, "--suspected-flaky", "test_zz_red.T.test_it_is_red")
        self.assertEqual([row for row in self.tickets_on_disk()
                          if row.get("flaky_case")], [])
        self.assertIn("decision.asked", self.kinds(), "關掉開票不等於關掉出聲")


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
        return self.status_of(ticket)

    def install_test_marker(self):
        self.write("tests/test_land.py",
                   "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_ok(self):\n"
                   "        with open(os.environ['AC_TEST_LOG'], 'a', encoding='utf-8') as handle:\n"
                   "            handle.write('test-started\\n')\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "測試啟動標記")

    def assert_preflight_stopped_before_tests(self, done):
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.log), "機械格紅了之後仍啟動測試")
        self.assertNotIn("gate.start", self.kinds())
        self.assertFalse(self.exists("reports"), "機械格紅了之後仍建立測試狀態")

    def test_verify_string_must_be_in_patch_content_not_its_filename(self):
        self.make_ticket(7, allowed_write_paths=["tests/*"], verify_strings=["filename-token"])
        self.write("tests/test_filename-token.py", "import unittest\n\nclass T(unittest.TestCase):\n    pass\n")
        done = self.gate("tests/test_filename-token.py", "--ticket", "7")
        self.assert_preflight_stopped_before_tests(done)
        self.assertIn("filename-token", done.stderr)
        self.assertIn("內容", done.stderr)

    def test_an_unregistered_ticket_tag_stops_before_tests(self):
        self.install_test_marker()
        self.make_ticket(7, verify={"files": [], "tags": ["not-registered"],
                                    "run": "", "notes": ""})
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assert_preflight_stopped_before_tests(done)
        self.assertIn("tags", done.stderr)
        self.assertIn("not-registered", done.stderr)
        self.assertIn("未登記", done.stderr)

    def test_a_patch_outside_allowed_write_paths_stops_before_tests(self):
        self.install_test_marker()
        self.make_ticket(7, allowed_write_paths=["src/*"])
        self.write("scripts/land.sh", self.read("scripts/land.sh") + "\n# outside scope\n")
        done = self.gate("--branch", "--ticket", "7")
        self.assert_preflight_stopped_before_tests(done)
        self.assertIn("allowed_write_paths", done.stderr)
        self.assertIn("scripts/land.sh", done.stderr)

    def test_valid_mechanical_fields_allow_tests_to_start(self):
        self.install_test_marker()
        self.make_ticket(7, allowed_write_paths=["scripts/land.sh"],
                         verify_strings=["gate-preflight-token"],
                         verify={"files": ["verify/example/test_example.py"],
                                 "tags": ["example"], "run": "", "notes": ""})
        self.git("add", "tickets/7.json")
        self.git("commit", "-q", "-m", "沙盒的票面")
        self.write("scripts/land.sh",
                   self.read("scripts/land.sh") + "\n# gate-preflight-token\n")
        done = self.gate("--branch", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.exists(self.log), "合法機械格沒有進入測試")

    def test_without_a_ticket_nothing_is_written(self):
        """沒給票號就完全照舊 —— 一支新功能不該改變舊呼叫者看到的東西。"""
        done = self.gate("scripts/land.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(self.exists("reports"), "沒給票號卻寫了檔")

    def test_the_outer_gate_env_does_not_leak_into_the_sandbox(self):
        """land.sh 跑全套時 `gate.sh --full` 帶著 AC_GATE_TICKET / AC_NO_INBOX /
        AC_GATE_RUN_ID / AC_ROOT;這一組測試就在那一層底下跑。舊沙盒只擋 AC_ROOT,
        其餘漏進去 —— 於是「沒給票號」變成有票、閘門該寫的 inbox 頁不見了,而落地時
        的紅榜指向 test_status / test_inbox / test_land(#7 第 1 輪落地)。"""
        outer = {"AC_GATE_TICKET": "7", "AC_NO_INBOX": "1",
                 "AC_GATE_RUN_ID": "outer-gate", "AC_ROOT": "/nope"}
        with mock.patch.dict(os.environ, outer):
            done = self.gate("scripts/land.sh")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertFalse(self.exists("reports"), "外面的 AC_GATE_TICKET 漏進沙盒")
            done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotEqual(self.load()["run_id"], "outer-gate",
                            "外面的 AC_GATE_RUN_ID 漏進沙盒")
        self.assertIn("inbox.posted", self.kinds(), "外面的 AC_NO_INBOX 漏進沙盒")

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

    def test_five_single_passes_and_a_group_pass_auto_pass_the_flake(self):
        """**變異**:少跑一次單例或不跑原順序整組 → 計數斷言紅。"""
        self.write("tests/test_land.py", FLAKY)
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("flaky=auto", done.stdout)
        self.assertEqual(len(self.read("flaky.count", where=self.home)), 7,
                         "原跑 1 次 + 單跑 5 次 + 原順序整組 1 次")
        data = self.load()
        self.assertEqual(data["rc"], 0)
        self.assertEqual(data["flaky"], "auto")
        self.assertEqual(data["failures"], [])
        self.assertEqual([row["case"] for row in data["auto_flaky"]],
                         ["test_land.T.test_flaky"])
        self.assertIn("flake.auto_pass", self.kinds())
        ledger = [json.loads(line) for line in self.read("reports/flaky.jsonl").splitlines()]
        self.assertEqual(ledger[-1]["classification"], "auto")

    def test_an_order_dependent_failure_is_not_written_off_as_flaky(self):
        """**審查的實測反例**:第一條污染共用狀態、第二條檢查乾淨狀態。整組必紅、
        乾淨程序單跑第二條必綠 —— 舊版正是以「所有紅的案例單跑都綠」為由回傳 0。

        **變異**:把整組重跑那一段拿掉並讓單跑綠回傳 0 → 這一條紅。
        """
        self.write("tests/test_land.py", ORDER_DEPENDENT)
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("單獨重跑 5 次全綠", done.stdout)
        self.assertIn("原順序整組第 1 次仍然紅", done.stdout,
                      "沒有用原順序整組重跑過")
        data = self.load()
        self.assertNotEqual(data["rc"], 0)
        self.assertTrue(data["order_dependent"])
        self.assertEqual(data["order_dependent_cases"],
                         ["test_land.T.test_b_wants_it_clean"])
        self.assertIn("order_dependent", data["note"])
        self.assertTrue(data["extra_logs"], "整組重跑的原始輸出被丟掉了")

    def test_flake_retry_counts_come_from_config(self):
        conf = json.loads(self.read("board/config.json"))
        conf["flake_auto_single_runs"] = 2
        conf["flake_auto_group_runs"] = 1
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False))
        self.write("tests/test_land.py", FLAKY)
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(self.read("flaky.count", where=self.home)), 4)

    def test_the_group_rerun_can_be_turned_off(self):
        """`AC_FLAKE_RERUN_GROUP=0`:整組重跑很貴,關得掉;**關掉也還是紅**。"""
        self.write("tests/test_land.py", ORDER_DEPENDENT)
        done = self.gate("scripts/land.sh", "--ticket", "7", AC_FLAKE_RERUN_GROUP="0")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("不做整組重跑", done.stdout)

    def test_a_case_that_is_red_on_its_own_stays_red(self):
        """flake 重跑不是一張免死金牌:單跑還是紅的就是真紅。"""
        self.write("tests/test_land.py", ALWAYS_RED)
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("標成 suspected_flaky", done.stdout)
        self.assertEqual(self.load()["suspected_flaky"], [])

    def test_nothing_to_run_still_leaves_a_terminal_status(self):
        """**變異**:把 `status_nothing` 從「沒有東西可跑」那條路上拿掉 → 這一條紅。

        「這一輪根本沒有東西跑」是一個結果,不是一次沒跑;而停在 running 的檔與還在
        跑的檔長得一樣。
        """
        done = self.gate("--branch", "--ticket", "7")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        data = self.load()
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["rc"], 2)
        self.assertIn("沒有任何要跑的東西", data["note"])

    def test_the_ticket_tags_really_call_the_regression_runner(self):
        """**變異**:把 `regression ticket` 那一段拿掉 → 這一條紅。

        舊版 `--ticket` 只拿票號寫狀態檔,**票的回歸從來沒有被閘門跑過** ——
        「驗證者交了案例」與「案例真的在守這張票」因此長得一樣(2026-09-21 外部審查)。
        """
        self.make_ticket(7, verify={"files": ["verify/example/test_example.py"],
                                    "tags": ["example"], "run": "", "notes": ""})
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("--tag example", done.stdout, "票的 tags 沒有被拿去叫 verify.py")
        data = self.load()
        self.assertTrue([row for row in data["logs"] if row.endswith("verify.log")],
                        "回歸的原始輸出沒有存檔")

    def test_a_ticket_whose_tags_match_no_case_is_a_gap_not_a_green(self):
        """票說它有 tags,執行器卻一個案例都選不到 —— 那是缺口。"""
        self.make_ticket(7, verify={"files": [], "tags": ["no-such-tag"],
                                    "run": "", "notes": ""})
        self.write(os.path.join("verify", "TAGS.md"),
                   "# 標籤\n- `example` — 沙盒\n- `no-such-tag` — 沙盒\n")
        self.git("add", "verify/TAGS.md")
        self.git("commit", "-q", "-m", "沙盒的標籤登記")
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("一個案例都選不到", done.stdout)

    def test_the_rerun_can_be_turned_off(self):
        self.write("tests/test_land.py", FLAKY)
        done = self.gate("scripts/land.sh", "--ticket", "7", AC_NO_FLAKE_RERUN="1")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("標成 flaky", done.stdout)

    def test_a_ticket_flag_with_no_value_is_a_usage_error(self):
        done = self.gate("--branch", "--ticket")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("要票號", done.stderr)


# --------------------------------------------------------- environment_suspect

# 案例自己宣告的那一行,**真的字面**(#644 / #645 兩個產出點各一句)。冒號後一個半角
# 空格,引擎與理由之間一個半角空格;引擎選填,理由裡有全形破折號、全形括號、`#` 與數字。
SUSPECT_LOCKED = "ENVIRONMENT-SUSPECT: safari 螢幕鎖著(#474/#644)"
SUSPECT_SESSION = "ENVIRONMENT-SUSPECT: safari session 斷了(#645)"

# 三條真的紅 + 真的收尾摘要 —— 「宣告行有沒有被讀到」要在一份**同時有真紅**的 log 上
# 問,不然「兩條來源各自都對」與「其中一條把另一條蓋掉了」分不開。
RED_BLOCK = """\
======================================================================
FAIL: test_%(name)s (test_browsers.TheJourney.test_%(name)s) [engine=safari]
----------------------------------------------------------------------
Traceback (most recent call last):
  File "demo/test_browsers.py", line 1530, in test_%(name)s
    browser.wait_until("location.hash === '#/home'")
AssertionError: 等了 25 秒還不成立:#gate 沒有收起來
"""

# 案例自己 `print` 一行再把自己記成 skip —— `addFailure` 根本不會被叫,所以連紅統計
# 看不到它。兩端之間唯一的線就是 log 上那一行。
DECLARES_THEN_SKIPS = """import unittest


class T(unittest.TestCase):
    def test_it_declares_and_skips(self):
        print("ENVIRONMENT-SUSPECT: firefox 假的宣告")
        self.skipTest("環境不對,這一條記成 skip")
"""


class EnvironmentSuspectHasOneShape(Sandbox):
    """`environment_suspect`:一格、一種形狀、兩條來源(D-019)。

    形狀的唯一真實來源是 `docs/DESIGN-ENV-SUSPECT.md`;這一組問的是**那份文件裡的
    每一句話在磁碟上成不成立**,期望值一個字都不從被測的 `status.py` 算回來。

    最重要的一條是「兩條來源同時有料」:合成寫成「後到的整格覆寫」時,單獨跑任一
    條來源的測試都會綠 —— 那正是這一格上一次長成兩種形狀的原因。
    """

    def status(self, *args, env=None):
        return self.run_py("scripts/status.py", *args, env=env)

    def a_log(self, name="gate.log", declared=(), reds=("journey", "layout", "session")):
        """一份真的 unittest 尾巴 + 幾行宣告。回傳絕對路徑(`--log` 收到的就是它)。"""
        body = ["verify: 選中 demo/test_browsers.py ['browsers']"]
        body += list(declared)
        body += [RED_BLOCK % {"name": one} for one in reds]
        body += ["----------------------------------------------------------------------",
                 "Ran 12 tests in 902.418s", "", "FAILED (failures=%d)" % len(reds), ""]
        return self.write(name, "\n".join(body), where=self.home)

    def an_environment_file(self, name="gate.log.env-suspect.json"):
        """`run-tests` 留下的那一份:**一個 list**,一筆 statistical。"""
        row = {"source": "statistical", "engine": "firefox",
               "why": "localStorage id=<id> empty after <n> seconds",
               "count": 8, "threshold": 8, "log": "gate.log",
               "line": "AssertionError: localStorage id=ab-1 empty after 101 seconds"}
        return self.write(name, json.dumps([row], ensure_ascii=False),
                          where=self.home)

    def suspects_of(self, ticket="7"):
        return self.status_of(ticket)["environment_suspect"]

    # ------------------------------------------------- 驗收 2:log 上的宣告行

    def test_two_declared_lines_become_two_rows_and_the_red_list_is_untouched(self):
        """**變異**:把 `parse_environment_suspects` 整支改成 `return []`
        → 這一條紅(0 != 2)。
        """
        log = self.a_log(declared=(SUSPECT_LOCKED, SUSPECT_SESSION))
        self.status("start", "--ticket", "7", "--kind", "gate")
        done = self.status("done", "--ticket", "7", "--rc", "1", "--log", log)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = self.status_of("7")
        rows = data["environment_suspect"]
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 2, rows)
        for row in rows:
            self.assertEqual(sorted(row), sorted(["source", "engine", "why", "count",
                                                  "threshold", "log", "line"]),
                             "七個鍵要一個都不缺:%r" % sorted(row))
            self.assertEqual(row["source"], "declared")
            self.assertEqual(row["engine"], "safari")
            self.assertEqual(row["count"], 1)
            self.assertIsNone(row["threshold"], "宣告那一條沒有門檻可言,是 null 不是 0")
            self.assertEqual(row["log"], log, "log 那一格要是傳進去的那個路徑")
            self.assertIn(row["why"], row["line"], "why 是那一行切出來的一段")
            self.assertTrue(row["line"].startswith("ENVIRONMENT-SUSPECT:"),
                            "line 是整行原樣,含前綴:%r" % row["line"])
        self.assertEqual([row["why"] for row in rows],
                         ["螢幕鎖著(#474/#644)", "session 斷了(#645)"],
                         "順序照行序")
        # **這一格不准動到 rc 與紅榜**:「這一段被中止」與「有人懷疑環境」是兩件事。
        self.assertEqual(data["rc"], 1)
        self.assertEqual(data["state"], "done")
        self.assertEqual(len(data["failures"]), 3, "三條真的紅要還在")
        self.assertEqual(data["suspected_flaky"], [])

    # --------------------------------------- 驗收 7:非空就發一則 env.suspect

    def test_a_non_empty_cell_emits_the_event_even_when_the_state_is_done(self):
        """**變異**:把事件那一支改回「只有 `state == env_suspect` 才發」
        → 這一條紅(一則事件都沒有)。

        `state` 記的是「這一段被中止」,這一格記的是「這一趟有人懷疑環境」—— 只看
        `state` 的那一版把所有 `declared` 筆漏掉,而漏掉與沒發生長得一樣。
        """
        log = self.a_log(declared=(SUSPECT_LOCKED, SUSPECT_SESSION))
        self.status("start", "--ticket", "7", "--kind", "gate")
        self.status("done", "--ticket", "7", "--rc", "1", "--log", log)
        rows = [row for row in self.events() if row["kind"] == "env.suspect"]
        self.assertEqual(len(rows), 1, self.kinds())
        self.assertEqual(rows[0]["rows"], 2)
        self.assertEqual(rows[0]["sources"], "declared")
        self.assertEqual(rows[0]["engines"], "safari")
        self.assertEqual(rows[0]["why"], "螢幕鎖著(#474/#644)")

    # ------------------------------------------- 驗收 3:兩份 log,一行一筆

    def test_the_same_red_in_two_logs_is_two_rows_not_one(self):
        """同一趟的 `gate.log` 與 `gate.log.rerun` 都會被餵給 `done`,而同一條紅在
        兩份裡各留一行 —— **那是兩次觀測,不是一筆重複的資料**。

        **變異**:在合成的時候去重(照 `why` 或 `line`)→ 這一條紅(2 != 1)。
        """
        first = self.a_log("gate.log", declared=(SUSPECT_LOCKED,))
        again = self.a_log("gate.log.rerun", declared=(SUSPECT_LOCKED,))
        self.status("start", "--ticket", "7", "--kind", "gate")
        self.status("done", "--ticket", "7", "--rc", "1", "--log", first,
                    "--log", again)
        rows = self.suspects_of()
        self.assertEqual(len(rows), 2, rows)
        self.assertEqual(sorted(os.path.basename(row["log"]) for row in rows),
                         ["gate.log", "gate.log.rerun"])

    # ------------------------------------------------- 驗收 4:空值只有一種

    def test_the_cell_exists_and_is_an_empty_list_when_there_is_nothing(self):
        """**變異**:`cmd_start` 不寫這一格 → 這一條紅(KeyError)。

        「這一趟沒有環境嫌疑」與「這一版根本沒在記」是兩件事,而少了這一格,讀的人
        用 `data["environment_suspect"]` 問的時候只會拿到一個 KeyError,用
        `.get()` 問的時候兩件事長得一模一樣。
        """
        self.status("start", "--ticket", "7", "--kind", "gate")
        self.assertEqual(self.suspects_of(), [], "start 寫出來的檔也要有這一格")
        log = self.a_log(declared=())
        self.status("done", "--ticket", "7", "--rc", "1", "--log", log)
        data = self.status_of("7")
        self.assertIn("environment_suspect", data, "這一格要存在")
        self.assertEqual(data["environment_suspect"], [], "空值只有 [] 一種寫法")
        self.assertEqual(len(data["failures"]), 3)
        self.assertNotIn("env.suspect", self.kinds(), "空的不准發事件")

    # -------------------------------- 驗收 5:兩條來源同時有料,一邊都不准少

    def test_both_sources_in_one_run_keep_all_their_rows(self):
        """**本票最重要的一條。** 合成若寫成「後到的整格覆寫」,單獨跑任一條來源的
        測試都是綠的,而同一趟兩條都有料時會靜靜地少掉一邊。

        **變異**:`cmd_done` 只讀環境檔、或只讀 `--log` 的宣告行(任一邊)
        → 這一條紅(3 != 1 或 3 != 2),而驗收 2 與驗收 1 全綠。
        """
        env_file = self.an_environment_file()
        log = self.a_log(declared=(SUSPECT_LOCKED, SUSPECT_SESSION))
        self.status("start", "--ticket", "7", "--kind", "gate")
        self.status("done", "--ticket", "7", "--rc", "1", "--log", log,
                    "--environment-log", env_file)
        rows = self.suspects_of()
        self.assertEqual(len(rows), 3, rows)
        self.assertEqual([row["source"] for row in rows],
                         ["statistical", "declared", "declared"],
                         "statistical 排在前面")
        self.assertEqual({row["source"] for row in rows},
                         {"statistical", "declared"})
        self.assertEqual({row["engine"] for row in rows}, {"firefox", "safari"},
                         "兩邊的引擎都要在")
        self.assertEqual(rows[0]["count"], 8)
        self.assertEqual(rows[0]["threshold"], 8)

    # ------------------- 驗收 6:案例自己 print 的那一行要進 --log 那份檔

    def test_a_case_that_prints_its_own_declaration_lands_in_the_log(self):
        """**變異**:拿掉 `cmd_run_tests` 的 `redirect_stdout` → 這一條紅
        (log 裡沒有那一行,接著 `done` 得 0 筆)。

        **這是本票最容易漏的一條**:`TextTestRunner(stream=…)` 只收 runner 自己的
        輸出,案例 `print()` 的那一行會流到呼叫者的 stdout。人看終端機時它在那裡,
        所以「有印出來」與「進了 log」長得一樣 —— 而 `done --log` 只讀那份檔。
        """
        self.write(os.path.join("tests", "test_declared.py"), DECLARES_THEN_SKIPS)
        log = os.path.join(self.home, "run.log")
        suspect = os.path.join(self.home, "run.log.env-suspect.json")
        done = self.status("run-tests", "--root", self.repo, "--log", log,
                           "--suspect-file", suspect, "--mode", "names",
                           "test_declared")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(log, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("ENVIRONMENT-SUSPECT: firefox 假的宣告", text,
                      "案例印的那一行不在 log 裡:%r" % text[-400:])
        # **在 log 裡**還不夠,要**在行首**:解析只認行首,而 `verbosity=2` 的 runner
        # 停在 `test_x (…) ... ` 半行上就去跑案例,不補換行的話那一行會黏在它後面
        # (實測:`… ... ENVIRONMENT-SUSPECT: firefox 假的宣告`)。那時 `assertIn`
        # 照樣綠、`done` 照樣讀得到(舊的 `find` 切法),但 `line` 那一格會多帶一截
        # 案例名 —— 「有那一行」與「那一行是原樣的」長得一樣。
        self.assertIn("\nENVIRONMENT-SUSPECT: firefox 假的宣告\n", text,
                      "那一行沒有落在行首:%r" % text[:300])
        with open(suspect, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), [],
                             "沒有連紅統計時那一份檔是 [],不是空字串")
        self.status("start", "--ticket", "7", "--kind", "gate")
        self.status("done", "--ticket", "7", "--rc", "0", "--log", log)
        rows = self.suspects_of()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source"], "declared")
        self.assertEqual(rows[0]["engine"], "firefox")
        self.assertEqual(rows[0]["why"], "假的宣告")
        self.assertEqual(rows[0]["line"], "ENVIRONMENT-SUSPECT: firefox 假的宣告",
                         "line 要是案例印的那一行原樣,不准前面黏一截案例名")

    # ------------------------------------------- 行格式的三種變異(切法)

    def test_the_three_shapes_of_a_declaration_line(self):
        """期望值從那一行的切法規則來,不從程式現在吐什麼來。

        **變異**:拿掉「引擎後面要真的還有話」那一條(改成一律切第一個字)
        → 第二種紅。
        """
        cases = (
            # 中文開頭:認不出引擎,整段都是理由 —— 硬切會把半句話當成引擎名。
            ("ENVIRONMENT-SUSPECT: 螢幕鎖著 ——(#474)", "", "螢幕鎖著 ——(#474)"),
            # 只有一個字:後面沒有話,所以那一個字**是理由不是引擎**。
            ("ENVIRONMENT-SUSPECT: safari", "", "safari"),
            # 前導空白吃掉(縮排過的輸出照樣算),引擎與理由照切。
            ("    ENVIRONMENT-SUSPECT: safari session 斷了(#645)",
             "safari", "session 斷了(#645)"),
        )
        for line, engine, why in cases:
            with self.subTest(line=line):
                log = self.a_log("one.log", declared=(line,), reds=())
                rows = status_module.parse_environment_suspects(log)
                self.assertEqual(len(rows), 1, rows)
                self.assertEqual(rows[0]["engine"], engine)
                self.assertEqual(rows[0]["why"], why)
                self.assertEqual(rows[0]["line"], line.strip(), "line 是整行原樣")
                os.remove(log)

    def test_only_a_line_that_starts_with_the_prefix_is_a_declaration(self):
        """**印出那一行**與**在講那一行**只有位置分得開。

        #23 第 1 輪實測踩到的就是這個:`unittest -v` 把案例 docstring 的第一行印進
        gate.log,而那一行裡逐字寫著前綴,於是被讀成一筆
        `engine="firefox"` / `why="假的 ... ok"` 的宣告 —— 「這一格非空就不自動派」
        照著把那一輪的 auto-fix 擋掉了(狀態檔 `20260923-123307-89768`)。

        **變異**:把 `line.strip().startswith(...)` 改回 `line.find(...) >= 0`
        → 第二組每一行都變成一筆,這一條紅。
        """
        prefix = status_module.SUSPECT_PREFIX
        taken = ("%s safari 螢幕鎖著(#474/#644)" % prefix,
                 "  \t%s safari session 斷了(#645)" % prefix)
        # 三種「在講那一行」:verbose 印出來的 docstring、註解、把它包在別的輸出裡。
        ignored = ('驗收 6:run-tests 跑一條會 `print("%s firefox 假的 ... ok' % prefix,
                   "# 案例自己宣告環境紅的那一行:%s safari 螢幕鎖著" % prefix,
                   "[chrome] %s safari session 斷了(#645)" % prefix)
        for line in taken:
            with self.subTest(taken=line):
                log = self.a_log("taken.log", declared=(line,), reds=())
                self.assertEqual(len(status_module.parse_environment_suspects(log)), 1)
                os.remove(log)
        for line in ignored:
            with self.subTest(ignored=line):
                log = self.a_log("ignored.log", declared=(line,), reds=())
                self.assertEqual(status_module.parse_environment_suspects(log), [],
                                 "中段出現的前綴不是一筆宣告")
                os.remove(log)

    def test_no_test_docstring_carries_the_prefix_verbatim(self):
        """反方向的守衛:`tests/` 底下的 docstring 不准逐字寫出那個前綴。

        上面那一條擋的是解析,這一條擋的是**來源** —— 兩道都要,因為 `-v` 印的不只有
        docstring(以後有人在案例裡 `print` 一段說明也會進 log),而位置這條線只在
        「說明文字不在行首」的時候才成立。要提到它就用 `status.SUSPECT_PREFIX` 拼,
        別寫死。

        **變異**:把哪一支測試的 docstring 改回逐字寫前綴 → 這一條紅並指名那個檔。
        """
        prefix = status_module.SUSPECT_PREFIX
        where = os.path.dirname(os.path.abspath(__file__))
        bad = []
        for name in sorted(os.listdir(where)):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            with open(os.path.join(where, name), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                    continue
                if prefix in (ast.get_docstring(node) or ""):
                    bad.append("%s:%s" % (name, getattr(node, "name", "<module>")))
        self.assertEqual(bad, [], "docstring 逐字寫了前綴,`-v` 會把它印成一筆宣告")

    # ---------------------------------- 驗收 9:讀端正規化吃掉舊檔的五種形狀

    def test_the_reader_normalises_every_old_way_of_saying_nothing(self):
        """**變異**:拿掉舊 dict 那一支分支 → 第三份得 `[]`(而它明明有料)。

        舊檔不改寫(D-014:一輪一目錄不覆寫),所以相容性全靠這一支。
        """
        shapes = {
            "missing": {"state": "done", "rc": 1},
            "empty_dict": {"state": "done", "rc": 1, "environment_suspect": {}},
            "old_dict": {"state": "done", "rc": 1,
                         "environment_suspect": {"engine": "safari", "count": 8}},
        }
        loaded = {}
        for name, body in shapes.items():
            path = self.write(os.path.join("reports", "t9", name, "status.json"),
                              json.dumps(body, ensure_ascii=False))
            with open(path, encoding="utf-8") as handle:
                loaded[name] = json.load(handle)
        self.assertEqual(status_module.environment_suspects(loaded["missing"]), [])
        self.assertEqual(status_module.environment_suspects(loaded["empty_dict"]), [])
        rows = status_module.environment_suspects(loaded["old_dict"])
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source"], "statistical",
                         "舊的那一格裝的就是連紅統計")
        self.assertEqual(rows[0]["engine"], "safari")
        self.assertEqual(rows[0]["count"], 8)
        self.assertIsNone(rows[0]["why"],
                          "舊形狀沒有 message_shape:那一格是 None(那一版沒記理由),"
                          "不是 \"\"(理由是一句空話)")
        self.assertEqual(rows[0]["line"], "", "舊形狀沒留原始那一行")
        self.assertEqual(rows[0]["log"], "", "舊形狀沒留 log")
        self.assertEqual(sorted(rows[0]), sorted(status_module.SUSPECT_KEYS),
                         "正規化出來的那一筆也要七鍵齊全")
        # `None` 與「這一格是一個字串」也都是「沒有」,不是一個例外。
        self.assertEqual(status_module.environment_suspects(
            {"environment_suspect": None}), [])
        self.assertEqual(status_module.environment_suspects({}), [])
        self.assertEqual(status_module.environment_suspects(None), [])

    # ------------------------------------------- suspects 子指令:三種答案

    def test_the_suspects_subcommand_separates_zero_from_cannot_answer(self):
        """`gate.sh` 判「要不要自動派」問的是這一支,**不是 grep `done` 的輸出** ——
        一份 grep 不到的輸出與一趟沒有嫌疑長得一樣(DISPATCH-TEMPLATE §5.5)。

        **變異**:讓「沒有狀態檔」那一支也印 `0` → 這一條紅(它會印出 0 而且 rc=0)。
        """
        blind = self.status("suspects", "--ticket", "7", "--count")
        self.assertEqual(blind.returncode, 2, blind.stdout + blind.stderr)
        self.assertEqual(blind.stdout, "", "問不出來的時候一個字都不准印")
        self.status("start", "--ticket", "7", "--kind", "gate")
        self.status("done", "--ticket", "7", "--rc", "1",
                    "--log", self.a_log(declared=()))
        zero = self.status("suspects", "--ticket", "7", "--count")
        self.assertEqual(zero.returncode, 0, zero.stdout + zero.stderr)
        self.assertEqual(zero.stdout.strip(), "0")
        self.status("start", "--ticket", "8", "--kind", "gate")
        self.status("done", "--ticket", "8", "--rc", "1", "--log",
                    self.a_log("eight.log", declared=(SUSPECT_LOCKED, SUSPECT_SESSION)))
        two = self.status("suspects", "--ticket", "8", "--count")
        self.assertEqual(two.stdout.strip(), "2")
        listed = self.status("suspects", "--ticket", "8")
        self.assertIn("rows=2", listed.stdout)
        self.assertIn("declared", listed.stdout)
        self.assertIn("safari", listed.stdout)


class TheShapeHasExactlyOneSourceOfTruth(unittest.TestCase):
    """`docs/DESIGN-ENV-SUSPECT.md` 是 `environment_suspect` 形狀的唯一真實來源。

    上一次這一格在兩個 repo 長成同名不同形,就是因為**沒有一份文件是它的來源** ——
    兩邊各自從自己的 code 讀出形狀,而兩份 code 都是對的。所以這裡釘兩件事:
    ① 文件裡那一塊的鍵名與 `status.py` 的 `SUSPECT_KEYS` **逐字相同**(改文件而不改
    code、或改 code 而不改文件,都在這裡紅);② 碰這一格的每一支都指得到那份文件。

    **變異**:把文件那一塊的任何一個鍵名改掉(code 不動)→ 第一條紅。
    """

    doc = os.path.join(ROOT, "docs", "DESIGN-ENV-SUSPECT.md")
    touches = ("scripts/status.py", "scripts/gate.sh", "scripts/auto-fix.sh",
               "scripts/metrics.py", "board/board.py")

    def body(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_the_documented_keys_are_the_keys_the_code_writes(self):
        self.assertTrue(os.path.exists(self.doc),
                        "形狀的來源不在 —— 沒有它,實作者就沒有規格")
        with open(self.doc, encoding="utf-8") as handle:
            text = handle.read()
        # 第一塊 fenced block 就是那七個鍵的定義。
        block = text.split("```")[1]
        named = [key for key in re.findall(r'"([a-z_]+)":', block)
                 if key != "environment_suspect"]
        self.assertEqual(named, list(status_module.SUSPECT_KEYS),
                         "文件與 code 的鍵名對不上:%r" % named)

    def test_every_place_that_touches_the_cell_points_at_that_document(self):
        for rel in self.touches:
            with self.subTest(file=rel):
                self.assertIn("DESIGN-ENV-SUSPECT", self.body(rel),
                              "%s 碰這一格卻沒有指回形狀的來源" % rel)

    def test_no_second_document_defines_the_shape_without_citing_it(self):
        """第二份規格與第一份長得一樣 —— 差別只在它會先過期。

        所以列出那七個鍵的文件,**要嘛就是那一份,要嘛要指名那一份**。
        """
        strays = []
        for name in sorted(os.listdir(os.path.join(ROOT, "docs"))):
            if not name.endswith(".md"):
                continue
            text = self.body(os.path.join("docs", name))
            lists_all = all(re.search(r"\b%s\b" % key, text)
                            for key in status_module.SUSPECT_KEYS)
            if not lists_all or name == "DESIGN-ENV-SUSPECT.md":
                continue
            if "DESIGN-ENV-SUSPECT" not in text:
                strays.append(name)
        self.assertEqual(strays, [], "這幾份自己定義了一套形狀:%s" % strays)


if __name__ == "__main__":
    unittest.main()
