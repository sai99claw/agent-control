"""#129 code-map 索引重產補齊:`apply.sh --batch` 同一次重產;`land.sh` 合併後過期自動重產併進同一次落地。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 字面 / 夾具自己寫下的檔名 / git 上取的 sha)
A1 | integ | 沙盒主線有真 repo-map.py、catalog(managed 含 tests/test_*.py)、新鮮索引;整合分支 land/X 頭上 apply --batch land/X 1 2,patch 只新增 tests/test_batch_new.py | rc 0;land/X 只多 1 commit;訊息有行以 `regen: repo-map build` 開頭;副本 check rc 0;managed 含 tests/test_batch_new.py;輸出含批次重產字樣 | 票面 A1 原話;檔名 = patch 新增的那一支
A2 | integ | (a) patch 只改既有已管理檔 tests/test_kept.py 內文 (b) patch 讓 catalog 指向不存在的 scripts/ghost.py | (a) 訊息無 `regen:` 行、該 commit diff 只有 tests/test_kept.py (b) rc 0、只多 1 commit、輸出含「索引沒重產」 | 票面 A2 原話;(a) 改前即綠(回歸鎖)
A3 | integ | 分支 t1 新增 tests/test_a.py 並自己 build(分支 check 綠);主線再直接 commit tests/test_z.py 不 build;land t1-map | rc 0;主線 check rc 0;managed 含 test_a 與 test_z;主線多出的 commit 恰一個首行 `regen: repo-map build (land <stamp>)`(stamp = land.pass 的 stamp)、只動兩支產出物;輸出含合併後過期字樣 | 票面 A3 原話
A4 | integ | 兩條分支各新增 tests/test_a1.py / tests/test_a2.py 且各自 build;前置在拋棄式 worktree 手動 merge 自證衝突只在 code-map/;land 兩條 | rc 0;主線 check rc 0;managed 含兩檔;輸出含合併後過期字樣 | 票面 A4 原話
A5 | integ | (a) 兩條分支都改 src/same.txt(衝突在 code-map/ 以外)(b) 假 repo-map.py:check 一律 1、build 寫兩支產出物外加 stray.txt | (a) rc 3、land.fail、主線不動 (b) rc 3、主線不動、land worktree 留著、輸出含動到以外字樣、land 分支沒有 regen commit | 票面 A5 原話;(a) 改前即綠(回歸鎖)
A6 | integ | (a) 分支只新增不受管理的 src/b1 | (a) rc 0、沒有 regen commit、沒有合併後過期字樣 | 票面 A6 原話;(a) 改前即綠(回歸鎖)。(b)「閘門以索引過期攔」隨 #138 刪閘門那一格刪掉(D-054 Q4)
A7 | integ | A3 那次落地 | land.pass 的 main_before = 跑 land 之前 `git rev-parse main`;sha 是新主線頭的前綴;first-parent 從 main_before 接到新頭、regen commit 在鏈上 | 票面 A7 原話;#127 讀事件檔的寫法

## 介面字串(全部出自票面 acceptance)
BATCH_REGEN   = "批次 " + "code-map 索引重產"        ← A1 apply 輸出
NOT_REBUILT   = "索引沒重產"                          ← A2(b) apply 輸出
REGEN_TRAILER = "regen: repo-map build"               ← A1 訊息某行開頭;A3 首行 + " (land <stamp>)"
REGEN_PREFIX  = "regen:"                              ← A2(a) / A5(b) / A6 不該有
STALE         = "code-map 索引過期"
MERGED_STALE  = "合併後 " + STALE                      ← A3 / A4 land 輸出
STRAY         = "重產動到 " + "code-map/ 以外"          ← A5(b) land 輸出
OUTPUTS       = code-map/index.json、code-map/INDEX.md ← A3 regen commit 只准動這兩支

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
沙盒借 tests/control_harness.Sandbox(拋棄式真 git repo + origin、外層 AC_* 一律不進沙盒)。
apply 那幾條借 tests/test_apply.ApplyBase + Sandbox.make_batch(整合分支 land/X 的真實形狀);
land 那幾條借 tests/test_land.LandBase,`gate_stub = None` 用**真的** gate.sh。
setUp 照 tests/test_repo_map_contract 的 A32:複製真的 scripts/repo-map.py、寫 catalog、build、commit 進主線。
patch 用 `diff -ruN base work` 在拋棄式目錄裡現做(檔頭是相對形式),不手打 hunk。
A5(b) 的假 repo-map.py 是夾具自己寫的一支 python,不碰真的那一支。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;
「沒有 repo-map.py 的既有 ApplyBatch / LandBase 沙盒全綠」由既有的 tests/test_apply.py、tests/test_land.py 守,本檔不重抄;
A7「auto-fix 越界守衛判可歸屬」只量 land.pass 兩格與 first-parent 鏈的形狀(守衛 land_attribution 走的那一條),
不端到端起 auto-fix(讀 code 推的,EVIDENCE 有標)。
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TAGS = ["batch-regen-index", "land-regen-index"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from control_harness import SCRIPTS  # noqa: E402
from test_apply import ApplyBase  # noqa: E402
from test_land import LandBase  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
BATCH_REGEN = "批次 " + "code-map 索引重產"
NOT_REBUILT = "索引沒重產"
REGEN_TRAILER = "regen: repo-map build"
REGEN_PREFIX = "regen:"
STALE = "code-map 索引過期"
MERGED_STALE = "合併後 " + STALE
STRAY = "重產動到 " + "code-map/ 以外"
OUTPUTS = ("code-map/index.json", "code-map/INDEX.md")

CATALOG = {"version": 1, "managed": ["scripts/*.py", "tests/test_*.py"],
           "topics": {"memory": {"summary": "沙盒", "match": ["memory"],
                                 "entry": "scripts/memory.py"}}}
BROKEN_CATALOG = dict(CATALOG, topics=dict(CATALOG["topics"], ghost={
    "summary": "指到不存在的檔", "match": ["ghost"], "entry": "scripts/ghost.py"}))

PASSING = """import unittest


class T(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)
"""

# A5(b) 的假產生器:check 一律說過期;build 寫兩支產出物,外加一支 code-map/ 以外的 stray.txt。
FAKE_REPO_MAP = """import os
import sys

root = sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else os.getcwd()
if "check" in sys.argv:
    sys.exit(1)
for name in ("code-map/index.json", "code-map/INDEX.md"):
    with open(os.path.join(root, name), "w") as handle:
        handle.write("fake build\\n")
with open(os.path.join(root, "stray.txt"), "w") as handle:
    handle.write("written by build\\n")
"""


def catalog_text(catalog):
    return json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class MapMixin(object):
    """沙盒主線放真的 repo-map.py + catalog + 新鮮索引;check / build / managed 都對某一棵樹量。"""

    def install_map(self):
        shutil.copy(os.path.join(SCRIPTS, "repo-map.py"),
                    os.path.join(self.repo, "scripts", "repo-map.py"))
        self.write(os.path.join("code-map", "catalog.json"), catalog_text(CATALOG))
        self.write(os.path.join("tests", "test_kept.py"), PASSING)

    def map_cmd(self, tree, verb):
        return subprocess.run(["python3", "-B", os.path.join(tree, "scripts", "repo-map.py"),
                               "--root", tree, verb], cwd=tree, env=self.env(),
                              capture_output=True, text=True, timeout=120)

    def build(self, tree):
        done = self.map_cmd(tree, "build")
        self.assertEqual(done.returncode, 0, "沙盒自己壞了:build " + done.stdout + done.stderr)

    def managed(self, tree):
        with open(os.path.join(tree, "code-map", "index.json"), encoding="utf-8") as handle:
            return json.load(handle)["managed"]

    def make_patch(self, name, changes):
        """`changes` = {相對路徑: 新內容};舊內容取主線上的那一份(沒有就是新增)。`diff -ruN base work`。"""
        where = tempfile.mkdtemp(prefix="t129-patch-"); self.addCleanup(shutil.rmtree, where, True)
        os.makedirs(os.path.join(where, "base"))
        os.makedirs(os.path.join(where, "work"))
        for rel, new in changes.items():
            old = os.path.join(self.repo, rel)
            if os.path.exists(old):
                with open(old, encoding="utf-8") as handle:
                    put(os.path.join(where, "base", rel), handle.read())
            put(os.path.join(where, "work", rel), new)
        made = subprocess.run(["diff", "-ruN", "base", "work"], cwd=where,
                              capture_output=True, text=True)
        self.assertEqual(made.returncode, 1, "沙盒自己壞了:diff " + made.stderr)
        path = os.path.join(self.home, name)
        put(path, made.stdout)
        return path


class TheBatchFixRebuildsTheIndex(MapMixin, ApplyBase):
    """A1–A2:`apply.sh --batch` 與單票那一支同一個判準,產出物有變就併進那一個 batch fix commit。"""

    def setUp(self):
        super().setUp()
        self.install_map()
        # make_batch 會寫 tests/test_x.py(已管理);先寫好同一份再 build,主線索引才是新的。
        self.write(os.path.join("tests", "test_x.py"),
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_one(self):\n        self.assertEqual(1, 1)\n")
        self.build(self.repo)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的地圖")
        self.batch = self.make_batch()
        check = self.map_cmd(self.repo, "check")
        self.assertEqual(check.returncode, 0, "前置:主線索引要是新的 " + check.stderr)

    def run_batch(self, patch):
        return self.apply("--batch", "land/X", "1", "2", patch)

    def wt(self, name="batch-land-X"):
        return os.path.join(self.home, "repo-wt", name)

    def message(self):
        return self.git("log", "-1", "--format=%B", "land/X")

    def new_commits(self):
        return self.git("rev-list", "--count", "%s..land/X" % self.batch["land"]).strip()

    def test_a1_a_new_managed_file_is_indexed_in_the_same_batch_commit(self):
        """A1 patch 只新增 tests/test_batch_new.py → 同一個 batch commit 帶新索引、regen 行、批次字樣。"""
        done = self.run_batch(self.make_patch("new.diff", {"tests/test_batch_new.py": PASSING}))
        output = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, output)
        self.assertEqual(self.new_commits(), "1", output)
        self.assertTrue(any(line.startswith(REGEN_TRAILER) for line in self.message().splitlines()),
                        "batch commit 訊息沒有 regen 行:" + self.message())
        wt = self.wt()
        check = self.map_cmd(wt, "check")
        self.assertEqual(check.returncode, 0, "整合分支的索引仍過期:" + check.stderr)
        self.assertIn("tests/test_batch_new.py", self.managed(wt))
        self.assertIn(BATCH_REGEN, output)

    def test_a2_an_edit_inside_a_managed_file_adds_no_regen(self):
        """A2 patch 只改 tests/test_kept.py 內文 → 訊息無 regen: 行、那個 commit 只動 tests/test_kept.py。"""
        done = self.run_batch(self.make_patch("edit.diff", {
            "tests/test_kept.py": PASSING + "\n# edited by the batch\n"}))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(any(line.startswith(REGEN_PREFIX) for line in self.message().splitlines()),
                         self.message())
        self.assertEqual(self.git("diff", "--name-only", "land/X^", "land/X").split(),
                         ["tests/test_kept.py"])

    def test_a2_a_failed_build_does_not_block_the_batch(self):
        """A2 catalog 指向不存在的 scripts/ghost.py(build rc≠0)→ rc 0、照樣 1 個 commit、輸出含「索引沒重產」。"""
        done = self.run_batch(self.make_patch("ghost.diff", {
            "code-map/catalog.json": catalog_text(BROKEN_CATALOG)}))
        output = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, output)
        self.assertEqual(self.new_commits(), "1", output)
        self.assertIn(NOT_REBUILT, output)


class LandRebuildsAStaleMergedIndex(MapMixin, LandBase):
    """A3–A7:合併結果的索引過期 → land 自己重產、併進同一次落地;其他差異照擋。真的 gate.sh。"""
    gate_stub = None

    def setUp(self):
        super().setUp()
        self.install_map()
        self.build(self.repo)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的地圖")
        self.git("push", "-q", "origin", "main")

    def git_may_fail(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env(), capture_output=True,
                              text=True, timeout=120)

    def branch_adding(self, ident, name):
        """票 #ident 的分支新增一支已管理的檔,並帶自己 build 過的索引(分支自己 check 綠)。"""
        branch = self.branch_for(ident, "t%s-map" % ident)
        self.write(name, PASSING, where=branch)
        self.build(branch)
        check = self.map_cmd(branch, "check")
        self.assertEqual(check.returncode, 0, "前置:分支自己的索引要是新的 " + check.stderr)
        self.git("add", "-A", cwd=branch)
        self.git("commit", "-q", "-m", "#%s 新增 %s" % (ident, name), cwd=branch)
        return branch

    def regen_commits(self, rev_range):
        found = []
        for line in self.git("log", "--format=%H %s", rev_range).splitlines():
            sha, _, subject = line.partition(" ")
            if subject.startswith(REGEN_PREFIX):
                found.append((sha, subject))
        return found

    def land_branches(self):
        return [line.strip().lstrip("*+ ").strip()
                for line in self.git("branch", "--list", "land/*").splitlines() if line.strip()]

    def land_worktrees(self):
        return glob.glob(os.path.join(self.wt_base(), "land-*"))

    def stale_merge(self):
        """A3 / A7 的前置:分支新增 tests/test_a.py 並 build;主線再直接 commit tests/test_z.py 不 build。"""
        self.branch_adding(1, "tests/test_a.py")
        self.write("tests/test_z.py", PASSING)
        self.git("add", "--", "tests/test_z.py")
        self.git("commit", "-q", "-m", "主線新增 tests/test_z.py,沒重產")
        self.git("push", "-q", "origin", "main")
        stale = self.map_cmd(self.repo, "check")
        self.assertNotEqual(stale.returncode, 0, "前置:主線的索引該是過期的")
        self.approve(1, "t1-map")
        return self.git("rev-parse", "main").strip()

    def test_a3_a_clean_but_stale_merge_is_rebuilt_into_the_same_landing(self):
        """A3 分支帶 test_a.py + 自己的索引、主線另加 test_z.py 不重產 → land rc 0、主線索引新、恰一個 regen commit 只動產出物。"""
        before = self.stale_merge()
        done = self.land("t1-map")
        output = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, output)
        check = self.map_cmd(self.repo, "check")
        self.assertEqual(check.returncode, 0, "落地後主線索引仍過期:" + check.stderr)
        managed = self.managed(self.repo)
        self.assertIn("tests/test_a.py", managed)
        self.assertIn("tests/test_z.py", managed)
        regen = self.regen_commits("%s..main" % before)
        self.assertEqual(len(regen), 1, self.git("log", "--oneline", "%s..main" % before))  # pin:票面 A3「有一個」regen commit
        passes = [row for row in self.events() if row["kind"] == "land.pass"]
        self.assertTrue(passes, "沒有 land.pass")
        self.assertEqual(regen[0][1], "%s (land %s)" % (REGEN_TRAILER, passes[-1].get("stamp")))
        files = self.git("show", "--name-only", "--format=", regen[0][0]).split()
        self.assertTrue(files, "regen commit 是空的")
        self.assertEqual([name for name in files if name not in OUTPUTS], [], files)
        self.assertIn(MERGED_STALE, output)
        self.assertIn("gate.pass", self.kinds())

    def test_a4_conflicts_only_in_the_outputs_are_rebuilt_away(self):
        """A4 兩邊各新增 tests/test_a1.py / tests/test_a2.py 並各自 build;衝突只在 code-map/ → land rc 0、兩檔都進索引。"""
        self.branch_adding(1, "tests/test_a1.py")
        self.branch_adding(2, "tests/test_a2.py")
        probe = tempfile.mkdtemp(prefix="t129-probe-"); self.addCleanup(shutil.rmtree, probe, True)
        probe = os.path.join(probe, "wt")
        self.git("worktree", "add", "-q", "--detach", probe, "main")
        self.git("merge", "-q", "--no-ff", "-m", "probe 1", "t1-map", cwd=probe)
        self.git_may_fail(probe, "merge", "--no-ff", "--no-commit", "t2-map")
        conflicted = self.git("diff", "--name-only", "--diff-filter=U", cwd=probe).split()
        self.git_may_fail(probe, "merge", "--abort")
        self.git("worktree", "remove", "--force", probe)
        self.assertTrue(conflicted, "前置:這兩條分支合起來沒有衝突,A4 證不到東西")
        self.assertEqual([name for name in conflicted if not name.startswith("code-map/")], [],
                         "前置:衝突不只在 code-map/ " + " ".join(conflicted))
        self.approve(1, "t1-map")
        self.approve(2, "t2-map")
        done = self.land("t1-map", "t2-map")
        output = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, output)
        check = self.map_cmd(self.repo, "check")
        self.assertEqual(check.returncode, 0, "落地後主線索引仍過期:" + check.stderr)
        managed = self.managed(self.repo)
        self.assertIn("tests/test_a1.py", managed)
        self.assertIn("tests/test_a2.py", managed)
        self.assertIn(MERGED_STALE, output)

    def test_a5a_a_conflict_outside_the_outputs_still_stops(self):
        """A5 (a) 兩條分支都改 src/same.txt(衝突含 code-map/ 以外)→ rc 3、land.fail、主線 HEAD 不動。"""
        self.write("src/same.txt", "base\n")
        self.git("add", "--", "src/same.txt")
        self.git("commit", "-q", "-m", "主線上先有 src/same.txt")
        self.git("push", "-q", "origin", "main")
        for ident, name, text in ((1, "tests/test_a1.py", "one\n"), (2, "tests/test_a2.py", "two\n")):
            branch = self.branch_adding(ident, name)
            self.write("src/same.txt", text, where=branch)
            self.git("add", "-A", cwd=branch)
            self.git("commit", "-q", "-m", "#%s 改 src/same.txt" % ident, cwd=branch)
        self.approve(1, "t1-map")
        self.approve(2, "t2-map")
        before = self.git("rev-parse", "main").strip()
        done = self.land("t1-map", "t2-map", "--no-auto-fix")
        output = done.stdout + done.stderr
        self.assertEqual(done.returncode, 3, output)
        self.assertIn("land.fail", self.kinds())
        self.assertEqual(self.git("rev-parse", "main").strip(), before)

    def test_a5b_a_rebuild_that_writes_outside_code_map_is_refused(self):
        """A5 (b) 假 repo-map.py(check 一律 1、build 多寫 stray.txt)→ rc 3、主線不動、worktree 留著、字樣、沒有 regen commit。"""
        self.write(os.path.join("scripts", "repo-map.py"), FAKE_REPO_MAP)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "假的 repo-map.py")
        self.git("push", "-q", "origin", "main")
        branch = self.branch_for(1, "t1-map")
        self.commit_in(branch, "src/b1", "有 commit 的那一張")
        self.approve(1, "t1-map")
        before = self.git("rev-parse", "main").strip()
        done = self.land("t1-map", "--no-auto-fix")
        output = done.stdout + done.stderr
        self.assertEqual(done.returncode, 3, output)
        self.assertEqual(self.git("rev-parse", "main").strip(), before)
        self.assertIn(STRAY, output)
        self.assertEqual(len(self.land_worktrees()), 1, "land worktree 要留著給人看:" + output)  # pin:這一次 land 只開一個 worktree
        self.assertEqual(self.regen_commits("%s..main" % before), [])
        for land in self.land_branches():
            self.assertEqual(self.regen_commits("main..%s" % land), [], land)

    def test_a6_a_fresh_merge_adds_no_regen_commit(self):
        """A6 合併結果 check 綠(分支只新增不受管理的 src/b1)→ 沒有 regen commit、沒有合併後過期字樣。"""
        branch = self.branch_for(1, "t1-map")
        self.commit_in(branch, "src/b1", "不受管理的檔")
        self.approve(1, "t1-map")
        before = self.git("rev-parse", "main").strip()
        done = self.land("t1-map")
        output = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, output)
        self.assertEqual(self.regen_commits("%s..main" % before), [])
        self.assertNotIn(MERGED_STALE, output)

    def test_a7_the_land_pass_still_spans_from_the_pre_merge_head(self):
        """A7 A3 那次落地的 land.pass:main_before = 合併前主線頭、sha = 含 regen commit 的新主線頭;first-parent 鏈接得上。"""
        before = self.stale_merge()
        done = self.land("t1-map")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        after = self.git("rev-parse", "main").strip()
        regen = self.regen_commits("%s..main" % before)
        self.assertEqual(len(regen), 1, self.git("log", "--oneline", "%s..main" % before))  # pin:票面 A3「有一個」regen commit
        passes = [row for row in self.events() if row["kind"] == "land.pass"]
        self.assertTrue(passes, "沒有 land.pass")
        row = passes[-1]
        self.assertEqual(row.get("main" + "_before"), before, row)
        sha = str(row.get("sha") or "")
        self.assertTrue(sha and after.startswith(sha), (row, after))
        chain = [line.split() for line in self.git(
            "log", "--first-parent", "--format=%H %P", "%s..%s" % (before, after)).splitlines()][::-1]
        self.assertEqual(chain[0][1], before, chain)
        self.assertEqual(chain[-1][0], after, chain)
        self.assertIn(regen[0][0], [item[0] for item in chain])


if __name__ == "__main__":
    unittest.main()
