"""#96 `auto-fix.sh --dry-run` 不刪 round 副本;`apply.sh` 套完交件在同一個 commit 重產 repo-map 索引。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 字面 / 夾具自己寫下的值)
A1 | integ | Ready、沒有狀態檔;fix-t1/round1/ 先放 patch-round1.diff 與 work/marker;auto-fix --dry-run | rc 0、dispatch-round1.md 一份、兩檔仍在且逐位元等於事先寫的 bytes | 票面 A1 原話;內容是夾具自己寫的
A2 | integ | needs_verifier=true、interface_fixed=true 的 Ready 票;verify-t1/round1/marker;--dry-run | rc 0、marker 仍在且內容不變(改前即綠,回歸鎖) | 票面 A2 原話
A3 | integ | 不帶 --dry-run:fix-t1/round1/ 先放舊 patch 與 work/marker,假 worker 起跑那一刻記下看到什麼;--dry-run --round 1 | worker 看不到舊檔(清掉重建);--round 1 印規則包首行、不起 worker | 票面 A3 原話;既有 TheFirstRoundPacket 的首行字面
A4 | integ | 沙盒主線有 repo-map.py、catalog(managed 含 tests/test_*.py)、新鮮索引;patch 只新增 tests/test_memory_new.py;apply --evidence | rc 0;副本 repo-map check rc 0;index.json managed 含 tests/test_memory_new.py;main..t1 恰 1;commit 訊息有行以 `regen: repo-map build` 開頭;result extra_paths 不含 code-map/ | 票面 A4 原話;檔名 = patch 新增的那一支
A5 | integ | A4 那條分支,副本內 gate.sh --branch --ticket 1 --no-auto-fix | stderr 不含「code-map 索引過期」、rc ≠ 4 | 票面 A5 原話;字面同 TheGateRefusesAStaleIndex
A6 | integ | patch 只改既有已管理檔 tests/test_repo_map_contract.py 的內文 | rc 0;commit 訊息沒有 `regen:` 行;main..t1 的 diff 不含 code-map/ | 票面 A6 原話
A7 | integ | patch 在 catalog 加一個 entry 指向不存在的 scripts/ghost.py | rc 0;main..t1 恰 1;輸出有一行含「索引沒重產」 | 票面 A7 原話

## 介面字串(全部出自票面 acceptance)
REGEN_TRAILER = "regen: repo-map build"   ← A4 commit 訊息某行的開頭
REGEN_PREFIX  = "regen:"                  ← A6 commit 訊息不該有的行首
NOT_REBUILT   = "索引沒重產"               ← A7 apply 輸出
STALE         = "code-map 索引過期"        ← A5 閘門 stderr 不該有
NEW_FILE      = "tests/test_memory_new.py" ← A4 patch 新增的已管理檔

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
沙盒借 tests/control_harness.Sandbox(拋棄式真 git repo、外層 AC_* 一律不進沙盒)。auto-fix 那幾條借
tests/test_auto_fix.AutoFixBase 與它的假 worker(config worker.command 指向的假 sh);apply 那幾條借
tests/test_apply.ApplyBase,setUp 照 tests/test_repo_map_contract.TheGateRefusesAStaleIndex:複製真的
scripts/repo-map.py 進沙盒、寫 catalog、build、commit。patch 用 `diff -ruN base work` 在拋棄式目錄裡現做
(檔頭是相對形式),不手打 hunk。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;A2、A3、A6 改前即綠(回歸鎖),不做變異。
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import unittest

TAGS = ["dry-run-keeps-copies", "apply-regen-index"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from control_harness import SCRIPTS  # noqa: E402
from test_apply import ApplyBase, FULL_EVIDENCE  # noqa: E402
from test_auto_fix import AutoFixBase, WORKER_NEVER  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
REGEN_TRAILER = "regen: repo-map build"
REGEN_PREFIX = "regen:"
NOT_REBUILT = "索引沒重產"
STALE = "code-map 索引過期"
NEW_FILE = "tests/test_memory_new.py"

OLD_PATCH = b"diff -ruN base/x work/x\n--- old patch the worker handed in ---\n"
OLD_MARKER = b"worker output that must survive a dry run\n"
VERIFIER_MARKER = b"verifier output that must survive a dry run\n"

PASSING = """import unittest


class T(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
"""

# 假 worker(A3):起跑那一刻看副本裡有沒有上一手留下的檔,看到什麼寫進 @SAW@。
WORKER_LOOKS = """#!/bin/sh
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
for f in patch-round1.diff work/marker; do
    [ -e "$f" ] && echo "$f" >> "@SAW@"
done
touch "@SAW@"
exit 0
"""


def put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def bytes_of(path):
    with open(path, "rb") as handle:
        return handle.read()


class DryRunKeepsTheRoundCopies(AutoFixBase):
    """A1–A3:`--dry-run` 只印派工文,不刪 `<wtbase>/fix-t1/round1` 與 `verify-t1/round1`。"""

    def fix_dir(self):
        return os.path.join(self.home, "repo-wt", "fix-t1", "round1")

    def vfix_dir(self):
        return os.path.join(self.home, "repo-wt", "verify-t1", "round1")

    def plant_worker_copy(self):
        put(os.path.join(self.fix_dir(), "patch-round1.diff"), OLD_PATCH)
        put(os.path.join(self.fix_dir(), "work", "marker"), OLD_MARKER)

    def test_a1_a_dry_run_keeps_the_worker_patch_and_work_marker(self):
        """A1 worker 副本留著:patch-round1.diff 與 work/marker 跑完 --dry-run 仍在、逐位元相同。"""
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        self.plant_worker_copy()
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.worker_rounds(), [], "--dry-run 不起 worker")
        self.assertEqual(len(self.result_files("dispatch-round1.md")), 1, "照舊印派工文")
        patch = os.path.join(self.fix_dir(), "patch-round1.diff")
        marker = os.path.join(self.fix_dir(), "work", "marker")
        self.assertTrue(os.path.isfile(patch), "--dry-run 刪了 worker 交的 patch-round1.diff")
        self.assertTrue(os.path.isfile(marker), "--dry-run 刪了 worker 的 work/marker")
        self.assertEqual(bytes_of(patch), OLD_PATCH)
        self.assertEqual(bytes_of(marker), OLD_MARKER)

    def test_a2_a_dry_run_keeps_the_verifier_marker(self):
        """A2 驗證者副本留著:needs_verifier=true 的票,verify-t1/round1/marker 跑完 --dry-run 仍在。"""
        self.set_worker(WORKER_NEVER)
        self.ticket_ready(needs_verifier=True, interface_fixed=True)
        marker = os.path.join(self.vfix_dir(), "marker")
        put(marker, VERIFIER_MARKER)
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.worker_rounds(), [], "--dry-run 不起 worker")
        self.assertEqual(len(self.result_files("dispatch-verifier-round1.md")), 1,
                         "沒走到驗證者那一段,marker 留著什麼都沒證到")
        self.assertTrue(os.path.isfile(marker), "--dry-run 刪了驗證者副本的 marker")
        self.assertEqual(bytes_of(marker), VERIFIER_MARKER)

    def test_a3_without_dry_run_the_round_copy_is_rebuilt(self):
        """A3 不帶 --dry-run 時 round 副本照舊清掉重建:worker 起跑時看不到上一手的舊檔。"""
        saw = os.path.join(self.home, "worker-saw.txt")
        self.set_worker(WORKER_LOOKS.replace("@SAW@", saw))
        self.ticket_ready()
        self.plant_worker_copy()
        done = self.auto_fix("--no-review")
        self.assertEqual(self.worker_rounds(), ["worker ran round 1"], done.stdout + done.stderr)
        self.assertTrue(os.path.exists(saw), "假 worker 沒留下它看到什麼(沒在看)")
        with open(saw, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "", "起跑時副本裡還有上一手的檔,沒有清掉重建")

    def test_a3_dry_run_round_one_still_prints_the_packet(self):
        """A3 --dry-run --round 1(first_round_dispatch)行為不變:印派工文、不起 worker。"""
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        done = self.auto_fix("--dry-run", "--round", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("# 規則包:worker"), done.stdout[:120])
        self.assertIn("patch-round1.diff", done.stdout)
        self.assertEqual(self.worker_rounds(), [])


class ApplyRebuildsTheCodeMap(ApplyBase):
    """A4–A7:候選樹有 repo-map.py 與 catalog ⇒ apply 在同一個 commit 重產索引;失敗不擋。"""

    def setUp(self):
        super().setUp()
        shutil.copy(os.path.join(SCRIPTS, "repo-map.py"),
                    os.path.join(self.repo, "scripts", "repo-map.py"))
        catalog = {"version": 1, "managed": ["scripts/*.py", "tests/test_*.py"],
                   "topics": {"memory": {"summary": "沙盒", "match": ["memory"],
                                         "entry": "scripts/memory.py"}}}
        self.write(os.path.join("code-map", "catalog.json"),
                   json.dumps(catalog, ensure_ascii=False, indent=2))
        self.write(os.path.join("tests", "test_repo_map_contract.py"), PASSING)
        built = self.run_py("scripts/repo-map.py", "build")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        self.make("1", allowed_write_paths=["tests/*"])
        self.git("push", "-q", "origin", "main")

    def make_patch(self, name, changes):
        """`changes` = {相對路徑: 新內容};舊內容取主線上的那一份(沒有就是新增)。"""
        where = os.path.join(self.home, "mk-" + name)
        for rel, new in changes.items():
            old = os.path.join(self.repo, rel)
            if os.path.exists(old):
                put(os.path.join(where, "base", rel), bytes_of(old))
            put(os.path.join(where, "work", rel), new.encode("utf-8"))
        os.makedirs(os.path.join(where, "base"), exist_ok=True)
        made = subprocess.run(["diff", "-ruN", "base", "work"], cwd=where,
                              capture_output=True, text=True)
        self.assertEqual(made.returncode, 1, made.stderr)
        return self.patch_file(name, made.stdout)

    def result_json(self):
        found = sorted(glob.glob(os.path.join(self.repo, "reports", "t1", "*",
                                              "result-round1.json")))
        self.assertEqual(len(found), 1, found)
        with open(found[0], encoding="utf-8") as handle:
            return json.load(handle)

    def message(self):
        return self.git("log", "-1", "--format=%B", "t1")

    def apply_new_managed_file(self):
        patch = self.make_patch("new.diff", {NEW_FILE: PASSING})
        done = self.apply("1", patch, "--evidence", self.patch_file("EVIDENCE.md", FULL_EVIDENCE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def test_a4_a_stale_code_map_is_rebuilt_into_the_same_commit(self):
        """A4 交件漏重生 → 套用後索引是新的、併進同一個 commit、不列 extra_paths。"""
        done = self.apply_new_managed_file()
        wt = self.wt()
        check = subprocess.run(["python3", os.path.join(wt, "scripts", "repo-map.py"),
                                "--root", wt, "check"], cwd=wt, env=self.env(),
                               capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, "副本的索引仍過期:" + check.stderr + done.stdout)
        with open(os.path.join(wt, "code-map", "index.json"), encoding="utf-8") as handle:
            self.assertIn(NEW_FILE, json.load(handle)["managed"])
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "1")
        self.assertTrue(any(line.startswith(REGEN_TRAILER)
                            for line in self.message().splitlines()), self.message())
        extra = self.result_json()["extra_paths"]
        self.assertEqual([rel for rel in extra if rel.startswith("code-map/")], [], extra)

    def test_a5_the_gate_on_that_branch_does_not_refuse_a_stale_index(self):
        """A5 A4 那條分支跑 gate.sh --branch --ticket 1 --no-auto-fix:不再擋「索引過期」、rc 不是 4。"""
        self.apply_new_managed_file()
        wt = self.wt()
        gate = self.run_sh(os.path.join(wt, "scripts", "gate.sh"), "--branch", "--ticket", "1",
                           "--no-auto-fix", cwd=wt)
        self.assertNotIn(STALE, gate.stderr)
        self.assertNotEqual(gate.returncode, 4, gate.stdout + gate.stderr)

    def test_a6_an_unchanged_managed_set_adds_no_regen(self):
        """A6 交件不改已管理檔集合 → commit 訊息沒有 regen: 行、分支 diff 不多出 code-map/。"""
        patch = self.make_patch("edit.diff", {"tests/test_repo_map_contract.py":
                                              PASSING + "\n# edited by the patch\n"})
        done = self.apply("1", patch)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(any(line.startswith(REGEN_PREFIX)
                             for line in self.message().splitlines()), self.message())
        names = self.git("diff", "--name-only", "main..t1").split()
        self.assertEqual(names, ["tests/test_repo_map_contract.py"])

    def test_a7_a_failed_rebuild_does_not_block_the_apply(self):
        """A7 catalog 指向不存在的檔(build rc≠0)→ apply 仍 rc=0、照樣 commit、輸出有「索引沒重產」。"""
        catalog = json.loads(self.read(os.path.join("code-map", "catalog.json")))
        catalog["topics"]["ghost"] = {"summary": "指到不存在的檔", "match": ["ghost"],
                                      "entry": "scripts/ghost.py"}
        patch = self.make_patch("ghost.diff", {
            "code-map/catalog.json": json.dumps(catalog, ensure_ascii=False, indent=2)})
        done = self.apply("1", patch)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "1")
        self.assertIn(NOT_REBUILT, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
