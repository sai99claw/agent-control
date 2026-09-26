"""`scripts/land.sh` 的演練:一顆拋棄式真 repo + 一支寫標記檔就退出的假閘門。

#497(前一個專案,2026-09-10):有人套完 patch、跑完閘門(3424 條綠),然後直接
land —— 中間漏了 `git commit`。腳本照樣開 worktree、照樣跑完九分鐘的全套,最後照樣
說「main -> <原本的 sha> 已推上」,而主線一個位元都沒變。一次「成功的落地」交付了零。

這裡的 git **是真的 git**:整支腳本就是 git 語意,假一支出來等於在測自己寫的模擬器
(`docs/DISPATCH-TEMPLATE.md` §5.6)。貴的那一支才換成假的 —— `scripts/gate.sh` 換成
寫一行標記檔就退出,**「閘門有沒有被叫到」因此是一個看得見的事實**,而那正是這張票
的 bug 的可觀察面。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import (GATE_STUB_GREEN, GATE_STUB_RED, Sandbox,
                             write_executable)  # noqa: E402


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

        self.approve(1, "t1-first")
        self.approve(2, "t2-second")
        done = self.land("t1-first", "t2-second")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("land: t1-first —— 2 個 commit", done.stdout)
        self.assertIn("land: t2-second —— 1 個 commit", done.stdout)
        for subject in ("第一件事", "第二件事", "另一張票"):
            self.assertIn(subject, done.stdout, "要落地的 commit 沒有被唸出來")


class VerifyFilesMustBeOnTheBranch(LandBase):
    """#587 那把尺:票說「案例在這幾個檔」,而分支上沒有那幾個檔。

    少了這一條,票的 `verify.tags` 照樣會被閘門呼叫、照樣一個案例都選不到、照樣印
    一行綠 —— 驗證者的交付沒有跟著進來,而畫面上一個徵兆都沒有。
    """

    def test_a_missing_verify_file_is_refused_with_its_own_exit_code(self):
        """**變異**:把 `verify.files` 那一段拿掉 → 這一條紅。"""
        branch = self.branch_for(1, "t1-x", verify={
            "files": ["verify/example/test_ticket_1.py"], "tags": ["example"],
            "run": "python3 scripts/verify.py --tag example", "notes": ""})
        self.commit_in(branch, "src/a", "做了事,但沒把案例帶進來")
        self.approve(1, "t1-x")
        done = self.land("t1-x")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("verify/example/test_ticket_1.py", done.stdout)
        self.assertIn("不在這條分支上", done.stdout)
        self.assertFalse(self.gate_ran(), "拒絕要發生在跑九分鐘全套之前")

    def test_a_verify_file_that_is_there_lands_normally(self):
        """守衛要分得開「沒帶進來」與「帶進來了」—— 不然它擋的是所有帶 verify 的票。"""
        branch = self.branch_for(1, "t1-x", allowed_write_paths=["src/*", "verify/*"],
                                 verify={
            "files": ["verify/example/test_ticket_1.py"], "tags": ["example"],
            "run": "", "notes": ""})
        self.write("verify/example/test_ticket_1.py",
                   "import unittest\nTAGS = [\"example\"]\n\n\n"
                   "class T(unittest.TestCase):\n    def test_one(self):\n"
                   "        self.assertTrue(True)\n", where=branch)
        self.git("add", "-A", cwd=branch)
        self.git("commit", "-q", "-m", "案例跟著進來", cwd=branch)
        self.approve(1, "t1-x")
        done = self.land("t1-x")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_ticket_with_no_verify_files_is_not_blocked_by_this(self):
        branch = self.branch_for(1, "t1-x")
        self.commit_in(branch, "src/a", "還沒有回歸案例的票")
        self.approve(1, "t1-x")
        self.assertEqual(self.land("t1-x").returncode, 0)


class ProductTicketsNeedVerifierCases(LandBase):

    def test_a_product_ticket_without_verify_files_is_refused_and_names_the_action(self):
        branch = self.branch_for(1, "t1-x", in_scope=["demo/app.py"],
                                 allowed_write_paths=["demo/*"])
        self.commit_in(branch, "demo/app.py", "產品改動")
        self.approve(1, "t1-x")

        done = self.land("t1-x")

        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("verify.files 是空的", done.stdout)
        self.assertIn("派驗證者", done.stdout)
        self.assertIn("驗證者", self.run_py(
            "scripts/inbox.py", "show", "1").stdout)
        self.assertFalse(self.gate_ran(), "拒絕要在全套門禁之前")

    def test_needs_verifier_false_allows_a_product_ticket(self):
        branch = self.branch_for(1, "t1-x", in_scope=["demo/app.py"],
                                 allowed_write_paths=["demo/*"],
                                 needs_verifier=False)
        self.commit_in(branch, "demo/app.py", "明確不需要驗證者")
        self.approve(1, "t1-x")

        done = self.land("t1-x")

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_control_and_documentation_scopes_do_not_require_verifier_cases(self):
        branches = []
        for ident, scope in enumerate(("docs/guide.md", "board/config.json",
                                       "scripts/control/tool.sh"), 1):
            name = "t%s-x" % ident
            branch = self.branch_for(ident, name, in_scope=[scope],
                                     allowed_write_paths=[scope])
            self.commit_in(branch, scope, "非產品範圍 %s" % ident)
            branches.append(name)
        for ident, branch in enumerate(branches, 1):
            self.approve(ident, branch)

        done = self.land(*branches)

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class LandWakesMainUp(LandBase):
    """每一條退出路徑都寫一則收件匣 —— 主線不輪詢(D-015)。"""

    def inbox(self):
        return self.run_py("scripts/inbox.py", "list", "--all").stdout

    def test_a_green_landing_says_the_ticket_still_needs_closing(self):
        branch = self.branch_for(1, "t1-x")
        self.commit_in(branch, "src/a", "一件事")
        self.approve(1, "t1-x")
        self.assertEqual(self.land("t1-x").returncode, 0)
        listed = self.inbox()
        self.assertIn("#1", listed)
        self.assertIn("尚未關票", listed)

    def test_a_refusal_also_leaves_a_page(self):
        """**拒收也是終態**:退回去而沒有人知道,與沒有退回去一樣。"""
        self.branch_for(1, "t1-empty")
        self.assertNotEqual(self.land("t1-empty").returncode, 0)
        self.assertIn("land 拒收", self.inbox())


class AutoFixHook(LandBase):
    """land 預設 auto-fix:**但只在這一批剛好一張票的時候**。"""

    gate_stub = GATE_STUB_RED

    def setUp(self):
        super(AutoFixHook, self).setUp()
        write_executable(os.path.join(self.repo, "scripts", "auto-fix.sh"), """#!/bin/sh
echo "autofix $1 source=$AC_FIX_SOURCE" >> "$AC_TEST_LOG"
""")

    def auto_fix_lines(self):
        with open(self.log, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.startswith("autofix ")]

    def test_a_single_ticket_dispatches_by_default_from_the_land_tree(self):
        branch = self.branch_for(1, "t1-a")
        self.commit_in(branch, "src/a", "第一張")
        self.approve(1, "t1-a")

        done = self.land("t1-a")

        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(len(self.auto_fix_lines()), 1)
        self.assertIn("autofix 1 source=land/", self.auto_fix_lines()[0])

    def test_no_auto_fix_leaves_a_single_red_land_for_a_human(self):
        branch = self.branch_for(1, "t1-a")
        self.commit_in(branch, "src/a", "第一張")
        self.approve(1, "t1-a")

        done = self.land("t1-a", "--no-auto-fix")

        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.auto_fix_lines(), [])

    def test_a_batch_with_two_tickets_refuses_to_guess_who_is_red(self):
        """一批裡哪一條紅對到哪一張票,要有票↔案例的對照才判得出來 ——
        **猜錯的歸責比不歸責更貴**:它會讓新 worker 去修一張沒有壞的票。"""
        first = self.branch_for(1, "t1-a")
        self.commit_in(first, "src/a", "第一張")
        second = self.branch_for(2, "t2-b")
        self.commit_in(second, "src/b", "第二張")
        self.approve(1, "t1-a")
        self.approve(2, "t2-b")
        done = self.land("t1-a", "t2-b")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("不猜是誰紅的", done.stdout)

    def test_an_unknown_flag_is_named(self):
        self.branch_for(1, "t1-a")
        done = self.land("t1-a", "--redo-everything")
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("不認得", done.stdout)


class AutoFlakeOnLand(LandBase):
    gate_stub = None

    def test_a_single_ticket_land_auto_passes_a_confirmed_flake(self):
        branch = self.branch_for(1, "t1-flake", allowed_write_paths=["tests/*"])
        marker = repr(os.path.join(self.home, "land-flake"))
        self.write("tests/test_one_time_flake.py",
                   "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_flaky(self):\n"
                   "        path = %s\n"
                   "        seen = os.path.exists(path)\n"
                   "        open(path, 'a', encoding='utf-8').close()\n"
                   "        self.assertTrue(seen, 'one-time flake')\n" % marker, where=branch)
        self.git("add", "-A", cwd=branch)
        self.git("commit", "-q", "-m", "add one-time flake", cwd=branch)
        self.approve(1, "t1-flake")

        done = self.land("t1-flake")

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("flaky=auto", done.stdout)
        self.assertIn("flake.auto_pass", self.kinds())
        self.assertEqual(self.status_of(1, kind="gate")["flaky"], "auto")


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
        """排順序的人就是拿這一格判能不能平行的 —— 越界不只是「改了不該改的檔」,是
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


class TheDocsChannel(LandBase):
    """#29 A10 / G10 / G17:票檔、`memory/`、`docs/` 進主線的**入口**。

    以前它們只有裸 commit 一條路,而 `memory/role/main.md` 明禁裸 commit 主線 ——
    一條每天都在走、卻沒有任何守衛的路,與沒有規矩長得一樣(2026-09-23 一天兩筆)。
    """

    def docs(self, *args):
        return self.run_sh("scripts/land.sh", "docs", *args)

    def test_tickets_and_memory_go_in_with_one_commit(self):
        """**變異**:把 `cmd_docs` 那一段拿掉 → 這一條紅(`docs` 會被當成分支名)。"""
        self.make_ticket(7)
        self.write("memory/role/implementer.inbox.md", "- 一條新的教訓 (#7)\n")
        before = self.git("rev-parse", "main").strip()
        done = self.docs("tickets: #7 開票與一條 inbox",
                         "tickets/7.json", "memory/role/implementer.inbox.md")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("tickets: #7 開票與一條 inbox", self.main_log())
        landed = self.git("show", "--name-only", "--format=", "main").split()
        self.assertEqual(sorted(landed),
                         ["memory/role/implementer.inbox.md", "tickets/7.json"])
        self.assertNotEqual(self.git("rev-parse", "main").strip(), before)
        self.assertEqual(self.git("rev-parse", "main", cwd=self.origin).strip(),
                         self.git("rev-parse", "main").strip(), "push 沒有跟上")
        self.assertFalse(self.gate_ran(),
                         "這三個前綴不進產品碼 —— 跑一次全套換來的是同一份綠")

    def test_a_file_outside_the_three_prefixes_is_refused_by_name(self):
        """**變異**:把前綴檢查那一段拿掉 → 這一條紅。

        產品碼從這裡進去的那一刻,一票一分支、閘門、覆核三件事一起被繞過。
        """
        self.make_ticket(7)
        self.write("scripts/sneaky.sh", "#!/bin/sh\n")
        before = self.git("rev-parse", "main").strip()
        done = self.docs("順手帶一支腳本", "tickets/7.json", "scripts/sneaky.sh")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("scripts/sneaky.sh", done.stderr, "越界要指名是哪一個檔")
        self.assertIn("apply.sh", done.stderr, "說得出下一步(§5.7)")
        self.assertEqual(self.git("rev-parse", "main").strip(), before,
                         "拒收的那一次主線一個 commit 都不該多")

    def test_a_path_that_escapes_with_dotdot_is_refused(self):
        done = self.docs("繞出去", "docs/../scripts/land.sh")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("..", done.stderr)

    def test_it_waits_behind_the_same_lock_as_a_ticket_landing(self):
        """**同一把鎖**:land 跑到一半有人往主線塞 commit,那一條 `ff-only` 就進不去,
        而它的失敗訊息說的是「主線在這中間動了」—— 沒有人會知道動它的是誰。

        **變異**:把 `cmd_docs` 裡的 `take_lock` 拿掉 → 這一條紅。
        """
        self.make_ticket(7)
        os.mkdir(os.path.join(self.repo, ".land.lock"))
        with open(os.path.join(self.repo, ".land.lock", "holder"), "w") as handle:
            handle.write("pid=1 開始=now 分支=t1-good\n")
        done = self.docs("擠進去", "tickets/7.json")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("已經有一個 land 在跑", done.stdout)
        self.assertIn("t1-good", done.stdout, "說得出現在是誰在落地")

    def test_running_it_twice_is_idempotent_and_says_which_one_happened(self):
        """**已經在主線上就是做完了**,所以第二次 rc=0 —— 這一手要的是「這幾個檔在
        main 上」,而它們已經在了。第一版回 rc=3(「空的 commit 不是落地」),而那讓
        一個幂等的動作變成失敗:主線接連落地時很常重跑同一句,收到的會是一個假的紅。

        §5.5 要的是兩者**看得出差別**,而差別在輸出:第一次印 `docs -> main <sha>`,
        第二次印「已經在 main 上了,沒有新的 commit」,主線也不會多一個 commit。

        **變異**:把第二次那一段的訊息改成與第一次一樣 → 這一條紅。
        """
        self.make_ticket(7)
        first = self.docs("第一次", "tickets/7.json")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("docs -> main", first.stdout)
        before = self.git("rev-parse", "main").strip()

        again = self.docs("第二次", "tickets/7.json")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("已經在 main 上了", again.stdout)
        self.assertNotIn("docs -> main", again.stdout,
                         "「剛剛落地了」與「本來就在」要看得出差別")
        self.assertEqual(self.git("rev-parse", "main").strip(), before,
                         "沒有東西要落地的那一次不該多一個 commit")

    def test_it_refuses_when_the_checkout_is_not_on_main(self):
        self.make_ticket(7)
        self.git("checkout", "-q", "-b", "somewhere-else")
        done = self.docs("在別的分支上", "tickets/7.json")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("要在 main 上跑", done.stderr)

    def test_usage_names_the_three_prefixes(self):
        done = self.docs("只給訊息沒給檔")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for prefix in ("tickets/", "docs/", "memory/"):
            self.assertIn(prefix, done.stderr)


class LeftoverCopiesAreNamed(LandBase):
    """#29 A6 / G6:落地成功之後,**唸出還躺在 worktree 基底下的修復 / 驗證副本**。

    只印不刪:這裡不知道哪一份還有人在看(綠了停 InReview 的那一輪就留著),而猜錯
    刪掉的是別人正在讀的證據。印出來是為了讓「沒人收」不再是靜的 —— 2026-09-16
    某個下游專案的副本 14 GB 塞滿磁碟,而在那之前它一聲都沒有出過。
    """

    def test_a_green_landing_lists_what_is_still_lying_around(self):
        """**變異**:把 land.sh 尾端那一段 `LEFT=` 拿掉 → 這一條紅。"""
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")
        stale = os.path.join(self.wt_base(), "fix-t1", "round2")
        os.makedirs(stale)
        with open(os.path.join(stale, "patch-round2.diff"), "w") as handle:
            handle.write("# 上一輪的 patch\n")
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("還留著這幾份副本", done.stdout)
        self.assertIn("fix-t1", done.stdout)
        self.assertTrue(os.path.isdir(stale), "只印不刪 —— 那幾份是證據")

    def test_a_clean_worktree_base_says_nothing(self):
        """沒有東西要說的時候不要說 —— 每次都印的那一行,下一次就沒有人看了。"""
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("還留著這幾份副本", done.stdout)


class LeftoversUnderTheConfiguredWorktreeDir(LandBase):
    """#38 A4:殘留清單要看 **auto-fix 開副本的那個目錄** —— `worktree_dir` 以主 repo 根解析。

    以前 land.sh 的副本根不讀 `worktree_dir`,設了它之後 auto-fix 開副本的地方與這裡唸
    清單的地方不是同一個目錄,而「沒有殘留」與「看錯地方」長得一樣。
    """

    def test_a_hand_built_round_one_is_listed_after_a_green_landing(self):
        """**變異**:land.sh 的 `WTBASE` 改回 `$ROOT/../$(basename "$ROOT")-wt`(不讀 `worktree_dir`)→ 這一條紅。"""
        conf = json.loads(self.read("board/config.json"))
        conf["worktree_dir"] = "../x-wt"
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")
        stale = os.path.join(self.home, "x-wt", "fix-t1", "round1")
        os.makedirs(stale)
        with open(os.path.join(stale, "patch-round1.diff"), "w") as handle:
            handle.write("# 主線手建的第 1 輪留下的 patch\n")
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("還留著這幾份副本", done.stdout)
        self.assertIn(os.path.join("x-wt", "fix-t1"), done.stdout)
        self.assertTrue(os.path.isdir(stale), "只印不刪 —— 那幾份是證據")


class HappyPath(LandBase):

    def test_a_branch_with_commits_still_lands_the_way_it_always_did(self):
        """新加的那幾段只是把話說出來、把該拒的拒掉,不改任何後續行為 —— 沒有測試
        的「我沒有動到成功路徑」跟沒有保證是同一件事。

        釘的是那一整條:閘門被叫到、主線前進、origin 跟上、退出碼 0,以及
        「N 個 commit 串好,跑全套 -> …」那一行的格式。
        """
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")
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
        self.approve(1, "t1-good")
        self.assertEqual(self.land("t1-good").returncode, 0)
        self.assertEqual(os.listdir(self.wt_base()), [])

    def test_every_step_is_an_event(self):
        """控制台只讀事件,不猜 —— 沒發事件的事對系統而言沒發生(D-003)。"""
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")
        self.land("t1-good")
        kinds = self.kinds()
        for kind in ("land.start", "gate.start", "gate.pass", "land.pass"):
            self.assertIn(kind, kinds)


class GateRed(LandBase):
    gate_stub = GATE_STUB_RED

    def test_a_red_gate_leaves_main_alone_and_keeps_the_worktree(self):
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")
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


class LandStatus(LandBase):
    """狀態檔:這一批每一張票各一份 `reports/t<票號>/<run_id>/status.json`(D-010)。

    讀它的人手上有的是**票號**,不是這一批的時間戳 —— 所以一張票一份,不是一批一份。
    """

    def status(self, ident):
        return self.status_of(ident, kind="land")

    def test_every_ticket_in_the_batch_gets_its_own_done_status(self):
        first = self.branch_for(1, "t1-first")
        self.commit_in(first, "src/a1", "第一張")
        second = self.branch_for(2, "t2-second")
        self.commit_in(second, "src/b1", "第二張")
        self.approve(1, "t1-first")
        self.approve(2, "t2-second")

        done = self.land("t1-first", "t2-second")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for ident in ("1", "2"):
            data = self.status(ident)
            self.assertEqual(data["state"], "done", "#%s 停在 running" % ident)
            self.assertEqual(data["rc"], 0)
            self.assertEqual(data["kind"], "land")
            self.assertEqual(data["ticket"], ident)


class LandStatusWhenRed(LandBase):
    # 這一支假閘門除了標記檔,還在 worktree 裡留一份真的像 unittest 輸出的 log ——
    # 紅榜是從那一份剖出來的,所以演練要餵它真的形狀,不是一句「紅了」。
    gate_stub = """#!/bin/sh
echo "gate $* $(git rev-parse --short HEAD)" >> "$AC_TEST_LOG"
cat > gate.log <<'LOG'
======================================================================
FAIL: test_it (test_thing.T.test_it)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_thing.py", line 9, in test_it
    self.assertEqual(1, 2)
AssertionError: 1 != 2

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (failures=1)
LOG
echo "FAILED (假的紅)"
exit 1
"""

    def test_a_red_land_writes_the_red_list_and_says_where_to_look(self):
        """**變異**:把紅的那條路上的 `status_all done 1` 拿掉 → 這一條紅。

        理由:停在 `running` 的狀態檔與**還在跑**的狀態檔長得一模一樣,而下一個
        agent 分不出來時,它會回去做這一份檔本來要取代的那兩件事(讀整份 log、
        或輪詢等它跑完)。
        """
        import json
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")

        done = self.land("t1-good")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("reports/t<票號>/<run_id>/status.json", done.stdout,
                      "紅了要告訴人去哪裡看")
        data = self.status_of("1", kind="land")
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["rc"], 1)
        self.assertEqual([row["case"] for row in data["failures"]],
                         ["test_thing.T.test_it"])
        self.assertIn("AssertionError: 1 != 2", data["failures"][0]["excerpt"])


class ReviewIsAHardGate(LandBase):
    """覆核與反駁從「寫在票上的一格」變成**land 會拒絕的條件**(D-014)。

    2026-09-21 外部審查:`review` 只有 verdict / by / at / note,修復或重新套 patch
    之後舊 review 仍然長得有效,而 land 根本不讀它;實作者的反駁也沒有收件與處置的
    契約 —— 「這張票寫錯了」講完之後東西照樣落地。
    """

    def ready(self, ident=1, name="t1-good"):
        branch = self.branch_for(ident, name)
        self.commit_in(branch, "src/g1", "做完的那一張")
        return branch

    def test_a_branch_nobody_reviewed_is_refused(self):
        """**變異**:把 land 裡讀 review 的那一段拿掉 → 這一條紅。"""
        self.ready()
        before = self.main_log()
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("票上沒有 review", done.stdout)
        self.assertEqual(self.main_log(), before, "沒人覆核過的東西不該進主線")
        self.assertFalse(self.gate_ran(), "拒絕要發生在九分鐘的全套之前")

    def test_a_review_bound_to_an_older_commit_is_refused(self):
        """覆核綁的是**那一份** patch。蓋完章又 commit 一次,章就過期了。"""
        branch = self.ready()
        self.approve(1, "t1-good")
        self.commit_in(branch, "src/g2", "蓋完章之後又改的")
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("覆核之後又 commit 過", done.stdout)

    def test_a_review_stamped_before_a_ticket_edit_is_refused(self):
        branch = self.ready()
        self.approve(1, "t1-good")
        self.ticket("set", "1", "objective", "蓋完章之後改的票面")
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("覆核之後票被改過", done.stdout)

    def test_an_unresolved_blocking_objection_is_refused(self):
        """**變異**:把 objections 那一段拿掉 → 這一條紅。"""
        self.ready()
        self.ticket("set", "1", "objections",
                    json.dumps([{"category": "ticket-wrong", "owner": "main",
                                 "body": "驗收第二條和設計文件對不上",
                                 "evidence": "EVIDENCE.md:12", "disposition": ""}],
                               ensure_ascii=False))
        self.approve(1, "t1-good")
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("還沒處置", done.stdout)

    def test_a_disposed_objection_does_not_block(self):
        """處置過的反駁不擋 —— 這一格要的是**有人收、有人答**,不是不准有異議。"""
        self.ready()
        self.ticket("set", "1", "objections",
                    json.dumps([{"category": "ticket-wrong", "owner": "main",
                                 "body": "驗收第二條對不上", "evidence": "EVIDENCE.md:12",
                                 "disposition": "accepted", "follow_up": "#9"}],
                               ensure_ascii=False))
        self.approve(1, "t1-good")
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class OnlyOneLandAtATime(LandBase):
    """`docs/WORKFLOW.md` 早就寫「同時只准一個 land」,而腳本從來沒有擋(外部審查)。"""

    def test_a_second_land_is_refused_while_the_lock_is_held(self):
        """**變異**:把 `mkdir "$LOCK"` 那一段拿掉 → 這一條紅。"""
        branch = self.branch_for(1, "t1-good")
        self.commit_in(branch, "src/g1", "做完的那一張")
        self.approve(1, "t1-good")
        os.makedirs(os.path.join(self.repo, ".land.lock"))
        self.write(os.path.join(".land.lock", "holder"), "pid=999 開始=剛剛\n")
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("已經有一個 land 在跑", done.stdout)
        self.assertIn("pid=999", done.stdout, "要說得出現在是誰在落地")
        self.assertFalse(self.gate_ran())

    def test_the_lock_is_released_when_the_land_finishes(self):
        branch = self.branch_for(1, "t1-good")
        self.commit_in(branch, "src/g1", "做完的那一張")
        self.approve(1, "t1-good")
        self.assertEqual(self.land("t1-good").returncode, 0)
        self.assertFalse(self.exists(".land.lock"), "鎖沒有放掉,下一次 land 永遠卡住")

    def test_the_lock_is_released_even_when_it_refuses(self):
        self.branch_for(1, "t1-empty")
        self.assertEqual(self.land("t1-empty").returncode, 2)
        self.assertFalse(self.exists(".land.lock"))


class PhasesAndClosing(LandBase):

    def test_gate_merge_and_push_are_recorded_separately(self):
        """**變異**:把 `status_phase_all` 拿掉 → 這一條紅。

        舊版在 merge 與 push 之前就寫 `done, rc=0`,所以「閘門綠了但沒合進去」與
        「已經落地」在狀態檔上長得一樣(外部審查)。
        """
        branch = self.branch_for(1, "t1-good")
        self.commit_in(branch, "src/g1", "做完的那一張")
        self.approve(1, "t1-good")
        self.assertEqual(self.land("t1-good").returncode, 0)
        phases = [(row["phase"], row["rc"]) for row in self.status_of("1", kind="land")["phases"]]
        self.assertEqual(phases, [("gate", 0), ("merge", 0), ("push", 0)])

    def test_a_green_landing_says_the_ticket_is_still_open(self):
        """能力表誤稱 land 會關票(外部審查)—— 它不會,而「已合併」與「已關票」是
        兩件事。"""
        branch = self.branch_for(1, "t1-good")
        self.commit_in(branch, "src/g1", "做完的那一張")
        self.approve(1, "t1-good")
        done = self.land("t1-good")
        self.assertIn("已合併、尚未關票", done.stdout)
        self.assertIn("ticket.py close 1", done.stdout)
        self.assertEqual(self.load_ticket("1")["state"], "Ready", "land 不關票")

    def test_a_refused_batch_still_leaves_a_terminal_status(self):
        """拒絕也是一個結果 —— 停在 running 的狀態檔與還在跑的長得一樣。"""
        branch = self.branch_for(1, "t1-good")
        self.commit_in(branch, "src/g1", "做完的那一張")
        self.assertEqual(self.land("t1-good").returncode, 2)
        data = self.status_of("1", kind="land")
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["rc"], 2)


if __name__ == "__main__":
    unittest.main()
