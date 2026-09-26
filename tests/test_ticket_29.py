"""#29(D-020,D-022)流程圖稽核(`docs/FLOW.html` §G,G1–G12)找出的十二個洞。

驗證者案例:只證乾淨基底紅,**不搭參考實作、不做變異、不等 patch**。每個 `test_`
的 docstring 第一行是票面 `acceptance` 的編號(A1–A11)。A12 例外:`docs/DESIGN-
VERIFY-CASES.md` 已由 #25 落地,這裡的案例是**確認**,在乾淨基底就該是綠的(見該
class 的說明;若之後又紅了,代表 #25 的產出被別的票動過)。
A5 在乾淨基底上就綠、屬確認用案例(同 A12)。

D-022 的 reviewer 角色卡與 `rules.py WANTED[reviewer]`(派工文說「算在 A1 內」)併
在 `Acceptance1DesignAndReviewerRolesExist` 裡,不另開一條。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 原文 /
## `docs/FLOW.html` §G 的 G1–G12)
A1  | unit    | `rules.py pack design/reviewer`、讀 `memory/role/`、`docs/ROLES.md`、`tickets/SCHEMA.md`+`docs/DESIGN.md` | rc / 檔存在 / 表格列 / 字樣命中 | 票面 acceptance A1(D-022 併 reviewer)
A2  | unit    | 讀 `templates/dispatch-opener.md`、`memory/role/opener.md`、`docs/ROLES.md` | 檔存在 / 字樣命中 / grep 計數 | 票面 acceptance A2
A3  | sandbox | 沒有 `reports/t1/` 時跑 `auto-fix.sh 1 --dry-run --round 1` | rc、stdout 含票號 | 票面 acceptance A3
A4  | sandbox | `apply.sh 1 <patch> --evidence <E>`(E 帶 result 區塊 / OBJECTION 行) | `reports/t1/*/result-round1.json` 是否存在;票的 `objections[]` | 票面 acceptance A4
A5  | sandbox | `apply.sh 1 <patch> --evidence-verifier <E>`;讀 `memory/role/verifier.md`、`templates/dispatch-verifier.md` | `reports/t1/*/result-verifier-round1.json` 存在且 `present:true`;兩份文件各 ≥1 次 | 票面 acceptance A5(#35 A4 改測行為)
A6  | sandbox | 真跑一輪紅 → worker 修好 → 閘門綠 → InReview | `fix-t1/round2/work`、`/base` 是否還在 | 票面 acceptance A6
A7  | sandbox | 手造票(baseline 缺、review.sha 在主線)走 `ticket.py close` | stdout 是否有一行同時含真的 `--ref <base_sha>` 與 `--candidate <review_sha>` | 票面 acceptance A7
A8  | sandbox/unit | 讀 `memory/role/consolidator.md`、`templates/dispatch-consolidator.md`;真跑 `memory.py check` | 檔存在;開出的整理票 `outline` 欄 | 票面 acceptance A8
A9  | unit    | 無 `.git` 的副本(有 `board/config.json`)內跑 `event.py emit` | rc、副本自己的 `board/events.jsonl` 是否被寫出 | 票面 acceptance A9
A10 | sandbox | `land.sh docs "<訊息>" tickets/1.json` | rc | 票面 acceptance A10
A11 | unit    | 空 dest(無任何 `scripts/*.sh` 呼叫點)跑 `sync-to-project.sh` | stdout 是否含「同步了但專案端沒有呼叫點」 | 票面 acceptance A11
A12 | unit    | 讀 `docs/DESIGN-VERIFY-CASES.md`、`docs/DECISIONS.md`、`docs/FLOW.html` | 字樣命中(確認,非證紅) | 票面 acceptance A12(已由 #25 落地)

## 介面字串(每條斷言靠哪個字串;票面沒定的在這裡定,EVIDENCE 不重抄)
"設計 session"          ← A1 斷言 `docs/ROLES.md` 角色表含它
"未來每票"              ← A1 斷言 `tickets/SCHEMA.md`/`docs/DESIGN.md` 含它
"TICKET-OPENER-PROMPT"  ← A2 斷言 `memory/role/opener.md` 的命中次數
"outline"               ← A2 斷言 `memory/role/opener.md` 交付物欄提到這個欄名
"EVIDENCE-verifier"     ← A5 斷言兩份文件各命中 ≥1 次
"result-verifier-round1" ← A5 斷言 `apply.sh --evidence-verifier` 抽出的檔名
"--ref" / "--candidate" ← A7 斷言 `ticket.py close` 印出的那一行同時含這兩個旗標與真的 sha
"templates/dispatch-consolidator.md" ← A8 斷言整理票 `outline` 欄引用它
"同步了但專案端沒有呼叫點" ← A11 斷言 `sync-to-project.sh` 的 stdout 含它
"#29 A1".."#29 A12"     ← A12 斷言 `docs/FLOW.html` §G 都點名 #29

## 怎麼做假(不上真埠、不起真服務、不殺行程、不鎖螢幕)
A1/A2/A9/A11 讀既有檔案或用 `subprocess` 真跑一次性的腳本指令(`rules.py
pack`、`sync-to-project.sh`),不碰任何 repo 的 git 狀態。確認組(乾淨基底上就綠):
A12 讀既有檔案;A5 在 `Sandbox` 裡真跑 `apply.sh --evidence-verifier`、另讀兩份文件。A3/A4/A6/A7/A8/A10 用
`tests/control_harness.py` 的 `Sandbox`——一顆 tempdir 裡的拋棄式真 git repo(埠
一律 0,`HOME`/`GIT_CONFIG_*` 都指進沙盒),不是這台機器上任何一個真的 repo。A9
與 A11 各自的拋棄式目錄用 `tempfile.mkdtemp()` + `case.addCleanup(shutil.rmtree,
…, True)`(與 `verify/_template_ticket.py` 的 `a_workdir()` 同形),不用
`with tempfile.TemporaryDirectory()`(F5)。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不碰 verify_strings 的原字(落地 grep
會命中自己);不搭參考實作、不做變異、不等 patch(D-020)。
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import DEFAULT_CONFIG, Sandbox, write_executable  # noqa: E402

TAGS = ["flow-audit"]   # 登記:verify/TAGS.d/29.md

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


def exists(rel):
    return os.path.exists(os.path.join(ROOT, rel))


# ---------------------------------------------------------------- acceptance 1


class Acceptance1DesignAndReviewerRolesExist(unittest.TestCase):
    """G1:設計 session(Fable)沒有角色卡、`rules.py WANTED` 沒有 design、
    `docs/ROLES.md` 角色表沒有這一列;D-022 待補的 reviewer 同一形狀,派工文說
    「算在 #29 A1 內」。"""

    def test_a1_design_role_card_is_missing(self):
        """A1 `memory/role/design.md` 存在(≤40 行,含 讀什麼/交什麼/不做/誰派/何時結束)。"""
        self.assertTrue(exists("memory/role/design.md"),
                        "乾淨基底上還沒有 memory/role/design.md")

    def test_a1_rules_py_pack_design_fails(self):
        """A1 `python3 scripts/rules.py pack design --model fable` rc=0。"""
        done = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "rules.py"),
             "pack", "design", "--model", "fable"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0,
                         "乾淨基底上 rules.py 還不認得 design 角色:"
                         + done.stdout + done.stderr)

    def test_a1_roles_doc_has_no_design_session_row(self):
        """A1 `docs/ROLES.md` 角色表多一列「設計 session」。"""
        table_rows = [line for line in read("docs/ROLES.md").splitlines()
                     if line.startswith("|") and "設計 session" in line]
        self.assertTrue(table_rows,
                        "docs/ROLES.md 的角色表現在還沒有「設計 session」那一列")

    def test_a1_design_doc_fixed_sections_are_undefined(self):
        """A1 `tickets/SCHEMA.md` 或 `docs/DESIGN.md` 定義 `docs/DESIGN-<題>.md`
        的固定段(結論表含「未來每票省/多花」欄、後續工作的模型表)。"""
        hits = read("tickets/SCHEMA.md").count("未來每票") \
            + read("docs/DESIGN.md").count("未來每票")
        self.assertGreaterEqual(hits, 1,
                                "tickets/SCHEMA.md 與 docs/DESIGN.md 都還沒有定義 "
                                "docs/DESIGN-<題>.md 的固定段落(「未來每票省/多花」欄)")

    def test_a1_reviewer_role_card_and_rules_entry_are_missing(self):
        """A1(D-022 併入):同樣形狀加一份 `memory/role/reviewer.md` 與
        `rules.py WANTED[reviewer]`。"""
        self.assertTrue(exists("memory/role/reviewer.md"),
                        "乾淨基底上還沒有 memory/role/reviewer.md(D-022 待補)")
        done = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "rules.py"),
             "pack", "reviewer", "--model", "opus"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0,
                         "乾淨基底上 rules.py 還不認得 reviewer 角色:"
                         + done.stdout + done.stderr)


# ---------------------------------------------------------------- acceptance 2


class Acceptance2OpenerHasATemplate(unittest.TestCase):
    """G2:主線 → 開題者的派工沒有範本檔;`opener.md` 指到不存在的
    `docs/TICKET-OPENER-PROMPT.md(專案自備)`。"""

    def test_a2_dispatch_opener_template_is_missing(self):
        """A2 `templates/dispatch-opener.md` 存在,含「四件事」的四個佔位。"""
        self.assertTrue(exists("templates/dispatch-opener.md"),
                        "乾淨基底上還沒有 templates/dispatch-opener.md")

    def test_a2_opener_card_still_points_at_the_missing_project_doc(self):
        """A2 `grep -c TICKET-OPENER-PROMPT memory/role/opener.md == 0`
        (或該檔存在於 docs/)。"""
        mentions = read("memory/role/opener.md").count("TICKET-OPENER-PROMPT")
        doc_exists = exists("docs/TICKET-OPENER-PROMPT.md")
        self.assertTrue(mentions == 0 or doc_exists,
                        "memory/role/opener.md 還指著不存在的 "
                        "docs/TICKET-OPENER-PROMPT.md(命中 %d 次,docs/ 裡沒有這份檔)"
                        % mentions)

    def test_a2_deliverable_wording_does_not_name_the_outline_field(self):
        """A2 `docs/ROLES.md` 與 `memory/role/opener.md` 的交付物欄改寫成
        `outline` 這個欄名(`ticket.py create --outline` 已經有這個旗標)。"""
        self.assertIn("outline", read("memory/role/opener.md"),
                      "memory/role/opener.md 的交付物欄還沒有點名 outline 欄")


# ---------------------------------------------------------------- acceptance 3


class Acceptance3FirstRoundDispatchViaDryRun(Sandbox):
    """G3:第 1 輪 worker 的派工文由主線手寫,第 2 輪起 `auto-fix.sh` 才產
    `dispatch-round{r}.md`;同一個角色兩輪拿到兩種形狀的派工文。"""

    def test_a3_dry_run_round_1_without_status_is_not_supported(self):
        """A3 `sh scripts/auto-fix.sh <n> --dry-run --round 1`(沒有
        `reports/t<n>/` 時)印出第一輪派工文,rc=0,含 `rules.py pack` 的首行與票號。"""
        self.make_ticket("1", allowed_write_paths=["tests/*"])
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.assertFalse(
            os.path.isdir(os.path.join(self.repo, "reports", "t1")),
            "沙盒自己壞了:這一條要問的是『沒有 reports/t1/ 時』")
        done = self.run_sh("scripts/auto-fix.sh", "1", "--dry-run", "--round", "1")
        self.assertEqual(done.returncode, 0,
                         "auto-fix.sh 現在還不認得 --round,沒有 reports/t1/ 時"
                         "也印不出第一輪的派工文:" + done.stdout + done.stderr)
        self.assertIn("#1", done.stdout)


# ---------------------------------------------------------------- acceptance 4


CHANGE = """diff -ruN base/src/a.txt work/src/a.txt
--- base/src/a.txt\t2026-09-23 10:00:00
+++ work/src/a.txt\t2026-09-23 10:00:00
@@ -1 +1 @@
-old
+new
"""

RESULT_EVIDENCE = """# Evidence

## result
```result
{"present": true, "gate": "green"}
```
"""

OBJECTION_EVIDENCE = """# Evidence

OBJECTION: test_defect 案例本身的 oracle 算錯了

## result
```result
{"present": true}
```
"""


class Acceptance4ApplyExtractsResultAndRecordsObjection(Sandbox):
    """G4:`apply.sh --evidence` 只跑 `memory.py harvest`:第 1 輪沒有
    `result-round1.json`,`OBJECTION:` 行沒人收進 `objections[]`。"""

    def setUp(self):
        super(Acceptance4ApplyExtractsResultAndRecordsObjection, self).setUp()
        self.write("src/a.txt", "old\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線上先有 src/a.txt")
        self.git("push", "-q", "origin", "main")
        self.make_ticket("1")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")

    def result_jsons(self):
        return glob.glob(os.path.join(self.repo, "reports", "t1", "*",
                                      "result-round1.json"))

    def test_a4_evidence_does_not_produce_result_round1_json(self):
        """A4 `apply.sh <n> <patch> --evidence <E>` 之後
        `reports/t<n>/<run_id>/result-round1.json` 存在且 `present:true`。"""
        patch = self.write("p.diff", CHANGE, where=self.home)
        evidence = self.write("EVIDENCE.md", RESULT_EVIDENCE, where=self.home)
        done = self.run_sh("scripts/apply.sh", "1", patch, "--evidence", evidence)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(self.result_jsons(),
                        "apply.sh --evidence 現在還沒有抽 result-round1.json"
                        "(它只跑 memory.py harvest)")

    def test_a4_objection_line_in_evidence_is_not_recorded(self):
        """A4 EVIDENCE 有 `^OBJECTION:` 行時 `apply.sh` 記進票的 `objections[]`
        並 rc≠0 指名。"""
        patch = self.write("p.diff", CHANGE, where=self.home)
        evidence = self.write("EVIDENCE.md", OBJECTION_EVIDENCE, where=self.home)
        self.run_sh("scripts/apply.sh", "1", patch, "--evidence", evidence)
        ticket = self.load_ticket("1")
        self.assertTrue(ticket.get("objections"),
                        "apply.sh --evidence 現在還不會把 OBJECTION: 那一行"
                        "記進票的 objections[](只有 auto-fix.sh 的 round_once 會)")


# ---------------------------------------------------------------- acceptance 5


VERIFIER_EVIDENCE = """# EVIDENCE-verifier

## result
```result
{"ticket": "1", "role": "verifier", "round": 1, "rc": 0}
```
"""


class Acceptance5EvidenceVerifierRecipientIsUndecided(Sandbox):
    """G5:驗證者角色卡要求交 `EVIDENCE-verifier.md`,但沒有任何入口讀它 ——
    一份沒有收件者的交付物。

    #35:以前這裡比「兩份文件的提及數相等 + `apply.sh` 裡有沒有那個子字串」
    —— 旗標只留在註解、抽取整段拿掉照樣綠,正確實作多寫一句提及反而紅。改成真的跑一次
    `apply.sh --evidence-verifier` 看產物;文件側只斷 ≥1。"""

    def setUp(self):
        super(Acceptance5EvidenceVerifierRecipientIsUndecided, self).setUp()
        self.write("src/a.txt", "old\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線上先有 src/a.txt")
        self.git("push", "-q", "origin", "main")
        self.make_ticket("1")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")

    def test_a5_apply_evidence_verifier_writes_result_verifier_round1(self):
        """A5 `apply.sh <n> <patch> --evidence-verifier <E>` 之後
        `reports/t<n>/<run_id>/result-verifier-round1.json` 存在且 `present` 為 true。

        **變異**:`apply.sh` 的 `--evidence-verifier` 抽取段註解掉 → 這一條紅。
        """
        patch = self.write("p.diff", CHANGE, where=self.home)
        evidence = self.write("EVIDENCE-verifier.md", VERIFIER_EVIDENCE, where=self.home)
        done = self.run_sh("scripts/apply.sh", "1", patch,
                           "--evidence-verifier", evidence)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        found = glob.glob(os.path.join(self.repo, "reports", "t1", "*",
                                       "result-verifier-round1.json"))
        self.assertEqual(len(found), 1,
                         "apply.sh --evidence-verifier 沒有抽出 result-verifier-round1.json:"
                         + done.stdout + done.stderr)
        with open(found[0], encoding="utf-8") as handle:
            self.assertIs(json.load(handle).get("present"), True)

    def test_a5_both_documents_still_mention_evidence_verifier(self):
        """A5 `memory/role/verifier.md` 與 `templates/dispatch-verifier.md` 對
        「EVIDENCE-verifier」提及各 ≥1(不比兩份相等)。"""
        for rel in ("memory/role/verifier.md", "templates/dispatch-verifier.md"):
            self.assertGreaterEqual(read(rel).count("EVIDENCE-verifier"), 1, rel)


# ---------------------------------------------------------------- acceptance 6


RED_CASE = """diff -ruN base/tests/test_thing.py work/tests/test_thing.py
--- base/tests/test_thing.py\t1970-01-01 08:00:00
+++ work/tests/test_thing.py\t2026-09-23 10:00:00
@@ -0,0 +1,6 @@
+import unittest
+
+
+class T(unittest.TestCase):
+    def test_thing(self):
+        self.assertEqual(1, 2, "第一輪是紅的")
"""

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


class Acceptance6AutoFixLeavesWorkAndBaseBehind(Sandbox):
    """G6:副本清理 —— `auto-fix.sh` 的 `fix-t{n}/round{r}` 只在**下一輪**開始
    `rm -rf`,綠了停在 InReview 就留著。"""

    def set_worker(self, body):
        path = os.path.join(self.home, "fake-worker.sh")
        write_executable(path, body)
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": "sh %s" % path, "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def test_a6_a_green_round_does_not_clean_up_its_own_copy(self):
        """A6 `auto-fix.sh` 收完 `patch-round<r>.diff` 與 `EVIDENCE-round<r>.md`
        後刪 `$FIX/work` 與 `$FIX/base`(留 patch / EVIDENCE / dispatch / result)。"""
        self.set_worker(WORKER_FIXES)
        self.make_ticket("1", allowed_write_paths=["tests/*"])
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")

        patch = self.write("p1.diff", RED_CASE, where=self.home)
        applied = self.run_sh("scripts/apply.sh", "1", patch)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        wt = os.path.join(self.home, "repo-wt", "t1")
        first = self.run_sh(os.path.join(wt, "scripts", "gate.sh"),
                            "--branch", "--ticket", "1", "--no-auto-fix", cwd=wt,
                            env=self.env(AC_ROOT=self.repo))
        self.assertNotEqual(first.returncode, 0, first.stdout + first.stderr)

        done = self.run_sh("scripts/auto-fix.sh", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["state"], "InReview",
                         "沙盒自己沒綠 —— 這一條要問的是『綠了以後』")

        fix = os.path.join(self.home, "repo-wt", "fix-t1", "round2")
        self.assertFalse(os.path.exists(os.path.join(fix, "work")) or
                         os.path.exists(os.path.join(fix, "base")),
                         "第 2 輪綠了、停在 InReview 之後,%s 底下的 work/base "
                         "現在還留著" % fix)


# ---------------------------------------------------------------- acceptance 7


class Acceptance7CloseNamesTheConcreteCheckCommand(Sandbox):
    """G7:落地後 `verify-case.py check {n} --ref {base_sha} --candidate {merge
    sha}` 要主線手打,`ticket.py close` 擋下時也不印那一句。"""

    def test_a7_close_blocker_does_not_name_ref_and_candidate_shas(self):
        """A7 `ticket.py close <n>` 在 `verify.baseline` 缺(或 `ok` 不為 true)、
        `review.sha` 在主線歷史裡時,印一句可貼的
        `python3 scripts/verify-case.py check <n> --ref <base_sha> --candidate
        <review.sha>`(rc 不變)。"""
        base_sha = self.git("rev-parse", "main").strip()
        # `./`:冒號前那段要像路徑才會切成 {path, contains}(#37);光寫 `README` 沒有 `/`
        # 也沒有副檔名,會被當成純字串整句去找,關票先卡在 verify,走不到這一句。
        self.make_ticket("1", verify_strings=["./README:main"], base_sha=base_sha)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")

        # 主線落地後又往前走一步,讓 review.sha 與 base_sha 是兩個不同的值 ——
        # 這樣才擋得住「隨便印一個 sha 兩次」那種假綠。
        self.write("NOTE.md", "落地後主線又往前一步\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "落地之後")
        review_sha = self.git("rev-parse", "main").strip()
        self.assertNotEqual(base_sha, review_sha, "沙盒自己壞了:兩個 sha 應該不同")

        review = {"verdict": "pass", "by": "main", "sha": review_sha,
                  "note": "沙盒的覆核"}
        self.ticket("set", "1", "review", json.dumps(review, ensure_ascii=False))

        done = self.ticket("close", "1")
        line_has_both = any(
            "verify-case.py check" in line and "--ref" in line
            and base_sha in line and "--candidate" in line and review_sha in line
            for line in done.stdout.splitlines())
        self.assertTrue(line_has_both,
                        "ticket.py close 現在還沒有印出一句可貼的 "
                        "`verify-case.py check 1 --ref %s --candidate %s`:\n%s"
                        % (base_sha, review_sha, done.stdout))


# ---------------------------------------------------------------- acceptance 8


class Acceptance8ConsolidatorHasItsOwnCardAndTemplate(Sandbox):
    """G8:consolidator 的角色卡借用 `implementer.md`,沒有派工範本;
    `memory.py check` 自動開的整理票,討論檔誰先寫沒有一句可貼的指令。"""

    def test_a8_consolidator_role_card_is_missing(self):
        """A8 `memory/role/consolidator.md` 存在(≤40 行)。"""
        self.assertTrue(exists("memory/role/consolidator.md"),
                        "乾淨基底上還沒有 memory/role/consolidator.md"
                        "(rules.py WANTED['consolidator'] 現在借用 implementer.md)")

    def test_a8_dispatch_consolidator_template_is_missing(self):
        """A8 `templates/dispatch-consolidator.md` 存在(兩個模型各一段、
        討論檔路徑、`memory.py consolidate` 那一句)。"""
        self.assertTrue(exists("templates/dispatch-consolidator.md"),
                        "乾淨基底上還沒有 templates/dispatch-consolidator.md")

    def test_a8_opened_ticket_outline_does_not_reference_the_template(self):
        """A8 `memory.py check` 開的整理票 `outline` 欄引用該範本路徑。"""
        conf = dict(DEFAULT_CONFIG)
        conf["memory"] = {"unit": "chars", "cap_chars": 5,
                          "consolidator": "fable",
                          "raise_allowed": True,
                          "applies_to": ["memory/model/*.md"]}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.write("memory/model/opus.md", "遠遠超過五個字元的一段備忘\n")
        done = self.run_py("scripts/memory.py", "check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        rows = self.tickets_on_disk()
        opened = [row for row in rows if row.get("role") == "consolidator"]
        self.assertTrue(opened, "memory.py check 沒有開出整理票:" + done.stdout)
        outline = str(opened[0].get("outline") or "")
        self.assertIn("templates/dispatch-consolidator.md", outline,
                     "整理票的 outline 欄現在沒有引用 "
                     "templates/dispatch-consolidator.md(欄位內容:%r)" % outline)


# ---------------------------------------------------------------- acceptance 9


class Acceptance9EmitInsideACopyWritesIntoIt(unittest.TestCase):
    """G9:副本裡 `event.repo_root()` 往上找到的是副本裡那份
    `board/config.json`,事件寫進一個等一下會被刪的目錄且不報錯。"""

    def test_a9_emit_inside_a_git_less_copy_still_writes_events_jsonl(self):
        """A9 在副本(有 `board/config.json`、無 `.git`)內執行
        `python3 scripts/event.py emit ticket.attempt.start --ticket <n>`,
        事件寫進 `AC_ROOT` 的 `events.jsonl`,或 rc≠0 並印
        「副本裡不發事件,由派工方代發」。"""
        copy = tempfile.mkdtemp(prefix="t29-a9-"); self.addCleanup(shutil.rmtree, copy, True)
        os.makedirs(os.path.join(copy, "scripts"))
        os.makedirs(os.path.join(copy, "board"))
        shutil.copy(os.path.join(ROOT, "scripts", "event.py"),
                   os.path.join(copy, "scripts", "event.py"))
        with open(os.path.join(copy, "board", "config.json"), "w",
                 encoding="utf-8") as handle:
            json.dump({"events_file": "board/events.jsonl"}, handle)
        self.assertFalse(os.path.exists(os.path.join(copy, ".git")),
                         "沙盒自己壞了:這一條要問的是『沒有 .git 的副本』")

        env = dict(os.environ)
        env.pop("AC_ROOT", None)
        done = subprocess.run(
            [sys.executable, os.path.join(copy, "scripts", "event.py"),
             "emit", "ticket.attempt.start", "--ticket", "1"],
            cwd=copy, env=env, capture_output=True, text=True, timeout=30)

        copy_events = os.path.join(copy, "board", "events.jsonl")
        self.assertTrue(
            done.returncode != 0 or not os.path.exists(copy_events),
            "event.py emit 在一個沒有 .git 的副本裡還是把事件寫進了副本自己"
            "的 board/events.jsonl(rc=%s):%s" % (done.returncode, copy_events))


# ---------------------------------------------------------------- acceptance 10


class Acceptance10LandHasNoDocsChannel(Sandbox):
    """G10:agent-control 自己沒有 docs 通道 —— 票檔、`memory/`、
    `docs/DECISIONS.md` 進主線只有裸 commit 一條路。"""

    def test_a10_land_sh_does_not_recognise_a_docs_subcommand(self):
        """A10 `sh scripts/land.sh docs "<訊息>" tickets/<n>.json
        memory/role/x.inbox.md` rc=0 且只准 `tickets/` `docs/` `memory/`
        三個前綴,與票的 land 共用 `.land.lock`。"""
        self.make_ticket("1")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")
        done = self.run_sh("scripts/land.sh", "docs", "測試訊息", "tickets/1.json")
        self.assertEqual(done.returncode, 0,
                         "land.sh 現在還沒有 docs 子指令,"
                         "把 'docs' 當成一條不存在的分支名處理:"
                         + done.stdout + done.stderr)


# ---------------------------------------------------------------- acceptance 11


def sync(dest, *extra):
    return subprocess.run(
        ["sh", os.path.join(ROOT, "scripts/sync-to-project.sh"), dest, *extra],
        capture_output=True, text=True, timeout=120)


class Acceptance11SyncDoesNotCheckCallPoints(unittest.TestCase):
    """G11:同步十支,`apply.sh inbox.py rules.py memory.py` 到了專案沒有呼叫
    點;sync 印接點清單,但不檢查接了沒。"""

    def test_a11_sync_does_not_report_missing_call_points(self):
        """A11 `sync-to-project.sh` 結尾對 `SCRIPT_LIST` 每一支
        `grep -l scripts/control/<name>` 專案的 `scripts/*.sh`(排除
        `scripts/control/`),沒有呼叫點就印
        「sync: 同步了但專案端沒有呼叫點:…」。"""
        dest = tempfile.mkdtemp(prefix="t29-a11-"); self.addCleanup(shutil.rmtree, dest, True)
        os.makedirs(os.path.join(dest, "board"), exist_ok=True)
        with open(os.path.join(dest, "board", "config.json"), "w",
                 encoding="utf-8") as handle:
            json.dump({"rules": {"roles_dir": "docs/roles",
                                 "models_dir": "docs/roles/model"},
                      "memory": {"applies_to": ["memory/model/*.md"]}},
                     handle)
        done = sync(dest)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("同步了但專案端沒有呼叫點", done.stdout,
                     "sync-to-project.sh 現在還沒有這一條檢查;"
                     "dest 裡沒有任何 scripts/*.sh 呼叫 apply.sh 等腳本,"
                     "但輸出裡沒有這句話")


# ---------------------------------------------------------------- acceptance 12


class Acceptance12DesignVerifyCasesDocIsLanded(unittest.TestCase):
    """G12:`DESIGN-VERIFY-CASES.md`(D-020)住在 scratchpad 不在 repo;§六 票號
    寫 A#24/#25/#26 而實際開出來是 #25/#26/#27;`docs/DECISIONS.md` 還沒有
    D-020 一列。**派工文說 A12 已由 #25 落地,這裡是確認,不是證紅。**

    實測:`docs/DESIGN-VERIFY-CASES.md` 進了 repo、§六 票號已經是 #25/#26/#27、
    `docs/DECISIONS.md` 有 D-020 一列、`docs/FLOW.html` §G 的票號與 #29 一致 ——
    這四項在乾淨基底上量到的確實是綠的。但 `docs/WORKFLOW.md` 能力表**還沒有**
    加上 A12 要求的那三列(`verify-case.py red` / `lint` / 閘門接線,標「設計中
    #26/#27」),`DESIGN-VERIFY-CASES.md` 裡也沒有 `T#650` 的「未開」標記 ——
    這兩項不在此檔斷言(派工文把整條 A12 定調成「只驗連結存在」),寫進
    EVIDENCE 的落差段。
    """

    def test_a12_design_doc_is_in_repo_with_correct_ticket_numbers(self):
        """A12 `docs/DESIGN-VERIFY-CASES.md` 進 repo(從 scratchpad 搬),§六 與
        C7 票號是 #25/#26/#27,不再是 A#24。"""
        self.assertTrue(exists("docs/DESIGN-VERIFY-CASES.md"))
        body = read("docs/DESIGN-VERIFY-CASES.md")
        self.assertIn("#25", body)
        self.assertIn("#26", body)
        self.assertIn("#27", body)
        self.assertNotIn("A#24", body)

    def test_a12_decisions_has_a_d020_row(self):
        """A12 `docs/DECISIONS.md` 有 D-020 一列指向它
        (`grep -c 'D-020' docs/DECISIONS.md` ≥ 1)。"""
        self.assertGreaterEqual(read("docs/DECISIONS.md").count("D-020"), 1)

    def test_a12_flow_html_section_g_ticket_numbers_match_this_ticket(self):
        """A12 `docs/FLOW.html` §G 的票號與本票(#29)一致。"""
        body = read("docs/FLOW.html")
        for label in ("#29 A1", "#29 A2", "#29 A3", "#29 A4", "#29 A5", "#29 A6",
                     "#29 A7", "#29 A8", "#29 A9", "#29 A10", "#29 A11",
                     "#29 A12"):
            self.assertIn(label, body,
                         "docs/FLOW.html §G 的票號與 #29 對不上,漏了 %r" % label)


if __name__ == "__main__":
    unittest.main()
