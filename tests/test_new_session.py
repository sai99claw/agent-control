"""`scripts/new-session.sh`:開 session 的固定動作。

**一張要靠人記得照做的清單,漏掉一項時長得跟做完了一樣。** 所以這一組釘的是「每一段
都真的跑過」以及「事件真的發出去了」。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402


class NewSession(Sandbox):

    def start(self, *args):
        return self.run_sh("scripts/new-session.sh", *args)

    def test_it_runs_every_step_and_puts_the_session_on_the_board(self):
        self.make_ticket(1, state="Ready", subject="開著的那一張")
        done = self.start("worker", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for step in ("站在哪個版本", "最近發生的事", "開著的票",
                     "記憶有沒有超過上限", "接下來要讀的"):
            self.assertIn(step, done.stdout, "少跑了一段")
        self.assertIn("開著的那一張", done.stdout)
        rows = [row for row in self.events() if row["kind"] == "session.start"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["role"], "worker")
        self.assertEqual(rows[0]["model"], "opus")

    def test_the_reading_list_points_at_this_model_s_own_memory(self):
        """同模型跨 session 共享:Opus 的下一個 session 應該知道 Opus 上次犯過什麼。"""
        done = self.start("worker", "opus")
        self.assertIn("memory/model/opus.md", done.stdout)
        self.assertIn("docs/HANDOFF.md", done.stdout)

    def test_a_worker_is_pointed_at_the_dispatch_rules_and_the_main_line_is_not(self):
        worker = self.start("worker", "opus")
        self.assertIn("DISPATCH-TEMPLATE", worker.stdout)
        main = self.start("main", "fable")
        self.assertIn("收件匣", main.stdout)
        self.assertIn("心跳", main.stdout)

    def test_the_main_line_also_gets_the_inbox_and_the_heartbeat(self):
        self.make_ticket(1, state="NeedsDecision", subject="等你裁決的")
        done = self.start("main", "fable")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("等你裁決的", done.stdout)
        self.assertIn("沒有到期的租約", done.stdout)

    def test_the_opening_prints_the_terminal_state_inbox(self):
        """主線開場讀一次收件匣,之後只在被通知時讀 —— **不輪詢**(D-015)。"""
        self.make_ticket(1)
        self.run_py("scripts/inbox.py", "post", "--ticket", "1", "--run-id", "r1",
                    "--kind", "gate", "--state", "閘門紅", "--what", "看紅榜",
                    "--where", "reports/t1/r1/status.json")
        done = self.start("main", "fable")
        self.assertIn("閘門紅", done.stdout, "跑完的事沒有在開場出現 = 沒有人會去看")
        self.assertIn("reports/inbox/", done.stdout)

    def test_the_opening_does_not_leak_a_shell_error(self):
        """🩸 反引號在雙引號裡是**命令替換**:那一行在乾淨 clone 裡吐出
        `command substitution: syntax error`,而畫面上其他每一段都正常 ——
        一個 session 的第一印象因此是「這份東西壞的」。

        **變異**:把那一行的單引號改回雙引號加反引號 → 這一條紅。
        """
        done = self.start("main", "fable")
        for noise in ("syntax error", "command not found", "command substitution"):
            self.assertNotIn(noise, done.stdout + done.stderr, "開場吐了 shell 的錯")

    def test_memory_over_cap_does_not_stop_the_session(self):
        """退出碼 1 只是讓這個 session 知道自己的記憶該整理了 —— **不停工**
        (docs/MEMORY.md 第 1 點)。

        **變異**:把那一行的 `|| echo …` 拿掉(讓 `set -e` 式的失敗傳出去)
        → 這一條紅。
        """
        self.write("memory/model/opus.md", "坑" * 2500)
        done = self.start("worker", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("不停工", done.stdout)
        self.assertIn("session.start", self.kinds())

    def test_missing_arguments_is_a_usage_error_and_emits_nothing(self):
        done = self.start("main")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.events(), [], "沒開成的 session 不該留下一筆 start")


if __name__ == "__main__":
    unittest.main()
