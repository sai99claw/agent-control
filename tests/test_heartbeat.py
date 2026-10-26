"""`scripts/heartbeat.sh`:誰死在半路。

**一個撞了額度靜停的 session,與一個正在思考的 session,在事件檔上長得一模一樣**
—— 兩者都是「有 start、沒有 end」。分辨它們的唯一辦法是時間,所以這一組每一條都在
操弄時間戳。
"""

import json
import os
import subprocess
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



# ===================================================================== #117 driver liveness
# Liveness fixtures without any signal (hard_block): a dead pid is a `true` child the test waited
# for; a live one is a `cat` child that ends when the test closes its stdin.


def current_boot():
    with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as handle:
        return handle.read().strip()


def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    assert not os.path.exists("/proc/%d" % proc.pid), proc.pid
    return proc.pid


class Driven(Sandbox):

    events_file = Heartbeat.events_file

    def live_pid(self):
        proc = subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)

        def end():
            proc.stdin.close()
            proc.wait(timeout=30)
        self.addCleanup(end)
        return proc.pid

    def beat(self, *args):
        return self.run_sh("scripts/heartbeat.sh", *args)

    def lines(self, done, needle):
        return [line for line in done.stdout.splitlines()
                if line.startswith("heartbeat:   ") and needle in line]

    @staticmethod
    def start(kind, seconds_ago, pid, boot, **fields):
        row = {"ts": ago(seconds_ago), "kind": kind, "pid": 4, "driver_pid": pid,
               "boot_id": boot}
        row.update(fields)
        return row


class Verdicts(Driven):
    """T1: one verdict word per open start, read from /proc and boot_id."""

    def test_a_dead_driver_is_dead_awaiting_takeover(self):
        self.events_file(self.start("ticket.attempt.start", 60, dead_pid(), current_boot(),
                                    ticket="7", attempt="1"))
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(len(self.lines(done, "dead awaiting-takeover")), 1, done.stdout)

    def test_a_live_driver_past_its_lease_is_alive_and_rc_0(self):
        """A slow live run is not a dead one: 3 h old (lease 7200) and still `alive`."""
        self.events_file(self.start("ticket.attempt.start", 3 * 3600, self.live_pid(),
                                    current_boot(), ticket="7", attempt="1"))
        done = self.beat()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.lines(done, "#7")
        self.assertEqual(len(rows), 1, done.stdout)
        self.assertTrue(rows[0].endswith("—— alive"), rows[0])

    def test_a_live_pid_under_another_boot_is_dead(self):
        """**M1**: drop the boot comparison in liveness.gone -> this one is red."""
        self.events_file(self.start("ticket.attempt.start", 60, 1, "00000000-dead",
                                    ticket="7", attempt="1"))
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(len(self.lines(done, "dead awaiting-takeover")), 1, done.stdout)

    def test_a_later_attempt_of_the_same_ticket_takes_it_over_another_ticket_does_not(self):
        gone, boot = dead_pid(), current_boot()
        self.events_file(
            self.start("ticket.attempt.start", 600, gone, boot, ticket="7", attempt="1"),
            self.start("ticket.attempt.start", 500, gone, boot, ticket="8", attempt="1"),
            self.start("ticket.attempt.start", 300, self.live_pid(), boot, ticket="7",
                       attempt="2"))
        done = self.beat()
        seven = self.lines(done, "#7 attempt=1")
        eight = self.lines(done, "#8 attempt=1")
        self.assertEqual(len(seven), 1, done.stdout)
        self.assertIn("dead taken-over by attempt 2", seven[0])
        self.assertIn("dead awaiting-takeover", eight[0])

    def test_a_row_without_a_driver_is_unknown_and_judged_by_the_lease(self):
        self.events_file({"ts": ago(3 * 3600), "kind": "ticket.attempt.start", "pid": 9,
                          "ticket": "7", "attempt": "1"})
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("租約到期還沒回來", done.stdout)
        rows = self.lines(done, "#7")
        self.assertEqual(len(rows), 1, done.stdout)
        self.assertIn("unknown (no driver recorded; lease rule)", rows[0])
        self.assertNotIn("alive", done.stdout)

    def test_a_dead_land_is_open_until_a_terminal_event_for_its_stamp(self):
        gone, boot = dead_pid(), current_boot()
        self.events_file(self.start("land.start", 60, gone, boot, stamp="S"))
        self.assertEqual(len(self.lines(self.beat(), "dead awaiting-takeover")), 1)
        self.events_file(self.start("land.start", 60, gone, boot, stamp="S"),
                         {"ts": ago(30), "kind": "land.fail", "pid": 5, "stamp": "S"})
        done = self.beat()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("stamp=S", done.stdout)

    def test_a_dead_scheduler_run_is_reported_with_its_resume_hint(self):
        self.write("reports/schedule/state.json", json.dumps(
            {"schema": 1, "run_id": "R9", "pid": dead_pid(), "boot_id": current_boot(),
             "stop": None, "children": {}}))
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        rows = self.lines(done, "schedule run R9")
        self.assertEqual(len(rows), 1, done.stdout)
        self.assertIn("dead awaiting-takeover", rows[0])
        self.assertIn("actl schedule run", rows[0])
        before = self.read("reports/schedule/state.json")
        self.beat("--reconcile")
        self.assertEqual(self.read("reports/schedule/state.json"), before)


class ReconcileAWorkerRound(Driven):
    """T2: a worker round whose auto-fix died, put back to the facts by `--reconcile`."""

    RUN = "20261020-101010-4242"

    def setUp(self):
        super(ReconcileAWorkerRound, self).setUp()
        self.make_ticket("7", state="Running", state_version=5)
        self.wt = self.ticket_branch_commit("7", {"src/a.txt": "committed\n"})
        self.head = self.git("rev-parse", "t7").strip()
        self.write("src/a.txt", "changed, not committed\n", where=self.wt)
        self.write("src/new.txt", "untracked\n", where=self.wt)
        self.run_dir = os.path.join("reports", "t7", self.RUN)
        self.write(os.path.join(self.run_dir, "worker.pid"), "%d\n" % dead_pid())
        self.write(os.path.join(self.run_dir, "worker-round1.log"), "the dead round's log\n")
        self.write(os.path.join("reports", "t7", "sessions.json"), json.dumps(
            {"worker": {"session_id": None, "cwd": "../repo-wt/t7", "round": 1,
                        "model": "opus", "state": "open", "resumable": False}}, indent=2))
        driver, boot = dead_pid(), current_boot()
        self.events_file(
            self.start("ticket.attempt.start", 600, driver, boot, ticket="7", attempt="1",
                       run_id=self.RUN),
            self.start("agent.start", 590, driver, boot, ticket="7", role="worker",
                       model="opus", run_id=self.RUN, round="1", agent="auto-fix"))

    def expected_diff(self):
        self.git("add", "-N", ".", cwd=self.wt)
        return subprocess.run(["git", "diff", "HEAD"], cwd=self.wt, env=self.env(),
                              capture_output=True, check=True).stdout

    def test_the_round_is_put_back_and_a_second_run_does_nothing(self):
        """**M3**: write the wip diff but skip cleaning -> red (porcelain not empty)."""
        expected = self.expected_diff()
        self.assertIn(b"untracked", expected)
        done = self.beat("--reconcile")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("heartbeat: reconciled #7 round 1", done.stdout)
        with open(os.path.join(self.repo, self.run_dir, "wip-round1-died.diff"), "rb") as fh:
            self.assertEqual(fh.read(), expected)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.wt), "")
        self.assertEqual(self.git("rev-parse", "t7").strip(), self.head)
        self.assertFalse(self.exists(os.path.join(self.run_dir, "worker.pid")))
        row = json.loads(self.read(os.path.join("reports", "t7", "sessions.json")))["worker"]
        self.assertEqual((row["state"], row["why"]), ("closed", "driver-died"))
        self.assertTrue(row.get("closed_at"))
        rows = self.events()
        failed = [r for r in rows if r["kind"] == "ticket.attempt.failed"]
        agents = [r for r in rows if r["kind"] == "agent.failed"]
        self.assertEqual([(r.get("attempt"), r.get("reason")) for r in failed], [("1", "driver-died")])
        self.assertEqual([(r.get("role"), r.get("round"), r.get("run_id"), r.get("reason"))
                          for r in agents], [("worker", "1", self.RUN, "driver-died")])
        ticket = self.load_ticket("7")
        self.assertEqual((ticket["state"], ticket["state_version"]), ("Ready", 6))
        cost = ticket.get("cost") or []
        self.assertEqual([(c["role"], c["round"], c["outcome"], c["run_id"]) for c in cost],
                         [("worker", 1, "error", self.RUN)])
        self.assertIn("driver-died", cost[0]["note"])
        self.assertFalse(os.path.isdir(os.path.join(self.repo, "reports", "inbox")))
        logs = os.listdir(os.path.join(self.repo, "reports", "heartbeat"))
        self.assertEqual(len(logs), 1, logs)

        count = len(self.events())
        again = self.beat("--reconcile")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("nothing to reconcile", again.stdout)
        self.assertEqual(len(self.events()), count)
        self.assertEqual(os.listdir(os.path.join(self.repo, "reports", "heartbeat")), logs)

    def test_a_round_taken_over_by_a_later_attempt_is_left_alone(self):
        """**M2**: reconcile treats taken-over like awaiting -> red (the old round would get a
        failed event and the worktree of the live attempt would be cleaned)."""
        rows = self.events()
        rows.append(self.start("ticket.attempt.start", 60, self.live_pid(), current_boot(),
                               ticket="7", attempt="2"))
        self.events_file(*rows)
        done = self.beat("--reconcile")
        self.assertIn("taken-over by attempt 2", done.stdout)
        self.assertNotIn("ticket.attempt.failed", self.kinds())
        self.assertNotIn("agent.failed", self.kinds())
        self.assertNotEqual(self.git("status", "--porcelain", cwd=self.wt), "")
        self.assertEqual(self.load_ticket("7")["state"], "Running")

    def test_a_ticket_already_in_review_keeps_its_state(self):
        self.make_ticket("7", state="InReview", state_version=9)
        done = self.beat("--reconcile")
        self.assertIn("heartbeat: reconciled #7 round 1", done.stdout)
        self.assertEqual(self.load_ticket("7")["state"], "InReview")
        self.assertEqual(self.load_ticket("7")["state_version"], 9)


class ReconcileALand(Driven):
    """T3: a land whose driver died; one whose stamp already ended; one still running."""

    def land_left_over(self, holder_pid, stamp="20261020-111111"):
        os.makedirs(os.path.join(self.repo, ".land.lock"))
        self.write(os.path.join(".land.lock", "holder"),
                   "pid=%d boot_id=%s 開始=x 分支=t7\n" % (holder_pid, current_boot()))
        self.git("worktree", "add", "-q", "-b", "land/%s" % stamp,
                 os.path.join(self.home, "land-%s" % stamp), "main")
        return stamp

    def has_branch(self, name):
        return subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/heads/" + name],
                              cwd=self.repo, env=self.env(), capture_output=True).returncode == 0

    def test_a_dead_land_loses_its_lock_worktree_and_branch(self):
        gone = dead_pid()
        stamp = self.land_left_over(gone)
        self.events_file(self.start("land.start", 60, gone, current_boot(), stamp=stamp,
                                    note="t7", main_before="abc"))
        done = self.beat("--reconcile")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(self.exists(".land.lock"))
        fails = [r for r in self.events() if r["kind"] == "land.fail"]
        self.assertEqual([(r["stamp"], r["stage"], r.get("main_before"), r.get("note"))
                          for r in fails], [(stamp, "died", "abc", "driver-died")])
        self.assertFalse(self.has_branch("land/" + stamp))
        self.assertFalse(os.path.isdir(os.path.join(self.home, "land-%s" % stamp)))

    def test_a_land_that_already_ended_keeps_its_debris(self):
        """**M4**: remove the land worktree regardless of a terminal event -> red."""
        gone = dead_pid()
        stamp = self.land_left_over(gone)
        self.events_file(self.start("land.start", 60, gone, current_boot(), stamp=stamp),
                         {"ts": ago(30), "kind": "land.fail", "pid": 5, "stamp": stamp,
                          "note": "閘門紅"})
        done = self.beat("--reconcile")
        self.assertIn("nothing to reconcile", done.stdout)
        self.assertTrue(self.has_branch("land/" + stamp))
        self.assertTrue(self.exists(os.path.join(".land.lock", "holder")))
        self.assertIn("land/%s" % stamp, done.stdout)

    def test_a_live_land_is_named_and_nothing_is_removed(self):
        live = self.live_pid()
        stamp = self.land_left_over(live)
        self.events_file(self.start("land.start", 60, live, current_boot(), stamp=stamp))
        done = self.beat("--reconcile")
        rows = self.lines(done, "stamp=%s" % stamp)
        self.assertTrue(rows and rows[0].endswith("—— alive"), done.stdout)
        self.assertTrue(self.exists(os.path.join(".land.lock", "holder")))
        self.assertTrue(self.has_branch("land/" + stamp))
        self.assertNotIn("land.fail", self.kinds())


class ReconcileAMainSession(Driven):
    """T4"""

    def test_a_dead_main_session_is_ended_one_without_a_driver_is_not(self):
        self.events_file(
            self.start("session.start", 60, dead_pid(), current_boot(), role="main",
                       session="dead-one"),
            {"ts": ago(60), "kind": "session.start", "pid": 3, "role": "main",
             "session": "old-one"})
        done = self.beat("--reconcile")
        ends = [r for r in self.events() if r["kind"] == "session.end"]
        self.assertEqual([(r.get("session"), r.get("reason")) for r in ends],
                         [("dead-one", "driver-died")], done.stdout)


if __name__ == "__main__":
    unittest.main()
