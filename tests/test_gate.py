"""`scripts/gate.sh`:三層介面,以及「對不到任何模組」那一條。

#511(前一個專案,2026-09-10,第三次):對照表漏了一格時,舊版印一行「沒有對到任何
測試模組」然後**退出碼 0** —— 印一行、看起來像跑完了。三次缺口都是別張票順手撞到才
發現的。所以這一組釘的重點不是那幾格對照表,是**缺口會不會出聲**。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

PASSING = """import os
import unittest


class T(unittest.TestCase):
    def test_it_ran(self):
        with open(os.environ["AC_TEST_LOG"], "a", encoding="utf-8") as handle:
            handle.write("%s\\n")
"""
FAILING = """import unittest


class T(unittest.TestCase):
    def test_it_is_red(self):
        self.assertEqual(1, 2, "假的紅")
"""
# #620 的形狀:同一個引擎、12 條 subTest 倒在同一句,只差流水號與 id。
ENVIRONMENT_WAVE = """import os
import unittest


class T(unittest.TestCase):
    def test_wave(self):
        for index in range(12):
            with self.subTest(engine="safari", id=index):
                with open(os.environ["AC_TEST_LOG"], "a", encoding="utf-8") as handle:
                    handle.write("safari-%d\\n" % index)
                self.assertEqual("home", "", "localStorage id=%d empty after %d seconds"
                                 % (index, index + 100))
"""
# 同一引擎但三句不同的紅:那是三個 bug,不是一次環境故障 —— 要照常跑完。
DIFFERENT_FAILURES = """import os
import unittest


class T(unittest.TestCase):
    def test_wave(self):
        for index, message in enumerate(("storage empty", "port occupied", "disk full")):
            with self.subTest(engine="safari", id=index):
                with open(os.environ["AC_TEST_LOG"], "a", encoding="utf-8") as handle:
                    handle.write("different-%d\\n" % index)
                self.fail(message)
"""


class GateSh(Sandbox):

    def setUp(self):
        super().setUp()
        # 沙盒裡放一組與真對照表同名的替身模組:跑起來只寫一行標記。
        # 換的是**代價**不是語意 —— 這一組問的是「哪幾支被挑到」,不是那幾支測什麼。
        for name in ("test_ticket", "test_event", "test_memory",
                     "test_no_project_names", "test_check_stale", "test_land"):
            self.write(os.path.join("tests", "%s.py" % name), PASSING % name)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的替身測試模組")

    def gate(self, *args):
        return self.run_sh("scripts/gate.sh", *args)

    def ran(self):
        if not os.path.exists(self.log):
            return []
        return [line.strip() for line in self.read("calls.log", where=self.home).splitlines()
                if line.strip()]

    # ------------------------------------------------------ 對照表挑得對不對

    def test_a_mapped_file_runs_the_modules_that_guard_it(self):
        done = self.gate("code-map/check-stale.py")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_check_stale"])

    def test_a_markdown_file_maps_to_the_guard_that_really_reads_it(self):
        """文件那一格不是硬塞的:那條「不准出現專案名 / 絕對路徑」的守衛掃的就是
        整個 repo,所以它真的讀 `*.md`。"""
        done = self.gate("docs/WORKFLOW.md")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_no_project_names"])

    def test_base_adds_the_contract_layer(self):
        done = self.gate("--base")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(self.ran()),
                         ["test_event", "test_memory", "test_no_project_names",
                          "test_ticket"])

    def test_branch_picks_up_what_this_branch_changed(self):
        self.write("scripts/land.sh", self.read("scripts/land.sh") + "\n# 動一下\n")
        done = self.gate("--branch")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_land"])

    # ----------------------------------------------- 對不到任何模組:要出聲

    def test_a_file_nobody_guards_is_named_and_the_exit_code_is_not_zero(self):
        """**變異**:把 `if [ -n "$unmapped" ]` 那一段拿掉(退回印一行、退出 0)
        → 這一條紅。
        """
        done = self.gate("assets/logo.png")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("assets/logo.png", done.stdout, "沒有指名是哪個檔")
        self.assertIn("對不到任何測試模組", done.stdout)
        self.assertEqual(self.ran(), [], "什麼都沒跑")

    def test_a_mixed_change_runs_what_it_can_and_still_refuses(self):
        """混合的情況是判斷題:**跑掉的測試是真的證據,不該丟**;而那幾個沒人守的
        檔要當場處理,所以退出碼仍然非零。"""
        done = self.gate("code-map/check-stale.py", "assets/logo.png")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_check_stale"], "對得到的那一支要照跑")
        self.assertIn("assets/logo.png", done.stdout)

    # ------------------------------------------------------------- 介面本身

    def test_full_runs_the_whole_discover_and_is_green(self):
        done = self.gate("--full")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Ran ", done.stdout)
        self.assertIn("OK", done.stdout)

    def test_full_is_red_when_a_test_is_red(self):
        """判綠先寫檔再讀退出碼,不用 `cmd | tail` —— 管線的退出碼是右邊那一支的。

        **變異**:把 `--full` 那一段改成 `python3 -m unittest … | tail -5` 並回
        `$?` → 這一條紅(它會判成綠)。
        """
        self.write("tests/test_zz_red.py", FAILING)
        done = self.gate("--full")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("FAILED", done.stdout)
        self.assertIn("紅了", done.stdout)

    # ------------------------------------------------- 環境壞了要當場停(#7)

    def test_same_safari_failure_shape_stops_the_segment_and_alerts(self):
        """**變異**:把 `EnvironmentResult._observe` 裡的 `self.failfast = True`
        拿掉 → 這一條紅(12 != 8)。

        理由:`shouldStop` 停的是下一條測試方法,而這 12 條 subTest 跑在同一個方法
        裡面 —— 只設它,那一段還是會整組跑完,而「跑完再說」就是這張票要擋的事。"""
        self.write("tests/test_env_wave.py", ENVIRONMENT_WAVE)
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.gate("tests/test_env_wave.py", "--ticket", "7", "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len([row for row in self.ran() if row.startswith("safari-")]), 8,
                         "預設門檻 8 到了就要停,不准把 12 條跑完")
        data = self.status_of("7")
        self.assertEqual(data["state"], "env_suspect")
        self.assertEqual(data["environment_suspect"]["engine"], "safari")
        self.assertEqual(data["environment_suspect"]["count"], 8)
        self.assertIn("env.suspect", self.kinds())
        pages = [name for name in os.listdir(os.path.join(self.repo, "reports", "inbox"))
                 if name.endswith(".md")]
        page = self.read(os.path.join("reports", "inbox", pages[0]))
        self.assertIn("Safari --automation", page)
        self.assertIn("引擎:safari", page)
        self.assertIn("同形訊息:", page)

    def test_the_aborted_segment_does_not_pay_for_flake_reruns(self):
        """環境 fail-fast 在 flake 判定**之前**:被中止的紅榜不完整,而在壞掉的環境
        裡每條紅例單跑 N 次只會把浪費加倍。所以這一段不准印出 flake 那幾句,狀態檔
        也不准留下 flaky 的判定。"""
        self.write("tests/test_env_wave.py", ENVIRONMENT_WAVE)
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.gate("tests/test_env_wave.py", "--ticket", "7", "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("單獨重跑", done.stdout)
        self.assertNotIn("整組重跑", done.stdout)
        data = self.status_of("7")
        self.assertEqual(data["suspected_flaky"], [])
        self.assertEqual(data["auto_flaky"], [])
        self.assertEqual(data["flaky"], "")
        self.assertNotIn("flake.auto_pass", self.kinds())

    def test_the_threshold_comes_from_board_config(self):
        """**變異**:把門檻改成讀死的常數、不讀 `board/config.json` → 這一條紅
        (停在 8 而不是 3)。N 住在設定檔裡才調得動:不同專案的環境壞法不一樣。"""
        self.write("tests/test_env_wave.py", ENVIRONMENT_WAVE)
        config = json.loads(self.read("board/config.json"))
        config["environment_fail_fast_threshold"] = 3
        self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        self.git("add", "board/config.json")
        self.git("commit", "-q", "-m", "沙盒的環境門檻")
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.gate("tests/test_env_wave.py", "--ticket", "7", "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len([row for row in self.ran() if row.startswith("safari-")]), 3,
                         "設定檔說 3 就停在 3")
        data = self.status_of("7")
        self.assertEqual(data["state"], "env_suspect")
        self.assertEqual(data["environment_suspect"]["threshold"], 3)

    def test_different_messages_do_not_trigger_environment_fail_fast(self):
        """**變異**:把形狀的鍵改成只看引擎、不看訊息 → 這一條紅(state 變
        `env_suspect`)。三條不同訊息是三個 bug,把它們算成一次環境故障等於**把真的
        紅榜丟掉**。"""
        self.write("tests/test_env_wave.py", DIFFERENT_FAILURES)
        config = json.loads(self.read("board/config.json"))
        config["environment_fail_fast_threshold"] = 3
        self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        self.git("add", "board/config.json")
        self.git("commit", "-q", "-m", "沙盒的環境門檻")
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.run_sh("scripts/gate.sh", "tests/test_env_wave.py", "--ticket", "7",
                           "--no-auto-fix", env=self.env(AC_NO_FLAKE_RERUN="1"))
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len([row for row in self.ran() if row.startswith("different-")]), 3,
                         "三條不同訊息要照常跑完")
        self.assertEqual(self.status_of("7")["state"], "done")
        self.assertNotIn("env.suspect", self.kinds())

    def test_an_unknown_flag_is_refused(self):
        done = self.gate("--quick")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("不認得", done.stderr)

    def test_no_arguments_at_all_is_a_usage_error(self):
        done = self.gate()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)


class GateExample(unittest.TestCase):
    """範例那一份要**自己的語法是對的** —— 一份 `sh -n` 過不了的範本,照抄的人第一
    件事是修語法,不是讀理由。"""

    def test_the_example_parses(self):
        import subprocess
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        done = subprocess.run(["sh", "-n", os.path.join(here, "scripts", "gate.example.sh")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_example_keeps_the_three_layer_interface(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "scripts", "gate.example.sh"), encoding="utf-8") as handle:
            text = handle.read()
        for flag in ("--branch", "--base", "--full"):
            self.assertIn(flag, text)
        self.assertIn("對不到任何測試模組", text)

    def test_the_example_accepts_the_ticket_flag(self):
        """**變異**:把範本的 `--ticket` 那一格拿掉 → 這一條紅。

        2026-09-21 外部審查:範本不接受 `--ticket`,照抄的人拿不到狀態檔與票的回歸,
        而他的 gate 收到 `--ticket 7` 只會回「不認得」+ 退出碼 2 —— 而 2 與「紅了」
        在呼叫者眼裡長得很像。
        """
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "scripts", "gate.example.sh"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("--ticket", text)
        self.assertIn("status.py", text, "範本要示範狀態檔怎麼寫")
        self.assertIn("verify.py", text, "範本要示範票的回歸怎麼跑")


if __name__ == "__main__":
    unittest.main()
