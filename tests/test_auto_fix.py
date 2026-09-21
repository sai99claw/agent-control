"""`scripts/auto-fix.sh`:回歸紅了自動派**新** worker(D-010、D-015)。

使用者的原話(D-010):「如果 regression 打出 bug 就找新 worker 輸入票跟 diff 跟 bug,
讓他直接去解,這樣這過程也不需要你來叫他」「新 worker 覺得 issue 真的有問題再直接問你」。

所以這一組問四件事:
1. 紅了會不會**真的**起一個新 worker,而且它拿到的那一份夠不夠開工(交接包);
2. 綠了會不會**停在等覆核** —— 覆核不自動,這一點是 D-010 明文;
3. 三種停下來(反駁 / 三輪耗盡 / 沒有歸因)會不會**留下票的狀態轉換 + 一頁收件匣**,
   而不是印一行就算;
4. **不該派的時候不派** —— 尤其是「rc 非零卻一條紅都解析不出來」那一種,它看起來
   最像沒紅,而派下去的 worker 會拿著空紅榜去猜。

worker 用一支假的可執行檔(`board/config.json` 的 `worker.command`)。**該替換的是代價,
不是語意**:真的 headless 模型要錢要時間,而這一組要問的是腳本怎麼接它的產出。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import DEFAULT_CONFIG, Sandbox, write_executable  # noqa: E402

RED_CASE = """diff -ruN base/tests/test_thing.py work/tests/test_thing.py
--- base/tests/test_thing.py\t1970-01-01 08:00:00
+++ work/tests/test_thing.py\t2026-09-21 10:00:00
@@ -0,0 +1,6 @@
+import unittest
+
+
+class T(unittest.TestCase):
+    def test_thing(self):
+        self.assertEqual(1, 2, "第一輪是紅的")
"""

# 假 worker:把那條紅的改綠,再照規矩出一份 `diff -ruN base work`。
WORKER_FIXES = """#!/bin/sh
set -e
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# 第 %s 輪\\n已排除的假設:沒有\\n最小重現:python3 -m unittest test_thing\\n' \\
    "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""

# 假 worker:改了東西但還是紅的(三輪耗盡那一條路)。
WORKER_STAYS_RED = """#!/bin/sh
set -e
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
cat > work/tests/test_thing.py <<CASE
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 2, "第 $AC_ROUND 輪還是紅的")
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# 第 %s 輪\\n還是紅的\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""

# 假 worker:看完票說票寫錯了。**它不交 patch,只交一行反駁。**
WORKER_OBJECTS = """#!/bin/sh
set -e
echo "worker stdout round $AC_ROUND"
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
printf 'OBJECTION: ticket-wrong 驗收第二條與設計文件對不上\\n' \\
    > "EVIDENCE-round$AC_ROUND.md"
"""

WORKER_NEVER = """#!/bin/sh
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
exit 0
"""

WORKER_FAILS = """#!/bin/sh
echo "worker stderr round $AC_ROUND" >&2
exit 7
"""

GREEN_LOG = """test_ok (test_thing.T.test_ok) ... ok

----------------------------------------------------------------------
Ran 1 test in 0.001s

OK
"""

RED_LOG = """test_thing (test_thing.T.test_thing) ... FAIL

======================================================================
FAIL: test_thing (test_thing.T.test_thing)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_thing.py", line 6, in test_thing
    self.assertEqual(1, 2, "第一輪是紅的")
AssertionError: 1 != 2 : 第一輪是紅的

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (failures=1)
"""

# rc 非零,但一行 `FAIL:` 都沒有 —— 測試根本沒跑起來的形狀。
NO_ATTRIBUTION_LOG = """Traceback (most recent call last):
  File "/usr/lib/python3/unittest/__main__.py", line 18, in <module>
    main(module=None)
ModuleNotFoundError: No module named 'test_thing'
"""


class AutoFixBase(Sandbox):

    def setUp(self):
        super(AutoFixBase, self).setUp()
        self.worker_log = os.path.join(self.home, "worker.log")

    def set_worker(self, body, rerun_cmd=None):
        path = os.path.join(self.home, "fake-worker.sh")
        write_executable(path, body)
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": "sh %s" % path, "timeout_seconds": 120}
        if rerun_cmd is not None:
            conf["gate"] = {"rerun_cmd": rerun_cmd}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        return path

    def auto_fix(self, *args):
        return self.run_sh("scripts/auto-fix.sh", "1", *args)

    def worker_rounds(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.startswith("worker ran")]

    def ticket_ready(self, **fields):
        row = self.make_ticket("1", allowed_write_paths=["tests/*"], **fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")
        return row

    def status(self, rc, log_text, run_id="20260921-100000-1", round_no=1):
        """直接寫一輪的狀態檔 —— 要測的是 auto-fix 怎麼讀它,不是閘門怎麼寫它。"""
        log = self.write("round.log", log_text, where=self.home)
        base = self.git("rev-parse", "main").strip()
        self.run_py("scripts/status.py", "start", "--ticket", "1", "--kind", "gate",
                    "--run-id", run_id, "--base-sha", base, "--round", str(round_no),
                    "--worktree", self.repo)
        return self.run_py("scripts/status.py", "done", "--ticket", "1", "--kind",
                           "gate", "--run-id", run_id, "--rc", str(rc), "--log", log)

    def inbox_list(self):
        return self.run_py("scripts/inbox.py", "list", "--all").stdout


class WhenThereIsNothingToFix(AutoFixBase):

    def test_a_ticket_that_never_ran_says_so_instead_of_dispatching(self):
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("一輪都還沒跑過", done.stderr)
        self.assertEqual(self.worker_rounds(), [], "沒有紅榜就不該起 worker")

    def test_a_green_round_stops_and_says_review_is_not_automatic(self):
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        self.status(0, GREEN_LOG)
        done = self.auto_fix()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("覆核不自動", done.stdout)
        self.assertEqual(self.worker_rounds(), [])


class ThingsThatStopIt(AutoFixBase):

    def test_a_failed_worker_records_its_rc_and_stderr(self):
        self.set_worker(WORKER_FAILS)
        self.ticket_ready()
        self.status(1, RED_LOG)
        done = self.auto_fix()
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        agent_events = [row for row in self.events()
                        if row["kind"].startswith("agent.")]
        self.assertEqual([row["kind"] for row in agent_events],
                         ["agent.start", "agent.failed"])
        self.assertEqual(agent_events[-1]["rc"], "7")
        worker_log = os.path.join(self.repo, "reports", "t1", "20260921-100000-1",
                                  "worker-round2.log")
        with open(worker_log, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "worker stderr round 2\n")

    def test_a_red_with_no_attribution_is_not_handed_to_a_new_worker(self):
        """rc 非零卻一條紅都解析不出來,**最像「沒有紅」** —— 而派下去的 worker 會
        拿著一份空紅榜去猜,猜出來的修法會改到沒有壞的地方。

        **變異**:把 `[ "$S_FAILS" -eq 0 ]` 那一段拿掉 → 這一條紅。
        """
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        self.status(1, NO_ATTRIBUTION_LOG)
        done = self.auto_fix()
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("沒有歸因", done.stderr)
        self.assertEqual(self.worker_rounds(), [])
        self.assertEqual(self.load_ticket("1")["state"], "Blocked")
        self.assertIn("decision.asked", self.kinds())
        self.assertIn("沒有歸因", self.inbox_list())

    def test_the_round_limit_stops_it_before_it_dispatches(self):
        self.set_worker(WORKER_NEVER)
        self.ticket_ready(retry_limit=2)
        self.status(1, RED_LOG, round_no=3)
        done = self.auto_fix()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.worker_rounds(), [])
        self.assertEqual(self.load_ticket("1")["state"], "Blocked")
        self.assertEqual(self.load_ticket("1")["owner"], "main")
        self.assertIn("三輪耗盡", self.inbox_list())

    def test_an_objection_from_the_worker_becomes_a_row_on_the_ticket(self):
        """worker 說「這張票寫錯了」而東西照樣落地,那句話等於沒有人收(D-014)。

        **變異**:把 `grep -q '^OBJECTION:'` 那一段拿掉 → 這一條紅
        (反駁會變成一句沒有收件者的話,而票照樣進下一輪)。
        """
        self.set_worker(WORKER_OBJECTS)
        self.ticket_ready()
        self.status(1, RED_LOG)
        done = self.auto_fix()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual(self.worker_rounds(), ["worker ran round 2"])
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["state"], "Blocked")
        self.assertEqual(ticket["owner"], "main")
        self.assertEqual(ticket["objections"][0]["category"], "ticket-wrong")
        self.assertIn("設計文件", ticket["objections"][0]["body"])
        self.assertEqual(ticket["objections"][0]["disposition"], "",
                         "反駁一進來就是未處置 —— 未處置的阻擋項 land 與 close 都會拒絕")
        self.assertIn("decision.asked", self.kinds())
        self.assertIn("反駁", self.inbox_list())

        agent_events = [row for row in self.events()
                        if row["kind"].startswith("agent.")]
        self.assertEqual([row["kind"] for row in agent_events],
                         ["agent.start", "agent.done"])
        for row in agent_events:
            self.assertEqual(row["ticket"], "1")
            self.assertEqual(row["model"], "opus")
            self.assertEqual(row["run_id"], "20260921-100000-1")
            self.assertEqual(row["round"], "2")
            self.assertEqual(row["agent"], "auto-fix")
        self.assertEqual(agent_events[-1]["rc"], "0")
        worker_log = os.path.join(self.repo, "reports", "t1", "20260921-100000-1",
                                  "worker-round2.log")
        with open(worker_log, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "worker stdout round 2\n")


class TheDispatchPacket(AutoFixBase):

    def test_dry_run_writes_the_packet_and_does_not_start_a_worker(self):
        """派工文要**自己就夠開工**:少了 base_sha、票面快照、副本位置、輪數、
        上一輪 EVIDENCE、紅榜 excerpt,新 worker 得回頭翻對話或猜檔案位置 ——
        那一趟比整份 log 還貴(2026-09-21 外部審查)。
        """
        self.set_worker(WORKER_NEVER)
        self.ticket_ready(subject="把清單接上票的回歸")
        self.status(1, RED_LOG)
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.worker_rounds(), [])
        self.assertEqual([row for row in self.events()
                          if row["kind"].startswith("agent.")], [],
                         "dry-run 沒有起 agent,不能留下假的 agent 事件")
        where = os.path.join(self.repo, "reports", "t1", "20260921-100000-1",
                             "dispatch-round2.md")
        self.assertTrue(os.path.exists(where), done.stdout)
        with open(where, encoding="utf-8") as handle:
            packet = handle.read()
        self.assertIn("把清單接上票的回歸", packet, "票面快照")
        self.assertIn("base_sha", packet)
        self.assertIn("test_thing.T.test_thing", packet, "紅榜逐條")
        self.assertIn("第一輪是紅的", packet, "excerpt 要帶著那一條斷言")
        self.assertIn("第 2 輪", packet, "第幾輪")
        self.assertIn("patch-round2.diff", packet, "要交什麼")
        self.assertIn("OBJECTION:", packet, "票寫錯的時候怎麼說")
        self.assertIn("memory/role/implementer.md", packet, "角色卡指路")

    def test_the_packet_points_at_the_rules_pack_instead_of_pasting_everything(self):
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        self.status(1, RED_LOG)
        self.auto_fix("--dry-run")
        where = os.path.join(self.repo, "reports", "t1", "20260921-100000-1",
                             "dispatch-round2.md")
        with open(where, encoding="utf-8") as handle:
            packet = handle.read()
        self.assertIn("規則包", packet)


class TheWholeLoop(AutoFixBase):
    """一整圈:紅 → 派 worker → 套 patch → 閘門 → 綠 → 停在等覆核。

    這裡用**真的** `apply.sh` 與真的 `gate.sh`:要問的正是「這幾支接得起來嗎」。
    """

    def first_round(self):
        patch = self.write("p1.diff", RED_CASE, where=self.home)
        done = self.run_sh("scripts/apply.sh", "1", patch)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        wt = os.path.join(self.home, "repo-wt", "t1")
        # **副本裡那一支** gate.sh(不是主 repo 那一支):`--branch` 問的是
        # 「這條分支改了什麼」,而在主 repo 上問等於問 main 對 main —— 答案是「沒有」,
        # 而「沒有東西可跑」與「跑完了都過」長得一樣(§5.5)。
        gate = self.run_sh(os.path.join(wt, "scripts", "gate.sh"),
                           "--branch", "--ticket", "1", cwd=wt,
                           env=self.env(AC_ROOT=self.repo))
        self.assertNotEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        return wt

    def test_a_red_round_gets_fixed_and_stops_at_awaiting_review(self):
        self.set_worker(WORKER_FIXES)
        self.ticket_ready()
        wt = self.first_round()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("沒有設 gate.rerun_cmd", done.stderr)
        self.assertEqual(self.worker_rounds(), ["worker ran round 2"])
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "2",
                         "第二輪的修補要進同一條分支")
        self.assertIn("assertEqual(1, 1)", self.git("show", "t1:tests/test_thing.py"))
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["state"], "InReview",
                         "綠了停在等覆核 —— 覆核不自動(D-010)")
        self.assertIn("等覆核", self.inbox_list())
        self.assertNotIn("review", json.dumps(ticket.get("review") or {}),
                         "auto-fix 不准自己蓋覆核那一格")
        self.assertTrue(os.path.isdir(wt))

    def test_a_configured_rerun_command_runs_after_apply_and_emits_an_event(self):
        rerun = os.path.join(self.home, "fake-rerun.sh")
        write_executable(rerun, """#!/bin/sh
set -e
grep -q 'assertEqual(1, 1)' tests/test_thing.py
echo "rerun $AC_ROUND" >> "$AC_TEST_LOG"
""")
        self.set_worker(WORKER_FIXES, "sh %s" % rerun)
        self.ticket_ready()
        self.first_round()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.worker_rounds(), ["worker ran round 2"])
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn("rerun 2", handle.read())
        rows = [row for row in self.events() if row["kind"] == "gate.rerun"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticket"], "1")
        self.assertEqual(rows[0]["round"], "2")
        self.assertTrue(rows[0]["run_id"])

    def test_the_gate_hook_dispatches_against_the_main_repo_not_the_worktree(self):
        """`gate.sh --branch --ticket n --auto-fix` 在**副本**裡跑,而票、reports 與
        收件匣住在主 repo。照 `$0` 算根的話,這一輪的結果會寫進一個等一下就被收掉的
        目錄 —— **而且不會報錯**。

        **變異**:把 `auto-fix.sh` 的 `ROOT=${AC_ROOT:-…}` 改回只看 `$0` → 這一條紅。
        """
        self.set_worker(WORKER_FIXES)
        self.ticket_ready()
        patch = self.write("p1.diff", RED_CASE, where=self.home)
        self.assertEqual(self.run_sh("scripts/apply.sh", "1", patch).returncode, 0)
        wt = os.path.join(self.home, "repo-wt", "t1")
        done = self.run_sh(os.path.join(wt, "scripts", "gate.sh"),
                           "--branch", "--ticket", "1", "--auto-fix", cwd=wt,
                           env=self.env(AC_ROOT=self.repo))
        self.assertNotEqual(done.returncode, 0,
                            "閘門自己的 rc 不因為下一輪修好了而變綠")
        self.assertEqual(self.worker_rounds(), ["worker ran round 2"], done.stdout)
        self.assertEqual(self.load_ticket("1")["state"], "InReview")
        self.assertIn("等覆核", self.inbox_list())

    def test_three_red_rounds_end_as_blocked_and_owned_by_main(self):
        """**三輪耗盡不是一句話,是一個狀態轉換**(D-014):
        「報了」與「沒報」以前在票上長得一樣。"""
        self.set_worker(WORKER_STAYS_RED)
        self.ticket_ready(retry_limit=2)
        self.first_round()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.worker_rounds(),
                         ["worker ran round 2", "worker ran round 3"],
                         "上限是 retry_limit+1 輪,不是無止境地派下去")
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["state"], "Blocked")
        self.assertEqual(ticket["owner"], "main")
        self.assertIn("ticket.attempt.failed", self.kinds())
        self.assertIn("三輪耗盡", self.inbox_list())


if __name__ == "__main__":
    unittest.main()
