"""`scripts/session-hook.sh` + `.claude/settings.json`:主線開場那一頁不靠自律(#39)。

今天以前,開場那一頁要主線自己記得跑 `new-session.sh`,而**整段被跳過的那一個 session
與跑過的長得一樣**。hook 讓它在第一個 prompt 之前就在上下文裡;這一組釘的是:
主線那一頁真的印了、事件只在 startup / clear 發、副本裡一個位元組都不印。

期望值的來源都在被測腳本之外:matcher 的值集合抄自 hooks 文件
(code.claude.com/docs/en/hooks,SessionStart:startup|resume|clear|compact|fork)、
`routing.main` / `worktree_dir` 是這裡自己寫進沙盒設定的、事件數是直接數事件檔的行數。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, SCRIPTS, TIMEOUT, Sandbox  # noqa: E402

SETTINGS = os.path.join(ROOT, ".claude", "settings.json")
SOURCES = {"startup", "resume", "clear", "compact", "fork"}
LIMIT = 16384
WORKTREE = "../agent-control-wt"


class A1HookShape(unittest.TestCase):

    def test_a1_settings_call_the_session_hook(self):
        """**變異 M4**:matcher 多一個 `startupp` → 這一條紅。"""
        with open(SETTINGS, encoding="utf-8") as handle:
            conf = json.load(handle)
        entry = conf["hooks"]["SessionStart"][0]
        hook = entry["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertIn("scripts/session-hook.sh", hook["command"])
        self.assertTrue(hook.get("timeout"), "timeout 要有值")
        if "matcher" in entry:
            extra = set(entry["matcher"].split("|")) - SOURCES
            self.assertEqual(extra, set(),
                             "matcher 只能由 SessionStart 的 source 值組成;多出來的那一個"
                             "永遠對不到,而它讀起來跟對得到的一樣")


class HookSandbox(Sandbox):
    config_extra = {"routing": {"main": "fable"}, "worktree_dir": WORKTREE}

    def setUp(self):
        super().setUp()
        target = os.path.join(self.repo, "scripts", "session-hook.sh")
        shutil.copy(os.path.join(SCRIPTS, "session-hook.sh"), target)
        os.chmod(target, 0o755)

    def hook(self, source="startup", cwd=None, session_id=None, **env):
        cwd = cwd or self.repo
        payload = {"source": source, "cwd": cwd, "hook_event_name": "SessionStart"}
        if session_id is not None:
            payload["session_id"] = session_id
        payload = json.dumps(payload)
        return subprocess.run(
            ["sh", os.path.join(self.repo, "scripts", "session-hook.sh")],
            cwd=cwd, env=self.env(**env), input=payload,
            capture_output=True, text=True, timeout=TIMEOUT)

    def event_lines(self):
        """直接數事件檔的行數 —— 不從被測腳本算。"""
        path = os.path.join(self.repo, "board", "events.jsonl")
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def starts(self):
        return [row for row in self.events() if row["kind"] == "session.start"]


class A2TheMainLinePage(HookSandbox):

    def test_a2_startup_prints_the_page_and_emits_one_start(self):
        before = self.event_lines()
        done = self.hook("startup")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for piece in ("站在哪個版本", "開著的票", "收件匣", "心跳",
                      "memory/role/main.md", "memory/model/fable.md"):
            self.assertIn(piece, done.stdout, "那一頁少了「%s」" % piece)
        self.assertEqual(self.event_lines(), before + 1)
        rows = self.starts()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["role"], rows[0]["model"]), ("main", "fable"))

    def test_a2_the_hook_s_session_id_goes_on_the_start(self):
        """#45 B3:hook JSON 的 `session_id` 轉給 new-session.sh,start 那一列帶它 ——
        之後的 session.end 帶同一個 id,heartbeat 才配得起來。"""
        done = self.hook("startup", session_id="abc")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([row.get("session") for row in self.starts()], ["abc"])
        self.assertIn("--session abc", done.stdout)


class A3ACopyStaysSilent(HookSandbox):
    """三條守衛各一案,各自只讓一條成立 —— 揉成一案的話,少實作兩條也綠。"""

    def assert_silent(self, done, before):
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "", "副本裡一個位元組都不該印")
        self.assertEqual(done.stderr, "")
        self.assertEqual(self.event_lines(), before, "副本裡一筆事件都不該發")

    def test_a3_a_cwd_under_the_worktree_dir(self):
        """**變異 M1**:拿掉 worktree_dir 那一條守衛 → 這一條紅。"""
        where = os.path.join(self.repo, WORKTREE, "t9", "work")
        os.makedirs(where)
        before = self.event_lines()
        self.assert_silent(self.hook("startup", cwd=where), before)

    def test_a3_the_off_switch(self):
        before = self.event_lines()
        self.assert_silent(self.hook("startup", AC_SESSION_HOOK="0"), before)

    def test_a3_a_role_that_is_not_main(self):
        before = self.event_lines()
        self.assert_silent(self.hook("startup", AC_ROLE="worker"), before)


class A4ReprintWithoutAnEvent(HookSandbox):
    """**變異 M2**:拿掉 `--no-event` 那一格 → compact / resume 兩條紅。"""

    def reprint(self, source):
        before = self.event_lines()
        done = self.hook(source)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("開著的票", done.stdout)
        self.assertIn("memory/role/main.md", done.stdout)
        self.assertEqual(self.event_lines(), before,
                         "%s 不是新的 session;多一筆 start,heartbeat 會當它死在半路" % source)

    def test_a4_compact_reprints_without_an_event(self):
        self.reprint("compact")

    def test_a4_resume_reprints_without_an_event(self):
        self.reprint("resume")

    def test_a4_clear_is_a_new_start(self):
        before = self.event_lines()
        done = self.hook("clear")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.event_lines(), before + 1)
        self.assertEqual(len(self.starts()), 1)


class A5WhereTheModelComesFrom(HookSandbox):
    config_extra = {"worktree_dir": WORKTREE}

    def test_a5_no_routing_main_is_said_out_loud(self):
        done = self.hook("startup")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        warned = [line for line in done.stdout.splitlines() if "routing.main" in line]
        self.assertTrue(warned, "缺 routing.main 卻不說 —— model=unknown 與量過的長得一樣")
        self.assertEqual([row["model"] for row in self.starts()], ["unknown"])

    def test_a5_ac_model_wins(self):
        done = self.hook("startup", AC_MODEL="opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([row["model"] for row in self.starts()], ["opus"])


class A6TheCap(HookSandbox):

    def test_a6_a_long_page_is_cut_and_says_how_long_it_was(self):
        for n in range(300):
            self.make_ticket(n + 1, subject="開著的第 %d 張:%s" % (n + 1, "撐" * 20))
        full = self.run_sh("scripts/new-session.sh", "main", "fable", "--no-event")
        self.assertEqual(full.stderr, "", "量原始長度那一趟要乾淨,不然兩邊量的不是同一頁")
        original = len(full.stdout.encode("utf-8"))
        self.assertGreater(original, LIMIT, "沙盒沒撐過上限 —— 這一條什麼都沒驗")

        done = self.hook("resume")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLessEqual(len(done.stdout.encode("utf-8")), LIMIT)
        last = done.stdout.rstrip("\n").splitlines()[-1]
        self.assertIn("截斷", last)
        self.assertIn(str(original), re.findall(r"\d+", last))
        self.assertIn("memory/role/main.md", done.stdout,
                      "截掉的是中間;「接下來要讀的」是這一頁的目的,要留著")

    def test_a6_a_short_page_has_no_cut_line(self):
        done = self.hook("resume")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLessEqual(len(done.stdout.encode("utf-8")), LIMIT)
        self.assertNotIn("截斷", done.stdout)


if __name__ == "__main__":
    unittest.main()
