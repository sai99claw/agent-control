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
diff -ruN base/config/private.ini work/config/private.ini
--- base/config/private.ini\t1970-01-01 08:00:00
+++ work/config/private.ini\t2026-09-21 10:00:00
@@ -0,0 +1 @@
+also outside allowed_write_paths
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

# 重套那幾條用一個**有上下文**的檔。三向合併看的就是上下文,而一行的檔沒有上下文:
# 主線在唯一那一行旁邊動一下,對 git 來說就是同一塊被兩邊改了(`git rebase` 自己也
# 衝突)。拿一行的檔去量,量到的是「模糊比對敢不敢猜」,不是重套對不對。
WIDE = "序\n一\n二\nold\n三\n四\n跋\n"

WIDE_CHANGE = """diff -ruN base/src/wide.txt work/src/wide.txt
--- base/src/wide.txt\t2026-09-21 10:00:00
+++ work/src/wide.txt\t2026-09-21 10:00:00
@@ -1,7 +1,7 @@
 序
 一
 二
-old
+new
 三
 四
 跋
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

    def test_it_harvests_two_memory_notes_from_evidence(self):
        self.make("1")
        evidence = self.patch_file(
            "EVIDENCE.md",
            "# Evidence\n\n## 記憶\n"
            "memory.py note role implementer \"第一條原則\" --ticket 1 --by worker@opus\n"
            "python3 scripts/memory.py note model opus \"第二條原則\" "
            "--ticket 1 --by worker@opus\n")
        patch = self.patch_file("p.diff", CHANGE)
        done = self.apply("1", patch, "--evidence", evidence)
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
        done = self.apply("1", self.patch_file("p.diff", CHANGE),
                          "--evidence", evidence)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stderr.count("拒絕 evidence 行"), 2, done.stderr)
        self.assertIn("仍會收這行", self.read("memory/role/implementer.inbox.md"))
        self.assertEqual(self.kinds().count("memory.noted"), 1)

    def test_an_explicit_no_memory_section_writes_nothing(self):
        self.make("1")
        evidence = self.patch_file("EVIDENCE.md", "## 記憶\n無\n")
        done = self.apply("1", self.patch_file("p.diff", CHANGE),
                          "--evidence", evidence)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("memory.noted", self.kinds())


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
        self.assertIn("docs/secret.md", done.stderr)
        self.assertIn("config/private.ini", done.stderr)
        self.assertIn(
            "python3 scripts/ticket.py set 1 allowed_write_paths "
            "'[\"src/*\", \"config/private.ini\", \"docs/secret.md\"]'",
            done.stderr)
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
    """落地前把 patch 重套到**當前主線**,重生清單,出一份乾淨的 diff(D-012)。

    重套是**三向合併**:祖先 = 票的 `base_sha`、我方 = 當前主線、對方 = base_sha + patch
    (#17)。以前這裡是 GNU `patch -F 2`,而判失敗只看 `.rej` —— 上下文走遠的時候
    `patch` 會整支 fatal 掉(「misordered hunks」)、**一個 `.rej` 都不留**,於是 0 byte
    的檔案被印成「乾淨的 diff」,一路要到下一步 `git apply` 才喊「一個檔頭都沒有」。
    """

    def setUp(self):
        super(Rebase, self).setUp()
        self.write("src/wide.txt", WIDE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線上先有一個有上下文的 src/wide.txt")

    def main_moves_near_the_patch(self):
        """主線在 patch 那一塊**旁邊**動一行:三向合併過得去,而重生的 diff 要說得出
        主線這一行 —— 那正是「對今天的主線說的」的意思。"""
        self.write("src/wide.txt", WIDE.replace("一\n", "壹\n", 1))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線在隔壁那一行走遠了")

    def rebased(self, ident="1"):
        out = os.path.join(self.repo, "reports", "t%s" % ident, "patch-rebased.diff")
        with open(out, encoding="utf-8") as handle:
            return out, handle.read()

    def test_it_rebuilds_a_clean_diff_against_todays_main(self):
        self.make("1")
        self.main_moves_near_the_patch()
        done = self.apply("rebase", "1", self.patch_file("p.diff", WIDE_CHANGE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        out, text = self.rebased()
        self.assertTrue(os.path.exists(out), done.stdout)
        self.assertIn("--- base/src/wide.txt", text)
        self.assertIn("+new", text)
        self.assertIn("壹", text, "重生出來的 diff 要是對今天的主線說的")

    def test_a_ticket_without_base_sha_names_the_missing_field(self):
        self.make("1", base_sha="")
        done = self.apply("rebase", "1", self.patch_file("p.diff", CHANGE))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("票 #1 缺 base_sha", done.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.home, "repo-wt", "rebase-t1")),
                         "答不出原 patch 的版本時不該先做一份看似可用的副本")

    def test_a_base_sha_the_repo_does_not_have_is_named_before_any_copy(self):
        """`base_sha` 是三向合併的祖先。取不出祖先的時候沒有任何一棵樹算得出來 ——
        而「算不出來」不可以長得像「算出來是空的」。"""
        self.make("1", base_sha="0" * 40)
        done = self.apply("rebase", "1", self.patch_file("p.diff", CHANGE))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("0" * 40, done.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.home, "repo-wt", "rebase-t1")),
                         "取不出祖先時不該先做一份看似可用的副本")

    def test_the_rebuilt_diff_applies_cleanly_through_the_normal_entry(self):
        """重生的 diff 要**能餵回這一支自己** —— 不然它只是一個好看的檔。"""
        self.make("1")
        self.main_moves_near_the_patch()
        self.assertEqual(
            self.apply("rebase", "1", self.patch_file("p.diff", WIDE_CHANGE)).returncode, 0)
        out, _ = self.rebased()
        done = self.apply("1", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.git("show", "t1:src/wide.txt"),
                         WIDE.replace("一\n", "壹\n", 1).replace("old\n", "new\n"))

    def test_a_deletion_comes_back_with_dev_null_on_the_plus_side(self):
        """重生出來的 diff 自己就是下一步的輸入,所以刪檔那一側要**這裡**改好 ——
        叫人手改的那一步漏掉時,畫面上一個徵兆都沒有。"""
        self.make("1")
        done = self.apply("rebase", "1", self.patch_file("p.diff", DELETE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("+++ /dev/null", self.rebased()[1])

    def test_a_three_way_conflict_fails_and_names_the_file_and_the_hunk(self):
        """「套了但有幾塊沒進去」與「全套進去了」在退出碼上長得一樣(D-012 第 2 點),
        所以衝突要非零 —— 而且要指名到**檔與行**:「有問題」不是一個可以執行的動作。

        **變異**:把 `gitw merge` 那一段的 `exit 3` 改成 `:` → 這一條紅。
        """
        self.make("1")
        self.write("src/wide.txt", "序\n一\n二\n完全不一樣的內容\n三\n四\n跋\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線把同一行重寫了")
        done = self.apply("rebase", "1", self.patch_file("p.diff", WIDE_CHANGE))
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        # 指名要是**自己說的那一句**,不是順手轉印的 git 輸出 —— 轉印那一段換個
        # git 版本就換句話,而這一條問的是「接手的人看不看得到要動哪個檔的哪幾行」。
        self.assertIn("衝突檔 src/wide.txt", done.stdout, "衝突的檔名要在 stdout 上")
        self.assertRegex(done.stdout, r"第 \d+ 行:<<<<<<<")
        self.assertFalse(
            os.path.exists(os.path.join(self.repo, "reports", "t1",
                                        "patch-rebased.diff")),
            "衝突的時候不該留下一份看起來可以餵給下一步的 diff")

    def test_an_empty_rebuilt_diff_is_never_called_clean(self):
        """🩸 #17:`patch(1)` 在上下文走遠時整支 fatal 掉、**不留 `.rej`**,於是舊版印
        「乾淨的 diff」而檔案是 0 byte,要到下一步 `git apply` 才喊「一個檔頭都沒有」。
        **「沒有東西可做」與「做完了」長得一樣**,所以 0 byte 一定要自己喊。

        **變異**:把 `[ ! -s "$out" ]` 那一段拿掉 → 這一條紅(rc 回 0、又印「乾淨的 diff」)。
        """
        self.make("1")
        # 主線上已經有 patch 要做的那件事:三向合併過得去,但重套出來的樹 == 主線。
        self.write("src/wide.txt", WIDE.replace("old\n", "new\n"))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線上已經是 new 了")
        out = self.patch_file("rebased.diff", "")
        done = self.apply("rebase", "1", self.patch_file("p.diff", WIDE_CHANGE), "-o", out)
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(os.path.getsize(out), 0)
        self.assertNotIn("乾淨的 diff", done.stdout, done.stdout)
        self.assertIn("0 byte", done.stderr, done.stderr)

    def test_the_rebuilt_tree_is_what_git_rebase_itself_would_have_produced(self):
        """重套的定義就是 rebase,所以答案要跟 **git 自己 rebase** 出來的那一棵樹
        逐位元相同 —— 不然這一支只是「某種會猜的東西」,而它猜錯的那一次沒有人會知道。

        **變異**:把三向合併換成「把 patch 那一版直接蓋上去」
        (`gitw merge …` → `gitw checkout ac-rebase-ticket -- .`)→ 這一條紅。
        """
        import subprocess
        self.make("1")
        self.main_moves_near_the_patch()
        patch = self.patch_file("p.diff", WIDE_CHANGE)
        self.assertEqual(self.apply("rebase", "1", patch).returncode, 0)
        out, _ = self.rebased()
        done = self.apply("1", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

        # 對照組:同一份 patch 在 base_sha 上 commit,然後讓 **git 自己** rebase 到主線。
        where = os.path.join(self.home, "wt", "git-rebase")
        self.git("worktree", "add", "-q", "-b", "g1", where,
                 self.load_ticket("1")["base_sha"])
        plain = subprocess.run(["git", "-C", where, "apply", "-p1", patch],
                               capture_output=True, text=True, env=self.env())
        self.assertEqual(plain.returncode, 0, plain.stdout + plain.stderr)
        self.git("add", "-A", cwd=where)
        self.git("commit", "-q", "-m", "t1 的那一手", cwd=where)
        self.git("rebase", "main", cwd=where)

        self.assertEqual(self.git("rev-parse", "t1^{tree}"),
                         self.git("rev-parse", "g1^{tree}"),
                         "重套出來的樹與 git 自己 rebase 的不是同一棵")

    def test_a_patch_that_does_not_fit_its_own_base_sha_is_refused(self):
        """三向合併的「對方」是 base_sha + patch。patch 套不回自己的 base_sha,就代表
        票面上那一格是錯的 —— 這時候猜出來的任何一棵樹都沒有意義。

        **變異**:把 `gitw apply` 後面的 `exit 3` 改成 `:` → 這一條紅。
        """
        self.make("1", base_sha=self.git("rev-list", "--max-parents=0", "main").strip())
        done = self.apply("rebase", "1", self.patch_file("p.diff", WIDE_CHANGE))
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("base_sha", done.stderr)


if __name__ == "__main__":
    unittest.main()
