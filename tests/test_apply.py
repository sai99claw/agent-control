"""`scripts/apply.sh`:收件 → 在票分支上補 commit 的程式入口(D-015;#144,D-054 Q9)。

worker 在票分支的 worktree 自己 commit(`auto-fix.sh` 判「分支有新 commit、工作樹乾淨」);
這一支收 EVIDENCE(記憶、result、反駁)、把驗證者的計畫與驗紅併進票,驗證者交了 patch-verify
就在分支上補一個 commit,code-map 索引有變再補一個。這一組問的就是這幾件:

- 收件不替 worker commit:分支上原本幾個 commit,只收 EVIDENCE 時還是幾個。
- patch-verify 進分支是**一個** commit,只帶它自己的改動(未追蹤的閘門殘留不跟進來)。
- 票分支不在 = 沒有交件可收(rc 2);patch-verify 套不上(rc 3)。
- 反駁收進票(rc 6)、驗證者的計畫與驗紅併進票。
- 批次那一支(`--batch`)也不收 patch(#145):worker 在整合分支上 commit,這一支量 `<base>..` 的 commit、
  補 code-map 重產、收 EVIDENCE。
"""

import glob
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import SCRIPTS, Sandbox  # noqa: E402

CHANGE = """diff -ruN base/src/a.txt work/src/a.txt
--- base/src/a.txt\t2026-09-21 10:00:00
+++ work/src/a.txt\t2026-09-21 10:00:00
@@ -1 +1 @@
-old
+new
"""

CREATE = """diff -ruN base/src/b.txt work/src/b.txt
--- base/src/b.txt\t1970-01-01 08:00:00
+++ work/src/b.txt\t2026-09-21 10:00:00
@@ -0,0 +1 @@
+made by the patch
"""

VERIFY_CASE = """diff -ruN base/verify/example/test_ticket_1.py work/verify/example/test_ticket_1.py
--- base/verify/example/test_ticket_1.py\t1970-01-01 08:00:00
+++ work/verify/example/test_ticket_1.py\t2026-09-21 10:00:00
@@ -0,0 +1,7 @@
+import unittest
+TAGS = ["example"]
+
+
+class T(unittest.TestCase):
+    def test_one(self):
+        self.assertTrue(True)
"""

class ApplyBase(Sandbox):

    def setUp(self):
        super(ApplyBase, self).setUp()
        self.write("src/a.txt", "old\n")
        self.write(".gitignore", self.read(".gitignore") + "junk/\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線上先有一個 src/a.txt")
        self.git("push", "-q", "origin", "main")

    def patch_file(self, name, body):
        return self.write(name, body, where=self.home)

    def apply(self, *args):
        return self.run_sh("scripts/apply.sh", *args)

    def make(self, ident="1", **fields):
        row = self.make_ticket(ident, **fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #%s" % ident)
        return row

    def branch_exists(self, name):
        import subprocess
        return subprocess.run(["git", "-C", self.repo, "rev-parse", "-q",
                               "--verify", "%s^{commit}" % name],
                              capture_output=True, env=self.env()).returncode == 0

    def wt(self, name="t1"):
        return os.path.join(self.home, "repo-wt", name)

    def deliver(self, ident="1", files=None, branch=None):
        """worker 交件的形狀(#144):票分支上一個 commit(預設把 src/a.txt 改成 new)。"""
        return self.ticket_branch_commit(ident, files or {"src/a.txt": "new\n"}, branch=branch)

    def deliver_apply(self, ident="1", *args, **kwargs):
        self.deliver(ident, branch=kwargs.pop("branch", None))
        return self.run_sh("scripts/apply.sh", ident, *args, **kwargs)


class TheHappyPath(ApplyBase):

    def test_receiving_does_not_commit_for_the_worker(self):
        """worker 的 commit 就是交件;只收 EVIDENCE 時 apply 不另 commit(D-054 Q9)。
        **變異**:收件前 `git add -A && git commit`(腳本替 worker commit)→ 這一條紅。"""
        self.make("1")
        wt = self.deliver("1")
        self.write("half.txt", "沒 commit 的半成品\n", where=wt)
        done = self.apply("1", "--evidence", self.patch_file("EVIDENCE.md", "# E\n"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("show", "t1:src/a.txt"), "new\n")
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "1")
        self.assertNotIn("half.txt", self.git("ls-tree", "-r", "--name-only", "t1"))

    def test_the_verify_patch_is_one_commit_of_its_own(self):
        """patch-verify 在分支上補**一個** commit,訊息帶票號與 patch-verify。
        **變異**:patch-verify 那一段不 commit → count 那一條紅。"""
        self.make("1")
        done = self.deliver_apply("1", self.patch_file("v.diff", VERIFY_CASE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("TAGS", self.git("show", "t1:verify/example/test_ticket_1.py"))
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "2")
        message = self.git("log", "-1", "--format=%B", "t1")
        self.assertIn("patch-verify:", message)
        self.assertIn("ticket: #1", message)

    def test_a_patch_verify_that_does_not_fit_the_branch_is_rc_3_and_leaves_it(self):
        """套不上分支 → rc 3,分支不動、worktree 沒有半套的改動。"""
        self.make("1")
        self.deliver("1")
        head = self.git("rev-parse", "t1").strip()
        done = self.apply("1", self.patch_file("v.diff", CHANGE))
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("patch-verify 套不上分支 t1", done.stderr)
        self.assertEqual(self.git("rev-parse", "t1").strip(), head)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.wt()), "")

    def test_no_ticket_branch_means_nothing_to_receive(self):
        """票分支不在 = worker 沒交件可收:rc 2,不從主線替它開分支。"""
        self.make("1")
        done = self.apply("1", "--evidence", self.patch_file("EVIDENCE.md", "# E\n"))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("票分支 t1 不在", done.stderr)
        self.assertFalse(self.branch_exists("t1"))

    def test_it_leaves_a_status_file_for_the_round(self):
        self.make("1")
        self.assertEqual(self.deliver_apply("1").returncode, 0)
        data = self.status_of("1", kind="apply")
        self.assertEqual(data["rc"], 0)
        self.assertEqual(data["state"], "done")

    def test_it_harvests_two_memory_notes_from_evidence(self):
        self.make("1")
        evidence = self.patch_file(
            "EVIDENCE.md",
            "# Evidence\n\n## 記憶\n"
            "memory.py note role implementer \"第一條原則\" --ticket 1 --by worker@opus\n"
            "python3 scripts/memory.py note model opus \"第二條原則\" "
            "--ticket 1 --by worker@opus\n")
        done = self.deliver_apply("1", "--evidence", evidence)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("第一條原則", self.read("memory/role/implementer.inbox.md"))
        self.assertIn("第二條原則", self.read("memory/model/opus.inbox.md"))
        self.assertEqual(self.kinds().count("memory.noted"), 2)

    def test_no_memory_and_bad_lines_do_not_block_apply(self):
        self.make("1")
        too_long = "x" * 301
        evidence = self.patch_file(
            "EVIDENCE.md",
            "## 記憶:\n"
            "memory.py note wrong implementer \"壞層名\" --ticket 1 --by worker@opus\n"
            "memory.py note role implementer \"%s\" --ticket 1 --by worker@opus\n"
            "memory.py note role implementer \"仍會收這行\" --ticket 1 --by worker@opus\n"
            % too_long)
        done = self.deliver_apply("1", "--evidence", evidence)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stderr.count("拒絕 evidence 行"), 2, done.stderr)
        self.assertIn("仍會收這行", self.read("memory/role/implementer.inbox.md"))
        self.assertEqual(self.kinds().count("memory.noted"), 1)

    def test_an_explicit_no_memory_section_writes_nothing(self):
        self.make("1")
        evidence = self.patch_file("EVIDENCE.md", "## 記憶\n無\n")
        done = self.deliver_apply("1", "--evidence", evidence)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("memory.noted", self.kinds())


FULL_EVIDENCE = """# EVIDENCE #1 第 1 輪

## 1 patch
sha256 0123456789abcdef

## 2 閘門
`python3 -m unittest discover -s tests`:Ran 3 tests,rc=0

## 3 變異表
| M1 | count 1 | tests/test_x.py::T::test_y | AssertionError: 0 != 2 |

## 4 已排除的假設
不是 worktree 沒收 —— 那一輪的 status.json 裡 rc 已經是 2。

## 5 最小重現
`python3 -m unittest tests.test_x`,預期 `Ran 3 tests … OK`

## result

```result
{"ticket": "1", "role": "worker", "round": 1, "rc": 0,
 "patch_sha256": "0123456789abcdef",
 "gate": {"cmd": "python3 -m unittest discover -s tests", "ran": 3, "rc": 0},
 "mutations": [{"id": "M1", "count": 1, "case": "T::test_y",
                "red_first_line": "AssertionError: 0 != 2"}],
 "objection": null, "excluded": [], "repro": {"cmd": "x", "expect": "OK"},
 "memory": []}
```
"""

THIN_EVIDENCE = "# EVIDENCE\n\n交了,但是只有散文,沒有那一塊。\n"

OBJECTING_EVIDENCE = """# EVIDENCE

OBJECTION: ticket-wrong 驗收 A3 指的那個欄位在這一版根本不存在

## result

```result
{"ticket": "1", "role": "worker", "round": 1, "rc": 0,
 "objection": {"category": "ticket-wrong", "body": "欄位不存在"}}
```
"""

VERIFIER_EVIDENCE = """# EVIDENCE-verifier

## result

```result
{"ticket": "1", "role": "verifier", "round": 1, "rc": 0}
```
"""

# 驗證者把 `verify-case.py red` 的證據檔抄進 result 的 `baseline`(#36)。
VERIFIER_EVIDENCE_WITH_RED = """# EVIDENCE-verifier

## result

```result
{"ticket": "1", "role": "verifier", "round": 1, "rc": 0,
 "baseline": {"stage": "%s", "ok": true, "why": "",
              "files": ["verify/nav/test_ticket_1.py"], "candidate_run": null,
              "baseline": {"cases": 1, "red": ["verify.nav.test_ticket_1.T.test_x"]}}}
```
"""


class TheLandingEntry(ApplyBase):
    """#87 A27:收尾那一行以 `落地入口:` 起頭,指真正存在的入口。"""

    def test_the_last_line_names_land_sh_when_there_is_no_land_ticket(self):
        self.make("1")
        done = self.deliver_apply("1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([l for l in done.stdout.splitlines() if l.startswith("落地入口:")],
                         ["落地入口:sh scripts/land.sh t1"])

    def test_the_last_line_names_land_ticket_when_the_project_has_it(self):
        self.write(os.path.join("scripts", "land-ticket.sh"), "#!/bin/sh\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "專案自己的落地入口")
        self.make("1")
        done = self.deliver_apply("1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([l for l in done.stdout.splitlines() if l.startswith("落地入口:")],
                         ["落地入口:sh scripts/land-ticket.sh t1"])


class TheStructuredDeliveryOfTheFirstRound(ApplyBase):
    """#29 A4 / G4:第 1 輪走 `apply.sh`,而 `apply.sh` 以前只跑 `memory.py harvest`
    —— 於是**第一輪永遠沒有 `result-round1.json`**,看板對那一輪只印得出「沒交結構化
    輸出」,與真的沒交長得一樣。抽取與 `auto-fix.sh`(第 2 輪起)是**同一支**
    (`ticket.py result`);抄第二份的那一天,兩份會往不同方向漂(D-018)。
    """

    def result_json(self, name="result-round1.json"):
        found = sorted(glob.glob(os.path.join(self.repo, "reports", "t1", "*", name)))
        self.assertEqual(len(found), 1, "找不到(或不只一份)%s:%s" % (name, found))
        with open(found[0], encoding="utf-8") as handle:
            return json.load(handle)

    def test_the_block_at_the_end_of_evidence_becomes_a_result_file(self):
        """**變異**:把 `apply.sh` 尾端那一段 `ticket.py result` 拿掉 → 這一條紅。"""
        self.make("1")
        evidence = self.patch_file("EVIDENCE.md", FULL_EVIDENCE)
        done = self.deliver_apply("1", "--evidence", evidence)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.result_json()
        self.assertTrue(row["present"])
        self.assertEqual(row["gate"]["ran"], 3)
        self.assertEqual(row["sections"],
                         {"patch_sha256": True, "gate": True, "mutations": True,
                          "excluded": True, "repro": True},
                         "五段散文在不在是**另一格**,不是靠 result 那一塊推的")

    def test_evidence_without_the_block_is_recorded_as_no_block_not_as_nothing(self):
        """三種缺漏各有各的樣子 —— 揉成同一個空檔的那一刻,「沒交」與「交了但都是
        空的」長得一樣(§5.5)。

        **變異**:把 `harvest_result()` 的 `reason` 三分支併成一個 → 這一條紅。
        """
        self.make("1")
        evidence = self.patch_file("EVIDENCE.md", THIN_EVIDENCE)
        done = self.deliver_apply("1", "--evidence", evidence)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.result_json()
        self.assertFalse(row["present"])
        self.assertEqual(row["reason"], "no-block")
        self.assertFalse(row["sections"]["mutations"], "缺段要看得出來")
        self.assertIn("少了這幾段", done.stdout, "缺段印出來(但不擋)")

    def test_an_objection_is_recorded_on_the_ticket_and_the_exit_code_says_so(self):
        """**沒被收進票的反駁,與沒有反駁長得一樣**(§7)。第 1 輪的 `OBJECTION:` 行
        以前沒有任何人收。

        **變異**:把 `apply.sh` 尾端收反駁那一段拿掉 → 這一條紅。
        """
        self.make("1")
        evidence = self.patch_file("EVIDENCE.md", OBJECTING_EVIDENCE)
        done = self.deliver_apply("1", "--evidence", evidence)
        self.assertEqual(done.returncode, 6, done.stdout + done.stderr)
        rows = self.load_ticket("1")["objections"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["category"], "ticket-wrong")
        self.assertIn("欄位在這一版根本不存在", rows[0]["body"])
        self.assertEqual(rows[0]["owner"], "main")
        self.assertTrue(self.branch_exists("t1"),
                        "patch 該 commit 的還是 commit 了 —— rc 說的是「這一手沒有結束」")

    def status_file(self, run_id):
        with open(os.path.join(self.repo, "reports", "t1", run_id, "status.json"),
                  encoding="utf-8") as handle:
            return json.load(handle)

    def test_the_same_objection_is_not_recorded_twice(self):
        """`auto-fix.sh` 在叫 `apply.sh` 之前就先收過一次 —— 兩邊各記一次的話,
        同一句話會在票上長成兩筆,而處置的人分不出哪一筆是哪一輪的。

        A1(#36):**不再記一次**不等於**放行** —— disposition 還空著,第二次照樣
        rc=6,狀態檔也是 6。以前這裡斷 `!= 6`,把「未處置的反駁第二輪就放行」釘死了。

        **變異**:把 `apply.sh` 尾端 `case $orc` 的 `3)` 分支拿掉 → 這一條紅。
        """
        self.make("1")
        evidence = self.patch_file("EVIDENCE.md", OBJECTING_EVIDENCE)
        self.deliver_apply("1", "--evidence", evidence)
        second = self.run_sh("scripts/apply.sh", "1", "--evidence", evidence,
                             env=self.env(AC_RUN_ID="second"))
        self.assertEqual(len(self.load_ticket("1")["objections"]), 1,
                         second.stdout + second.stderr)
        self.assertEqual(second.returncode, 6, second.stdout + second.stderr)
        self.assertIn("還沒處置", second.stderr)
        self.assertEqual(self.status_file("second")["rc"], 6,
                         "狀態檔說的要是最後那個 rc,不是中途寫的 0")

    def test_an_objection_already_disposed_lets_the_second_apply_through(self):
        """A2(#36):處置過的那一筆(accepted / rejected / deferred / fixed)第二次
        收到才放行,並說出「已處置」—— 沒說的話,rc=0 與「沒看到反駁」長得一樣。

        **變異**:把 `apply.sh` 尾端 `case $orc` 的 `4)` 分支拿掉 → 這一條紅。
        """
        self.make("1")
        evidence = self.patch_file("EVIDENCE.md", OBJECTING_EVIDENCE)
        self.deliver_apply("1", "--evidence", evidence)
        rows = self.load_ticket("1")["objections"]
        rows[0]["disposition"] = "rejected"
        self.assertEqual(self.ticket("set", "1", "objections",
                                     json.dumps(rows, ensure_ascii=False)).returncode, 0)
        second = self.run_sh("scripts/apply.sh", "1", "--evidence", evidence,
                             env=self.env(AC_RUN_ID="second"))
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("已處置", second.stdout + second.stderr)
        self.assertEqual(len(self.load_ticket("1")["objections"]), 1)
        self.assertEqual(self.status_file("second")["rc"], 0)

    def test_an_objection_that_cannot_be_recorded_is_not_called_already_there(self):
        """A3(#36):`ticket.py objection` 記不進票(rc=2)以前也掉到「已經有了」那一句
        而 exit 0 —— 沒人收的反駁與沒有反駁長得一樣(§7)。票庫唯讀 = 鎖拿不到、
        票寫不回去,apply 前面那幾手都不寫票庫,所以只有這一手會撞到。
        """
        self.make("1")
        tickets = os.path.join(self.repo, "tickets")
        os.chmod(tickets, 0o555)
        self.addCleanup(os.chmod, tickets, 0o755)
        done = self.deliver_apply("1", "--evidence", self.patch_file("EVIDENCE.md", OBJECTING_EVIDENCE))
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("已經有了", done.stderr)
        self.assertIn("記不進票", done.stderr)

    def test_the_verifier_evidence_has_a_receiver_now(self):
        """#29 A5 / G5:`EVIDENCE-verifier.md` 以前是**一份沒有收件者的交付物**。"""
        self.make("1")
        done = self.deliver_apply("1", "--evidence", self.patch_file("EVIDENCE.md", FULL_EVIDENCE),
                          "--evidence-verifier",
                          self.patch_file("EV.md", VERIFIER_EVIDENCE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.result_json("result-verifier-round1.json")
        self.assertTrue(row["present"])
        self.assertEqual(row["role"], "verifier")

    def test_the_verifier_red_record_is_merged_into_the_ticket_baseline(self):
        """A5(#36):`verify-case.py red` 不寫票了,驗證者的驗紅由收件這一手在鎖裡
        併進票的 `verify.baseline`(同一格,D-018)。

        **變異**:把 `apply.sh` 裡 `ticket.save_baseline(...)` 那一行換成 `version = 0`
        → 這一條紅。
        """
        self.make("1", verify={"files": ["verify/nav/test_ticket_1.py"],
                               "tags": ["example"], "run": "", "notes": ""})
        before = self.load_ticket("1")["state_version"]
        done = self.deliver_apply("1", "--evidence", self.patch_file("EVIDENCE.md", FULL_EVIDENCE),
                          "--evidence-verifier",
                          self.patch_file("EV.md", VERIFIER_EVIDENCE_WITH_RED % "red"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["verify"]["baseline"]["stage"], "red")
        self.assertEqual(ticket["verify"]["files"], ["verify/nav/test_ticket_1.py"],
                         "只換 baseline 那一格,verify 其他格不動")
        self.assertEqual(ticket["state_version"], before + 1)

    def test_a_verifier_baseline_that_claims_check_is_not_merged(self):
        """驗證者量不到綠:一份自稱 `stage=check` 的 baseline 併進去,`close` 就放行了。"""
        self.make("1", verify={"files": ["verify/nav/test_ticket_1.py"],
                               "tags": ["example"], "run": "", "notes": ""})
        done = self.deliver_apply("1", "--evidence", self.patch_file("EVIDENCE.md", FULL_EVIDENCE),
                          "--evidence-verifier",
                          self.patch_file("EV.md", VERIFIER_EVIDENCE_WITH_RED % "check"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])
        self.assertIn("不是 stage=red", done.stderr)

    def test_every_verifier_evidence_given_is_merged_in_order(self):
        """#112 P3:`--evidence-verifier` 可重複給,逐份併進票 —— 平行驗證者與 test_defect
        驗證者各一份。兩份給的是不同格(files+驗紅 / tags),誰被漏掉都看得出來。

        **變異**:只併最後一份(迴圈換回單一個 VEVIDENCE)→ files 與 baseline 那兩條紅。
        """
        self.make("1")
        first = self.patch_file("EV1.md", (
            "# v1\n\n## result\n\n```result\n"
            '{"ticket": "1", "role": "verifier", "round": 1, "rc": 0,\n'
            ' "verify": {"files": ["verify/example/test_ticket_1.py"]},\n'
            ' "baseline": {"stage": "red", "ok": true, "why": "",\n'
            '              "files": ["verify/example/test_ticket_1.py"]}}\n```\n'))
        second = self.patch_file("EV2.md", (
            "# v2\n\n## result\n\n```result\n"
            '{"ticket": "1", "role": "verifier", "round": 1, "rc": 0,\n'
            ' "verify": {"tags": ["example"]}}\n```\n'))
        done = self.deliver_apply("1", "--evidence", self.patch_file("EVIDENCE.md", FULL_EVIDENCE),
                          "--evidence-verifier", first, "--evidence-verifier", second)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        plan = self.load_ticket("1")["verify"]
        self.assertEqual(plan.get("files"), ["verify/example/test_ticket_1.py"], plan)
        self.assertEqual(plan.get("tags"), ["example"], plan)
        self.assertEqual((plan.get("baseline") or {}).get("stage"), "red", plan)


# #100:驗證者的 result 那一塊。計畫與驗紅寫死在這裡,期望值從這裡讀,不從被測程式算。
PLAN_RUN = "python3 -m unittest verify.nav.test_ticket_1"
PLAN_NOTES = "驗證者的備註:P6 要開著篩選鈕"
RED = {"stage": "red", "ok": True, "why": "",
       "files": ["verify/nav/test_ticket_1.py"], "candidate_run": None,
       "baseline": {"cases": 1, "red": ["verify.nav.test_ticket_1.T.test_x"]}}
PLAN_RESULT = json.dumps(
    {"ticket": "1", "role": "verifier", "round": 1, "rc": 0, "baseline": RED,
     "verify": {"files": ["verify/nav/test_ticket_1.py"], "tags": ["log-period"],
                "run": PLAN_RUN, "notes": PLAN_NOTES}},
    ensure_ascii=False)

# A7:解析不到的三種樣子。(iii) 是 #731 的實際寫法:`## result` + 空行 + ```json。
UNPARSABLE = (
    ("prose-only", "no-block",
     "# EVIDENCE-verifier\n\n驗紅做了,案例在 verify/nav/test_ticket_1.py。\n"),
    ("broken-json", "bad-json",
     "# EVIDENCE-verifier\n\n## result\n\n```result\n{\"ticket\": \"1\", \"verify\": \n```\n"),
    ("json-fence", "no-block",
     "# EVIDENCE-verifier\n\n## result\n\n```json\n" + PLAN_RESULT + "\n```\n"),
)

# A8:§8.5 / SCHEMA 允許的寫法(柵欄長度 ≥3 的 ``` 或 ~~~、後面可有空白、前後有散文)。
PARSABLE = (
    ("three-backticks", "```result", "```"),
    ("four-backticks", "````result", "````"),
    ("tildes", "~~~result", "~~~"),
    ("long-tildes", "~~~~~result", "~~~~~"),
    ("trailing-space", "```result   ", "```  "),
    ("space-before-word", "``` result", "```"),
)


class TheVerifierResultIsParsedOrSaysWhyNot(ApplyBase):
    """#100 A7–A9:收驗證者 EVIDENCE 的那一手。

    #731:result 那一塊寫成 `## result` + ```json,收件記成 `no-block`,而 apply 一聲
    不吭 —— verify 與驗紅沒進票,要到落地後 close 被拒「沒有回歸證據」才看得出來。
    #706:併入時 `merged.update(keep)` 把 tags 整組換掉,回歸層少跑 P6。
    """

    def apply_verifier(self, text, run_id="r1"):
        self.deliver("1")
        return self.run_sh("scripts/apply.sh", "1",
                           "--evidence", self.patch_file("EVIDENCE.md", FULL_EVIDENCE),
                           "--evidence-verifier", self.patch_file("EV.md", text),
                           env=self.env(AC_RUN_ID=run_id))

    def drop_branch(self):
        """表驅動的下一列從乾淨的主線重來:收掉這一列套出來的 worktree 與分支。"""
        self.git("worktree", "remove", "--force", self.wt())
        self.git("branch", "-D", "t1")

    def verifier_result(self, run_id):
        with open(os.path.join(self.repo, "reports", "t1", run_id,
                               "result-verifier-round1.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def test_an_unparsable_result_says_so_on_one_line(self):
        """A7。**變異 m8**:把 apply.sh 解析失敗那一行 `print(...)` 拿掉 → 這一條紅。"""
        for name, reason, text in UNPARSABLE:
            with self.subTest(name):
                self.make("1", verify={"files": [], "tags": [], "run": "", "notes": ""})
                before = self.load_ticket("1")
                done = self.apply_verifier(text, run_id=name)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                said = [line for line in (done.stdout + done.stderr).splitlines()
                        if "驗證者" in line and "result" in line and reason in line
                        and "verify 計畫與驗紅沒有併進票" in line]
                self.assertEqual(len(said), 1, done.stdout + done.stderr)
                row = self.verifier_result(name)
                self.assertFalse(row["present"])
                self.assertEqual(row["reason"], reason)
                after = self.load_ticket("1")
                self.assertEqual(after["verify"], before["verify"])
                self.assertEqual(after["state_version"], before["state_version"])
                self.drop_branch()

    def test_every_allowed_fence_is_parsed_and_merged(self):
        """A8。**變異 m6**:`RESULT_OPEN` / `RESULT_CLOSE` 改回只認三個反引號 → 這一條紅。"""
        for name, opening, closing in PARSABLE:
            with self.subTest(name):
                self.make("1")
                text = ("# EVIDENCE-verifier\n\n前面的散文。\n\n## result\n\n"
                        + opening + "\n" + PLAN_RESULT + "\n" + closing
                        + "\n\n後面還有一段散文。\n")
                done = self.apply_verifier(text, run_id=name)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertNotIn("沒有併進票", done.stdout + done.stderr)
                self.assertTrue(self.verifier_result(name)["present"])
                plan = self.load_ticket("1")["verify"]
                self.assertEqual(plan["files"], ["verify/nav/test_ticket_1.py"])
                self.assertEqual(plan["tags"], ["log-period"])
                self.assertEqual(plan["run"], PLAN_RUN)
                self.assertEqual(plan["notes"], PLAN_NOTES)
                self.assertEqual(plan["baseline"], RED)
                shown = self.ticket("show", "1")
                self.assertIn('"baseline"', shown.stdout, shown.stdout + shown.stderr)
                closed = self.ticket("close", "1")
                self.assertNotIn("沒有回歸證據", closed.stdout + closed.stderr)
                self.drop_branch()

    def test_the_merge_keeps_what_the_ticket_already_had(self):
        """A9(#706)。**變異 m7**:併入改回 `merged.update(given)` 整組換 → 這一條紅。"""
        mine = {"stage": "check", "ok": False, "why": "票上原本那一格"}
        self.make("1", verify={"files": ["verify/f1.py"],
                               "tags": ["log-period", "report-period", "report-rows",
                                        "log-filter-button"],
                               "run": "python3 -m unittest verify.票上的",
                               "notes": "票上的備註,不能被蓋",
                               "baseline": mine})
        before = self.load_ticket("1")["state_version"]
        given = json.dumps({"ticket": "1", "role": "verifier", "round": 1, "rc": 0,
                            "verify": {"files": ["verify/f2.py"], "tags": ["log-period"],
                                       "run": "python3 -m unittest verify.驗證者的",
                                       "notes": "驗證者的備註"}}, ensure_ascii=False)
        done = self.apply_verifier("# EVIDENCE-verifier\n\n## result\n\n```result\n"
                                   + given + "\n```\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        after = self.load_ticket("1")
        self.assertEqual(after["verify"]["tags"],
                         ["log-period", "report-period", "report-rows", "log-filter-button"])
        self.assertEqual(after["verify"]["files"], ["verify/f1.py", "verify/f2.py"])
        self.assertEqual(after["verify"]["run"], "python3 -m unittest verify.票上的")
        self.assertEqual(after["verify"]["notes"], "票上的備註,不能被蓋")
        self.assertEqual(after["verify"]["baseline"], mine)
        self.assertEqual(after["state_version"], before + 1)
        out = done.stdout + done.stderr
        differ = [line for line in out.splitlines()
                  if "驗證者給的 run/notes 與票上不同,保留票上" in line]
        self.assertEqual(len(differ), 1, out)
        for text in ("verify.票上的", "verify.驗證者的", "票上的備註", "驗證者的備註"):
            self.assertIn(text, differ[0])
        merged = [line for line in out.splitlines() if "併進票" in line]
        self.assertEqual(len(merged), 1, out)
        self.assertIn("新增 files:verify/f2.py", merged[0])
        self.assertIn("保留", merged[0])
        self.assertIn("tags:log-filter-button", merged[0].split("保留", 1)[1])

    def test_empty_run_and_notes_take_the_verifier_values(self):
        """A9 的另一半:票上 run / notes 空著才採驗證者給的。"""
        self.make("1", verify={"files": ["verify/f1.py"], "tags": ["a"],
                               "run": "", "notes": ""})
        done = self.apply_verifier("# EVIDENCE-verifier\n\n## result\n\n```result\n"
                                   + PLAN_RESULT + "\n```\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        plan = self.load_ticket("1")["verify"]
        self.assertEqual(plan["run"], PLAN_RUN)
        self.assertEqual(plan["notes"], PLAN_NOTES)
        self.assertEqual(plan["tags"], ["a", "log-period"])
        self.assertEqual(plan["files"], ["verify/f1.py", "verify/nav/test_ticket_1.py"])
        self.assertNotIn("保留票上", done.stdout + done.stderr)


class VerifyPlanMergesWithoutAPatch(ApplyBase):
    """#130 A4:`apply.sh verify-plan <票號> --evidence-verifier <檔>…` —— 驗證者這輪 patch-verify
    0 byte、只改 result 的 verify。只跑 apply 既有的那一段合併(同鎖、同解析、只收 stage=red),
    不建分支、不 commit、不碰 patch。期望值是這一檔上面寫死的 PLAN_RESULT / RED。"""

    def refs(self):
        return (self.git("branch", "--list").splitlines(), self.git("rev-parse", "HEAD").strip(),
                self.git("worktree", "list", "--porcelain").count("worktree "))

    def verify_plan(self, text, *extra):
        return self.apply("verify-plan", "1", "--evidence-verifier",
                          self.patch_file("EV.md", text), *extra)

    def test_a4_the_plan_and_the_red_baseline_reach_the_ticket_and_git_does_not_move(self):
        """**變異 M4a**:cmd_verify_plan 不呼叫 merge_verifier_plans(直接 exit 0)→ 這一條紅。"""
        self.make("1", verify={"files": [], "tags": [], "run": "", "notes": ""})
        before = self.refs()
        version = self.load_ticket("1")["state_version"]
        done = self.verify_plan("# EVIDENCE-verifier\n\n## result\n\n```result\n"
                                + PLAN_RESULT + "\n```\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        plan = self.load_ticket("1")["verify"]
        self.assertEqual(plan["files"], ["verify/nav/test_ticket_1.py"])
        self.assertEqual(plan["tags"], ["log-period"])
        self.assertEqual(plan["run"], PLAN_RUN)
        self.assertEqual(plan["notes"], PLAN_NOTES)
        self.assertEqual(plan["baseline"], RED)
        self.assertGreater(self.load_ticket("1")["state_version"], version)
        self.assertEqual(self.refs(), before, "verify-plan 不建分支、不 commit、不開 worktree")
        self.assertFalse(self.branch_exists("t1"))

    def test_a4_a_baseline_that_claims_check_is_not_merged(self):
        """**變異 M4**:verify-plan 另寫一份合併、不收 stage 限制 → 這一條紅。"""
        self.make("1", verify={"files": ["verify/nav/test_ticket_1.py"],
                               "tags": ["example"], "run": "", "notes": ""})
        done = self.verify_plan(VERIFIER_EVIDENCE_WITH_RED % "check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])
        self.assertIn("不是 stage=red", done.stderr)

    def test_a4_an_unparsable_result_is_loud_and_nonzero(self):
        """**變異 M4b**:merge_verifier_plans 解析不到照舊 `sys.exit(0)` → rc 0 → 這一條紅。"""
        self.make("1", verify={"files": [], "tags": [], "run": "", "notes": ""})
        for name, reason, text in UNPARSABLE:
            with self.subTest(name):
                before = self.load_ticket("1")
                done = self.verify_plan(text)
                self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("驗證者的 result 解析不到", done.stderr)
                self.assertIn(reason, done.stderr)
                after = self.load_ticket("1")
                self.assertEqual(after["verify"], before["verify"])
                self.assertEqual(after["state_version"], before["state_version"])

    def test_a4_help_lists_verify_plan(self):
        done = self.apply("--help")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("sh scripts/apply.sh verify-plan <票號> --evidence-verifier <檔>", done.stdout)


class TheCopyRootComesFromTheMainRepo(ApplyBase):
    """#46 C1:副本根以**主 repo 根**解析(`scripts/wtbase.sh`),與 auto-fix / land / review
    同一個來源。以前 apply.sh 用 `$ROOT` 拼:在票分支的 worktree 裡手跑時 `$ROOT` 是
    worktree 自己,副本開到 `…-wt/t1-wt/` 底下。

    期望路徑由這裡從夾具的主 repo 路徑拼,不問被測腳本;比較前兩邊都過 `realpath`
    (macOS 的 `/var` → `/private/var`),同 test_auto_fix 的 TheCopyRootComesFromTheMainRepo。
    """

    def test_run_from_a_ticket_worktree_the_copy_opens_under_the_main_repo_wt(self):
        """**變異 M1**:apply.sh 改回 `$ROOT` 拼 WTBASE → 這一條紅。分支 t2 已經在(主線從別處
        fetch 回來的那一種)而 worktree 不在:apply 把它掛在主 repo 的 `-wt` 底下。"""
        self.make("2")
        wt = os.path.join(self.home, "repo-wt", "t1")
        self.git("worktree", "add", "-q", wt, "-b", "t1")
        self.git("branch", "t2", "main")
        done = self.run_sh(os.path.join(wt, "scripts", "apply.sh"), "2", cwd=wt)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        expected = os.path.realpath(os.path.join(self.home, "repo-wt", "t2"))
        found = [line for line in done.stdout.splitlines()
                 if line.startswith("apply: 分支 t2 掛上 worktree ")]
        self.assertEqual(len(found), 1, done.stdout)
        copy = found[0][len("apply: 分支 t2 掛上 worktree "):]
        self.assertEqual(os.path.realpath(copy), expected)
        self.assertTrue(os.path.isdir(expected), done.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.home, "repo-wt", "t1-wt")), "副本巢狀了")


class TheBranchNameComesFromTheTicket(ApplyBase):
    """#53 D3(D-018):分支名**只有一個來源 —— 票的 `branch` 欄**。`AC_BRANCH` > 票的
    `branch` > `t<n>`;用到的名字寫回票。下游專案的票用 `t<n>-<slug>`,apply 寫死 `t<n>`
    時 auto-fix / review 讀票就找不到這條分支。分支名由夾具寫進票,不問被測腳本。
    """

    def test_a_ticket_branch_is_the_branch_and_the_worktree(self):
        """**變異 M4**:apply.sh 改回 `BR=${AC_BRANCH:-t$ID}` → 這一條紅。"""
        self.make("7", branch="t7-gaps")
        done = self.deliver_apply("7", branch="t7-gaps")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(self.branch_exists("t7-gaps"), done.stdout)
        self.assertFalse(self.branch_exists("t7"), "另開了一條 t7")
        self.assertTrue(os.path.isdir(self.wt("t7-gaps")), done.stdout)
        self.assertEqual(self.load_ticket("7").get("branch"), "t7-gaps")

    def test_an_empty_ticket_branch_opens_t_n_and_writes_it_back(self):
        self.make("7")
        done = self.deliver_apply("7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(self.branch_exists("t7"), done.stdout)
        self.assertEqual(self.load_ticket("7").get("branch"), "t7")

    def test_ac_branch_that_disagrees_with_the_ticket_is_refused(self):
        self.make("7", branch="t7-a")
        done = self.run_sh("scripts/apply.sh", "7", env=self.env(AC_BRANCH="t7-b"))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("t7-a", done.stderr)
        self.assertIn("t7-b", done.stderr)
        self.assertFalse(self.branch_exists("t7-b"), done.stdout)
        self.assertEqual(self.load_ticket("7").get("branch"), "t7-a")


class TheTicketItself(ApplyBase):

    def test_the_verifier_hand_in_merges_verify_files_with_a_patch_verify(self):
        """#101 A4:驗證者收件照既有角色管道把 verify.files 併進票(同一手帶 patch-verify)。
        量票的 verify.files;紅 = 驗證者收件併不進票;期望值 = 夾具的 PLAN_RESULT。"""
        self.make("1", needs_verifier=True)
        vcase = VERIFY_CASE.replace("verify/example/test_ticket_1.py", "verify/nav/test_ticket_1.py")
        done = self.deliver_apply("1", self.patch_file("pv.diff", vcase),
                                  "--evidence", self.patch_file("EVIDENCE.md", FULL_EVIDENCE),
                                  "--evidence-verifier",
                                  self.patch_file("EV.md", "# EV\n\n## result\n\n```result\n"
                                                  + PLAN_RESULT + "\n```\n"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("併不進票", done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["verify"]["files"], ["verify/nav/test_ticket_1.py"])

    def test_a_ticket_with_no_base_sha_is_refused(self):
        self.make("1", base_sha="")
        done = self.deliver_apply("1")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("base_sha", done.stderr)

    def test_a_missing_ticket_is_named(self):
        done = self.apply("9")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("#9", done.stderr)


KILL_CALL = "os." + "kill(pid, 9)"
KILLING_TEST = """diff -ruN base/tests/test_k.py work/tests/test_k.py
--- base/tests/test_k.py\t1970-01-01 08:00:00
+++ work/tests/test_k.py\t2026-09-21 10:00:00
@@ -0,0 +1 @@
+%s
""" % KILL_CALL


class TheHardBlock(ApplyBase):
    """D-054 Q2、Q3(#135):硬擋只剩 config 的 `hard_block`;收件量的是這張票的改動 =
    分支上 worker 的 commit 加 patch-verify。期望值 = 測試寫進 config 的那幾格。"""

    def protect(self, *globs, patterns=()):
        conf = json.loads(self.read("board/config.json"))
        conf["hard_block"] = {"paths": list(globs), "dirs": [], "patterns": list(patterns)}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def test_a_worker_commit_on_a_hard_block_path_is_refused_by_name_and_field(self):
        """**變異 M4**:收件不量分支上的 commit(只量 patch-verify)→ 這一條紅。"""
        self.protect("config/**", "docs/DECISIONS.md")
        self.make("1")
        self.deliver("1", {"config/private.ini": "a private setting\n",
                           "docs/secret.md": "a secret doc\n"})
        done = self.apply("1")
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        line = [row for row in done.stderr.splitlines() if "config/private.ini" in row]
        self.assertEqual(len(line), 1, done.stderr)
        self.assertIn("board/config.json 的 hard_block.paths", line[0])
        self.assertIn("`config/**`", line[0])
        self.assertNotIn("docs/secret.md", done.stderr)
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "1",
                         "收件這一手沒有 commit")

    def test_a_pattern_in_a_patch_verify_test_line_is_refused_and_backed_out(self):
        """tests/** 的新增行命中 `hard_block.patterns` → rc 5、patch-verify 退出來、分支不動。
        **變異**:收件不把 stage 的 patch-verify 交給硬擋 → 這一條紅。"""
        self.protect(patterns=[r"os\.kill"])
        self.make("1")
        self.deliver("1")
        head = self.git("rev-parse", "t1").strip()
        done = self.apply("1", self.patch_file("v.diff", KILLING_TEST))
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        line = [row for row in done.stderr.splitlines() if "tests/test_k.py" in row]
        self.assertEqual(len(line), 1, done.stderr)
        self.assertIn("hard_block.patterns", line[0])
        self.assertEqual(self.git("rev-parse", "t1").strip(), head)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.wt()), "",
                         "patch-verify 要退出來,不留在 index / 工作樹")

    def test_the_same_pattern_outside_tests_and_verify_is_not_refused(self):
        """patterns 只查 tests/** 與 verify/** 的新增行:scripts 自己合法用 kill。
        **變異 M2**:patterns 對 scripts/** 也查 → 這一條紅。"""
        self.protect(patterns=[r"os\.kill"])
        self.make("1")
        self.deliver("1", {"scripts/k.py": KILL_CALL + "\n"})
        done = self.apply("1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class TheGateResidueOfTheLastRound(ApplyBase):
    """#57 (a):第 2 輪起 apply 沿用閘門跑過的 worktree,上一輪閘門寫的 `gate.log*` 還在。

    這一份 repo 的 `.gitignore` 沒有 `gate.log*`(下游專案的形狀 —— 真 repo 與沙盒預設都
    忽略它們,所以不拿掉就測不到):它們是**未追蹤**的殘留,不是 patch 的改動。
    """

    def setUp(self):
        super(TheGateResidueOfTheLastRound, self).setUp()
        kept = [line for line in self.read(".gitignore").splitlines()
                if not line.startswith("gate.log")]
        self.write(".gitignore", "\n".join(kept) + "\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "下游專案:gate.log 沒被忽略")

    def test_untracked_gate_logs_in_the_worktree_stay_out_of_the_patch_verify_commit(self):
        """**變異 M2**:patch-verify 改回 `git add -A` 再 commit → 分支上多出 gate.log*,紅。"""
        self.make("1")
        self.deliver("1")
        self.write("gate.log", "Ran 1 test\n", where=self.wt())
        self.write("gate.log.verify-check", "check\n", where=self.wt())
        self.write(os.path.join("gate.log.verify-case.d", "x.log"), "case\n", where=self.wt())
        self.assertIn("?? gate.log.verify-check",
                      self.git("status", "--porcelain", cwd=self.wt()),
                      "前提:殘留是未追蹤、沒被忽略的檔")

        done = self.run_sh("scripts/apply.sh", "1", self.patch_file("v.diff", CREATE),
                           env=self.env(AC_ROUND="2"))

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("show", "t1:src/b.txt"), "made by the patch\n")
        committed = self.git("show", "--name-only", "--format=", "t1").split()
        self.assertEqual(committed, ["src/b.txt"], "殘留不進 commit")
        self.assertTrue(os.path.isfile(os.path.join(self.wt(), "gate.log.verify-check")),
                        "殘留是上一輪的證據,不刪")


def land_commit(case, files, subject="t1 fix: 批次 worker 的 commit"):
    """批次 worker 的交件(#145):在 land/X 頭上 commit 一次。`files` = {路徑: 新內容};
    用拋棄式 detached worktree 做完就收掉,land/X 本身不被 worktree 佔著(apply 自己開)。"""
    scratch = os.path.join(case.home, "scratch-land")
    case.git("worktree", "add", "-q", "--detach", scratch, "land/X")
    for path, text in files.items():
        case.write(path, text, where=scratch)
    case.git("add", "-A", cwd=scratch)
    case.git("commit", "-q", "-m", subject, cwd=scratch)
    case.git("branch", "-f", "land/X", "HEAD", cwd=scratch)
    case.git("worktree", "remove", "--force", scratch)


TEST_X = ("import unittest\n\n\nclass T(unittest.TestCase):\n"
          "    def test_one(self):\n        self.assertEqual(1, 1)\n")


class ApplyBatch(ApplyBase):
    """#108 A2–A4,#145:批次 worker 在 land/X 上 commit,`apply.sh --batch land/X 1 2 --base <派工前>`
    不收 patch、不另加 commit(沒有重產時),各票分支與票檔一個位元都不動;寫入範圍硬擋只剩 config 的
    `hard_block`(D-054 Q2);測試檔裡刪斷言 / 加 skip 是「放寬測試」,rc 5 —— 除非 EVIDENCE 對那條
    案例提了 test_defect。期望值從夾具寫下的票檔與 `git rev-parse` 讀,不從 apply.sh 算。"""

    def setUp(self):
        super().setUp()
        self.batch = self.make_batch()
        self.tickets_before = {ident: self.read("tickets/%s.json" % ident) for ident in ("1", "2")}

    def run_batch(self, files, evidence=None, ids=("1", "2")):
        land_commit(self, files)
        args = ["--batch", "land/X", *ids, "--base", self.batch["land"]]
        if evidence is not None:
            args += ["--evidence", self.patch_file("EVIDENCE.md", evidence)]
        return self.apply(*args)

    def assert_only_the_worker_commit(self):
        self.assertEqual(self.git("rev-list", "--count", "%s..land/X" % self.batch["land"]).strip(),
                         "1", "收件不另加 commit")

    def head(self, name="land/X"):
        return self.git("rev-parse", name).strip()

    def result_round1(self):
        found = glob.glob(os.path.join(self.repo, "reports", "batch", "*", "result-round1.json"))
        self.assertEqual(len(found), 1, found)
        with open(found[0], encoding="utf-8") as handle:
            return json.load(handle)

    def test_the_worker_commit_is_taken_as_is_and_touches_no_ticket(self):
        """A2(#145)。量什麼 = land/X 上只有 worker 那一個 commit(收件不另加、不改它)、紀錄目錄沒有
        任何 patch 檔、t1-a / t2-b 與 1.json / 2.json 位元不變;為什麼會紅 = 收件另疊 commit、寫了票;
        期望值來源 = 夾具的 rev-parse 與票檔原文;不測 = 閘門。

        **變異 ②**:apply --batch 對票 1 跑一次 `ticket.py set 1 patch_ref` → 票檔那一條紅。
        """
        done = self.run_batch({"src/one.txt": "one fixed\n", "src/two.txt": "two fixed\n"})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("rev-parse", "land/X^").strip(), self.batch["land"])
        self.assert_only_the_worker_commit()
        self.assertEqual(self.git("show", "land/X:src/one.txt"), "one fixed\n")
        self.assertEqual(self.git("log", "-1", "--format=%s", "land/X").strip(),
                         "t1 fix: 批次 worker 的 commit")
        self.assertEqual(glob.glob(os.path.join(self.repo, "reports", "batch", "*", "*.diff")), [])
        for branch, sha in self.batch["shas"].items():
            self.assertEqual(self.head(branch), sha, "%s 被動了" % branch)
        for ident, text in self.tickets_before.items():
            self.assertEqual(self.read("tickets/%s.json" % ident), text,
                             "#%s 的票檔被寫了(state_version / review 會跟著漂)" % ident)

    def test_hard_block_paths_block_a_file_of_a_ticket_in_the_batch(self):
        """A3 的硬擋那一格。量 rc 5 + 「hard_block」;紅 = 批次路不量 hard_block;
        期望值 = 測試寫進 config 的 hard_block.paths。"""
        conf = json.loads(self.read("board/config.json"))
        conf["hard_block"] = {"paths": ["src/two.txt"], "dirs": [], "patterns": []}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        done = self.run_batch({"src/two.txt": "two fixed\n"})
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("src/two.txt", done.stderr)
        self.assertIn("hard_block", done.stderr)
        self.assert_only_the_worker_commit()

    def test_deleting_an_assert_in_a_test_file_is_blocked(self):
        """A4。量 rc 5 + 「tests/test_x.py:6 放寬測試」+ 收件沒另加 commit;紅 = 守衛只印不擋;
        期望值 = 夾具 test_x.py 第 6 行就是那一行 assertEqual。

        **變異 ①**:放寬測試守衛改成只印不擋 → 這一條 rc 紅。
        """
        done = self.run_batch({"tests/test_x.py": TEST_X.replace("self.assertEqual(1, 1)", "pass")})
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("tests/test_x.py:6 放寬測試", done.stderr)
        self.assert_only_the_worker_commit()

    def test_adding_a_skip_in_a_test_file_is_blocked(self):
        """A4 的 skip 那一半。量 rc 5 + 新增那一行的行號;紅 = 只看刪掉的行;
        期望值 = skip 插在第 5 行(def 之前)。"""
        body = TEST_X.replace("    def test_one", "    @unittest.skip(\"紅的先關掉\")\n    def test_one")
        done = self.run_batch({"tests/test_x.py": body})
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("tests/test_x.py:5 放寬測試", done.stderr)

    def test_a_test_defect_objection_on_that_case_lets_it_through(self):
        """A4 的豁免。量 stderr 沒有「放寬測試」、rc 6(反駁沒收進票);
        紅 = 豁免沒認案例檔或反駁寫進票;期望值 = 案例 tests.test_x.T.test_one ⇒ tests/test_x.py、
        result-round1.json 的 objections 就是 EVIDENCE 那一行(A4 改版)。"""
        done = self.run_batch(
            {"tests/test_x.py": TEST_X.replace("self.assertEqual(1, 1)", "pass")},
            evidence="# EVIDENCE\nOBJECTION: test_defect tests.test_x.T.test_one 斷言本身錯\n")
        self.assertEqual(done.returncode, 6, done.stdout + done.stderr)
        self.assertNotIn("放寬測試 ——", done.stderr)
        self.assert_only_the_worker_commit()
        for ident, text in self.tickets_before.items():
            self.assertEqual(self.read("tickets/%s.json" % ident), text)
        self.assertEqual(self.result_round1()["objections"],
                         ["OBJECTION: test_defect tests.test_x.T.test_one 斷言本身錯"],
                         "反駁要留在 result-round1.json(不記進票)")

    def test_a_test_defect_objection_on_another_case_does_not(self):
        """A4 豁免只對那條案例的檔。量 rc 5;紅 = 有任何 test_defect 就全放;
        期望值 = 反駁點名的是 tests/test_other.py。"""
        done = self.run_batch(
            {"tests/test_x.py": TEST_X.replace("self.assertEqual(1, 1)", "pass")},
            evidence="# EVIDENCE\nOBJECTION: test_defect tests.test_other.T.test_one 別的案例\n")
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("tests/test_x.py:6 放寬測試", done.stderr)

    def test_no_commit_since_the_base_is_rc4(self):
        """#145:收件的對象是 `<base>..land/X` 的 commit;一個都沒有 ⇒ rc 4、land/X 不動、沒有 result;
        紅 = 沒交也照收;期望值 = 夾具的 land/X 頭當 base。"""
        done = self.apply("--batch", "land/X", "1", "2", "--base", self.batch["land"])
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("沒有新 commit", done.stderr)
        self.assertEqual(self.head(), self.batch["land"])
        self.assertEqual(glob.glob(os.path.join(self.repo, "reports", "batch", "*",
                                                "result-round1.json")), [])

    def test_the_base_defaults_to_the_last_merge_on_land(self):
        """#145:沒給 --base ⇒ 整合分支 first-parent 上最近一個 merge(最後併進來的那張票)。
        量 rc 0 與輸出的 base;紅 = 預設成別的(例如 main 或 HEAD^ 之外的點);期望值 = 夾具的 land/X 頭
        (merge t2-b 那一個)。"""
        land_commit(self, {"src/one.txt": "one fixed\n"})
        land_commit(self, {"src/two.txt": "two fixed\n"}, subject="t2 fix: 第二個 commit")
        done = self.apply("--batch", "land/X", "1", "2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("base %s  2 個 commit" % self.batch["land"][:12], done.stdout)


MAP_CATALOG = {"version": 1, "managed": ["scripts/*.py", "tests/test_*.py"],
               "topics": {"memory": {"summary": "沙盒", "match": ["memory"],
                                     "entry": "scripts/memory.py"}}}


class TheCodeMapIsRebuiltIntoACommitOfItsOwn(ApplyBase):
    """#96、D-051:候選樹有 `scripts/repo-map.py` 與 `code-map/catalog.json` ⇒ 收件時跑一次
    `repo-map.py build`,產出物有變就在分支上補一個 commit(訊息一行 `regen: repo-map build`;#144 起
    worker 自己 commit,重產不再併進它的 commit)。
    沙盒照 test_repo_map_contract 的 A32:複製真的 repo-map.py、寫 catalog、build、commit 進主線。
    期望值(新檔名、commit 數、字樣)抄票面,不從 repo-map 算回來。
    """

    def setUp(self):
        super(TheCodeMapIsRebuiltIntoACommitOfItsOwn, self).setUp()
        shutil.copy(os.path.join(SCRIPTS, "repo-map.py"),
                    os.path.join(self.repo, "scripts", "repo-map.py"))
        self.write(os.path.join("code-map", "catalog.json"),
                   json.dumps(MAP_CATALOG, ensure_ascii=False, indent=2) + "\n")
        # gate.sh 把 code-map/* 的改動對到 test_repo_map_contract;沙盒放一支會綠的替身。
        self.write(os.path.join("tests", "test_repo_map_contract.py"), TEST_X)
        self.write(os.path.join("tests", "test_kept.py"), TEST_X)
        done = self.run_py("scripts/repo-map.py", "build")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.make("1", state="Running")

    def regen_lines(self):
        return [line for line in self.git("log", "-1", "--format=%B", "t1").splitlines()
                if line.startswith("regen:")]

    def apply_a_new_managed_file(self, *args):
        """交件只新增一支已管理的檔、不含 code-map/ 的改動(漏重生)。"""
        self.deliver("1", {"tests/test_memory_new.py": TEST_X})
        done = self.apply("1", *args)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_stale_code_map_is_rebuilt_into_a_commit_of_its_own(self):
        """A4。量 check rc、managed、commit 數、regen 行;
        紅 = 拿掉重產那一步(m3)時 check rc=1、managed 沒有新檔;
        期望值 = patch 新增的檔名 tests/test_memory_new.py。
        """
        self.apply_a_new_managed_file("--evidence", self.patch_file("EVIDENCE.md", FULL_EVIDENCE))
        wt = self.wt()
        check = self.run_py(os.path.join(wt, "scripts", "repo-map.py"), "--root", wt, "check",
                            cwd=wt)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        with open(os.path.join(wt, "code-map", "index.json"), encoding="utf-8") as handle:
            self.assertIn("tests/test_memory_new.py", json.load(handle)["managed"])
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "2",
                         "worker 的 commit + 重產那一個")
        self.assertEqual(len(self.regen_lines()), 1, self.git("log", "-1", "--format=%B", "t1"))
        self.assertTrue(self.regen_lines()[0].startswith("regen: repo-map build"))

    def test_an_unchanged_map_adds_no_regen_line_and_no_code_map_diff(self):
        """A6。量 regen 行與分支 diff 的檔名;紅 = 不比對前後、每次都加 regen 行;
        期望值 = 只改既有檔內文時分支 diff 只有 tests/test_kept.py。
        """
        self.deliver("1", {"tests/test_kept.py": TEST_X.replace("test_one", "test_two")})
        done = self.apply("1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.regen_lines(), [])
        self.assertEqual(self.git("diff", "--name-only", "main..t1").split(),
                         ["tests/test_kept.py"])

    def test_a_failed_rebuild_does_not_block_the_apply(self):
        """A7。量 rc、commit 數、輸出字樣;紅 = 重產失敗改成 die(m4)時 rc≠0;
        期望值 = 票面字樣「索引沒重產」。
        """
        broken = dict(MAP_CATALOG, topics=dict(MAP_CATALOG["topics"], ghost={
            "summary": "指到不存在的檔", "match": ["ghost"], "entry": "scripts/ghost.py"}))
        self.deliver("1", {"code-map/catalog.json":
                           json.dumps(broken, ensure_ascii=False, indent=2) + "\n"})
        done = self.apply("1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "1")
        self.assertIn("索引沒重產", done.stdout + done.stderr)



class TheBatchRebuildsTheCodeMap(ApplyBase):
    """#129 A1–A2:`apply.sh --batch` 與單票那一支同一個判準(候選樹有 repo-map.py 與 catalog),
    產出物有變就在整合分支上補一個 commit(#145 起 worker 自己 commit,重產不再併進它的 commit)。沙盒照 #96 那一類:真的 repo-map.py + catalog +
    新鮮索引進主線,再照 ApplyBatch 開整合分支 land/X(test_x.py 先寫好再 build,主線索引才是新的)。
    期望值(新檔名、commit 數、字樣)抄票面,不從 repo-map 算回來。"""

    def setUp(self):
        super(TheBatchRebuildsTheCodeMap, self).setUp()
        shutil.copy(os.path.join(SCRIPTS, "repo-map.py"),
                    os.path.join(self.repo, "scripts", "repo-map.py"))
        self.write(os.path.join("code-map", "catalog.json"),
                   json.dumps(MAP_CATALOG, ensure_ascii=False, indent=2) + "\n")
        self.write(os.path.join("tests", "test_kept.py"), TEST_X)
        self.write(os.path.join("tests", "test_x.py"), TEST_X)
        done = self.run_py("scripts/repo-map.py", "build")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的地圖")
        self.batch = self.make_batch()

    def run_batch(self, files):
        land_commit(self, files)
        return self.apply("--batch", "land/X", "1", "2", "--base", self.batch["land"])

    def wt(self, name="batch-land-X"):
        return os.path.join(self.home, "repo-wt", name)

    def message(self):
        return self.git("log", "-1", "--format=%B", "land/X")

    def test_a1_a_stale_code_map_is_rebuilt_into_the_batch_commit(self):
        """A1。量 commit 數、regen 行、副本 check、managed、字樣;
        紅 = **m1**(拿掉 --batch 的重產)時 check rc=1、沒有 regen 行;
        期望值 = worker 新增的檔名 tests/test_batch_new.py;commit 數 = worker 的 + 重產那一個。"""
        done = self.run_batch({"tests/test_batch_new.py": TEST_X})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("rev-list", "--count",
                                  "%s..land/X" % self.batch["land"]).strip(), "2")
        self.assertTrue(any(line.startswith("regen: repo-map build")
                            for line in self.message().splitlines()), self.message())
        wt = self.wt()
        check = self.run_py(os.path.join(wt, "scripts", "repo-map.py"), "--root", wt, "check",
                            cwd=wt)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        with open(os.path.join(wt, "code-map", "index.json"), encoding="utf-8") as handle:
            self.assertIn("tests/test_batch_new.py", json.load(handle)["managed"])
        self.assertIn("批次 code-map 索引重產", done.stdout + done.stderr)

    def test_a2_an_unchanged_map_adds_no_regen_line(self):
        """A2 前半。量 regen 行與那個 commit 的檔名;紅 = 每次都加 regen 行;
        期望值 = 只改 tests/test_kept.py 的內文。"""
        done = self.run_batch({"tests/test_kept.py": TEST_X.replace("test_one", "test_two")})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(any(line.startswith("regen:") for line in self.message().splitlines()),
                         self.message())
        self.assertEqual(self.git("diff", "--name-only", "land/X^", "land/X").split(),
                         ["tests/test_kept.py"])

    def test_a2_a_failed_rebuild_does_not_block_the_batch(self):
        """A2 後半。量 rc、commit 數、字樣;紅 = build 失敗改成擋;
        期望值 = 票面字樣「索引沒重產」。"""
        broken = dict(MAP_CATALOG, topics=dict(MAP_CATALOG["topics"], ghost={
            "summary": "指到不存在的檔", "match": ["ghost"], "entry": "scripts/ghost.py"}))
        done = self.run_batch({"code-map/catalog.json":
                               json.dumps(broken, ensure_ascii=False, indent=2) + "\n"})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("rev-list", "--count",
                                  "%s..land/X" % self.batch["land"]).strip(), "1")
        self.assertIn("索引沒重產", done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
