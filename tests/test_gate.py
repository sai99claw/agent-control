"""`scripts/gate.sh`:三層介面,以及「對不到任何模組」那一條。

#511(前一個專案,2026-09-10,第三次):對照表漏了一格時,舊版印一行「沒有對到任何
測試模組」然後**退出碼 0** —— 印一行、看起來像跑完了。三次缺口都是別張票順手撞到才
發現的。所以這一組釘的重點不是那幾格對照表,是**缺口會不會出聲**。
"""

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


if __name__ == "__main__":
    unittest.main()
