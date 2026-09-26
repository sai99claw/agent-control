"""`scripts/heartbeat.sh`:誰死在半路。

**一個撞了額度靜停的 session,與一個正在思考的 session,在事件檔上長得一模一樣**
—— 兩者都是「有 start、沒有 end」。分辨它們的唯一辦法是時間,所以這一組每一條都在
操弄時間戳。
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402


def ago(seconds):
    return (datetime.now().astimezone()
            - timedelta(seconds=seconds)).isoformat(timespec="seconds")


class Heartbeat(Sandbox):

    def events_file(self, *rows):
        self.write("board/events.jsonl",
                   "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))

    def beat(self):
        return self.run_sh("scripts/heartbeat.sh")

    def test_nothing_open_is_a_clean_bill(self):
        self.events_file({"ts": ago(10), "kind": "session.start", "pid": 1,
                          "role": "main", "model": "fable"},
                         {"ts": ago(5), "kind": "session.end", "pid": 1, "role": "main"})
        done = self.beat()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("沒有到期的租約", done.stdout)
        self.assertIn("沒有 land 殘骸", done.stdout)

    def test_a_session_past_its_lease_with_no_end_is_named(self):
        """**變異**:把 `age > lease` 改成 `age > lease * 100` → 這一條紅。"""
        self.events_file({"ts": ago(3600), "kind": "session.start", "pid": 4242,
                          "role": "opener", "model": "fable"})
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("租約到期還沒回來", done.stdout)
        self.assertIn("pid=4242", done.stdout)
        self.assertIn("role=opener", done.stdout)
        self.assertIn("租約 900 秒", done.stdout)

    def test_a_session_still_inside_its_lease_is_left_alone(self):
        """「還在跑」與「死了」的差別只有時間 —— 所以時間之內的不准被點名。"""
        self.events_file({"ts": ago(60), "kind": "session.start", "pid": 4242,
                          "role": "opener", "model": "fable"})
        done = self.beat()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("4242", done.stdout)

    def test_two_lines_running_at_once_do_not_cancel_each_other_out(self):
        """配對的鍵要選得夠細:用「最近一筆 end」去配所有 start,兩條同時在跑的線
        會互相把對方銷掉,而那看起來就像大家都回來了。

        **變異**:把 `key_of` 的 pid 拿掉(只用 role 配)→ 這一條紅。
        """
        self.events_file(
            {"ts": ago(20000), "kind": "session.start", "pid": 11, "role": "worker"},
            {"ts": ago(19000), "kind": "session.start", "pid": 22, "role": "worker"},
            {"ts": ago(10), "kind": "session.end", "pid": 22, "role": "worker"})
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("pid=11", done.stdout)
        self.assertNotIn("pid=22", done.stdout)

    def test_a_session_is_matched_by_its_session_id_not_its_pid(self):
        """#45 B3:start 與 end 是兩次 `event.py emit`、兩個 pid —— 拿 pid 配永遠配不上,
        每個 session 都被當成沒回來。同一個 `session` 欄就是同一個 session。

        **變異 M4**:`key_of` 忽略 `session` 欄 → 這一條紅。
        """
        self.events_file(
            {"ts": ago(20000), "kind": "session.start", "pid": 1, "role": "main",
             "session": "x"},
            {"ts": ago(10), "kind": "session.end", "pid": 2, "role": "main",
             "session": "x"})
        done = self.beat()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("沒有到期的租約", done.stdout)

    def test_a_land_is_matched_by_its_own_stamp(self):
        self.events_file(
            {"ts": ago(9000), "kind": "land.start", "pid": 1, "stamp": "A"},
            {"ts": ago(8900), "kind": "land.pass", "pid": 1, "stamp": "A"},
            {"ts": ago(9000), "kind": "land.start", "pid": 2, "stamp": "B"})
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("stamp=B", done.stdout)
        self.assertNotIn("stamp=A", done.stdout)
        self.assertIn("租約 1800 秒", done.stdout)

    def test_a_refused_land_counts_as_come_back(self):
        """`land.refused` 也是一種回來 —— 不然每一次拒絕都會在心跳上留一具屍體。"""
        self.events_file(
            {"ts": ago(9000), "kind": "land.start", "pid": 1, "stamp": "A"},
            {"ts": ago(8990), "kind": "land.refused", "pid": 1, "stamp": "A"})
        self.assertEqual(self.beat().returncode, 0)

    def test_an_attempt_that_never_finished_is_named_with_its_ticket(self):
        self.events_file({"ts": ago(20000), "kind": "ticket.attempt.start",
                          "pid": 9, "ticket": "7", "attempt": "2", "model": "opus"})
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("#7", done.stdout)
        self.assertIn("租約 7200 秒", done.stdout)

    def test_a_leftover_land_worktree_is_listed(self):
        """閘門紅時 worktree 是**故意留著給人看的**,所以「有殘骸」不是錯誤,是一件
        還沒有人處理完的事。"""
        self.git("worktree", "add", "-q", "-b", "land/20260912-010101",
                 os.path.join(self.home, "leftover"), "main")
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("land/20260912-010101", done.stdout)
        self.assertIn("git worktree remove", done.stdout)

    def test_an_ordinary_ticket_worktree_is_not_mistaken_for_land_debris(self):
        self.worktree("t1-something")
        done = self.beat()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("沒有 land 殘骸", done.stdout)

    def test_no_events_file_at_all_is_not_a_crash(self):
        done = self.beat()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
