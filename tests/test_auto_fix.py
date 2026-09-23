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

import glob
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
printf '## 記憶\\nmemory.py note role implementer "auto-fix 收割" --ticket 1 --by worker@opus\\n' \\
    >> "EVIDENCE-round$AC_ROUND.md"
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

WORKER_REPORTS_TEST_DEFECT = """#!/bin/sh
set -e
echo "$AC_ROLE ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
if [ "$AC_ROLE" = worker ]; then
    printf 'OBJECTION: test_defect fixture 把正確結果寫成 2\\n' \\
        > "EVIDENCE-round$AC_ROUND.md"
    exit 0
fi
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > patch-verify.diff || true
printf '# verifier\\n案例已修\\n' > EVIDENCE-verifier.md
"""

WORKER_TEST_DEFECT_THEN_FIXES_PRODUCT = """#!/bin/sh
set -e
echo "$AC_ROLE ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
if [ "$AC_ROLE" = verifier ]; then
    sed 's/assertEqual(1, 2/assertEqual(1, 3/' base/tests/test_thing.py \\
        > work/tests/test_thing.py
    diff -ruN base work > patch-verify.diff || true
    printf '# verifier\\n案例已修但產品仍紅\\n' > EVIDENCE-verifier.md
elif [ "$AC_ROUND" = 2 ]; then
    printf 'OBJECTION: test_defect fixture 把正確結果寫成 2\\n' \\
        > "EVIDENCE-round$AC_ROUND.md"
else
    sed 's/assertEqual(1, 3/assertEqual(1, 1/' base/tests/test_thing.py \\
        > work/tests/test_thing.py
    diff -ruN base work > "patch-round$AC_ROUND.diff" || true
    printf '# worker\\n產品修復\\n' > "EVIDENCE-round$AC_ROUND.md"
fi
"""

WORKER_NEVER = """#!/bin/sh
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
exit 0
"""

WORKER_FAILS = """#!/bin/sh
echo "worker stderr round $AC_ROUND" >&2
exit 7
"""

# 假 worker:修好那條紅,並照 §8.5 在 EVIDENCE 尾端交一塊 `result`。
# `memory` 那一格是**鏡像**:這一份 EVIDENCE 裡沒有 `## 記憶` 段,所以跑完一輪之後
# 記憶收件匣一行都不該多 —— 寫入仍然只由 `memory.py harvest` 那一手做(#10)。
WORKER_WITH_RESULT = """#!/bin/sh
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
sed "s/@R@/$AC_ROUND/g; s/@T@/$AC_TICKET/g" > "EVIDENCE-round$AC_ROUND.md" <<'EV'
# 第 @R@ 輪
已排除的假設:沒有

## result

```result
{"ticket": "@T@", "role": "worker", "round": @R@, "rc": 0,
 "patch_sha256": "0f0f0f",
 "gate": {"cmd": "python3 -m unittest test_thing", "ran": 1, "rc": 0},
 "mutations": [{"id": "M1", "count": 1, "case": "T.test_thing",
                "red_first_line": "AssertionError: 1 != 2"}],
 "objection": null,
 "excluded": ["不是副本沒同步 —— base/ 與 work/ 只差那一個檔"],
 "repro": {"cmd": "python3 -m unittest test_thing", "expect": "Ran 1 test ... OK"},
 "memory": [{"layer": "role", "name": "implementer", "line": "一句原則", "ticket": "@T@"},
            {"layer": "model", "name": "opus", "line": "另一句原則", "ticket": "@T@"}]}
```
EV
"""

# 假 worker:交了 EVIDENCE **但沒有那一塊**(五段散文照舊,機器那一份忘了)。
WORKER_NO_BLOCK = """#!/bin/sh
set -e
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""

# 假 worker:那一塊在,但裡面不是 JSON(少一個右括號那一種)。
WORKER_BAD_JSON = """#!/bin/sh
set -e
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
cat > "EVIDENCE-round$AC_ROUND.md" <<'EV'
# 交了,但那一塊解不開

## result

```result
{"ticket": "1", "role": "worker", "rc": 0, 這裡少了一個引號}
```
EV
"""

# 假 worker:`OBJECTION:` 那一行說 ticket-wrong,而同一份的 block 說 test_defect。
WORKER_CONFLICTING_OBJECTION = """#!/bin/sh
set -e
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
cat > "EVIDENCE-round$AC_ROUND.md" <<'EV'
OBJECTION: ticket-wrong 驗收第二條與設計文件對不上

## result

```result
{"ticket": "1", "role": "worker", "round": 2, "rc": 1, "patch_sha256": "",
 "gate": {"cmd": "", "ran": null, "rc": null}, "mutations": [],
 "objection": {"category": "test_defect", "body": "fixture 把正確結果寫成 2"},
 "excluded": [], "repro": {"cmd": "", "expect": ""}, "memory": []}
```
EV
"""

# 假 worker:說 test_defect(走驗證者那條路);驗證者交 EVIDENCE 但不交 patch-verify。
WORKER_TEST_DEFECT_VERIFIER_RESULT = """#!/bin/sh
set -e
echo "$AC_ROLE ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
if [ "$AC_ROLE" = worker ]; then
    printf 'OBJECTION: test_defect fixture 把正確結果寫成 2\\n' \\
        > "EVIDENCE-round$AC_ROUND.md"
    exit 0
fi
cat > EVIDENCE-verifier.md <<'EV'
# verifier

## result

```result
{"ticket": "1", "role": "verifier", "round": 2, "rc": 0, "patch_sha256": "",
 "gate": {"cmd": "", "ran": null, "rc": null}, "mutations": [],
 "objection": null, "excluded": [], "repro": {"cmd": "", "expect": ""}, "memory": []}
```
EV
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

# 同一份紅,外加一行案例自己宣告的環境紅(#644 的真實字面)。手打 `auto-fix.sh` 的人
# 面對的就是這一種:`gate.sh` 已經因為這一格非空而拒絕自動派了(D-019)。
RED_LOG_DECLARED = RED_LOG.replace(
    "test_thing (test_thing.T.test_thing) ... FAIL",
    "ENVIRONMENT-SUSPECT: safari 螢幕鎖著(#474)\n"
    "test_thing (test_thing.T.test_thing) ... FAIL", 1)

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

    def first_round(self):
        patch = self.write("p1.diff", RED_CASE, where=self.home)
        done = self.run_sh("scripts/apply.sh", "1", patch)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        wt = os.path.join(self.home, "repo-wt", "t1")
        # **副本裡那一支** gate.sh(不是主 repo 那一支):`--branch` 問的是
        # 「這條分支改了什麼」,而在主 repo 上問等於問 main 對 main —— 答案是「沒有」,
        # 而「沒有東西可跑」與「跑完了都過」長得一樣(§5.5)。
        gate = self.run_sh(os.path.join(wt, "scripts", "gate.sh"),
                           "--branch", "--ticket", "1", "--no-auto-fix", cwd=wt,
                           env=self.env(AC_ROOT=self.repo))
        self.assertNotEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        return wt

    def result_files(self, name):
        return sorted(glob.glob(os.path.join(self.repo, "reports", "t1", "*", name)))

    def result_json(self, name="result-round2.json"):
        found = self.result_files(name)
        self.assertEqual(len(found), 1, "找不到(或不只一份)%s:%s" % (name, found))
        with open(found[0], encoding="utf-8") as handle:
            return json.load(handle)

    def memory_inbox_lines(self, rel="memory/role/implementer.inbox.md"):
        path = os.path.join(self.repo, rel)
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as handle:
            return len(handle.read().splitlines())


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

    def test_a_typed_dispatch_over_an_environment_suspect_warns_and_still_goes(self):
        """驗收 12(D-019):**手打就是覆寫。**

        `gate.sh` 那一側這一格非空就不自動派;而人自己打這一支的時候照派 —— 只是
        那一行覆寫要看得見。一次靜靜的拒絕會讓人以為腳本壞了,然後去改腳本
        (`docs/DISPATCH-TEMPLATE.md` §5.7:守衛要給得出下一步)。

        **變異**:拿掉 `auto-fix.sh` 那一行警告 → 這一條紅(沒有那句話);
        把它改成 `exit 0` 不派 → 這一條也紅(worker 沒被叫)。
        """
        self.set_worker(WORKER_NO_BLOCK)
        self.ticket_ready()
        self.status(1, RED_LOG_DECLARED)
        suspects = json.loads(self.read(os.path.join(
            "reports", "t1", "20260921-100000-1", "status.json")))
        self.assertEqual(len(suspects["environment_suspect"]), 1,
                         "前提沒成立:那一輪的狀態檔沒有記到宣告行")
        done = self.auto_fix()
        self.assertIn("上一輪環境可疑(safari:螢幕鎖著(#474)),你確定要派?",
                      done.stdout, done.stdout + done.stderr)
        self.assertEqual(self.worker_rounds(), ["worker ran round 2"],
                         "手打就是覆寫 —— 警告完還是要派")

    def test_a_typed_dispatch_over_a_clean_round_says_nothing_about_the_environment(self):
        """警告不准每一輪都印:一句每次都出現的警告與一句沒有人看的話一樣。

        **變異**:把 `S_ENV` 那一問改成永遠非零 → 這一條紅。
        """
        self.set_worker(WORKER_NO_BLOCK)
        self.ticket_ready()
        self.status(1, RED_LOG)
        done = self.auto_fix()
        self.assertNotIn("上一輪環境可疑", done.stdout)
        self.assertEqual(self.worker_rounds(), ["worker ran round 2"])

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

    def test_a_test_defect_dispatches_a_new_verifier_and_resumes_the_gate(self):
        self.set_worker(WORKER_REPORTS_TEST_DEFECT)
        self.ticket_ready(in_scope=["src/app.py"])
        patch = self.write("p1.diff", RED_CASE, where=self.home)
        self.assertEqual(self.run_sh("scripts/apply.sh", "1", patch).returncode, 0)
        wt = os.path.join(self.home, "repo-wt", "t1")
        gate = self.run_sh(os.path.join(wt, "scripts", "gate.sh"),
                           "--branch", "--ticket", "1", "--no-auto-fix", cwd=wt,
                           env=self.env(AC_ROOT=self.repo))
        self.assertNotEqual(gate.returncode, 0, gate.stdout + gate.stderr)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("test_defect", done.stdout)
        with open(self.log, encoding="utf-8") as handle:
            self.assertEqual(handle.read().splitlines(),
                             ["worker ran round 2", "verifier ran round 2"])
        starts = [row for row in self.events() if row["kind"] == "agent.start"]
        self.assertEqual(starts[-1]["role"], "verifier")
        self.assertEqual(starts[-1]["agent"], "auto-fix-verifier")
        ticket = self.load_ticket("1")
        objection = ticket["objections"][0]
        self.assertEqual(objection["owner"], "verifier")
        self.assertEqual(objection["disposition"], "fixed")
        self.assertIn("tests/*", ticket["allowed_write_paths"])
        packets = glob.glob(os.path.join(self.repo, "reports", "t1", "*",
                                         "dispatch-verifier-round2.md"))
        self.assertEqual(len(packets), 1)
        with open(packets[0], encoding="utf-8") as handle:
            packet = handle.read()
        for text in ("role=verifier", "test_thing.T.test_thing",
                     "fixture 把正確結果寫成 2", "tests/test_thing.py"):
            self.assertIn(text, packet)
        self.assertIn("案例已修,第 2 輪綠", self.inbox_list())

    def test_a_fixed_case_that_is_still_red_is_reported_before_the_next_round(self):
        self.set_worker(WORKER_TEST_DEFECT_THEN_FIXES_PRODUCT)
        self.ticket_ready()
        patch = self.write("p1.diff", RED_CASE, where=self.home)
        self.assertEqual(self.run_sh("scripts/apply.sh", "1", patch).returncode, 0)
        wt = os.path.join(self.home, "repo-wt", "t1")
        gate = self.run_sh(os.path.join(wt, "scripts", "gate.sh"),
                           "--branch", "--ticket", "1", "--no-auto-fix", cwd=wt,
                           env=self.env(AC_ROOT=self.repo))
        self.assertNotEqual(gate.returncode, 0, gate.stdout + gate.stderr)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(self.log, encoding="utf-8") as handle:
            self.assertEqual(handle.read().splitlines(),
                             ["worker ran round 2", "verifier ran round 2",
                              "worker ran round 3"])
        self.assertIn("案例已修,第 2 輪仍紅", self.inbox_list())


class TheEventModelIsTheOneThatActuallyRan(AutoFixBase):
    """`routing.implement` 只是路由標籤;`worker.command` 才是真的起的那個(#21)。
    `agent.start`/`agent.done`/`agent.failed` 的 `model` 欄要記後者,兩者不一致時
    還要印一行警告 —— 不然 events.jsonl 會記著一個從沒跑過的模型。

    **變異**:把 `round_once` 裡三個 `ev agent.*` 的 `--model "$WORKER_MODEL"` 改回
    `--model "$MODEL"` → 這一條紅(`row["model"]` 變回 routing 那個標籤)。
    """

    def set_worker_with_model(self, body, worker_model, routing_model):
        path = os.path.join(self.home, "fake-worker.sh")
        write_executable(path, body)
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {
            "command": "sh %s --model %s --permission-mode acceptEdits" % (
                path, worker_model),
            "timeout_seconds": 120,
        }
        conf["routing"] = dict(DEFAULT_CONFIG["routing"])
        conf["routing"]["implement"] = routing_model
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        return path

    def test_events_record_the_worker_command_model_not_the_routing_label(self):
        self.set_worker_with_model(WORKER_NEVER, "claude-opus-x", "codex:gpt-5.6-sol")
        self.ticket_ready()
        self.status(1, RED_LOG)

        done = self.auto_fix()

        self.assertIn("警告", done.stdout, done.stdout + done.stderr)
        self.assertIn("routing.implement=codex:gpt-5.6-sol", done.stdout)
        self.assertIn("claude-opus-x", done.stdout)
        agent_events = [row for row in self.events()
                        if row["kind"].startswith("agent.")]
        self.assertTrue(agent_events, "沒有 agent 事件可以查")
        for row in agent_events:
            self.assertEqual(row["model"], "claude-opus-x",
                             "events.jsonl 記著一個從沒跑過的模型 —— 這正是 #21 要擋的事")


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
        self.assertIn("auto-fix 收割", self.read("memory/role/implementer.inbox.md"))
        self.assertEqual(self.kinds().count("memory.noted"), 1)
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
echo "rerun $AC_ROUND ticket=$AC_TICKET" >> "$AC_TEST_LOG"
""")
        self.set_worker(WORKER_FIXES, "sh %s" % rerun)
        self.ticket_ready()
        self.first_round()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.worker_rounds(), ["worker ran round 2"])
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn("rerun 2 ticket=1", handle.read())
        rows = [row for row in self.events() if row["kind"] == "gate.rerun"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticket"], "1")
        self.assertEqual(rows[0]["round"], "2")
        self.assertTrue(rows[0]["run_id"])

    def test_a_configured_rerun_command_receives_the_ticket_number(self):
        self.set_worker(WORKER_FIXES,
                        'printf %s "$AC_TICKET" > "$AC_WT/seen"')
        self.ticket_ready()
        wt = self.first_round()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(os.path.join(wt, "seen"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "1")

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
                           "--branch", "--ticket", "1", cwd=wt,
                           env=self.env(AC_ROOT=self.repo))
        self.assertNotEqual(done.returncode, 0,
                            "閘門自己的 rc 不因為下一輪修好了而變綠")
        self.assertEqual(self.worker_rounds(), ["worker ran round 2"], done.stdout)
        self.assertEqual(self.load_ticket("1")["state"], "InReview")
        self.assertIn("等覆核", self.inbox_list())

    def test_no_auto_fix_leaves_the_red_round_for_a_human(self):
        self.set_worker(WORKER_FIXES)
        self.ticket_ready()
        patch = self.write("p1.diff", RED_CASE, where=self.home)
        self.assertEqual(self.run_sh("scripts/apply.sh", "1", patch).returncode, 0)
        wt = os.path.join(self.home, "repo-wt", "t1")

        done = self.run_sh(os.path.join(wt, "scripts", "gate.sh"),
                           "--branch", "--ticket", "1", "--no-auto-fix", cwd=wt,
                           env=self.env(AC_ROOT=self.repo))

        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.worker_rounds(), [])

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


class TheResultBlockAtTheEndOfEvidence(AutoFixBase):
    """EVIDENCE 尾端那一塊 `result`,由這一支在**收 patch 的同一手**抽成
    `reports/t<票號>/<run_id>/result-round<輪>.json`(D-017,#20)。

    以前機器讀得懂的只有一行 `OBJECTION:` 與一個退出碼 —— **停下來的理由沒有一格
    寫得下**,而看板只能印「worker 沒交結構化輸出」。所以這一組問四件事:
    1. 交了那一塊會不會**真的**落在 `status.json` 隔壁,而且十一個鍵一個不少;
    2. **三種缺漏有沒有三種樣子** —— 揉成同一個空檔的那一刻,「沒交」與「交了但都是
       空的」長得一樣(§5.5);
    3. 反駁**不開第二條路**:`OBJECTION:` 那一行照舊說了算,分岔只留一格 `conflict`;
    4. `memory` 那一格是**鏡像不是入口** —— 抽它不會把同一句記憶寫第二次(#10)。
    """

    KEYS = ("ticket", "role", "round", "rc", "patch_sha256", "gate",
            "mutations", "objection", "excluded", "repro", "memory")

    def test_a_shipped_block_lands_next_to_the_status_file_of_that_round(self):
        """**變異**:把 `round_once` 裡 `harvest_result` 那一行拿掉 → 這一條紅。"""
        self.set_worker(WORKER_WITH_RESULT)
        self.ticket_ready()
        self.first_round()

        done = self.auto_fix()

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        found = self.result_files("result-round2.json")
        self.assertEqual(len(found), 1, done.stdout)
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(found[0]),
                                                    "status.json")),
                        "抽出來的要與那一輪的 status.json 同目錄 —— 讀的人已經在那裡了")
        data = self.result_json()
        for key in self.KEYS:
            self.assertIn(key, data, key)
        self.assertEqual(data["ticket"], "1")
        self.assertEqual(data["round"], 2, "第幾輪要對得上這一輪")
        self.assertEqual(data["role"], "worker")
        self.assertIs(data["present"], True)
        self.assertIs(data["conflict"], False)
        # 看板(#19)讀的就是這三格。
        self.assertEqual(data["rc"], 0)
        self.assertEqual(data["gate"]["ran"], 1)
        self.assertEqual(len(data["mutations"]), 1)

    def test_harvesting_the_memory_mirror_does_not_write_it_a_second_time(self):
        """`memory[]` 只是同一份記憶的鏡像,這一手**只讀不寫** —— 真正的寫入是
        `apply.sh` 叫的那一支 `memory.py harvest`(#10)。

        **變異**:讓 `harvest_result` 順手把 `memory[]` 餵給 `memory.py note`
        → 這一條紅(收件匣多兩行、`memory.noted` 多兩筆)。
        """
        self.set_worker(WORKER_WITH_RESULT)
        self.ticket_ready()
        self.first_round()
        before = self.memory_inbox_lines()

        done = self.auto_fix()

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(self.result_json()["memory"]), 2,
                         "鏡像那一格本身要抽得出來")
        self.assertEqual(self.memory_inbox_lines(), before,
                         "記憶收件匣的行數不准因為這一手而變多")
        self.assertEqual(self.kinds().count("memory.noted"), 0)

    def test_no_evidence_at_all_leaves_a_no_evidence_trace(self):
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        self.status(1, RED_LOG)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        data = self.result_json()
        self.assertIs(data["present"], False)
        self.assertEqual(data["reason"], "no-evidence")
        self.assertEqual(data["round"], 2)

    def test_an_evidence_without_the_block_says_so_in_its_own_words(self):
        """**變異**:把 `no-block` 那一支改成與 `no-evidence` 同一個 reason
        → 這一條紅(兩種缺漏會變成同一句話)。
        """
        self.set_worker(WORKER_NO_BLOCK)
        self.ticket_ready()
        self.status(1, RED_LOG)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        data = self.result_json()
        self.assertIs(data["present"], False)
        self.assertEqual(data["reason"], "no-block")
        self.assertNotEqual(data["reason"], "no-evidence",
                            "交了 EVIDENCE 卻忘了那一塊,與根本沒交不是同一件事")
        self.assertNotIn("raw", data, "沒有那一塊就沒有原文可留")

    def test_a_block_that_will_not_parse_keeps_the_first_500_characters(self):
        self.set_worker(WORKER_BAD_JSON)
        self.ticket_ready()
        self.status(1, RED_LOG)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        data = self.result_json()
        self.assertIs(data["present"], False)
        self.assertEqual(data["reason"], "bad-json")
        self.assertIn("這裡少了一個引號", data["raw"],
                      "解不開的那一份要留得下原文,不然沒有人知道它長什麼樣")
        self.assertLessEqual(len(data["raw"]), 500)

    def test_the_objection_line_still_wins_and_the_disagreement_is_recorded(self):
        """反駁**不另開第二條路**:收件、轉 Blocked、退出碼 3 一個字不改,
        兩個來源對不上只多一格 `conflict`。

        **變異**:把 `conflict` 那一行改成永遠 `False` → 這一條紅。
        """
        self.set_worker(WORKER_CONFLICTING_OBJECTION)
        self.ticket_ready()
        self.status(1, RED_LOG)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(len(ticket["objections"]), 1, "只准多一筆")
        self.assertEqual(ticket["objections"][0]["category"], "ticket-wrong",
                         "以 OBJECTION: 那一行為準")
        self.assertEqual(ticket["state"], "Blocked")
        data = self.result_json()
        self.assertIs(data["conflict"], True)
        self.assertEqual(data["objection"]["category"], "test_defect",
                         "block 裡那一句照實留著 —— 分岔要看得見,不是被蓋掉")

    def test_the_verifier_path_writes_a_file_of_its_own_name(self):
        """驗證者那條路的檔名不一樣(`result-verifier-round<輪>.json`),而且**在
        「沒交 patch-verify 就回去」之前**就寫 —— 沒交的那一次正是最需要痕跡的那一次。
        """
        self.set_worker(WORKER_TEST_DEFECT_VERIFIER_RESULT)
        self.ticket_ready()
        self.status(1, RED_LOG)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        verifier = self.result_json("result-verifier-round2.json")
        self.assertEqual(verifier["role"], "verifier")
        self.assertIs(verifier["present"], True)
        worker = self.result_json("result-round2.json")
        self.assertEqual(worker["reason"], "no-block",
                         "worker 只交了一行反駁,那一份也要留得下痕跡")


if __name__ == "__main__":
    unittest.main()
