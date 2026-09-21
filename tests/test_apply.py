"""`scripts/apply.sh`:套 patch → 建分支 → commit 的程式入口(D-015)。

這一手以前是**主線手動做的**(`docs/ROLES.md` 的引言框),而手動的問題不是慢,是
**每一次都重新決定要不要檢查那五件事**。這一組問的就是那五件:

- 檔頭是不是 `base/…` / `work/…` 的相對形式 —— 🩸 絕對路徑的檔頭真的發生過:檔案被
  寫進暫存目錄底下的同名路徑,套用成功、閘門也綠,而**被改的不是 repo 裡那一份**。
- `diff -ruN` 的刪檔有沒有把 `+++` 側改成 `/dev/null` —— 不改的話 `git apply` 只會把
  檔案**清空**,而清空的檔在 diffstat 上看起來像「改過」。
- 套完的樹與 patch 說的是不是同一件事(該在的在、該刪的不在、反著套回得去)。
- 有沒有動到 `allowed_write_paths` 以外。
- commit 訊息答不答得出「分支上這一手是哪一份 patch 做的」—— 路徑答不出來,
  重套過的那一份路徑一樣、內容不一樣,所以要 sha256。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

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

ABSOLUTE = """diff -ruN /somewhere/base/src/a.txt /somewhere/work/src/a.txt
--- /somewhere/base/src/a.txt\t2026-09-21 10:00:00
+++ /somewhere/work/src/a.txt\t2026-09-21 10:00:00
@@ -1 +1 @@
-old
+new
"""

# 刪檔,而 `+++` 側還指著真的路徑 —— `git apply` 會把它清空,不是刪掉。
EMPTYING = """diff -ruN base/src/a.txt work/src/a.txt
--- base/src/a.txt\t2026-09-21 10:00:00
+++ work/src/a.txt\t1970-01-01 08:00:00
@@ -1 +0,0 @@
-old
"""

DELETE = """diff -ruN base/src/a.txt work/src/a.txt
--- base/src/a.txt\t2026-09-21 10:00:00
+++ /dev/null\t1970-01-01 08:00:00
@@ -1 +0,0 @@
-old
"""

OUTSIDE = """diff -ruN base/docs/secret.md work/docs/secret.md
--- base/docs/secret.md\t1970-01-01 08:00:00
+++ work/docs/secret.md\t2026-09-21 10:00:00
@@ -0,0 +1 @@
+not in allowed_write_paths
"""

IGNORED = """diff -ruN base/junk/x.log work/junk/x.log
--- base/junk/x.log\t1970-01-01 08:00:00
+++ work/junk/x.log\t2026-09-21 10:00:00
@@ -0,0 +1 @@
+ignored by .gitignore
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


class TheHappyPath(ApplyBase):

    def test_it_makes_the_branch_applies_the_patch_and_commits(self):
        self.make("1")
        patch = self.patch_file("p.diff", CHANGE)
        done = self.apply("1", patch)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(self.branch_exists("t1"), done.stdout)
        self.assertEqual(self.git("show", "t1:src/a.txt"), "new\n")
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "1")

    def test_the_commit_message_names_the_ticket_and_the_patch_hash(self):
        """路徑答不出「是不是同一份 patch」—— 重套過的那一份路徑一樣、內容不一樣。

        **變異**:把 commit 訊息裡的 `sha256=` 拿掉 → 這一條紅。
        """
        import hashlib
        self.make("1")
        patch = self.patch_file("p.diff", CHANGE)
        self.assertEqual(self.apply("1", patch).returncode, 0)
        message = self.git("log", "-1", "--format=%B", "t1")
        self.assertIn("#1", message)
        self.assertIn("sha256=" + hashlib.sha256(CHANGE.encode()).hexdigest(), message)

    def test_a_second_round_adds_a_commit_to_the_same_branch(self):
        """一票一分支:第二輪的修補進同一條分支,不另開一條。"""
        self.make("1")
        self.assertEqual(self.apply("1", self.patch_file("p1.diff", CHANGE)).returncode, 0)
        done = self.apply("1", self.patch_file("p2.diff", CREATE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "2")
        self.assertEqual(self.git("show", "t1:src/b.txt"), "made by the patch\n")

    def test_the_verify_patch_rides_along_in_the_same_commit(self):
        self.make("1", allowed_write_paths=["src/*", "verify/*"])
        done = self.apply("1", self.patch_file("p.diff", CHANGE),
                          self.patch_file("v.diff", VERIFY_CASE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("TAGS", self.git("show", "t1:verify/example/test_ticket_1.py"))
        self.assertIn("patch-verify:", self.git("log", "-1", "--format=%B", "t1"))

    def test_it_leaves_a_status_file_for_the_round(self):
        self.make("1")
        self.assertEqual(self.apply("1", self.patch_file("p.diff", CHANGE)).returncode, 0)
        data = self.status_of("1", kind="apply")
        self.assertEqual(data["rc"], 0)
        self.assertEqual(data["state"], "done")
        self.assertTrue(data["repair_context"]["patch"]["sha256"])


class ThingsItRefuses(ApplyBase):

    def test_an_absolute_header_is_refused_before_anything_is_touched(self):
        """🩸 絕對路徑的檔頭讓檔案被寫進暫存目錄,而套用本身成功、閘門也綠。

        **變異**:把 `headers()` 裡 `name.startswith("/")` 那一條拿掉 → 這一條紅。
        """
        self.make("1")
        done = self.apply("1", self.patch_file("p.diff", ABSOLUTE))
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("絕對路徑", done.stderr)
        self.assertFalse(self.branch_exists("t1"), "拒絕要發生在開分支之前")

    def test_a_deletion_that_only_empties_the_file_is_refused(self):
        """`diff -ruN` 的刪檔:`+++` 側不是 `/dev/null` 的話,`git apply` 只會清空它,
        而清空的檔在 diffstat 上看起來像「改過」。"""
        self.make("1")
        done = self.apply("1", self.patch_file("p.diff", EMPTYING))
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("/dev/null", done.stderr)

    def test_a_real_deletion_with_dev_null_goes_through(self):
        """守衛要分得開「刪檔」與「寫錯的刪檔」—— 不然它擋的是所有刪檔。"""
        self.make("1")
        done = self.apply("1", self.patch_file("p.diff", DELETE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        listed = self.git("ls-tree", "-r", "--name-only", "t1")
        self.assertNotIn("src/a.txt", listed.split())

    def test_writing_outside_allowed_write_paths_is_refused_and_not_committed(self):
        self.make("1", allowed_write_paths=["src/*"])
        done = self.apply("1", self.patch_file("p.diff", OUTSIDE))
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("allowed_write_paths", done.stderr)
        self.assertEqual(self.git("rev-list", "--count", "main..t1").strip(), "0",
                         "越界的 patch 不該留下 commit")

    def test_a_patch_that_only_touches_ignored_files_is_not_a_green_apply(self):
        """套完之後 `git add -A` 一個位元都沒進 index —— **「套好了」與「什麼都沒做」
        在退出碼上長得一樣**,而下一步(land)只看 commit 數。"""
        self.make("1", allowed_write_paths=["junk/*"])
        done = self.apply("1", self.patch_file("p.diff", IGNORED))
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("等於什麼都沒做", done.stderr)

    def test_a_ticket_with_no_base_sha_is_refused(self):
        self.make("1", base_sha="")
        done = self.apply("1", self.patch_file("p.diff", CHANGE))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("base_sha", done.stderr)

    def test_a_missing_ticket_is_named(self):
        done = self.apply("9", self.patch_file("p.diff", CHANGE))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("#9", done.stderr)

    def test_verify_strings_that_are_not_in_the_patch_are_called_out(self):
        """字串不在 patch 裡就不會在分支上,而那要等到 `close` 才會說話(D-012 第 4 點)。"""
        self.make("1", verify_strings=[{"path": "src/a.txt", "contains": "沒有這串"}])
        done = self.apply("1", self.patch_file("p.diff", CHANGE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("verify_strings", done.stdout)


class Rebase(ApplyBase):
    """落地前把 patch 套到**當前主線**的副本,重生清單,出一份乾淨的 diff(D-012)。"""

    def test_it_rebuilds_a_clean_diff_against_todays_main(self):
        self.make("1")
        # 主線往前走一步:patch 的上下文位移了,`git apply` 會拒絕,GNU patch 吃得下。
        self.write("src/a.txt", "一行前言\nold\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線走遠一步")
        done = self.apply("rebase", "1", self.patch_file("p.diff", CHANGE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        out = os.path.join(self.repo, "reports", "t1", "patch-rebased.diff")
        self.assertTrue(os.path.exists(out), done.stdout)
        with open(out, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("--- base/src/a.txt", text)
        self.assertIn("+new", text)
        self.assertIn("一行前言", text, "重生出來的 diff 要是對今天的主線說的")

    def test_a_ticket_without_base_sha_names_the_missing_field(self):
        self.make("1", base_sha="")
        done = self.apply("rebase", "1", self.patch_file("p.diff", CHANGE))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("票 #1 缺 base_sha", done.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.home, "repo-wt", "rebase-t1")),
                         "答不出原 patch 的版本時不該先做一份看似可用的副本")

    def test_the_rebuilt_diff_applies_cleanly_through_the_normal_entry(self):
        """重生的 diff 要**能餵回這一支自己** —— 不然它只是一個好看的檔。"""
        self.make("1")
        self.write("src/a.txt", "一行前言\nold\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線走遠一步")
        self.assertEqual(
            self.apply("rebase", "1", self.patch_file("p.diff", CHANGE)).returncode, 0)
        out = os.path.join(self.repo, "reports", "t1", "patch-rebased.diff")
        done = self.apply("1", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("show", "t1:src/a.txt"), "一行前言\nnew\n")

    def test_a_deletion_comes_back_with_dev_null_on_the_plus_side(self):
        """重生出來的 diff 自己就是下一步的輸入,所以刪檔那一側要**這裡**改好 ——
        叫人手改的那一步漏掉時,畫面上一個徵兆都沒有。"""
        self.make("1")
        done = self.apply("rebase", "1", self.patch_file("p.diff", DELETE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(os.path.join(self.repo, "reports", "t1", "patch-rebased.diff"),
                  encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("+++ /dev/null", text)

    def test_leftover_rej_files_are_a_failure_not_a_warning(self):
        """**`.rej` 數量 ≠ 0 一律當失敗**:「套了但有幾塊沒進去」與「全套進去了」
        在退出碼上長得一樣(D-012 第 2 點)。"""
        self.make("1")
        self.write("src/a.txt", "完全不一樣的內容\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線把那個檔重寫了")
        done = self.apply("rebase", "1", self.patch_file("p.diff", CHANGE))
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn(".rej", done.stderr)


if __name__ == "__main__":
    unittest.main()
