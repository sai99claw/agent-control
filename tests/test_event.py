"""`scripts/event.py`:控制台只讀這份檔,所以這一組釘的是「寫進去的長什麼樣」。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import SCRIPTS, Sandbox  # noqa: E402

sys.path.insert(0, SCRIPTS)
import event  # noqa: E402


class InsideACopy(Sandbox):
    """副本裡不發事件,由派工方代發(#29 A9,G9)。

    `docs/SESSION-START.md` 以前要 worker / 驗證者自己發 `ticket.attempt.start`,而
    worker 住在 `git archive | tar -x` 展出來的副本裡 —— `repo_root()` 往上找到的是
    **副本自己那份** `board/config.json`,事件因此寫進一個等一下會被刪掉的檔,
    **而且一聲都不吭**。發出去了與沒發出去於是長得一樣。
    """

    def copy_of_the_repo(self):
        """一份**像副本的副本**:有 `board/config.json`、沒有 `.git`。"""
        import shutil
        where = os.path.join(self.home, "fix-t1", "work")
        os.makedirs(where)
        for rel in ("scripts", "board"):
            shutil.copytree(os.path.join(self.repo, rel), os.path.join(where, rel))
        for junk in ("events.jsonl", "answers.jsonl"):
            path = os.path.join(where, "board", junk)
            if os.path.exists(path):
                os.remove(path)
        self.assertFalse(os.path.exists(os.path.join(where, ".git")))
        return where

    def emit_in(self, where, *args, **extra):
        import subprocess
        return subprocess.run(
            ["python3", os.path.join(where, "scripts", "event.py"), "emit", *args],
            cwd=where, env=self.env(**extra), capture_output=True, text=True,
            timeout=60)

    def test_emitting_from_a_copy_is_refused_and_writes_nothing_there(self):
        """**變異**:把 `cmd_emit` 裡那一段 `looks_like_a_copy` 拿掉 → 這一條紅。"""
        where = self.copy_of_the_repo()
        done = self.emit_in(where, "ticket.attempt.start", "--ticket", "1")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("副本裡不發事件,由派工方代發", done.stderr)
        self.assertIn("AC_ROOT", done.stderr, "說得出下一步")
        self.assertFalse(os.path.exists(os.path.join(where, "board", "events.jsonl")),
                         "副本裡那一行會跟著副本一起被刪 —— 不該寫得出來")

    def test_with_ac_root_it_writes_into_the_real_repo(self):
        """派工方帶著 `AC_ROOT` 就照發 —— 擋的是「寫進副本」,不是「從副本發」。"""
        where = self.copy_of_the_repo()
        done = self.emit_in(where, "ticket.attempt.start", "--ticket", "1",
                            AC_ROOT=self.repo)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([row["kind"] for row in self.events()],
                         ["ticket.attempt.start"])
        self.assertFalse(os.path.exists(os.path.join(where, "board", "events.jsonl")))

    def test_a_worktree_is_not_a_copy(self):
        """worktree 的 `.git` 是一個**檔**,不是目錄 —— 判準要看得到它,
        不然票分支的 worktree 會被誤判成副本,而那一側是真的要發事件的。"""
        wt = self.worktree("t1")
        import subprocess
        done = subprocess.run(
            ["python3", os.path.join(wt, "scripts", "event.py"), "emit",
             "gate.start"],
            cwd=wt, env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class Emit(Sandbox):

    def test_a_line_carries_time_kind_pid_and_where_it_ran(self):
        done = self.event("emit", "session.start", "--role", "main", "--model", "fable")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.events()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["kind"], "session.start")
        self.assertEqual(row["role"], "main")
        self.assertEqual(row["model"], "fable")
        self.assertTrue(row["ts"].startswith("20"), row["ts"])
        self.assertIsInstance(row["pid"], int)
        self.assertEqual(row["cwd"], ".")

    def test_the_cwd_is_repo_relative_not_an_absolute_path(self):
        """事件檔會被貼進回報與 commit,裡面不該有這台機器的家目錄;而「在哪個
        worktree 跑的」才是要問的事。

        **變異**:把 `relative_cwd` 改成回 `os.getcwd()` → 這一條紅。
        """
        done = self.event("emit", "gate.start",
                          cwd=os.path.join(self.repo, "scripts"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.events()[0]
        self.assertEqual(row["cwd"], "scripts")
        self.assertNotIn(self.home, json.dumps(row))

    def test_running_from_outside_the_repo_says_so_instead_of_leaking_a_path(self):
        done = self.event("emit", "gate.start", cwd=self.home)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.events()[0]["cwd"], "(repo 外)")

    def test_an_unknown_kind_is_refused_and_the_table_is_printed(self):
        """拼錯的種類與沒發的事件長得一樣:`grep gate.fail` 對一份寫成
        `gate.failed` 的檔案回零筆,而零筆正是「一切正常」的樣子。

        **變異**:把 `emit` 裡的 `if kind not in KINDS` 拿掉 → 這一條紅。
        """
        done = self.event("emit", "gate.failed")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("不認得的事件種類", done.stderr)
        self.assertIn("gate.fail", done.stderr, "沒有把表印出來")
        self.assertEqual(self.events(), [], "拒收的事件不該留下一行")

    def test_kv_pairs_land_as_fields(self):
        done = self.event("emit", "land.pass", "--kv", "stamp=20260912-010101",
                          "--kv", "sha=abc1234", "--note", "兩張一起進去")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.events()[0]
        self.assertEqual(row["stamp"], "20260912-010101")
        self.assertEqual(row["sha"], "abc1234")
        self.assertEqual(row["note"], "兩張一起進去")

    def test_kv_without_an_equals_sign_is_refused(self):
        done = self.event("emit", "land.pass", "--kv", "stamp")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.events(), [])


class TailAndGrep(Sandbox):

    def seed(self):
        for _ in range(3):
            self.event("emit", "gate.start")
        self.event("emit", "ticket.attempt.start", "--ticket", "7", "--attempt", "1")
        self.event("emit", "land.refused", "--note", "0 個 commit")

    def test_tail_shows_the_last_n(self):
        self.seed()
        done = self.event("tail", "2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertIn("land.refused", lines[-1])

    def test_grep_finds_by_kind_prefix_and_by_ticket(self):
        self.seed()
        by_kind = self.event("grep", "gate")
        self.assertEqual(len(by_kind.stdout.strip().splitlines()), 3)
        by_ticket = self.event("grep", "#7")
        self.assertIn("ticket.attempt.start", by_ticket.stdout)

    def test_grep_says_so_when_there_is_nothing(self):
        """空輸出與「沒有這種事件」長得一樣 —— 所以它要說出來。"""
        self.seed()
        done = self.event("grep", "release")
        self.assertEqual(done.returncode, 0)
        self.assertIn("一發都沒有", done.stdout)

    def test_tail_on_an_empty_file_says_so(self):
        done = self.event("tail", "20")
        self.assertEqual(done.returncode, 0)
        self.assertIn("還沒有事件", done.stdout)


class Help(Sandbox):

    def test_emit_help_prints_its_flags_the_kind_table_and_an_example(self):
        done = self.event("emit", "--help")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("用法:", done.stdout)
        for flag in ("--ticket", "--role", "--model", "--attempt", "--note", "--kv"):
            self.assertIn(flag, done.stdout)
        self.assertIn("session.start", done.stdout, "沒有把種類表印出來")
        self.assertIn("python3 scripts/event.py emit", done.stdout)
        self.assertEqual(self.events(), [], "印說明不該發出一筆事件")

    def test_an_unknown_flag_lists_the_ones_it_does_know(self):
        """**變異**:把 `unknown_flag()` 裡列出名單那一行拿掉 → 這一條紅。"""
        done = self.event("emit", "gate.pass", "--sha", "abc1234")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("--kv", done.stderr, "沒有告訴他該用 --kv")
        self.assertIn("--note", done.stderr)
        self.assertIn("--help", done.stderr)
        self.assertEqual(self.events(), [])


class KindTable(unittest.TestCase):
    """事件表是一張固定的表(檔頭)。這一條釘住派工、閘門、落地、決策、記憶那幾族
    都在裡面 —— 少一族的話,那一族的動作就只能靜靜發生。"""

    def test_every_family_the_workflow_needs_is_on_the_table(self):
        for kind in ("session.start", "session.end",
                     "ticket.created", "ticket.state", "ticket.frozen", "ticket.closed",
                     "ticket.attempt.start", "ticket.attempt.done",
                     "ticket.attempt.failed", "schedule.proposed",
                     "agent.start", "agent.done", "agent.failed",
                     "gate.start", "gate.rerun", "gate.pass", "gate.fail",
                     "flake.auto_pass", "env.suspect",
                     "land.start", "land.refused", "land.pass", "land.fail",
                     "release.start", "release.pass", "release.fail",
                     "decision.asked", "decision.answered",
                     "memory.over_cap", "memory.consolidated", "memory.noted"):
            self.assertIn(kind, event.KINDS)

    def test_the_table_has_no_duplicates(self):
        self.assertEqual(len(event.KINDS), len(set(event.KINDS)))


if __name__ == "__main__":
    unittest.main()
