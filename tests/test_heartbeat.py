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


class ADeadSchedulerRun(Driven):
    """T1: the scheduler's own state file is a start too (no verifier counterpart)."""

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


class ADeadRoundInReview(Driven):
    """T2: a dead round whose ticket already moved on keeps that state (no verifier
    counterpart)."""

    RUN = "20261020-101010-4242"

    def setUp(self):
        super(ADeadRoundInReview, self).setUp()
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

    def test_a_ticket_already_in_review_keeps_its_state(self):
        self.make_ticket("7", state="InReview", state_version=9)
        done = self.beat("--reconcile")
        self.assertIn("heartbeat: reconciled #7 round 1", done.stdout)
        self.assertEqual(self.load_ticket("7")["state"], "InReview")
        self.assertEqual(self.load_ticket("7")["state_version"], 9)


class ALiveHolderOnADeadDriversPid(Driven):
    """A5 / A2 one-gone rule: the lock goes only when its holder is gone by its own pid and
    boot. A dead land.start from another boot whose driver_pid is now reused by a live land in
    this boot must not cost that land its lock."""

    def test_a_live_holder_whose_pid_equals_a_dead_other_boot_driver_keeps_the_lock(self):
        """**Mutation**: take the lock over when holder pid == driver_pid (no gone()) -> red."""
        live, stamp = self.live_pid(), "20261020-111111"
        os.makedirs(os.path.join(self.repo, ".land.lock"))
        self.write(os.path.join(".land.lock", "holder"),
                   "pid=%d boot_id=%s 開始=x 分支=t7\n" % (live, current_boot()))
        holder = self.read(os.path.join(".land.lock", "holder"))
        self.events_file(self.start("land.start", 600, live, "00000000-dead", stamp=stamp))
        done = self.beat("--reconcile")
        self.assertTrue(self.exists(os.path.join(".land.lock", "holder")),
                        done.stdout + done.stderr)
        self.assertEqual(self.read(os.path.join(".land.lock", "holder")), holder)
        self.assertIn("lock kept: holder pid %d" % live, done.stdout)



class DriverLiveness(Heartbeat):
    """#117:start 事件帶 driver_pid + boot_id,心跳據此判 alive / dead / unknown,
    `--reconcile` 只收 dead + awaiting-takeover 的那幾列。期望值全部來自夾具本身:
    死的 pid 是測試自己起、等它結束的 `true`;活的是測試自己起的 `sleep`;boot 是測試
    自己讀的 /proc。"""

    def boot(self):
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as handle:
            return handle.read().strip()

    def dead_pid(self):
        import subprocess
        child = subprocess.Popen(["true"])
        child.wait()
        self.assertFalse(os.path.isdir("/proc/%d" % child.pid), "夾具:pid 還在")
        return child.pid

    def live_pid(self):
        import subprocess
        child = subprocess.Popen(["sleep", "300"])
        self.addCleanup(child.wait)
        self.addCleanup(child.terminate)
        self.assertTrue(os.path.isdir("/proc/%d" % child.pid))
        return child.pid

    def line_with(self, out, needle):
        lines = [line for line in out.splitlines()
                 if line.startswith("heartbeat:") and needle in line]
        self.assertEqual(len(lines), 1, "要剛好一行含 %r:\n%s" % (needle, out))
        return lines[0]

    def reconcile(self):
        return self.run_sh("scripts/heartbeat.sh", "--reconcile")

    # ------------------------------------------------------------ T1 判詞

    def test_t1a_a_start_whose_driver_is_gone_is_dead_awaiting_takeover(self):
        self.events_file({"ts": ago(60), "kind": "ticket.attempt.start", "pid": 9001,
                          "ticket": "7", "attempt": "1",
                          "driver_pid": self.dead_pid(), "boot_id": self.boot()})
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("dead awaiting-takeover", self.line_with(done.stdout, "pid=9001"))

    def test_t1b_a_live_driver_past_its_lease_is_alive_and_not_a_failure(self):
        self.events_file({"ts": ago(3 * 3600), "kind": "ticket.attempt.start", "pid": 9001,
                          "ticket": "7", "attempt": "1",
                          "driver_pid": self.live_pid(), "boot_id": self.boot()})
        done = self.beat()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        line = self.line_with(done.stdout, "pid=9001")
        self.assertIn("alive", line)
        self.assertNotIn("dead", line)

    def test_t1c_a_live_pid_from_another_boot_is_dead(self):
        self.events_file({"ts": ago(60), "kind": "ticket.attempt.start", "pid": 9001,
                          "ticket": "7", "attempt": "1",
                          "driver_pid": 1, "boot_id": "00000000-dead"})
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        line = self.line_with(done.stdout, "pid=9001")
        self.assertIn("dead", line)
        self.assertNotIn("alive", line)

    def test_t1d_a_later_attempt_of_the_same_ticket_takes_over(self):
        boot = self.boot()
        self.events_file(
            {"ts": ago(600), "kind": "ticket.attempt.start", "pid": 9001,
             "ticket": "7", "attempt": "1", "driver_pid": self.dead_pid(), "boot_id": boot},
            {"ts": ago(60), "kind": "ticket.attempt.start", "pid": 9002,
             "ticket": "7", "attempt": "2", "driver_pid": self.live_pid(), "boot_id": boot})
        done = self.beat()
        line = self.line_with(done.stdout, "pid=9001")
        self.assertIn("dead taken-over by attempt 2", line)
        self.assertNotIn("awaiting-takeover", line)

    def test_t1d_a_later_start_of_another_ticket_does_not_take_over(self):
        boot = self.boot()
        self.events_file(
            {"ts": ago(600), "kind": "ticket.attempt.start", "pid": 9001,
             "ticket": "7", "attempt": "1", "driver_pid": self.dead_pid(), "boot_id": boot},
            {"ts": ago(60), "kind": "ticket.attempt.start", "pid": 9002,
             "ticket": "8", "attempt": "2", "driver_pid": self.live_pid(), "boot_id": boot})
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        line = self.line_with(done.stdout, "pid=9001")
        self.assertIn("dead awaiting-takeover", line)
        self.assertNotIn("taken-over", line)

    def test_t1e_a_row_without_a_driver_is_unknown_under_the_lease_rule(self):
        self.events_file({"ts": ago(3 * 3600), "kind": "ticket.attempt.start", "pid": 9001,
                          "ticket": "7", "attempt": "1"})
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        line = self.line_with(done.stdout, "pid=9001")
        self.assertIn("unknown", line)
        self.assertNotIn("alive", line)
        self.assertIn("租約 7200 秒", line)

    def test_t1f_a_land_whose_driver_is_gone_is_dead_awaiting_takeover(self):
        self.events_file({"ts": ago(60), "kind": "land.start", "pid": 9001, "stamp": "S1",
                          "driver_pid": self.dead_pid(), "boot_id": self.boot()})
        done = self.beat()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("dead awaiting-takeover", self.line_with(done.stdout, "stamp=S1"))

    def test_t1f_a_dead_land_that_already_failed_is_not_open(self):
        self.events_file(
            {"ts": ago(600), "kind": "land.start", "pid": 9001, "stamp": "S1",
             "driver_pid": self.dead_pid(), "boot_id": self.boot()},
            {"ts": ago(590), "kind": "land.fail", "pid": 9002, "stamp": "S1"})
        done = self.beat()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("stamp=S1", done.stdout)

    # ------------------------------------------------------------ T2 死掉的 worker 輪

    def dead_worker_round(self, ident="5", run_id="20261020-101010-1"):
        self.make_ticket(ident, state="Running", state_version=3)
        wt = self.ticket_branch_commit(ident, {"src/a.txt": "one\n"})
        head = self.git("rev-parse", "t%s" % ident).strip()
        self.write("src/a.txt", "one\ntwo\n", where=wt)
        self.write("src/new.txt", "untracked\n", where=wt)
        self.git("add", "-N", ".", cwd=wt)
        expected = self.git("diff", "HEAD", cwd=wt)
        self.assertIn("src/new.txt", expected, "夾具:未追蹤的檔要進 diff")
        run_dir = os.path.join("reports", "t%s" % ident, run_id)
        dead = self.dead_pid()
        self.write(os.path.join(run_dir, "worker.pid"), "%d\n" % dead)
        self.write(os.path.join(run_dir, "worker-round1.log"), "worker said something\n")
        self.write(os.path.join("reports", "t%s" % ident, "sessions.json"), json.dumps(
            {"worker": {"session_id": None, "cwd": os.path.relpath(wt, self.repo),
                        "round": 1, "model": "opus", "state": "open",
                        "resumable": False, "why": "fixture"}}))
        boot = self.boot()
        self.events_file(
            {"ts": ago(600), "kind": "ticket.attempt.start", "pid": 9001, "ticket": ident,
             "attempt": "1", "driver_pid": dead, "boot_id": boot},
            {"ts": ago(599), "kind": "agent.start", "pid": 9002, "ticket": ident,
             "role": "worker", "model": "opus", "run_id": run_id, "round": "1",
             "driver_pid": dead, "boot_id": boot})
        return wt, head, expected, run_dir

    def test_t2_a_dead_worker_round_is_saved_cleaned_closed_and_requeued(self):
        wt, head, expected, run_dir = self.dead_worker_round()
        done = self.reconcile()
        out = done.stdout + done.stderr
        self.assertIn("heartbeat: reconciled", done.stdout, out)
        self.assertEqual(self.read(os.path.join(run_dir, "wip-round1-died.diff")), expected)
        self.assertEqual(self.git("status", "--porcelain", cwd=wt), "")
        self.assertEqual(self.git("rev-parse", "t5").strip(), head)
        self.assertFalse(self.exists(os.path.join(run_dir, "worker.pid")), out)
        row = json.loads(self.read("reports/t5/sessions.json"))["worker"]
        self.assertEqual(row["state"], "closed")
        self.assertEqual(row["why"], "driver-died")
        self.assertTrue(row.get("closed_at"))
        rows = self.events()
        self.assertEqual(len([r for r in rows if r["kind"] == "ticket.attempt.failed"
                              and str(r.get("ticket")) == "5"
                              and r.get("reason") == "driver-died"]), 1, out)
        self.assertEqual(len([r for r in rows if r["kind"] == "agent.failed"
                              and r.get("role") == "worker"
                              and r.get("reason") == "driver-died"]), 1, out)
        ticket = self.load_ticket("5")
        self.assertEqual(ticket["state"], "Ready")
        self.assertEqual(ticket["state_version"], 4)
        cost = [c for c in ticket.get("cost") or [] if c.get("role") == "worker"]
        self.assertEqual(len(cost), 1, cost)
        self.assertEqual(cost[0]["round"], 1)
        self.assertEqual(cost[0]["outcome"], "error")
        self.assertIn("driver-died", cost[0]["note"] or "")
        self.assertEqual(cost[0]["run_id"], "20261020-101010-1")
        self.assertEqual(self.inbox_rows(), [])
        logs = os.path.join(self.repo, "reports", "heartbeat")
        self.assertTrue(os.path.isdir(logs) and any(n.endswith(".log") for n in os.listdir(logs)))

        again = self.reconcile()
        self.assertIn("nothing to reconcile", again.stdout, again.stdout + again.stderr)
        self.assertEqual(len(self.events()), len(rows))
        self.assertEqual(self.load_ticket("5")["state_version"], 4)

    def test_t2_a_taken_over_round_is_left_alone(self):
        wt, head, expected, run_dir = self.dead_worker_round()
        with open(os.path.join(self.repo, "board", "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": ago(30), "kind": "ticket.attempt.start", "pid": 9003,
                                 "ticket": "5", "attempt": "2",
                                 "driver_pid": self.live_pid(), "boot_id": self.boot()}) + "\n")
        before = len(self.events())
        done = self.reconcile()
        self.assertNotIn("ticket.attempt.failed", [r["kind"] for r in self.events()],
                         done.stdout + done.stderr)
        self.assertEqual(len(self.events()), before)
        self.assertEqual(self.load_ticket("5")["state"], "Running")
        self.assertNotEqual(self.git("status", "--porcelain", cwd=wt), "")

    # ------------------------------------------------------------ T3 死掉的 land

    def dead_land(self, holder_pid, driver_pid, stamp="20261020-111111"):
        os.makedirs(os.path.join(self.repo, ".land.lock"))
        self.write(".land.lock/holder", "pid=%d boot_id=%s 開始=2026-10-20T11:11:11 分支=t1-x\n"
                   % (holder_pid, self.boot()))
        self.events_file({"ts": ago(600), "kind": "land.start", "pid": 9001, "stamp": stamp,
                          "driver_pid": driver_pid, "boot_id": self.boot()})
        path = os.path.join(self.home, "land-wt")
        self.git("worktree", "add", "-q", "-b", "land/%s" % stamp, path, "main")
        return path, stamp

    def has_branch(self, name):
        import subprocess
        return subprocess.run(["git", "-C", self.repo, "rev-parse", "-q", "--verify",
                               "refs/heads/%s" % name], capture_output=True,
                              env=self.env()).returncode == 0

    def test_t3_a_dead_land_is_unlocked_failed_and_swept(self):
        dead = self.dead_pid()
        path, stamp = self.dead_land(dead, dead)
        done = self.reconcile()
        out = done.stdout + done.stderr
        self.assertFalse(self.exists(".land.lock"), out)
        fails = [r for r in self.events() if r["kind"] == "land.fail"
                 and r.get("stamp") == stamp]
        self.assertEqual(len(fails), 1, out)
        self.assertEqual(fails[0].get("stage"), "died")
        self.assertFalse(os.path.exists(path), out)
        self.assertFalse(self.has_branch("land/%s" % stamp), out)

    def test_t3_a_land_that_already_ended_keeps_its_worktree_and_lock(self):
        dead = self.dead_pid()
        path, stamp = self.dead_land(dead, dead)
        with open(os.path.join(self.repo, "board", "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": ago(590), "kind": "land.fail", "pid": 9002,
                                 "stamp": stamp}) + "\n")
        holder = self.read(".land.lock/holder")
        self.reconcile()
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(self.has_branch("land/%s" % stamp))
        self.assertEqual(self.read(".land.lock/holder"), holder)
        self.assertEqual(len([r for r in self.events() if r["kind"] == "land.fail"]), 1)

    def test_t3_a_live_land_is_named_alive_and_nothing_is_removed(self):
        live = self.live_pid()
        path, stamp = self.dead_land(live, live)
        done = self.reconcile()
        self.assertIn("alive", done.stdout, done.stdout + done.stderr)
        self.assertTrue(self.exists(".land.lock/holder"))
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(self.has_branch("land/%s" % stamp))
        self.assertNotIn("land.fail", self.kinds())

    # ------------------------------------------------------------ T4 主線 session

    def test_t4_a_dead_main_session_gets_its_end(self):
        self.events_file({"ts": ago(600), "kind": "session.start", "pid": 9001,
                          "role": "main", "session": "s-dead",
                          "driver_pid": self.dead_pid(), "boot_id": self.boot()})
        done = self.reconcile()
        ends = [r for r in self.events() if r["kind"] == "session.end"]
        self.assertEqual(len(ends), 1, done.stdout + done.stderr)
        self.assertEqual(ends[0].get("reason"), "driver-died")
        self.assertEqual(ends[0].get("session"), "s-dead")

    def test_t4_a_main_session_without_a_driver_is_not_ended(self):
        self.events_file({"ts": ago(3 * 3600), "kind": "session.start", "pid": 9001,
                          "role": "main", "session": "s-old"})
        self.reconcile()
        self.assertNotIn("session.end", self.kinds())

    # ------------------------------------------------------------ T7 一份活性判準、不發訊號

    def test_t7_one_liveness_module_shared_with_schedule_and_no_signals(self):
        import re
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scripts = os.path.join(here, "scripts")

        def text(name):
            with open(os.path.join(scripts, name), encoding="utf-8") as handle:
                return handle.read()

        schedule = text("schedule.py")
        self.assertNotIn("def gone(", schedule, "schedule.py 還留著自己那一份 gone()")
        homes = [name[:-3] for name in sorted(os.listdir(scripts))
                 if name.endswith(".py") and "\ndef gone(" in "\n" + text(name)]
        self.assertEqual(len(homes), 1, "gone() 要剛好一份:%s" % homes)
        module = homes[0]
        pattern = r"(^|\n)\s*(import|from)\s+%s\b" % re.escape(module)
        self.assertRegex(schedule, pattern)
        beat = text("heartbeat.sh") + (text("heartbeat.py") if os.path.exists(
            os.path.join(scripts, "heartbeat.py")) else "")
        self.assertRegex(beat, pattern)
        body = text(module + ".py")
        # 字面拆開:hard_block.patterns 查 tests/ 的新增行(A9)。
        for word in ("os" + ".kill(", "kill" + "pg(", "signal."):
            self.assertNotIn(word, body)


if __name__ == "__main__":
    unittest.main()
