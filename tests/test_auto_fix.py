"""`scripts/auto-fix.sh`:回歸紅了自動派**新** worker(D-010、D-015)。

使用者的原話(D-010):「如果 regression 打出 bug 就找新 worker 輸入票跟 diff 跟 bug,
讓他直接去解,這樣這過程也不需要你來叫他」「新 worker 覺得 issue 真的有問題再直接問你」。

所以這一組問四件事:
1. 紅了會不會**真的**起一個新 worker,而且它拿到的那一份夠不夠開工(交接包);
2. 綠了會不會**轉 InReview 並把覆核交給 `review.sh`**(#42,D-025 ②;`--no-review` 才停在
   InReview)—— auto-fix 自己不蓋覆核那一格,也不發頁(D-032:只寫事件);
3. 三種停下來(反駁 / 三輪耗盡 / 沒有歸因)會不會**留下票的狀態轉換 + 一頁 decision**,
   而不是印一行就算;
4. **不該派的時候不派** —— 尤其是「rc 非零卻一條紅都解析不出來」那一種,它看起來
   最像沒紅,而派下去的 worker 會拿著空紅榜去猜。

worker 用一支假的可執行檔(`board/config.json` 的 `worker.command`)。**該替換的是代價,
不是語意**:真的 headless 模型要錢要時間,而這一組要問的是腳本怎麼接它的產出。
"""

import glob
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import (DEFAULT_CONFIG, REVIEWER_PASS, Sandbox,  # noqa: E402
                             envelope, write_executable)

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

# 同一條紅,案例方法多一行 docstring —— unittest 把它當**說明行**印在 `FAIL:` 標頭下(G16)。
RED_CASE_WITH_DOCSTRING = """diff -ruN base/tests/test_thing.py work/tests/test_thing.py
--- base/tests/test_thing.py\t1970-01-01 08:00:00
+++ work/tests/test_thing.py\t2026-09-21 10:00:00
@@ -0,0 +1,7 @@
+import unittest
+
+
+class T(unittest.TestCase):
+    def test_thing(self):
+        \"\"\"A5 說明行\"\"\"
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

# 假 worker(第 1 輪,#40):先把**跑的那一刻**看到的事實寫進標記檔(票的 state、副本
# base/ 裡的 README、自己的副本路徑),再交一份會綠的 patch。票檔與標記檔的路徑由測試
# 填進來(@TICKET@ / @MARK@),不靠被測腳本傳的環境變數去找 —— 讀回來的才是事實。
WORKER_FIRST_ROUND = """#!/bin/sh
set -e
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
python3 - "@TICKET@" "@MARK@" "$AC_WORK" <<'PY'
import json, sys
ticket, mark, work = sys.argv[1:4]
with open(ticket, encoding="utf-8") as handle:
    state = json.load(handle).get("state")
with open("base/README", encoding="utf-8") as handle:
    readme = handle.read()
with open(mark, "w", encoding="utf-8") as out:
    json.dump({"state": state, "base_readme": readme, "work": work}, out,
              ensure_ascii=False)
PY
mkdir -p work/tests
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""

# 假 worker(#49):第 1 輪交一份會綠的 patch;`@ENVELOPE@` 換成一行信封時,照真的
# `claude -p --output-format json` 把它印在 stdout,**之後**再往 stderr 寫一行 ——
# log 是兩者混寫的,最後一行不一定是信封。
WORKER_ROUND_ONE_COSTS = """#!/bin/sh
set -e
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
mkdir -p work/tests
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
echo '@ENVELOPE@'
echo "worker stderr after the envelope" >&2
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

    def inbox_rows(self):
        path = os.path.join(self.repo, "reports", "inbox", "index.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def went_in_review(self):
        return any(row["kind"] == "ticket.state" and row.get("field") == "state"
                   and str(row.get("to")).strip('"') == "InReview"
                   for row in self.events())

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

    def test_a_ready_ticket_that_never_ran_starts_round_one_instead_of_stopping(self):
        """#40 A1:這一條以前斷言 rc=2「一輪都還沒跑過」—— 那正是 C1 要拆掉的門檻。

        **Ready 且沒有狀態檔**現在起第 1 輪;這個 worker 什麼都沒交,所以照既有的路
        停在 rc=5(沒交出可用的 patch),而不是在門口就 rc=2。

        **變異 M1**:把「無狀態檔 ⇒ r=1」那條分支拿掉(退回 exit 2)→ 這一條紅。
        """
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertNotIn("一輪都還沒跑過", done.stderr)
        self.assertEqual(self.worker_rounds(), ["worker ran round 1"])
        self.assertEqual(len(self.result_files("dispatch-round1.md")), 1,
                         "第 1 輪的派工文要落在這一輪的 reports 目錄裡")

    def test_a_green_round_stops_and_points_at_review_sh(self):
        """上一輪已經綠了:沒有東西要修。覆核不在這裡觸發(#42 的觸發點是「第 r 輪綠」
        與「手跑閘門綠」),但下一步要說得出是哪一支。"""
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        self.status(0, GREEN_LOG)
        done = self.auto_fix()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("sh scripts/review.sh 1", done.stdout)
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
        # 綠了之後 review.sh 還會起一個 reviewer(#42)—— 這一條問的是 auto-fix 自己最後派的那一個。
        starts = [row for row in self.events() if row["kind"] == "agent.start"
                  and row.get("role") != "reviewer"]
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
        self.assertEqual(self.inbox_rows(), [], "綠了只寫事件,不發頁(D-032)")
        self.assertTrue(self.went_in_review())

    def test_a_red_case_with_a_docstring_still_names_its_file_to_the_verifier(self):
        """#34 A5:有 docstring 的案例紅了,閘門寫的 `status.json` 要有 `file`,驗證者
        派工文才不會退成「紅榜沒有 file;從 case 名定位」(G16)。

        **變異**:`status.parse_failures` 裡 `if opened:` 換成 `if opened or body:`
        → 這一條紅。
        """
        self.set_worker(WORKER_REPORTS_TEST_DEFECT)
        self.ticket_ready(in_scope=["src/app.py"])
        patch = self.write("p1.diff", RED_CASE_WITH_DOCSTRING, where=self.home)
        self.assertEqual(self.run_sh("scripts/apply.sh", "1", patch).returncode, 0)
        wt = os.path.join(self.home, "repo-wt", "t1")
        gate = self.run_sh(os.path.join(wt, "scripts", "gate.sh"),
                           "--branch", "--ticket", "1", "--no-auto-fix", cwd=wt,
                           env=self.env(AC_ROOT=self.repo))
        self.assertNotEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        rows = [row for path in glob.glob(os.path.join(self.repo, "reports", "t1", "*",
                                                        "status.json"))
                for row in json.load(open(path, encoding="utf-8")).get("failures") or []]
        self.assertEqual([row["case"] for row in rows], ["test_thing.T.test_thing"], rows)
        self.assertTrue(rows[0]["file"].endswith("tests/test_thing.py"), rows[0])

        self.auto_fix()

        packets = glob.glob(os.path.join(self.repo, "reports", "t1", "*",
                                         "dispatch-verifier-round2.md"))
        self.assertEqual(len(packets), 1)
        with open(packets[0], encoding="utf-8") as handle:
            packet = handle.read()
        self.assertNotIn("紅榜沒有 file", packet)
        self.assertIn("tests/test_thing.py", packet)

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


class TheFirstRoundPacket(AutoFixBase):
    """#29 A3 / G3:第 1 輪的派工文**也由工具產**。

    以前第 1 輪是主線手寫、只有 `docs/DISPATCH-TEMPLATE.md` §8 的散文可抄,第 2 輪起
    `auto-fix.sh` 才產 `dispatch-round<r>.md` —— 同一個角色的兩輪因此拿到兩種形狀的
    派工文,而**少了哪一格沒有人看得出來**。
    """

    def fix_dir(self, round_no=1):
        return os.path.join(self.home, "repo-wt", "fix-t1", "round%d" % round_no)

    def test_round_one_prints_a_packet_before_any_gate_has_run(self):
        """**變異**:把 `--round 1` 那一段分支拿掉 → 這一條紅(#40 起退回 `round_once 1` 的
        dry-run,印的不再是派工文本身)。"""
        self.set_worker(WORKER_NEVER)
        self.ticket_ready(subject="把清單接上票的回歸")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "reports", "t1")),
                         "這一條問的正是「還沒有 status.json 的時候」")
        done = self.auto_fix("--dry-run", "--round", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("# 規則包:worker"),
                        "第一行要是規則包的首行:" + done.stdout[:120])
        self.assertIn("#1", done.stdout, "票號")
        self.assertIn("把清單接上票的回歸", done.stdout, "票面快照")
        self.assertIn("base sha", done.stdout)
        self.assertIn(self.fix_dir(), done.stdout, "副本路徑")
        self.assertIn("patch-round1.diff", done.stdout, "要交什麼")
        self.assertIn("OBJECTION:", done.stdout, "票寫錯的時候怎麼說")
        self.assertIn("前景跑", done.stdout, "G14:丟背景就結束回合 = 什麼都沒交")
        self.assertEqual(self.worker_rounds(), [], "第 1 輪不由這一支起 worker")

    def test_the_packet_is_also_written_where_the_second_round_would_go(self):
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        self.auto_fix("--dry-run", "--round", "1")
        found = glob.glob(os.path.join(self.repo, "reports", "t1", "*",
                                       "dispatch-round1.md"))
        self.assertEqual(len(found), 1, found)

    def test_a_round_number_it_does_not_support_is_refused_by_name(self):
        """「不認得 X」只說了它不是什麼(§5.7)。"""
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        done = self.auto_fix("--round", "4")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("--round 只接 1", done.stderr)

    def test_without_round_one_it_still_says_where_to_start(self):
        """沒有狀態檔又沒給 `--round 1` 時,那一句要**指得到兩條路**。

        #40 之後 Ready 的票不帶 `--round` 就起第 1 輪,會停在門口的只剩**不是 Ready**
        的票 —— 所以這裡拿一張 Draft 票問同一件事。
        """
        self.set_worker(WORKER_NEVER)
        self.ticket_ready(state="Draft")
        done = self.auto_fix()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("--round 1", done.stderr)


class TheFirstRoundStartsFromReady(AutoFixBase):
    """#40 / D-025 C1:票 **Ready 且沒有狀態檔** ⇒ `auto-fix.sh <n>` 自己起第 1 輪。

    以前這一格是 exit 2「一輪都還沒跑過」,主線要自己派 worker、apply、帶
    `AC_TICKETS_DIR` 跑閘門三下 —— 而第 2 輪起那一條 `round_once` 早就全都會做。
    這一組問的是**同一條 `round_once` 接得住第 1 輪**:派工文、副本的 base、事件、
    票的狀態、收件匣,一格都不少;以及**不是 Ready 的票不猜**。
    """

    def fix_dir(self):
        return os.path.join(self.home, "repo-wt", "fix-t1", "round1")

    def mark(self):
        return os.path.join(self.home, "worker-saw.json")

    def worker_saw(self):
        with open(self.mark(), encoding="utf-8") as handle:
            return json.load(handle)

    def ready_and_main_moved_on(self):
        """一張 Ready 票,而且主線在開票之後又往前走了一個 commit(README 變了)——
        副本 base 取的是票的 base_sha 還是主線的頭,只有這樣才分得出來。"""
        self.set_worker(WORKER_FIRST_ROUND
                        .replace("@TICKET@", os.path.join(self.repo, "tickets", "1.json"))
                        .replace("@MARK@", self.mark()))
        row = self.ticket_ready(subject="第 1 輪由 auto-fix 起")
        self.write("README", "主線在開票之後又走了一步\n")
        self.git("commit", "-q", "-am", "主線往前走")
        self.git("push", "-q", "origin", "main")
        return row

    def test_a_ready_ticket_with_no_status_file_starts_round_one(self):
        """A1:不再 rc=2;派工文與 `--dry-run --round 1` 給人看的是**同一份**;副本在
        `<WTBASE>/fix-t1/round1`,`base/` 是票的 base_sha 那一版(分支 t1 還不存在)。

        **變異 M1**:把「無狀態檔 ⇒ r=1」那條分支拿掉(退回 exit 2)→ 紅。
        **變異 M5**:`round_once` 第 1 輪改用第 2 輪起那一份派工文 → 派工文那一段紅。
        **變異 M6**:`S_BASE` 改取主線的頭 → `base/` 那一段紅。
        **變異 M9**:拿掉「沒有歸因」那一格的 `S_RUN` 前提 → 紅(空紅榜被讀成 rc=4)。
        """
        row = self.ready_and_main_moved_on()
        self.assertFalse(os.path.exists(os.path.join(self.repo, "reports", "t1")),
                         "前提:這張票一個狀態檔都沒有")
        self.assertEqual(self.git("branch", "--list", "t1").strip(), "",
                         "前提:分支 t1 還不存在")
        shown = self.auto_fix("--dry-run", "--round", "1")
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("一輪都還沒跑過", done.stderr)
        self.assertEqual(self.worker_rounds(), ["worker ran round 1"])
        sent = [path for path in self.result_files("dispatch-round1.md")
                if os.path.exists(os.path.join(os.path.dirname(path), "worker-round1.log"))]
        self.assertEqual(len(sent), 1, "餵給 worker 的第 1 輪派工文:%s"
                         % self.result_files("dispatch-round1.md"))
        with open(sent[0], encoding="utf-8") as handle:
            packet = handle.read()
        self.assertTrue(packet.startswith("# 規則包:worker"), packet[:120])
        self.assertIn("第 1 輪(第一次實作)", packet)
        self.assertIn(self.fix_dir(), packet, "副本路徑")
        self.assertEqual(packet, shown.stdout,
                         "worker 拿到的要與 --dry-run --round 1 給人看的是同一份")

        saw = self.worker_saw()
        self.assertEqual(saw["work"], self.fix_dir())
        expected = self.git("show", "%s:README" % row["base_sha"])
        self.assertNotEqual(expected, self.git("show", "main:README"),
                            "前提:主線已經離開票的 base_sha")
        self.assertEqual(saw["base_readme"], expected,
                         "副本 base/ 要是票的 base_sha 那一版,不是主線的頭")

    def test_round_one_is_announced_runs_as_running_and_stops_awaiting_review(self):
        """A2:`ticket.attempt.start attempt=1` 由 auto-fix 發(只發一次);worker 跑的
        那一刻票是 Running;綠了轉 InReview —— 只有 ticket.state 事件,收件匣 0 頁(D-032)。

        **變異 M4**:拿掉 `round_once` 裡轉 Running 那一手 → 標記檔記的是 Ready,紅。
        **變異**:綠了照發頁 → 「0 頁」紅。
        """
        self.ready_and_main_moved_on()

        done = self.auto_fix()

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        starts = [row for row in self.events() if row["kind"] == "ticket.attempt.start"]
        self.assertEqual(len(starts), 1, starts)
        self.assertEqual(str(starts[0].get("attempt")), "1", starts[0])
        self.assertIn("auto-fix", starts[0].get("note") or "", "發出者要是 auto-fix")
        self.assertEqual(self.worker_saw()["state"], "Running",
                         "worker 跑的那一刻票要是 Running")
        self.assertEqual(self.load_ticket("1")["state"], "InReview")
        self.assertEqual(self.inbox_rows(), [])
        self.assertTrue(self.went_in_review(), "events.jsonl 要有 ticket.state → InReview")

    def test_a_ticket_that_is_not_ready_is_stopped_by_its_state(self):
        """A3:沒有狀態檔、票不是 Ready ⇒ 指名 state 停下 rc=2,不起 worker、不動票。

        **變異 M2**:拿掉 state 檢查 → 紅(Draft 票也起了第 1 輪)。
        """
        self.set_worker(WORKER_NEVER)
        for state in ("Draft", "Running", "Blocked"):
            with self.subTest(state=state):
                self.make_ticket("1", allowed_write_paths=["tests/*"], state=state)
                done = self.auto_fix()
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertIn("state=%s" % state, done.stderr)
                self.assertEqual(self.worker_rounds(), [])
                self.assertEqual(self.load_ticket("1")["state"], state, "停下來就不動票")
                self.assertNotIn("ticket.attempt.start", self.kinds())

    def test_a_dry_run_of_a_ready_ticket_does_not_touch_it(self):
        """A3 的另一半:`--dry-run` 不起 worker、不動票、不發 attempt.start。

        **變異 M7**:把轉 Running 那一手搬到 `--dry-run` 那一格之前 → 紅(票變 Running)。
        """
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.worker_rounds(), [])
        self.assertEqual(self.load_ticket("1")["state"], "Ready")
        self.assertNotIn("ticket.attempt.start", self.kinds())
        self.assertEqual(len(self.result_files("dispatch-round1.md")), 1)


class EveryRoundCostsARowAndClosesItsAttempt(AutoFixBase):
    """#49 A4 / A7:每一輪派工在票的 `cost[]` 記一筆(role=worker),每一個
    `ticket.attempt.start` 都有配對的 done / failed —— heartbeat.sh 按 attempt 配對。
    期望的 token 數是這裡寫死的,不從 ticket.py 算回去。"""

    def worker_with(self, line):
        self.set_worker(WORKER_ROUND_ONE_COSTS.replace("@ENVELOPE@", line))
        self.ticket_ready()

    def attempts(self, kind):
        return [row for row in self.events() if row["kind"] == kind]

    def test_a_green_round_writes_exactly_one_worker_row_from_the_envelope(self):
        """**變異**:拿掉 round_once 裡 `write_cost worker` 那一行 → 「恰 1 筆」紅。"""
        self.worker_with(envelope("done", input_tokens=21, output_tokens=4242,
                                  cache_write=1000, cache_read=50000, duration_ms=9000))
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.load_ticket("1").get("cost") or []
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual((row["role"], row["round"], row["model"], row["by"]),
                         ("worker", 1, "opus", "auto-fix.sh"))
        self.assertEqual(row["tokens_out"], 4242)
        self.assertEqual(row["tokens_in"], 21)
        self.assertEqual(row["cache_write"], 1000)
        self.assertEqual(row["cache_read"], 50000)
        self.assertIsInstance(row["wall_seconds"], int,
                              "wall_seconds 是 auto-fix 自己量的派出前後差")

    def test_a_worker_that_prints_no_envelope_still_costs_one_row_of_nulls(self):
        self.worker_with("not an envelope")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.load_ticket("1").get("cost") or []
        self.assertEqual(len(rows), 1, rows)
        self.assertIsNone(rows[0]["tokens_out"])
        self.assertIsInstance(rows[0]["wall_seconds"], int)

    def test_a_round_whose_gate_ran_is_done_with_the_same_attempt(self):
        """A7。**變異**:拿掉 `ev ticket.attempt.done` 那一行 → 這一條紅。"""
        self.worker_with("not an envelope")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        starts = self.attempts("ticket.attempt.start")
        dones = self.attempts("ticket.attempt.done")
        self.assertEqual(len(starts), 1, starts)
        self.assertEqual(len(dones), 1, dones)
        self.assertEqual(str(dones[0].get("attempt")), str(starts[0].get("attempt")))
        self.assertEqual(dones[0].get("rc"), "0")
        self.assertEqual(self.attempts("ticket.attempt.failed"), [])

    def test_a_worker_that_hands_in_no_patch_fails_its_attempt(self):
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        failed = self.attempts("ticket.attempt.failed")
        self.assertEqual([(str(row.get("attempt")), row.get("reason")) for row in failed],
                         [("1", "no-patch")])
        self.assertEqual(self.attempts("ticket.attempt.done"), [])
        self.assertEqual(len(self.load_ticket("1").get("cost") or []), 1,
                         "沒交 patch 也派過一次,成本照記")

    def test_an_objection_fails_its_attempt_by_that_name(self):
        self.set_worker(WORKER_OBJECTS)
        self.ticket_ready()
        self.status(1, RED_LOG)
        done = self.auto_fix()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        failed = self.attempts("ticket.attempt.failed")
        self.assertEqual([(str(row.get("attempt")), row.get("reason")) for row in failed],
                         [("2", "objection")])


class WhichRoundIsTheLatestOne(AutoFixBase):
    """#29 第 4 輪:「最新一輪」不准靠同一秒的運氣。

    `run_id` 是 `<YYYYMMDD-HHMMSS>-<pid>`,而 `sorted()` 比整個字串 —— 第二段因此按
    **十進位字面**排:`…-99993` 排在 `…-100017` 後面(`9` > `1`)。同一秒裡誰算
    「最新」於是由 pid 的位數決定。

    🩸 實測(#29 第 3 輪的閘門):`apply` 與 `gate` 落在同一秒,`apply` 那一筆
    (rc=0、紅 0 條)排到最後,auto-fix 讀成「上一輪是綠的」就不派下一輪,而樹是紅的。
    **同一份 code 在別台機器上是綠的** —— 那正是這一類 bug 最貴的地方。
    """

    # 同一秒,而且 apply 那一筆的 pid **字串**排在 gate 後面(`9…` > `1…`)。
    SECOND = "20260921-100000"
    GATE_RUN = SECOND + "-100017"
    APPLY_RUN = SECOND + "-99993"

    def apply_run(self, run_id, rc=0):
        """一筆 `kind=apply` 的狀態:一條紅都沒有 —— rc=0 說的是「patch 套上了」,
        rc=6 說的是「worker 提了反駁」,兩個都不是「測試過了」。"""
        base = self.git("rev-parse", "main").strip()
        self.run_py("scripts/status.py", "start", "--ticket", "1", "--kind", "apply",
                    "--run-id", run_id, "--base-sha", base, "--round", "1",
                    "--worktree", self.repo)
        return self.run_py("scripts/status.py", "done", "--ticket", "1",
                           "--kind", "apply", "--run-id", run_id, "--rc", str(rc),
                           "--note", "套好並 commit")

    def test_an_apply_run_in_the_same_second_does_not_hide_the_red_gate(self):
        """**變異**:把 `run_key()` 換回 `status.latest_run(root, ident)` → 這一條紅
        (auto-fix 會說「上一輪是綠的」而不派)。"""
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        self.status(1, RED_LOG, run_id=self.GATE_RUN)
        self.apply_run(self.APPLY_RUN)

        # 先證明**字典序真的會挑錯**,不然這一條在排序沒問題的機器上是空跑的(§5.5)。
        self.assertEqual(sorted([self.GATE_RUN, self.APPLY_RUN])[-1], self.APPLY_RUN,
                         "夾具沒有重現那個排序,這一條什麼都沒問到")

        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("%s(gate)" % self.GATE_RUN, done.stdout,
                      "挑到的不是帶判決的那一筆:" + done.stdout)
        self.assertNotIn("上一輪是綠的", done.stdout,
                         "apply 的 rc=0 被當成「測試過了」")
        self.assertTrue(
            os.path.exists(os.path.join(self.repo, "reports", "t1", self.GATE_RUN,
                                        "dispatch-round2.md")),
            "派工文要落在 gate 那一輪的目錄裡:" + done.stdout)

    GATE_EARLIER = "20260921-100000-100017"
    APPLY_LATER = "20260921-100005-100018"

    def gate_dir_packet(self):
        where = os.path.join(self.repo, "reports", "t1", self.GATE_EARLIER,
                             "dispatch-round2.md")
        self.assertTrue(os.path.exists(where), "派工文要落在 gate 那一輪的目錄裡")
        with open(where, encoding="utf-8") as handle:
            return handle.read()

    def test_an_apply_run_seconds_later_does_not_hide_the_red_gate(self):
        """🩸 **跨秒**(#34 A1):同秒才看種類的那一版,晚 5 秒的 apply(rc=0)就蓋掉紅
        gate,auto-fix 說「沒有東西要修」exit 0。判紅不紅只看判決;apply 晚於最後一次
        判決 = 未驗,不是綠。

        **變異**:`run_key()` 回傳值拿掉第一格(有沒有判決)→ 這一條紅。
        """
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        self.status(1, RED_LOG, run_id=self.GATE_EARLIER)
        self.apply_run(self.APPLY_LATER)
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("沒有東西要修", done.stdout, "apply 的 rc=0 被當成「測試過了」")
        self.assertIn("%s(gate)" % self.GATE_EARLIER, done.stdout, done.stdout)
        self.assertIn("晚於最後一次判決", done.stderr, done.stderr)
        self.assertIn(self.APPLY_LATER, done.stderr, "要指名是哪一筆沒判決的蓋在上面")
        packet = self.gate_dir_packet()
        self.assertIn("test_thing.T.test_thing", packet, "紅榜要有 gate 那一條")
        self.assertIn("第一輪是紅的", packet)

    def test_an_objection_apply_seconds_later_is_not_read_as_unattributed(self):
        """#34 A2:apply rc=6(OBJECTION)、紅 0 條,晚於紅 gate —— 讀成最新一輪就是
        「rc 非零卻一條紅都沒有」,走「沒有歸因」exit 4。仍以 gate 為準。

        **變異**:同上一條 → 這一條紅(exit 4)。
        """
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        self.status(1, RED_LOG, run_id=self.GATE_EARLIER)
        self.apply_run(self.APPLY_LATER, rc=6)
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("沒有歸因", done.stdout + done.stderr)
        self.assertIn("%s(gate)" % self.GATE_EARLIER, done.stdout, done.stdout)
        self.assertIn("test_thing.T.test_thing", self.gate_dir_packet())


class RunFromInsideATicketWorktree(AutoFixBase):
    """#29 A10(G10 的另一半):票分支的 worktree **不是**控制根。

    `_ac_root()` 往上找 `board/config.json`,在 worktree 裡找到的是 worktree 自己 ——
    於是票檔要在**那一條分支上進了版控**才找得到,而票檔是走 docs 通道進主線的,常常
    還沒進去。#23 第 2 輪就是這樣 rc=2 停掉的。
    """

    def test_a_ticket_that_is_not_in_version_control_yet_is_still_found(self):
        """**變異**:把 `TF` 那一段 `--git-common-dir` 的退路拿掉 → 這一條紅(rc=2 找不到票)。"""
        self.set_worker(WORKER_NEVER)
        self.make_ticket("1", allowed_write_paths=["tests/*"])   # **故意不 commit**
        wt = self.worktree("t1")
        self.assertFalse(os.path.exists(os.path.join(wt, "tickets", "1.json")),
                         "這一條問的正是「票檔還沒進版控」")
        done = self.run_sh(os.path.join(wt, "scripts", "auto-fix.sh"),
                           "1", "--dry-run", "--round", "1", cwd=wt)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("找不到票", done.stderr)
        self.assertIn("是 worktree", done.stdout, "改用主 repo 這件事要說出來")
        self.assertIn("#1", done.stdout)
        found = glob.glob(os.path.join(self.repo, "reports", "t1", "*",
                                       "dispatch-round1.md"))
        self.assertEqual(len(found), 1,
                         "reports 要落在主 repo,不是等一下會被收掉的 worktree:%s" % found)


class TheCopyRootComesFromTheMainRepo(AutoFixBase):
    """#38(G15):副本根 `WTBASE` 的相對 `worktree_dir` 一律以**主 repo 根**解析 ——
    主 repo 根只有一種定義:`git rev-parse --git-common-dir` 的上一層。

    以前用 `$ROOT` 拼,而在票分支的 worktree 裡 `$ROOT` 是 worktree 自己:副本於是
    **巢狀**開到 `x-wt/x-wt/` 底下(9/23 實際開在 `agent-control-wt/agent-control-wt/`)。
    上層不存在時 `cd` 失敗、前綴變成空字串,副本根更會算成檔案系統根下的 `/x-wt`。

    期望路徑一律由這裡從夾具的主 repo 路徑拼,不問被測腳本。比較前兩邊都過
    `realpath`:worktree 裡的 `--git-common-dir` 是 git 給的實體路徑(macOS 的
    `/var` → `/private/var`),而夾具的 `self.home` 是邏輯路徑。
    """

    def config_with(self, worktree_dir):
        self.set_worker(WORKER_NEVER)
        conf = json.loads(self.read("board/config.json"))
        conf["worktree_dir"] = worktree_dir
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def ticket_worktree(self):
        """真實的形狀:票 worktree 自己就住在 `worktree_dir` 底下。"""
        path = os.path.join(self.home, "x-wt", "t1")
        self.git("worktree", "add", "-q", "-b", "t1", path, "main")
        return path

    def copy_root_in(self, done):
        found = re.search(r"副本路徑\*\*:`([^`]+)/work`", done.stdout)
        self.assertIsNotNone(found, "派工文裡找不到副本路徑:" + done.stdout[-1500:] + done.stderr)
        return found.group(1)

    def expected(self):
        return os.path.realpath(os.path.join(self.home, "x-wt", "fix-t1", "round1"))

    def test_a_committed_ticket_run_from_its_worktree_does_not_nest_the_copy(self):
        """A1:票檔已在分支上,在票 worktree 裡跑 —— 副本不得巢狀成 `x-wt/x-wt`。

        **變異**:`WTBASE=$MAINROOT/$WTBASE` 改回 `$ROOT/$WTBASE` → 這一條紅。
        """
        self.config_with("../x-wt")
        self.ticket_ready()
        wt = self.ticket_worktree()
        self.assertTrue(os.path.isfile(os.path.join(wt, "tickets", "1.json")),
                        "這一條問的是「票檔已經在分支上」—— 走不到找票的退路")
        done = self.run_sh(os.path.join(wt, "scripts", "auto-fix.sh"),
                           "1", "--dry-run", "--round", "1", cwd=wt)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn(os.path.join("x-wt", "x-wt"), done.stdout, "副本巢狀了")
        self.assertEqual(os.path.realpath(self.copy_root_in(done)), self.expected())

    def test_a_tickets_dir_from_the_environment_resolves_the_same_way(self):
        """A2:設了 `AC_TICKETS_DIR`、cwd 是票 worktree —— 同 A1。

        **變異**:`WTBASE=$MAINROOT/$WTBASE` 改回 `$ROOT/$WTBASE` → 這一條紅。
        """
        self.config_with("../x-wt")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "worktree_dir")
        wt = self.ticket_worktree()
        self.make_ticket("1", allowed_write_paths=["tests/*"])   # 只在主 repo,不進版控
        self.assertFalse(os.path.exists(os.path.join(wt, "tickets", "1.json")))
        done = self.run_sh(os.path.join(wt, "scripts", "auto-fix.sh"),
                           "1", "--dry-run", "--round", "1", cwd=wt,
                           env=self.env(AC_TICKETS_DIR=os.path.join(self.repo, "tickets")))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn(os.path.join("x-wt", "x-wt"), done.stdout, "副本巢狀了")
        self.assertEqual(os.path.realpath(self.copy_root_in(done)), self.expected())

    def test_a_missing_parent_stops_by_name_instead_of_landing_at_the_root(self):
        """A3:`worktree_dir` 的上層不存在 → rc=2,stderr 指名解析後的路徑;不得算出 `/x-wt`。

        **變異**:拿掉上層存在性檢查(`exit 2` 那一句)→ 這一條紅。
        """
        self.config_with("../nope/x-wt")
        self.ticket_ready()
        self.assertFalse(os.path.exists(os.path.join(self.home, "nope")))
        done = self.auto_fix("--dry-run", "--round", "1")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        named = re.search(r"副本根 (\S+) 的上層不存在", done.stderr)
        self.assertIsNotNone(named, "要指名停在哪一個路徑:" + done.stderr)
        self.assertEqual(os.path.realpath(named.group(1)),
                         os.path.realpath(os.path.join(self.home, "nope", "x-wt")))
        self.assertNotIn("`/x-wt/", done.stdout, "算到檔案系統根下了")
        self.assertNotIn("# 規則包", done.stdout, "停下來就不該再印派工文")


class TheCopiesGetCollected(AutoFixBase):
    """#29 A6 / G6:副本自己收 —— `work/` 與 `base/` 刪掉,patch 與 EVIDENCE 留著。

    以前它們只在**下一輪開始**才 `rm -rf`,綠了停 InReview 就永遠留著;而「沒人收」與
    「收過了」在磁碟上長得一樣,直到滿的那一刻(2026-09-16 某個下游專案的副本 14 GB 塞滿磁碟,
    全套當場 disk I/O error)。
    """

    def fix_dir(self, round_no=2):
        return os.path.join(self.home, "repo-wt", "fix-t1", "round%d" % round_no)

    def test_work_and_base_are_gone_but_the_patch_stays(self):
        """**變異**:把 `round_once` 裡那一句 `shed_copies "$FIX"` 拿掉 → 這一條紅。"""
        self.set_worker(WORKER_FIXES)
        self.ticket_ready()
        self.first_round()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        where = self.fix_dir()
        self.assertFalse(os.path.isdir(os.path.join(where, "work")), "work/ 沒被收掉")
        self.assertFalse(os.path.isdir(os.path.join(where, "base")), "base/ 沒被收掉")
        self.assertTrue(os.path.isfile(os.path.join(where, "patch-round2.diff")),
                        "patch 是證據,不能跟著被掃掉")
        self.assertTrue(os.path.isfile(os.path.join(where, "EVIDENCE-round2.md")))
        self.assertIn("收掉副本", done.stdout)

    def test_a_patch_left_inside_work_is_picked_out_before_the_copy_goes(self):
        """worker 有時把交付物放在 `work/` 裡。**先撿出來再刪** —— 反過來的話,
        刪掉的是這一輪唯一的一份 patch。

        **變異**:把 `collect_from_copy` 那一句拿掉 → 這一條紅。
        """
        self.set_worker(WORKER_FIXES.replace(
            'diff -ruN base work > "patch-round$AC_ROUND.diff" || true',
            'diff -ruN base work > "work/patch-round$AC_ROUND.diff" || true')
            .replace('> "EVIDENCE-round$AC_ROUND.md"',
                     '> "work/EVIDENCE-round$AC_ROUND.md"')
            .replace('>> "EVIDENCE-round$AC_ROUND.md"',
                     '>> "work/EVIDENCE-round$AC_ROUND.md"'))
        self.ticket_ready()
        self.first_round()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        where = self.fix_dir()
        self.assertTrue(os.path.isfile(os.path.join(where, "patch-round2.diff")),
                        done.stdout + done.stderr)
        self.assertFalse(os.path.isdir(os.path.join(where, "work")))


    def test_a_hand_built_round_one_copy_is_collected_too(self):
        """#38 A4:主線用 Agent 手建的 `fix-t<n>/round1` —— 迴圈從 `S_ROUND+1` 起,碰不到它。
        auto-fix 收過 patch、派下一輪之後,它的 `work/` 與 `base/` 也要不在,patch 留著。

        **變異**:拿掉迴圈前收前幾輪的那一句 `shed_copies "$WTBASE/fix-t$ID/round$p"` → 這一條紅。
        """
        self.set_worker(WORKER_FIXES)
        self.ticket_ready()
        self.first_round()
        where = self.fix_dir(1)
        for sub in ("work", "base"):
            self.write(os.path.join(sub, "README"), "main\n", where=where)
        self.write("patch-round1.diff", RED_CASE, where=where)
        done = self.auto_fix()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(os.path.isdir(os.path.join(where, "work")), "round1 的 work/ 沒被收掉")
        self.assertFalse(os.path.isdir(os.path.join(where, "base")), "round1 的 base/ 沒被收掉")
        self.assertTrue(os.path.isfile(os.path.join(where, "patch-round1.diff")),
                        "patch 是證據,不能跟著被掃掉")


class TheWholeLoop(AutoFixBase):
    """一整圈:紅 → 派 worker → 套 patch → 閘門 → 綠 → InReview → review.sh。

    這裡用**真的** `apply.sh` 與真的 `gate.sh`:要問的正是「這幾支接得起來嗎」。
    """

    def test_a_red_round_gets_fixed_and_stops_at_awaiting_review(self):
        """#42 A3(a):綠了轉 InReview 之後叫 `review.sh`;假 reviewer 說 pass,票的
        `review.by` 是 `reviewer@…`、`sha` 是分支頭 —— 由 review.sh 寫,不是 auto-fix。"""
        self.set_worker(WORKER_FIXES)
        model = self.set_reviewer(REVIEWER_PASS)
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
        self.assertEqual(ticket["state"], "InReview", "綠了轉 InReview,覆核 pass 不改 state")
        self.assertEqual(self.inbox_rows(), [], "綠了、覆核通過都只寫事件")
        self.assertTrue(self.went_in_review())
        self.assertIn("review.pass", self.kinds())
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn("reviewer ran 1", handle.read(), "fixture reviewer 沒被叫到")
        review = ticket.get("review") or {}
        self.assertTrue(str(review.get("by") or "").startswith("reviewer@"), review)
        self.assertEqual(review.get("by"), "reviewer@" + model)
        self.assertEqual(review.get("sha"), self.git("rev-parse", "t1").strip())
        self.assertTrue(os.path.isdir(wt))

    def test_no_review_leaves_the_review_to_a_human(self):
        """`--no-review`:綠了停在 InReview,reviewer 不被叫、auto-fix 不准自己蓋覆核那一格。"""
        self.set_worker(WORKER_FIXES)
        self.set_reviewer(REVIEWER_PASS)
        self.ticket_ready()
        self.first_round()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["state"], "InReview")
        self.assertIsNone(ticket.get("review"), "auto-fix 不准自己蓋覆核那一格")
        with open(self.log, encoding="utf-8") as handle:
            self.assertNotIn("reviewer ran", handle.read())
        self.assertIn("--no-review", done.stdout)
        self.assertEqual(self.inbox_rows(), [])

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
        self.assertEqual(self.inbox_rows(), [])
        self.assertIn("gate.fail", self.kinds())

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
        rows = self.inbox_rows()
        self.assertEqual([row["kind"] for row in rows], ["decision"],
                         "三輪紅恰一頁 decision(auto-fix 那頁;ticket.py 只寫事件)")
        self.assertIn("三輪耗盡", rows[0]["state"])


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


# 第 1 輪的驗證者(#51):worker 與驗證者是**同一支假的可執行檔**(`verifier.command` 沒設時
# 退回 `worker.command`),由 `AC_ROLE` 分流。驗證者的呼叫紀錄寫在自己的檔(@VCALLS@),
# 測試先建一個空檔 —— 「一發都沒有」要是「檔在而且空」,不是「檔不在」(§5.5)。
# 驗證者一開始先在自己的副本根寫 `started`(C2),再交一份只含案例的 patch-verify 與
# 一份 result 區塊帶 `verify` / `baseline` 的 EVIDENCE。@WAIT@ 換成 1 時是**雙向會合**(C2):
# worker 一進來先在自己的副本根寫 `worker-started`,再輪詢驗證者的 `started` 最多 20 秒,等不到
# 就 exit 9、不交 patch;驗證者寫完 `started` 也輪詢 `worker-started` 最多 20 秒,等不到就
# exit 9、不交件。單向的標記只擋得住「worker 先、驗證者後」—— 驗證者先跑完再派 worker 的
# 串行,標記檔早就在了。@VERIFIER_HANDS_IN@ 換成 0 時驗證者什麼都不交就退出(C6)。
FIRST_ROUND_PAIR = '''#!/bin/sh
set -e
cd "$AC_WORK"
if [ "$AC_ROLE" = verifier ]; then
    echo "verifier ran round $AC_ROUND" >> "@VCALLS@"
    : > started
    [ "@VERIFIER_HANDS_IN@" = 1 ] || exit 0
    if [ "@WAIT@" = 1 ]; then
        i=0
        while [ ! -f "@WMARK@" ]; do
            i=$((i + 1))
            [ "$i" -le 200 ] || exit 9
            sleep 0.1
        done
    fi
    mkdir -p work/verify/example
    cat > work/verify/example/test_ticket_1.py <<'CASE'
"""#1 第 1 輪驗證者的案例:worker 交的 tests/test_thing.py 要在候選樹上。

## 驗收表(期望值來源獨立於被測程式)
C4 | sandbox | 看 tests/test_thing.py 在不在 | 在 | 票面 C4:乾淨基底上沒有這個檔

## 介面字串
(沒有;這一條不斷言任何字串)

## 怎麼做假
不上真埠、不起真服務、不殺行程。

## 不做
不改產品碼;不放寬任何票面驗收。
"""
import os
import unittest

TAGS = ["example"]
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class T(unittest.TestCase):
    def test_worker_case_is_there(self):
        """C4 worker 交的案例檔在候選樹上。"""
        self.assertTrue(os.path.exists(os.path.join(ROOT, "tests", "test_thing.py")))
CASE
    diff -ruN base work > patch-verify.diff || true
    cat > EVIDENCE-verifier.md <<'EV'
# verifier
案例一個,基底紅。

## result

```result
{"ticket": "1", "role": "verifier", "round": 1, "rc": 0, "patch_sha256": null,
 "gate": null, "mutations": [], "objection": null, "excluded": [], "repro": null,
 "memory": [],
 "verify": {"files": ["verify/example/test_ticket_1.py"], "tags": ["example"],
            "run": "python3 scripts/verify.py --tag example", "notes": "沙盒"},
 "baseline": {"stage": "red", "ok": true, "why": "",
              "files": ["verify/example/test_ticket_1.py"]}}
```
EV
    exit 0
fi
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
: > worker-started
if [ "@WAIT@" = 1 ]; then
    i=0
    while [ ! -f "@VMARK@" ]; do
        i=$((i + 1))
        [ "$i" -le 200 ] || exit 9
        sleep 0.1
    done
fi
mkdir -p work/tests
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
'''

# 票面上 `<票號>` 這種人手填的角括號、與範本裡 `@…@` 這種腳本填的佔位 —— 派工文裡
# 一個都不該剩(#51 C3)。`<feature>` / `<tag>` 是驗證者自己取的名字,不在這裡。
UNFILLED = re.compile(r"@[A-Z_]+@|<票號>|<票庫路徑>|<sha>|<\$W|<主線 / session 名>|<輪>|<你的模型>")


class TheFirstRoundVerifierRunsAlongside(AutoFixBase):
    """#51 / D-032 ③(C6):票 `needs_verifier` 是 JSON `true` ⇒ auto-fix 第 1 輪在派 worker
    **之前**起一個背景驗證者,worker 回來之後才收;兩份 patch 一起套進閘門那一棵樹。
    `false` / 缺這一格 ⇒ 不派(缺的那一種 stderr 說一聲)。期望值(事件的 role、cost
    筆數、頁數、案例名)寫死在這裡,不從被測腳本算回去。"""

    def vcalls(self):
        return os.path.join(self.home, "verifier-calls.log")

    def vfix(self):
        return os.path.join(self.home, "repo-wt", "verify-t1", "round1")

    def arm(self, wait=False, hands_in=True, **fields):
        self.write("verifier-calls.log", "", where=self.home)
        self.set_worker(FIRST_ROUND_PAIR
                        .replace("@VCALLS@", self.vcalls())
                        .replace("@VMARK@", os.path.join(self.vfix(), "started"))
                        .replace("@WMARK@", os.path.join(self.home, "repo-wt", "fix-t1",
                                                         "round1", "worker-started"))
                        .replace("@WAIT@", "1" if wait else "0")
                        .replace("@VERIFIER_HANDS_IN@", "1" if hands_in else "0"))
        fields.setdefault("allowed_write_paths", ["tests/*", "verify/*"])
        # `gate.sh --branch` 把 `verify/*` 的改動對到 `test_verify_runner`(真 repo 有這一支);
        # 沙盒少了它,閘門紅在「模組不存在」—— 那是沙盒與事實的差異,不是受測腳本的行為。
        self.write(os.path.join("tests", "test_verify_runner.py"),
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_there(self):\n        self.assertTrue(True)\n")
        row = self.make_ticket("1", **fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")
        return row

    def verifier_calls(self):
        self.assertTrue(os.path.exists(self.vcalls()), "驗證者的呼叫紀錄檔要在(有在看)")
        with open(self.vcalls(), encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]

    def verifier_starts(self):
        return [row for row in self.events() if row["kind"] == "agent.start"
                and row.get("role") == "verifier"]

    def test_c1_true_starts_a_verifier_in_round_one(self):
        """C1 true。**變異**:`verifier_wanted` 的 true 分支不設 V_WANT → 紅。"""
        self.arm(needs_verifier=True)
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        starts = self.verifier_starts()
        self.assertEqual(len(starts), 1, starts)
        self.assertEqual((str(starts[0].get("round")), starts[0].get("agent")),
                         ("1", "auto-fix-verifier"))
        self.assertEqual(self.verifier_calls(), ["verifier ran round 1"])

    def test_c1_false_starts_no_verifier(self):
        """C1 false:紀錄檔在、而且是空的。"""
        self.arm(needs_verifier=False)
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.verifier_calls(), [])
        self.assertEqual(self.verifier_starts(), [])
        self.assertNotIn("needs_verifier", done.stderr)

    def test_c1_missing_starts_no_verifier_and_says_so(self):
        """C1 缺這一格。**變異**:條件改成「缺也派」→ 這一條紅。"""
        row = self.arm()
        self.assertNotIn("needs_verifier", row, "前提:票面沒有這一格")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.verifier_calls(), [])
        self.assertEqual(self.verifier_starts(), [])
        self.assertIn("needs_verifier", done.stderr)
        self.assertIn("D-028", done.stderr)

    def test_c2_the_verifier_is_running_before_the_worker_is_dispatched(self):
        """C2:worker 等得到驗證者的 `started` 才交 patch;綠一輪後 cost 兩筆。

        **變異**:改成先等驗證者交件再派 worker(串行)→ 驗證者等不到 worker 的標記、
        不交件 → rc=5 → 紅;反過來先 worker 後驗證者 → worker 等不到標記 exit 9 → 紅。
        """
        self.arm(wait=True, needs_verifier=True)
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        worker_done = [row for row in self.events() if row["kind"] == "agent.done"
                       and row.get("agent") == "auto-fix"]
        self.assertEqual([row.get("rc") for row in worker_done], ["0"])
        rows = self.load_ticket("1").get("cost") or []
        self.assertEqual(sorted((row["role"], row["round"]) for row in rows),
                         [("verifier", 1), ("worker", 1)], rows)

    def test_c3_the_verifier_packet_is_the_template_filled_in(self):
        """C3:派工文 = 規則包 + 範本逐格填好;`--dry-run --round 1` 兩份路徑都印、不起行程。

        **變異**:`verifier_packet` 少填一格(拿掉 @BASE@)→ 「沒有沒填的佔位」紅。
        """
        row = self.arm(needs_verifier=True)
        shown = self.auto_fix("--dry-run", "--round", "1")
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        self.assertIn("dispatch-round1.md", shown.stderr)
        self.assertIn("dispatch-verifier-round1.md", shown.stderr)
        self.assertEqual(self.verifier_calls(), [], "--dry-run 不起驗證者")
        self.assertEqual(self.worker_rounds(), [], "--dry-run 不起 worker")

        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        sent = [path for path in self.result_files("dispatch-verifier-round1.md")
                if os.path.exists(os.path.join(os.path.dirname(path), "verifier-round1.log"))]
        self.assertEqual(len(sent), 1, self.result_files("dispatch-verifier-round1.md"))
        with open(sent[0], encoding="utf-8") as handle:
            packet = handle.read()
        self.assertIn("role=verifier", packet)
        self.assertIn("#1", packet)
        self.assertIn(row["base_sha"], packet)
        self.assertIn(os.path.join(self.vfix(), "work"), packet)
        self.assertIn(os.path.join(self.repo, "tickets", "1.json"), packet)
        self.assertIn("auto-fix.sh(交檔即回報", packet)
        self.assertIn("verify-case.py red 1 --ref %s" % row["base_sha"], packet)
        # 規則包在範本之前,它自己的說明文字裡就寫著 `<票號>` 當通稱 —— 量的是範本那一段。
        self.assertIn("# 派工:驗證者 —— #1 第 1 輪", packet)
        filled = packet.split("# 派工:驗證者", 1)[1]
        self.assertEqual(UNFILLED.findall(filled), [], "派工文裡還有沒填的佔位")

    def test_c4_the_verifier_plan_and_baseline_are_merged_into_the_ticket(self):
        """C4:綠一輪後票的 verify.files 非空、baseline.stage 是 red 或 check。

        **變異**:`apply.sh` 不併驗證者的 verify / baseline → 紅。
        """
        self.arm(needs_verifier=True)
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        plan = self.load_ticket("1").get("verify") or {}
        self.assertEqual(plan.get("files"), ["verify/example/test_ticket_1.py"], plan)
        self.assertEqual(plan.get("tags"), ["example"], plan)
        self.assertIn((plan.get("baseline") or {}).get("stage"), ("red", "check"), plan)
        self.assertEqual(len(self.result_files("result-verifier-round1.json")), 1)

    def test_c5_the_gate_runs_the_tree_with_both_patches(self):
        """C5:閘門那一輪的 log 裡跑過驗證者 patch-verify 帶進來的案例。

        **變異**:apply 只套 worker 那一份 → 紅。
        """
        self.arm(needs_verifier=True)
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        gate = self.status_of("1", kind="gate")
        self.assertEqual(gate.get("rc"), 0, gate)
        text = ""
        for row in gate.get("kept_logs") or []:
            if row.get("kept"):
                text += self.read(row["kept"])
        self.assertIn("test_ticket_1.T.test_worker_case_is_there", text)
        branch = self.git("show", "t1:verify/example/test_ticket_1.py")
        self.assertIn("test_worker_case_is_there", branch)

    def test_c6_a_verifier_that_hands_in_nothing_blocks_but_keeps_the_worker_patch(self):
        """C6:驗證者沒交件 ⇒ worker 的 patch 先收進 reports,票 Blocked、恰 1 頁 decision、
        attempt.failed reason=verifier-no-patch、rc=5。

        **變異**:worker 的 patch 被一起清掉(不先收進 reports)→ 紅。
        """
        self.arm(needs_verifier=True, hands_in=False)
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertEqual(self.verifier_calls(), ["verifier ran round 1"])
        self.assertEqual(self.load_ticket("1")["state"], "Blocked")
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertEqual(pages[0].get("kind"), "decision")
        self.assertIn("驗證者沒交出 patch-verify", json.dumps(pages[0], ensure_ascii=False))
        self.assertEqual(len(self.result_files("patch-round1.diff")), 1,
                         "worker 的 patch 要留在 reports 目錄")
        failed = [row for row in self.events() if row["kind"] == "ticket.attempt.failed"]
        self.assertEqual([row.get("reason") for row in failed], ["verifier-no-patch"])
        self.assertFalse(self.git("branch", "--list", "t1").strip(),
                         "沒有案例就不套、不量綠")


if __name__ == "__main__":
    unittest.main()
