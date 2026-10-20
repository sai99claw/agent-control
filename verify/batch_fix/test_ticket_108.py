"""#108 批次修復:auto-fix.sh / apply.sh / review.sh 的 --batch 一條路,紀錄只在 reports/batch/<stamp>/、不碰票檔。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面原話 / 夾具自己寫的票檔與 status.json / git 自己算)
A1 | unit | auto-fix.sh --batch land/X 1 2(AC_BATCH_RUN_ID=R)     | reports/batch/<stamp>/dispatch-round1.md 含五項(④ 寫入範圍聯集 #137 起不再列) | 票面 A1 原話;期望值取自夾具的 1.json / 2.json、reports/t1/R/status.json、`git diff --stat main...land/X`
A2 | unit | apply.sh --batch land/X 1 2 <patch>                    | land/X 前進恰 1 個 commit、訊息含 batch fix round 1 / #1 / #2;t1-a t2-b 不動;票的 state_version / review 不動 | 票面 A2 原話;sha 由 git 自己算
A3 | unit | patch 動 secrets/k.txt | rc=5 且 stderr 點名檔與格 | 夾具寫進 config 的 hard_block.paths;#137(D-054 Q2)起寫入範圍只剩這一道
A4 | unit | patch 在測試檔刪 assert 行 / 加 skip / expectedFailure  | rc=5、stderr 含「<檔>:<行> 放寬測試」;OBJECTION: test_defect 指到同一檔 → 不擋 | 票面 A4 原話;行號從夾具手寫的檔數
A5 | unit | review.sh --batch land/X 1 2 --run-id S                | pass → reports/batch/S/review.json(sha = land/X 頭);fail → 沒有 review.json、一頁 decision 掛 #1 內文有 #2、rc≠0 | 票面 A5 原話
A6 | unit | 整條 auto-fix.sh --batch 綠到底                         | 1.json cost 多 worker / reviewer 各一列(note 含 batch <stamp> 與票號、run_id = R);2.json 一個 byte 不變 | 票面 A6 原話
A7 | unit | worker 不交 patch / 逾時 / 阻擋反駁 / 覆核 fail          | rc 5 / 5 / 3 / 非 0;恰一頁 decision 掛 #1、內文有 #2;副本的 patch 與 EVIDENCE 留在 reports/batch/<stamp>/ | 票面 A7 原話;單票的退出碼表(scripts/auto-fix.sh 檔頭,基底)
A8 | unit | 三支既有測試檔載入                                     | 案例數 ≥ 92 / 45 / 17                                           | 票面 A8 原話(「照綠」是閘門 L2 的事,這裡只守「沒被刪」)

## 介面字串(每條斷言靠哪個字串;全部出自票面 acceptance / outline,不從腳本抄)
BATCH_DIR   = reports/batch/<stamp>/       ← 所有 --batch 紀錄的家;<stamp> 由列目錄取(恰一個)
DISPATCH    = dispatch-round1.md           ← A1
REVIEW_JSON = review.json                  ← A5 {verdict, sha, by, at, note}
COMMIT_MSG  = "batch fix round 1"          ← A2
LOOSENED    = "放寬測試"                   ← A4 stderr「<檔>:<行> 放寬測試」
RULE_NO_LOOSEN = "不得放寬測試"、RULE_OBJ = "OBJECTION: test_defect" ← A1 ⑤
CONFIG_GLOBS = board/config.json batch_fix.test_globs ← A4

## 怎麼做假(不上真埠、不起真服務、不起真模型)
沙盒借 tests/control_harness.Sandbox(拋棄式真 git repo + 絆線 claude);閘門換成 GATE_STUB_GREEN;
worker / reviewer 是 board/config.json 的 worker.command / reviewer.command 指到的 sh 替身,
worker 照單票的慣例在 $AC_WORK 交 patch-round$AC_ROUND.diff 與 EVIDENCE-round$AC_ROUND.md。
分支 t1-a / t2-b / land/X 用拋棄式 detached worktree 做完 commit 就收掉,分支本身不留 worktree。
patch 由 `diff -ruN base work` 真的產生(base 側取自 land/X 的內容),不手寫 hunk。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不跑真的 gate / 模型。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TAGS = ["batch-fix"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import (DEFAULT_CONFIG, GATE_STUB_GREEN, REVIEWER_PASS,  # noqa: E402
                             Sandbox, write_executable)

RUN = "20261010-120000-9"          # land 那一輪的 run_id(AC_BATCH_RUN_ID)

COMMIT_MSG = "batch fix round 1"
LOOSENED = "放寬測試"
RULE_NO_LOOSEN = "不得放寬測試"
RULE_OBJ = "OBJECTION: test_defect"

TEST_X = ("import unittest\n"
          "\n"
          "\n"
          "class T(unittest.TestCase):\n"
          "    def test_one(self):\n"
          "        self.assertEqual(1, 1)\n"
          "        self.assertTrue(True)\n")
TEST_Y = ("import unittest\n"
          "\n"
          "\n"
          "class Y(unittest.TestCase):\n"
          "    def test_y(self):\n"
          "        assert 1 + 1 == 2\n"
          "        self.assertTrue(True)\n")
CHECKS_C = ("def check():\n"
            "    assert 2 * 2 == 4\n"
            "    return True\n")
SRC_LIB = ("def f(x):\n"
           "    assert x\n"
           "    return x\n")

# 第一張票那一輪的紅榜:兩條。第二張票另有一份只屬於它的紅(A1「取第一張票那份」)。
RED_LOG_T1 = """test_alpha (test_x.T.test_alpha) ... FAIL
test_beta (test_x.T.test_beta) ... FAIL

======================================================================
FAIL: test_alpha (test_x.T.test_alpha)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_x.py", line 6, in test_alpha
    self.assertEqual(1, 2, "批次紅一號")
AssertionError: 1 != 2 : 批次紅一號

======================================================================
FAIL: test_beta (test_x.T.test_beta)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_x.py", line 9, in test_beta
    self.assertTrue(False, "批次紅二號")
AssertionError: False is not true : 批次紅二號

----------------------------------------------------------------------
Ran 2 tests in 0.001s

FAILED (failures=2)
"""
RED_LOG_T2 = """test_gamma (test_z.Z.test_gamma) ... FAIL

======================================================================
FAIL: test_gamma (test_z.Z.test_gamma)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_z.py", line 6, in test_gamma
    self.assertEqual(3, 4, "只屬於票二的紅")
AssertionError: 3 != 4 : 只屬於票二的紅

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (failures=1)
"""

# worker 替身:照單票慣例在 $AC_WORK 交件。@BODY@ 換成各自那一段。
WORKER_HEAD = """#!/bin/sh
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cat > /dev/null
cd "$AC_WORK" || exit 9
"""
WORKER_FIXES = WORKER_HEAD + """mkdir -p work/src
printf 'batch fix line\\n' > work/src/fix.txt
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# batch round %s\\n修了紅榜\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""
WORKER_NO_PATCH = WORKER_HEAD + """printf '# batch\\n沒交 patch 的證據標記\\n' > "EVIDENCE-round$AC_ROUND.md"
"""
WORKER_TIMES_OUT = WORKER_HEAD + """mkdir -p work/src
printf 'partial\\n' > work/src/partial.txt
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '逾時前寫到這裡的標記\\n' > "EVIDENCE-round$AC_ROUND.md"
exec sleep 60
"""
WORKER_OBJECTS = WORKER_HEAD + """printf 'OBJECTION: blocking 紅榜兩條互相矛盾\\n' > "EVIDENCE-round$AC_ROUND.md"
"""

REVIEWER_FAIL = """#!/bin/sh
echo "reviewer ran $AC_TICKET" >> "$AC_TEST_LOG"
cat > "$(dirname "$AC_TEST_LOG")/reviewer-stdin.md"
cat <<'EOF'
# 覆核
① verdict:fail
OBJECTION: blocking 批次修復把紅榜的斷言改掉了

## result

```result
{"ticket": "1", "role": "reviewer", "round": 1, "verdict": "fail",
 "rc": null, "patch_sha256": null, "gate": null, "mutations": [],
 "objection": {"category": "blocking", "body": "批次修復把紅榜的斷言改掉了"},
 "excluded": [], "repro": null, "memory": []}
```
EOF
"""

FIX_LINE = "批次修好的那一行"


class BatchWorld(Sandbox):
    """主線 + t1-a / t2-b 兩條票分支 + land/X(兩條都 --no-ff merge 進去)+ 1.json 2.json
    + reports/t1/R/status.json(兩條紅)、reports/t2/R/status.json(一條只屬於票二的紅)。"""

    gate_stub = GATE_STUB_GREEN

    def setUp(self):
        super().setUp()
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- 沙盒替身模型:照派工文做,不猜。\n")
        for rel, text in (("src/a.txt", "old\n"), ("src/lib.py", SRC_LIB),
                          ("tests/test_x.py", TEST_X), ("tests/test_other.py", TEST_X),
                          ("pkg/test_y.py", TEST_Y), ("checks/c.py", CHECKS_C),
                          ("secrets/k.txt", "k\n")):
            self.write(rel, text)
        self.configure(hard_block={"paths": ["secrets/*"], "dirs": [], "patterns": []})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線的夾具")
        self.git("push", "-q", "origin", "main")

        self.branch_from("t1-a", "main", {"src/one.txt": "t1-a 的實作\n"})
        self.branch_from("t2-b", "main", {"src/two.txt": "t2-b 的實作\n"})
        self.branch_from("land/X", "main", {}, merges=("t1-a", "t2-b"))
        self.sha_t1 = self.rev("t1-a")
        self.sha_t2 = self.rev("t2-b")

        self.t1 = self.make_ticket(
            "1", subject="票一:把 A 接上", objective="票一的目標:A 能被讀到",
            acceptance=["票一驗收:讀得到 A"], branch="t1-a", state="InReview",
            state_version=3,
            review={"verdict": "pass", "by": "main", "sha": self.sha_t1, "note": "夾具"})
        self.t2 = self.make_ticket(
            "2", subject="票二:把 B 接上", objective="票二的目標:B 能被寫入",
            acceptance=["票二驗收:寫得進 B"], branch="t2-b", state="InReview",
            state_version=4,
            review={"verdict": "pass", "by": "main", "sha": self.sha_t2, "note": "夾具"})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1 #2")
        self.git("push", "-q", "origin", "main")
        self.status("1", RED_LOG_T1)
        self.status("2", RED_LOG_T2)
        self.land_before = self.rev("land/X")
        with open(self.ticket_path("2"), "rb") as handle:
            self.t2_bytes = handle.read()

    # ------------------------------------------------------------ 夾具動作

    def configure(self, **over):
        path = os.path.join(self.repo, "board", "config.json")
        conf = json.loads(self.read("board/config.json")) if os.path.exists(path) \
            else dict(DEFAULT_CONFIG)
        conf.update(over)
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def set_worker(self, body, timeout=120):
        path = os.path.join(self.home, "fake-worker.sh")
        write_executable(path, body)
        self.configure(worker={"command": "sh %s" % path, "timeout_seconds": timeout})

    def rev(self, ref):
        return self.git("rev-parse", ref).strip()

    def branch_from(self, name, base, files, merges=()):
        """拋棄式 detached worktree 裡 commit / merge,做完把分支指過去、收掉 worktree ——
        分支本身不被任何 worktree 佔著,被測腳本要自己開 worktree 也開得起來。"""
        scratch = os.path.join(self.home, "scratch-%s" % name.replace("/", "-"))
        self.git("worktree", "add", "-q", "--detach", scratch, base)
        for rel, text in files.items():
            self.write(rel, text, where=scratch)
        if files:
            self.git("add", "-A", cwd=scratch)
            self.git("commit", "-q", "-m", "%s 的實作" % name, cwd=scratch)
        for other in merges:
            self.git("merge", "-q", "--no-ff", "-m", "merge %s" % other, other, cwd=scratch)
        self.git("branch", "-f", name, "HEAD", cwd=scratch)
        self.git("worktree", "remove", "--force", scratch)

    def status(self, ident, log_text):
        log = self.write("round-t%s.log" % ident, log_text, where=self.home)
        base = self.rev("main")
        self.run_py("scripts/status.py", "start", "--ticket", ident, "--kind", "gate",
                    "--run-id", RUN, "--base-sha", base, "--round", "1",
                    "--worktree", self.repo)
        self.run_py("scripts/status.py", "done", "--ticket", ident, "--kind", "gate",
                    "--run-id", RUN, "--rc", "1", "--log", log)

    def ticket_path(self, ident):
        return os.path.join(self.repo, "tickets", "%s.json" % ident)

    def make_patch(self, edits):
        """`diff -ruN base work`:base 側是 land/X 上的內容(沒有就不放),work 側是新內容。"""
        where = tempfile.mkdtemp(dir=self.home); self.addCleanup(shutil.rmtree, where, True)
        for rel, text in edits.items():
            shown = subprocess.run(["git", "show", "land/X:%s" % rel], cwd=self.repo,
                                   env=self.env(), capture_output=True, text=True)
            if shown.returncode == 0:
                self.write(rel, shown.stdout, where=os.path.join(where, "base"))
            self.write(rel, text, where=os.path.join(where, "work"))
        os.makedirs(os.path.join(where, "base"), exist_ok=True)
        done = subprocess.run(["diff", "-ruN", "base", "work"], cwd=where,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 1, "夾具的 patch 沒有差異:%s" % done.stderr)
        return self.write("p-%d.diff" % len(os.listdir(self.home)), done.stdout,
                          where=self.home)

    def apply_batch(self, patch, *extra):
        return self.run_sh("scripts/apply.sh", "--batch", "land/X", "1", "2", patch, *extra)

    def auto_fix_batch(self):
        return self.run_sh("scripts/auto-fix.sh", "--batch", "land/X", "1", "2",
                           env=self.env(AC_BATCH_RUN_ID=RUN))

    def evidence(self, text):
        return self.write("EVIDENCE-batch.md", text, where=self.home)

    # ------------------------------------------------------------ 讀回結果

    def batch_dirs(self):
        where = os.path.join(self.repo, "reports", "batch")
        if not os.path.isdir(where):
            return []
        return sorted(os.path.join(where, name) for name in os.listdir(where)
                      if os.path.isdir(os.path.join(where, name)))

    def one_batch_dir(self):
        found = self.batch_dirs()
        self.assertEqual(len(found), 1, "reports/batch/ 底下要恰一個 <stamp>:%s" % found)
        return found[0]

    def decisions(self):
        return [row for row in self.inbox_rows() if row.get("kind") == "decision"]

    def assert_one_decision_on_first_ticket_naming_the_batch(self, done):
        rows = self.decisions()
        self.assertEqual(len(rows), 1, "恰一頁 decision:%s\n%s" % (rows, done.stdout + done.stderr))
        self.assertEqual(str(rows[0].get("ticket")).lstrip("#"), "1", "掛第一張票:%s" % rows)
        page = os.path.join(self.repo, rows[0].get("page", ""))
        self.assertTrue(os.path.isfile(page), "那一頁的檔不在:%s" % rows[0])
        with open(page, encoding="utf-8") as handle:
            self.assertIn("#2", handle.read(), "內文要列整批(#2)")

    def assert_tickets_untouched(self):
        for ident, before in (("1", self.t1), ("2", self.t2)):
            now = self.load_ticket(ident)
            self.assertEqual(now.get("state_version"), before["state_version"], ident)
            self.assertEqual(now.get("review"), before["review"], ident)
            self.assertEqual(now.get("state"), before["state"], ident)


# ================================================================== A1

class TheBatchDispatchCarriesFiveThings(BatchWorld):

    def setUp(self):
        super().setUp()
        self.set_worker(WORKER_NO_PATCH)
        self.done = self.auto_fix_batch()
        where = None
        for stamp in self.batch_dirs():
            if os.path.isfile(os.path.join(stamp, "dispatch-round1.md")):
                where = stamp
        self.assertIsNotNone(where, "reports/batch/<stamp>/dispatch-round1.md 沒有寫出來:\n"
                             + self.done.stdout + self.done.stderr)
        self.stamp_dir = where
        with open(os.path.join(where, "dispatch-round1.md"), encoding="utf-8") as handle:
            self.packet = handle.read()

    def test_a1_both_ticket_faces_are_in_the_packet(self):
        """A1 ① 票 1 與票 2 的 subject / objective / acceptance 快照。"""
        for row in (self.t1, self.t2):
            for text in [row["subject"], row["objective"]] + row["acceptance"]:
                self.assertIn(text, self.packet, "#%s 的票面少了:%s" % (row["id"], text))

    def test_a1_the_red_list_is_the_first_tickets(self):
        """A1 ② 紅榜逐條(case、file、excerpt),來源是 reports/t1/<run>/status.json。"""
        with open(os.path.join(self.repo, "reports", "t1", RUN, "status.json"),
                  encoding="utf-8") as handle:
            failures = json.load(handle)["failures"]
        self.assertEqual(len(failures), 2, "夾具前提:t1 那一輪兩條紅 %s" % failures)
        for row in failures:
            self.assertIn(row["case"], self.packet, "紅榜少了 %s" % row["case"])
            if row.get("file"):
                self.assertIn(row["file"], self.packet, "紅榜少了 file %s" % row["file"])
            lines = [line.strip() for line in row["excerpt"].splitlines() if line.strip()]
            key = [line for line in lines if "AssertionError" in line] or lines[:1]
            self.assertIn(key[0], self.packet, "紅榜少了 excerpt:%s" % key[0])
        self.assertNotIn("只屬於票二的紅", self.packet, "紅榜取第一張票那份,不是票二的")

    def test_a1_the_diffstat_and_the_batch_diff_path(self):
        """A1 ③ git diff --stat main...land/X 的輸出與整批 diff 檔路徑。"""
        stat = self.git("diff", "--stat", "main...land/X")
        for line in stat.splitlines():
            if line.strip():
                self.assertIn(line.strip(), self.packet, "diffstat 少了一行:%s" % line)
        found = False
        for token in re.findall(r"[^\s`'\"()<>\[\]]+\.diff", self.packet):
            for path in (token, os.path.join(self.repo, token),
                         os.path.join(self.stamp_dir, token),
                         os.path.join(self.stamp_dir, os.path.basename(token))):
                if os.path.isfile(path):
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        text = handle.read()
                    if "src/one.txt" in text and "src/two.txt" in text:
                        found = True
        self.assertTrue(found, "派工文裡沒有一條指得到整批 diff(含 t1-a 與 t2-b 的改動)的路徑")

    def test_a1_the_no_loosening_rule(self):
        """A1 ⑤ 一句含「不得放寬測試」與「OBJECTION: test_defect」的規則。"""
        self.assertIn(RULE_NO_LOOSEN, self.packet)
        self.assertIn(RULE_OBJ, self.packet)


# ================================================================== A2

class TheFixIsOneCommitOnTheIntegrationBranch(BatchWorld):

    def test_a2_one_commit_on_land_x_and_nothing_else_moves(self):
        """A2 patch 成為 land/X 頭上恰一個 commit;t1-a / t2-b 與票的 state_version / review 不變。"""
        patch = self.make_patch({"src/fix.txt": FIX_LINE + "\n"})
        done = self.apply_batch(patch)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("rev-list", "--count",
                                  "%s..land/X" % self.land_before).strip(), "1")
        self.assertEqual(self.rev("land/X^"), self.land_before, "疊在原本的頭上")
        self.assertEqual(self.git("show", "land/X:src/fix.txt"), FIX_LINE + "\n")
        message = self.git("log", "-1", "--format=%B", "land/X")
        for text in (COMMIT_MSG, "#1", "#2"):
            self.assertIn(text, message)
        self.assertEqual(self.rev("t1-a"), self.sha_t1)
        self.assertEqual(self.rev("t2-b"), self.sha_t2)
        self.assert_tickets_untouched()


# ================================================================== A3

class OnlyTheConfigHardBlockStopsTheBatch(BatchWorld):

    def assert_blocked(self, rel, field, text):
        done = self.apply_batch(self.make_patch({rel: text}))
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn(rel, done.stderr)
        self.assertIn(field, done.stderr)
        self.assertEqual(self.rev("land/X"), self.land_before, "擋下就不 commit")

    def test_a3_hard_block_paths_block_the_batch(self):
        """A3 secrets/k.txt:命中 config 的 hard_block.paths → rc 5、點名檔與格、land/X 不動。"""
        self.assert_blocked("secrets/k.txt", "hard_block.paths", "leaked\n")


# ================================================================== A4

class LooseningATestIsBlocked(BatchWorld):

    def test_a4_deleting_an_assert_line_in_tests_is_blocked(self):
        """A4 tests/test_x.py 刪第 6 行 self.assertEqual → rc 5、「tests/test_x.py:6 放寬測試」。"""
        loosened = TEST_X.replace("        self.assertEqual(1, 1)\n", "")
        done = self.apply_batch(self.make_patch({"tests/test_x.py": loosened}))
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("tests/test_x.py:6 " + LOOSENED, done.stderr)
        self.assertEqual(self.rev("land/X"), self.land_before)

    def test_a4_adding_a_skip_is_blocked(self):
        """A4 新增 @unittest.skip(新檔第 5 行)→ rc 5、「tests/test_x.py:5 放寬測試」。"""
        skipped = TEST_X.replace("    def test_one", "    @unittest.skip(\"later\")\n    def test_one")
        done = self.apply_batch(self.make_patch({"tests/test_x.py": skipped}))
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("tests/test_x.py:5 " + LOOSENED, done.stderr)

    def test_a4_expected_failure_under_verify_is_blocked(self):
        """A4 verify/** 底下新增 @unittest.expectedFailure → rc 5、點名 verify/example/test_example.py。"""
        rel = "verify/example/test_example.py"
        original = self.git("show", "land/X:" + rel)
        self.assertIn("    def test_true", original, "夾具前提")
        changed = original.replace("    def test_true",
                                   "    @unittest.expectedFailure\n    def test_true")
        done = self.apply_batch(self.make_patch({rel: changed}))
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertRegex(done.stderr, re.escape(rel) + r":\d+ " + LOOSENED)

    def test_a4_any_test_star_py_is_a_test_file_by_default(self):
        """A4 預設 glob **/test_*.py:pkg/test_y.py 刪 assert 行 → rc 5。"""
        loosened = TEST_Y.replace("        assert 1 + 1 == 2\n", "")
        done = self.apply_batch(self.make_patch({"pkg/test_y.py": loosened}))
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("pkg/test_y.py:6 " + LOOSENED, done.stderr)

    def test_a4_the_globs_come_from_board_config(self):
        """A4 batch_fix.test_globs = ["checks/**"] → checks/c.py 刪 assert 行也擋。"""
        self.configure(batch_fix={"test_globs": ["checks/**"]})
        loosened = CHECKS_C.replace("    assert 2 * 2 == 4\n", "")
        done = self.apply_batch(self.make_patch({"checks/c.py": loosened}))
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("checks/c.py:2 " + LOOSENED, done.stderr)

    def test_a4_an_assert_outside_test_files_is_not_guarded(self):
        """A4 src/lib.py 不是測試檔:刪 assert 行不擋。"""
        loosened = SRC_LIB.replace("    assert x\n", "")
        done = self.apply_batch(self.make_patch({"src/lib.py": loosened}))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a4_a_test_defect_objection_on_that_file_lets_it_through(self):
        """A4 EVIDENCE 有 OBJECTION: test_defect tests.test_x.T.test_one → 不擋(rc≠5)。"""
        loosened = TEST_X.replace("        self.assertEqual(1, 1)\n", "")
        evidence = self.evidence("# batch\nOBJECTION: test_defect tests.test_x.T.test_one "
                                 "期望值寫反了\n")
        done = self.apply_batch(self.make_patch({"tests/test_x.py": loosened}),
                                "--evidence", evidence)
        self.assertNotIn(done.returncode, (2, 5), "不擋,也不是用法錯:\n"
                         + done.stdout + done.stderr)
        self.assertNotIn("tests/test_x.py:6 " + LOOSENED, done.stderr)

    def test_a4_a_test_defect_objection_on_another_file_does_not(self):
        """A4 反駁指的案例在 tests/test_other.py,被放寬的是 tests/test_x.py → 照擋 rc 5。"""
        loosened = TEST_X.replace("        self.assertEqual(1, 1)\n", "")
        evidence = self.evidence("# batch\nOBJECTION: test_defect tests.test_other.T.test_one "
                                 "期望值寫反了\n")
        done = self.apply_batch(self.make_patch({"tests/test_x.py": loosened}),
                                "--evidence", evidence)
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("tests/test_x.py:6 " + LOOSENED, done.stderr)


# ================================================================== A5

class TheBatchReview(BatchWorld):
    STAMP = "20261010-130000-batch"

    def setUp(self):
        super().setUp()
        self.branch_from("land/X", "land/X", {"src/fix.txt": FIX_LINE + "\n"})
        self.head = self.rev("land/X")

    def review_batch(self):
        return self.run_sh("scripts/review.sh", "--batch", "land/X", "1", "2",
                           "--run-id", self.STAMP)

    def review_json(self):
        return os.path.join(self.repo, "reports", "batch", self.STAMP, "review.json")

    def test_a5_pass_writes_review_json_bound_to_the_integration_head(self):
        """A5 pass → reports/batch/<stamp>/review.json {verdict, sha=land/X 頭, by, at, note};派工文 = fix diff + 兩張票面。"""
        self.set_reviewer(REVIEWER_PASS)
        done = self.review_batch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.isfile(self.review_json()), done.stdout + done.stderr)
        with open(self.review_json(), encoding="utf-8") as handle:
            data = json.load(handle)
        for key in ("verdict", "sha", "by", "at", "note"):
            self.assertIn(key, data)
        self.assertEqual(data["verdict"], "pass")
        self.assertEqual(data["sha"], self.head, "綁 land/X 頭,不是票分支頭")
        stdin = os.path.join(self.home, "reviewer-stdin.md")
        self.assertTrue(os.path.isfile(stdin), "reviewer 沒被叫到")
        with open(stdin, encoding="utf-8") as handle:
            packet = handle.read()
        self.assertIn(FIX_LINE, packet, "派工文要帶 fix commit 的 diff")
        for row in (self.t1, self.t2):
            self.assertIn(row["subject"], packet)
        self.assert_tickets_untouched()

    def test_a5_fail_writes_no_review_and_pages_the_first_ticket(self):
        """A5 fail → 沒有 review.json、不寫票、一頁 decision 掛票 1 內文列 #2、rc≠0。"""
        self.set_reviewer(REVIEWER_FAIL)
        done = self.review_batch()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.review_json()), "fail 不寫 review.json")
        self.assert_one_decision_on_first_ticket_naming_the_batch(done)
        self.assert_tickets_untouched()
        for ident, before in (("1", self.t1), ("2", self.t2)):
            self.assertEqual(self.load_ticket(ident).get("objections"),
                             before.get("objections"), "不寫票:#%s 的 objections 動了" % ident)


# ================================================================== A6

class TheCostGoesOnTheFirstTicketOnly(BatchWorld):

    def test_a6_one_worker_row_and_one_reviewer_row_on_ticket_one(self):
        """A6 整條綠:1.json cost 多 worker / reviewer 各一列(note 含 batch <stamp> 與票號、run_id=R);2.json 不變。"""
        self.set_worker(WORKER_FIXES)
        self.set_reviewer(REVIEWER_PASS)
        done = self.auto_fix_batch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        stamp_dir = self.one_batch_dir()
        stamp = os.path.basename(stamp_dir)
        self.assertEqual(self.git("rev-list", "--count",
                                  "%s..land/X" % self.land_before).strip(), "1")
        with open(os.path.join(stamp_dir, "review.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle).get("sha"), self.rev("land/X"))
        rows = self.load_ticket("1").get("cost") or []
        self.assertEqual(sorted(row.get("role") for row in rows), ["reviewer", "worker"], rows)
        for row in rows:
            note = row.get("note") or ""
            self.assertIn("batch", note, row)
            self.assertIn(stamp, note, row)
            rest = note.replace(stamp, "")
            for ident in ("1", "2"):
                self.assertRegex(rest, r"(?<![0-9A-Za-z])#?%s(?![0-9])" % ident, row)
            self.assertEqual(row.get("run_id"), RUN, row)
        with open(self.ticket_path("2"), "rb") as handle:
            self.assertEqual(handle.read(), self.t2_bytes, "第二張票一個 byte 都不該動")
        self.assert_tickets_untouched()


# ================================================================== A7

class TheStopsLookLikeTheSingleTicketOnes(BatchWorld):

    def test_a7_no_patch_is_rc5_with_one_page(self):
        """A7 worker 沒交 patch → rc 5、一頁 decision 掛 #1 列 #2、EVIDENCE 留在 reports/batch/<stamp>/。"""
        self.set_worker(WORKER_NO_PATCH)
        done = self.auto_fix_batch()
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assert_one_decision_on_first_ticket_naming_the_batch(done)
        evidence = os.path.join(self.one_batch_dir(), "EVIDENCE-round1.md")
        self.assertTrue(os.path.isfile(evidence), "EVIDENCE 要留在 reports/batch/<stamp>/")
        with open(evidence, encoding="utf-8") as handle:
            self.assertIn("沒交 patch 的證據標記", handle.read())
        self.assertEqual(self.rev("land/X"), self.land_before)
        self.assert_tickets_untouched()

    def test_a7_timeout_is_rc5_and_the_partial_work_survives(self):
        """A7 逾時 → rc 5、一頁 decision;副本的 patch 與 EVIDENCE 留在 reports/batch/<stamp>/。"""
        self.set_worker(WORKER_TIMES_OUT, timeout=2)
        done = self.auto_fix_batch()
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assert_one_decision_on_first_ticket_naming_the_batch(done)
        stamp_dir = self.one_batch_dir()
        patch = os.path.join(stamp_dir, "patch-round1.diff")
        evidence = os.path.join(stamp_dir, "EVIDENCE-round1.md")
        self.assertTrue(os.path.isfile(patch), "逾時前的 patch 要留下來")
        self.assertTrue(os.path.isfile(evidence), "逾時前的 EVIDENCE 要留下來")
        with open(evidence, encoding="utf-8") as handle:
            self.assertIn("逾時前寫到這裡的標記", handle.read())
        self.assertEqual(self.rev("land/X"), self.land_before)

    def test_a7_a_blocking_objection_is_rc3_with_one_page(self):
        """A7 阻擋反駁 → rc 3、一頁 decision 掛 #1 列 #2。"""
        self.set_worker(WORKER_OBJECTS)
        done = self.auto_fix_batch()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assert_one_decision_on_first_ticket_naming_the_batch(done)
        self.assertEqual(self.rev("land/X"), self.land_before)

    def test_a7_a_failed_review_is_nonzero_with_one_page(self):
        """A7 覆核 fail → rc≠0、一頁 decision 掛 #1 列 #2、沒有 review.json。"""
        self.set_worker(WORKER_FIXES)
        self.set_reviewer(REVIEWER_FAIL)
        done = self.auto_fix_batch()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_one_decision_on_first_ticket_naming_the_batch(done)
        self.assertFalse(os.path.exists(os.path.join(self.one_batch_dir(), "review.json")))


# ================================================================== A8

class TheSingleTicketSuitesAreStillThere(unittest.TestCase):

    def test_a8_existing_suites_keep_their_cases(self):
        """A8 既有案例沒被刪:test_auto_fix ≥ 92、test_apply ≥ 45、test_review ≥ 17(照綠由閘門 L2 量)。

        票面寫 test_apply 46 條是數 `def test_`;基底 400eb84 上 unittest 實際載入 45 條,下限照載入數。
        """
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        loader = unittest.TestLoader()
        for name, floor in (("tests.test_auto_fix", 92), ("tests.test_apply", 45),
                            ("tests.test_review", 17)):
            count = loader.loadTestsFromName(name).countTestCases()
            self.assertGreaterEqual(count, floor, "%s 只剩 %d 條" % (name, count))


if __name__ == "__main__":
    unittest.main()
