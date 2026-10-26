"""`scripts/event.py`:控制台只讀這份檔,所以這一組釘的是「寫進去的長什麼樣」。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import SCRIPTS, Sandbox  # noqa: E402


class InsideACopy(Sandbox):
    """副本裡不發事件,由派工方代發(#29 A9,G9)。

    `docs/SESSION-START.md` 以前要 worker / 驗證者自己發 `ticket.attempt.start`,而
    worker 住在 `git archive | tar -x` 展出來的副本裡 —— 舊的 `repo_root()` 往上找到的是
    **副本自己那份** `board/config.json`,事件因此寫進一個等一下會被刪掉的檔,
    **而且一聲都不吭**。發出去了與沒發出去於是長得一樣。

    #159(D-071 C2)之後根不從檔案位置推:副本不是 git 樹,沒有 `AC_ROOT` 就 rc 2、一個
    位元組都不寫;`looks_like_a_copy()` 照舊認得副本。
    """

    def copy_of_the_repo(self):
        """一份**像副本的副本**:有 `board/config.json`、沒有 `.git`。"""
        import shutil
        where = os.path.join(self.home, "fix-t1", "work")
        os.makedirs(where)
        shutil.copytree(os.path.join(self.repo, "board"), os.path.join(where, "board"))
        for junk in ("events.jsonl", "answers.jsonl"):
            path = os.path.join(where, "board", junk)
            if os.path.exists(path):
                os.remove(path)
        self.assertFalse(os.path.exists(os.path.join(where, ".git")))
        return where

    def emit_in(self, where, *args, **extra):
        env = self.env(**extra)
        if "AC_ROOT" not in extra:
            env.pop("AC_ROOT")
        return self.event("emit", *args, cwd=where, env=env)

    def test_emitting_from_a_copy_is_refused_and_writes_nothing_there(self):
        """**變異**:`repo_root()` 找不到根時退回這支檔案的上一層(或副本自己)→ 這一條紅。"""
        where = self.copy_of_the_repo()
        before = self.events()
        done = self.emit_in(where, "ticket.attempt.start", "--ticket", "1")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("not inside a project", done.stderr)
        self.assertIn("AC_ROOT", done.stderr, "說得出下一步")
        self.assertFalse(os.path.exists(os.path.join(where, "board", "events.jsonl")),
                         "副本裡那一行會跟著副本一起被刪 —— 不該寫得出來")
        self.assertEqual(self.events(), before)

    def test_the_copy_still_looks_like_a_copy(self):
        """`looks_like_a_copy()` 照舊:有 board/config.json、沒有 .git 是副本;worktree 不是。"""
        where = self.copy_of_the_repo()
        wt = self.worktree("t1")
        sys.path.insert(0, self.scripts)
        import event as event_mod
        self.assertTrue(event_mod.looks_like_a_copy(where))
        self.assertFalse(event_mod.looks_like_a_copy(wt))
        self.assertFalse(event_mod.looks_like_a_copy(self.repo))

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
        """worktree 的 `.git` 是一個**檔**,不是目錄 —— 票分支的 worktree 不是副本,而那一側
        是真的要發事件的。沒有 `AC_ROOT` 時 cwd 規則指回主 repo(#159):事件寫進沙盒那一份。"""
        wt = self.worktree("t1")
        done = self.emit_in(wt, "gate.start")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.kinds(), ["gate.start"])
        self.assertFalse(os.path.exists(os.path.join(wt, "board", "events.jsonl")))


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
                          cwd=os.path.join(self.repo, "docs"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.events()[0]
        self.assertEqual(row["cwd"], "docs")
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

    def test_review_has_exactly_four_endings(self):
        """#136 刪了覆核綁定:「遲到不收」那一種從登記表拿掉、不新增種類 —— 覆核只剩
        pass / fail / missing / refused 四種結局。

        **變異**:KINDS 留著遲到那一種(或多登記一種)→ 這一條紅。
        """
        sys.path.insert(0, SCRIPTS)
        import event as event_mod
        self.assertEqual([kind for kind in event_mod.KINDS if kind.startswith("review.")],
                         ["review.pass", "review.fail", "review.missing", "review.refused"])

    def test_the_table_is_exactly_the_kinds_that_stay(self):
        """#143(D-054 Q5):票 JSON 是唯一的狀態 —— 票欄位變更、cost 列、收件匣發頁
        三種鏡像事件從登記表拿掉,不多任何一種;#119 加 schedule.* 五種。期望值是這份手抄的表(44 種),不從
        KINDS 讀回來;不在表上的種類 `emit` 一律拒收(上一條)。land.* / agent.* /
        ticket.attempt.* / ticket.closed 照舊(#115 守衛與看板時間線靠它們)。

        **變異**:KINDS 留著三種鏡像事件的任一種 → 這一條紅。
        """
        sys.path.insert(0, SCRIPTS)
        import event as event_mod
        self.assertEqual(list(event_mod.KINDS), [
            "session.start", "session.end",
            "ticket.created", "ticket.frozen", "ticket.closed",
            "ticket.attempt.start", "ticket.attempt.done", "ticket.attempt.failed",
            "agent.start", "agent.done", "agent.failed", "agent.recovery_needed",
            "agent.cost", "schedule.proposed",
            "schedule.start", "schedule.resume", "schedule.dispatch", "schedule.land",
            "schedule.stop",
            "gate.start", "gate.rerun", "gate.pass", "gate.fail", "flake.auto_pass",
            "env.suspect", "regression.red",
            "land.start", "land.refused", "land.pass", "land.fail",
            "release.start", "release.pass", "release.fail",
            "decision.asked", "decision.answered",
            "objection.triage.start", "objection.triage.done",
            "review.pass", "review.fail", "review.missing", "review.refused",
            "memory.pending", "memory.consolidated", "memory.noted"])

    def test_writing_a_baseline_adds_no_event(self):
        """#143:`verify.baseline` 寫回票(verify-case.py check / apply.sh 共用的
        `ticket.save_baseline`)只動票 JSON,events.jsonl 不多任何一筆。

        **變異**:`save_baseline` 存檔後仍發一筆事件 → 這一條紅。
        """
        self.make_ticket(1)
        before = self.events()
        import subprocess
        done = subprocess.run(
            ["python3", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import ticket; "
             "print(ticket.save_baseline('1', {'stage': 'red', 'case': 'x'}))",
             self.scripts],
            cwd=self.repo, env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = self.load_ticket(1)
        self.assertEqual(data["verify"]["baseline"], {"stage": "red", "case": "x"})
        self.assertEqual(done.stdout.strip(), str(data["state_version"]))
        self.assertEqual(self.events(), before, "寫 baseline 發了事件")

    def test_kv_pairs_land_as_fields(self):
        done = self.event("emit", "land.pass", "--kv", "stamp=20260912-010101",
                          "--kv", "sha=abc1234", "--note", "兩張一起進去")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.events()[0]
        self.assertEqual(row["stamp"], "20260912-010101")
        self.assertEqual(row["sha"], "abc1234")
        self.assertEqual(row["note"], "兩張一起進去")

    def test_session_lands_as_a_field_and_is_absent_when_not_given(self):
        """#45 B3:heartbeat 拿 `session` 欄配 start / end。沒給就沒有這個鍵 ——
        空字串的鍵會被當成一個 id,讓所有沒帶 id 的列配成同一個 session。"""
        done = self.event("emit", "session.start", "--role", "main", "--session", "x")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        done = self.event("emit", "session.start", "--role", "main")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        first, second = self.events()
        self.assertEqual(first["session"], "x")
        self.assertNotIn("session", second)

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


if __name__ == "__main__":
    unittest.main()
