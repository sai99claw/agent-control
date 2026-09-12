"""`scripts/land.sh` 的演練:一顆拋棄式真 repo + 一支寫標記檔就退出的假閘門。

#497(前一個專案,2026-09-10):有人套完 patch、跑完閘門(3424 條綠),然後直接
land —— 中間漏了 `git commit`。腳本照樣開 worktree、照樣跑完九分鐘的全套,最後照樣
說「main -> <原本的 sha> 已推上」,而主線一個位元都沒變。一次「成功的落地」交付了零。

這裡的 git **是真的 git**:整支腳本就是 git 語意,假一支出來等於在測自己寫的模擬器
(`docs/DISPATCH-TEMPLATE.md` §5.6)。貴的那一支才換成假的 —— `scripts/gate.sh` 換成
寫一行標記檔就退出,**「閘門有沒有被叫到」因此是一個看得見的事實**,而那正是這張票
的 bug 的可觀察面。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import GATE_STUB_GREEN, GATE_STUB_RED, Sandbox  # noqa: E402


class LandBase(Sandbox):
    gate_stub = GATE_STUB_GREEN

    def land(self, *branches):
        return self.run_sh("scripts/land.sh", *branches)

    def main_log(self):
        return self.git("log", "--oneline", "main")

    def wt_base(self):
        return os.path.join(self.home, "repo-wt")

    def branch_for(self, ident, name=None, **ticket_fields):
        """一票一分支:票寫進 `tickets/`(要在主線上,land 才讀得到),分支名
        `t<票號>-…`。"""
        self.make_ticket(ident, **ticket_fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #%s" % ident)
        self.git("push", "-q", "origin", "main")
        return self.worktree(name or "t%s-x" % ident)


class NamesWhatItIsAbout(LandBase):

    def test_it_names_every_commit_it_is_about_to_land(self):
        """光是「幾個 commit」還不夠 —— 標題逐條印出來,才連「以為套了 patch、
        其實套在別的分支」也會當場現形。

        **變異**:把 `git log --oneline "$MAIN..$b"` 那一行拿掉 → 這一條紅。
        """
        first = self.branch_for(1, "t1-first")
        self.commit_in(first, "src/a1", "第一件事")
        self.commit_in(first, "src/a2", "第二件事")
        second = self.branch_for(2, "t2-second")
        self.commit_in(second, "src/b1", "另一張票")

        done = self.land("t1-first", "t2-second")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("land: t1-first —— 2 個 commit", done.stdout)
        self.assertIn("land: t2-second —— 1 個 commit", done.stdout)
        for subject in ("第一件事", "第二件事", "另一張票"):
            self.assertIn(subject, done.stdout, "要落地的 commit 沒有被唸出來")


class ZeroCommits(LandBase):

    def test_a_branch_with_no_new_commits_is_refused_in_that_many_words(self):
        """退出碼之外連那句話一起釘 —— 它是給人看的,下一步就寫在裡面。

        **變異**:把 `if [ "$n" -eq 0 ]` 那一段拿掉 → 這一條紅。
        """
        self.branch_for(1, "t1-empty")
        before = self.main_log()

        done = self.land("t1-empty")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("land: t1-empty —— 0 個 commit", done.stdout)
        self.assertIn("是不是忘了 `git commit`?", done.stdout)
        self.assertFalse(self.gate_ran(),
                         "0 個 commit 還跑完整套,正是那個 bug")
        self.assertEqual(self.main_log(), before, "主線不該動")
        self.assertNotIn("串好,跑全套", done.stdout)

    def test_it_does_not_open_a_land_worktree_when_it_refuses(self):
        """拒絕發生在 `worktree add` 之前,所以沒有殘骸要人收。"""
        self.branch_for(1, "t1-empty")
        done = self.land("t1-empty")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.wt_base()))

    def test_a_clean_worktree_is_not_accused_of_holding_uncommitted_work(self):
        """「有東西沒 commit」與「這條分支就是空的」是兩件事,不能都印同一句。"""
        self.branch_for(1, "t1-empty")
        self.assertNotIn("還沒 commit 的改動", self.land("t1-empty").stdout)

    def test_the_uncommitted_changes_in_that_branch_worktree_are_named(self):
        """當時的處境是「東西都在,只差一個 commit」—— 指著那幾個檔比只說「沒有
        commit」少一次來回。

        **變異**:把 `status --porcelain` 那一段拿掉 → 這一條紅。
        """
        forgot = self.branch_for(1, "t1-forgot")
        self.write("README", "main\n改了但沒 commit\n", where=forgot)
        self.write("new_view.py", "還沒 add\n", where=forgot)

        done = self.land("t1-forgot")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn(forgot, done.stdout, "沒說是哪一個 worktree")
        self.assertIn("M README", done.stdout)
        self.assertIn("?? new_view.py", done.stdout)

    def test_a_branch_that_is_not_there_is_not_reported_as_zero_commits(self):
        """`git log main..打錯的名字` 一樣印不出東西 —— 兩件事分開講,不然下一步
        會找錯方向(改名字 vs 補一個 commit)。"""
        done = self.land("t9-typo")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("沒有這條分支", done.stdout)
        self.assertNotIn("忘了", done.stdout)
        self.assertFalse(self.gate_ran())

    def test_one_empty_branch_refuses_the_whole_batch(self):
        """`land.sh a b` 的語意是「這兩張一起進去」。跳掉 b 只合 a 會生出一個沒有人
        要求過的組合,而那個組合跑出來的綠只證明了那個組合 —— 所以斷言的重點是
        **有 commit 的那一支也沒有被合進去**,不是只看退出碼。

        **變異**:把整批拒絕改成跳過空的那一支 → 這一條紅。
        """
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.branch_for(2, "t2-empty")
        before = self.main_log()

        done = self.land("t1-good", "t2-empty")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("land: t1-good —— 1 個 commit", done.stdout)
        self.assertIn("land: t2-empty —— 0 個 commit", done.stdout)
        self.assertIn("跳掉一條會生出一個沒有人要求過的組合", done.stdout)
        self.assertEqual(self.main_log(), before, "主線不該動")
        self.assertNotIn("有 commit 的那一張", self.main_log(),
                         "只合了 t1-good = 一個沒有人要求過的組合")
        self.assertFalse(self.gate_ran(), "拒絕了就不該跑閘門")
        self.assertIn("land.refused", self.kinds())

    def test_no_branches_at_all_is_still_a_usage_error(self):
        done = self.land()
        self.assertEqual(done.returncode, 2)
        self.assertIn("給我至少一條分支", done.stdout)


class TicketChecks(LandBase):

    def test_a_branch_whose_name_matches_no_ticket_is_refused(self):
        """沒有票的工作不落地(CLAUDE.md)。"""
        path = self.worktree("hotfix-something")
        self.commit_in(path, "src/x", "順手修的")
        done = self.land("hotfix-something")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("分支名對不到票", done.stdout)
        self.assertFalse(self.gate_ran())

    def test_a_stale_base_sha_is_refused_with_the_reason(self):
        """閘門的綠是對某一個 base 說的。分支擱著沒落地的期間主線會往前走,那個綠
        就過期了 —— 而畫面上那一行 OK 一個字都沒變。

        **變異**:把 `merge-base --is-ancestor` 那一段拿掉 → 這一條紅。
        """
        # 真的存在、但不在主線上的 commit —— 那才是「閘門跑在一個主線走遠了的
        # 基準上」的形狀。用一個這顆 repo 沒有的 sha 測到的是另一件事(下一條)。
        aside = self.worktree("aside")
        self.commit_in(aside, "src/aside", "岔出去的一個 commit")
        stale = self.git("rev-parse", "aside").strip()
        branch = self.branch_for(1, "t1-stale", base_sha=stale)
        self.commit_in(branch, "src/a", "在過期的基準上做的")
        before = self.main_log()

        done = self.land("t1-stale")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("已經不是 main 的祖先", done.stdout)
        self.assertIn("先 rebase 再重跑閘門", done.stdout)
        self.assertEqual(self.main_log(), before)
        self.assertFalse(self.gate_ran())
        self.assertFalse(os.path.exists(self.wt_base()))

    def test_a_base_sha_this_repo_does_not_have_is_not_called_stale(self):
        """「答不出來」與「答案是不是」分開:一個這顆 repo 沒有的 sha,叫人去
        rebase 是一句假話 —— 它要回去查副本的 base 是拿哪個 ref 做的。

        **變異**:把 `cat-file -e` 那一段拿掉 → 這一條紅。
        """
        branch = self.branch_for(1, "t1-unknown", base_sha="0" * 40)
        self.commit_in(branch, "src/a", "base 對不上")
        done = self.land("t1-unknown")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("這顆 repo 沒有", done.stdout)
        self.assertNotIn("先 rebase", done.stdout)

    def test_a_ticket_with_no_base_sha_is_refused(self):
        branch = self.branch_for(1, "t1-nobase", base_sha="")
        self.commit_in(branch, "src/a", "票上沒有 base_sha")
        done = self.land("t1-nobase")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("沒有 base_sha", done.stdout)

    def test_writing_outside_allowed_write_paths_is_refused_and_the_files_are_named(self):
        """排程器就是拿這一格判能不能平行的 —— 越界不只是「改了不該改的檔」,是
        排程當時算出來的那張衝突圖已經不成立。

        **變異**:把越界那一段的 `if [ -n "$out" ]` 改成永遠不成立 → 這一條紅。
        """
        branch = self.branch_for(1, "t1-wide", allowed_write_paths=["src/*"])
        self.commit_in(branch, "src/inside.py", "範圍內")
        self.commit_in(branch, "docs/outside.md", "範圍外")
        before = self.main_log()

        done = self.land("t1-wide")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("allowed_write_paths 以外", done.stdout)
        self.assertIn("docs/outside.md", done.stdout)
        self.assertNotIn("land:   src/inside.py", done.stdout,
                         "範圍內的檔不該被當成越界")
        self.assertEqual(self.main_log(), before)
        self.assertFalse(self.gate_ran())


class HappyPath(LandBase):

    def test_a_branch_with_commits_still_lands_the_way_it_always_did(self):
        """新加的那幾段只是把話說出來、把該拒的拒掉,不改任何後續行為 —— 沒有測試
        的「我沒有動到成功路徑」跟沒有保證是同一件事。

        釘的是那一整條:閘門被叫到、主線前進、origin 跟上、退出碼 0,以及
        「N 個 commit 串好,跑全套 -> …」那一行的格式。
        """
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        before = self.git("rev-parse", "main").strip()

        done = self.land("t1-good")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertRegex(done.stdout, r"land: \d+ 個 commit 串好,跑全套 -> .*gate\.log")
        self.assertTrue(self.gate_ran(), "閘門沒有被叫到")
        self.assertIn("--full", self.read("calls.log", where=self.home),
                      "落地要跑的是全套")
        after = self.git("rev-parse", "main").strip()
        self.assertNotEqual(after, before, "主線沒有前進")
        self.assertIn("有 commit 的那一張", self.main_log())
        self.assertIn("land: main -> ", done.stdout)
        self.assertEqual(self.git("rev-parse", "main", cwd=self.origin).strip(), after,
                         "push 沒有跟上")

    def test_the_land_worktree_is_cleaned_up_after_a_green_landing(self):
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.assertEqual(self.land("t1-good").returncode, 0)
        self.assertEqual(os.listdir(self.wt_base()), [])

    def test_every_step_is_an_event(self):
        """控制台只讀事件,不猜 —— 沒發事件的事對系統而言沒發生(D-003)。"""
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.land("t1-good")
        kinds = self.kinds()
        for kind in ("land.start", "gate.start", "gate.pass", "land.pass"):
            self.assertIn(kind, kinds)


class GateRed(LandBase):
    gate_stub = GATE_STUB_RED

    def test_a_red_gate_leaves_main_alone_and_keeps_the_worktree(self):
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        before = self.main_log()

        done = self.land("t1-good")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("全套紅", done.stdout)
        self.assertEqual(self.main_log(), before, "紅了主線不該動")
        self.assertTrue(os.listdir(self.wt_base()), "worktree 要留著給人看")
        kinds = self.kinds()
        self.assertIn("gate.fail", kinds)
        self.assertIn("land.fail", kinds)
        self.assertNotIn("land.pass", kinds)


if __name__ == "__main__":
    unittest.main()
