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

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

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
        open(mark, "a", encoding="utf-8").close()
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

    def test_a_case_that_passes_on_its_own_is_only_suspected_not_green(self):
        """單跑綠的那一條只降級成 `suspected_flaky`,**rc 一個位元都不動**。

        **變異**:把 `flake_rerun` 改回「全 flaky 就 `return 0`」→ 這一條紅。
        理由就是下一條那個反例:順序依賴的紅單跑一定綠,而判它綠等於每次都放它過去。
        """
        self.write("tests/test_land.py", FLAKY)
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0,
                            "單跑綠不准把這一輪判綠\n" + done.stdout + done.stderr)
        self.assertIn("標成 suspected_flaky", done.stdout)
        data = self.load()
        self.assertNotEqual(data["rc"], 0)
        self.assertEqual([row["case"] for row in data["failures"]],
                         ["test_land.T.test_flaky"], "原始失敗要留在紅榜")
        self.assertEqual([row["case"] for row in data["suspected_flaky"]],
                         ["test_land.T.test_flaky"])

    def test_an_order_dependent_failure_is_not_written_off_as_flaky(self):
        """**審查的實測反例**:第一條污染共用狀態、第二條檢查乾淨狀態。整組必紅、
        乾淨程序單跑第二條必綠 —— 舊版正是以「所有紅的案例單跑都綠」為由回傳 0。

        **變異**:把整組重跑那一段拿掉並讓單跑綠回傳 0 → 這一條紅。
        """
        self.write("tests/test_land.py", ORDER_DEPENDENT)
        done = self.gate("scripts/land.sh", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("單獨重跑是綠的", done.stdout)
        self.assertIn("整組重跑仍然紅", done.stdout, "沒有用原順序整組重跑過")
        data = self.load()
        self.assertNotEqual(data["rc"], 0)
        self.assertIn("真紅", data["note"])
        self.assertTrue(data["extra_logs"], "整組重跑的原始輸出被丟掉了")

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


if __name__ == "__main__":
    unittest.main()
