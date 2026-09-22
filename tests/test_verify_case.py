"""`scripts/verify-case.py`:同一份案例,乾淨主線該紅、candidate 該綠。

2026-09-21 外部審查:「沒有『乾淨主線該紅』的機器檢查。」範本只要求驗證者貼兩份
Ran/OK,而**一份貼上來的輸出沒有辦法被機器比對** —— 票的 `verify` 裡沒有一格說得出
「乾淨主線上真的紅過」,於是一條永遠綠的斷言與一條真的在驗的斷言長得一模一樣。

這一組釘的是那一格能不能被偽造:
- 案例在兩邊都綠 → 不准算過(「一條都沒紅」)。
- 乾淨主線上是 `import` 炸掉 → 不算紅,而且要**明列**(那是還沒接上,不是驗到了)。
- 過了才寫進票的 `verify.baseline`,讓 `ticket.py close` 有東西可以問。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

# 期望值來源獨立於被測程式:這個 2 是票面說的,不是跑一次記下來的。
CASE = """import unittest
TAGS = ["example"]


class NavSize(unittest.TestCase):
    def test_value(self):
        with open("src/value.txt", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "2")
"""

ALWAYS_GREEN = """import unittest
TAGS = ["example"]


class Nothing(unittest.TestCase):
    def test_true(self):
        self.assertTrue(True)
"""

NEEDS_NEW_MODULE = """import unittest
from src.newthing import size

TAGS = ["example"]


class NeedsIt(unittest.TestCase):
    def test_size(self):
        self.assertEqual(size(), 2)
"""


class VerifyCase(Sandbox):

    def tool(self, *args):
        return self.run_py("scripts/verify-case.py", *args)

    def setUp(self):
        super().setUp()
        self.write("src/value.txt", "1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線上的值是 1")
        self.write(os.path.join("verify", "nav", "__init__.py"), "")

    def a_ticket(self, case=CASE):
        self.write(os.path.join("verify", "nav", "test_ticket_1.py"), case)
        self.make_ticket(1, verify={"files": ["verify/nav/test_ticket_1.py"],
                                    "tags": ["example"], "run": "", "notes": ""})

    # ------------------------------------------------------------ 驗紅驗綠

    def test_red_on_clean_main_and_green_on_candidate_is_recorded(self):
        """**變異**:把 `cmd_check` 裡「乾淨主線上一條都沒紅」那一句拿掉 → 下一條紅。"""
        self.a_ticket()
        self.write("src/value.txt", "2\n")          # candidate 上的改動
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertTrue(base["ok"], base["why"])
        self.assertEqual(base["baseline"]["red"], ["verify.nav.test_ticket_1.NavSize.test_value"])
        self.assertEqual(base["candidate_run"]["red"], [])
        self.assertEqual(base["baseline"]["cases"], 1)
        self.assertTrue(base["base_sha"], "沒記兩邊的 sha 就答不出這是對哪一版說的")
        self.assertTrue(base["candidate_sha"])

    def test_a_case_that_is_green_everywhere_does_not_count(self):
        """🩸 少了「乾淨主線上會紅」這一半,一個永遠綠的案例與一個真的在驗的案例
        長得一樣。"""
        self.a_ticket(ALWAYS_GREEN)
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("一條都沒紅", done.stdout)
        self.assertFalse(self.load_ticket("1")["verify"]["baseline"]["ok"])

    def test_an_import_failure_is_listed_and_does_not_count_as_red(self):
        """**變異**:把 `IMPORT_MARKS` 那一段拿掉 → 這一條紅。

        乾淨主線上沒有那個新符號,案例 `import` 就會炸;它與真的驗到了一樣讓
        unittest 回非零,而下一步差很多。
        """
        self.a_ticket(NEEDS_NEW_MODULE)
        self.write("src/newthing.py", "def size():\n    return 2\n")
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("import 失敗(不算紅)", done.stdout)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertFalse(base["ok"])
        self.assertTrue(base["baseline"]["import_failures"])
        self.assertEqual(base["baseline"]["red"], [])

    def test_a_ticket_without_verify_files_says_so(self):
        self.make_ticket(1)
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("verify.files 是空的", done.stderr)

    # ------------------------------------------ candidate 的三種寫法(#22)

    def a_branch(self, branch="t1", case=CASE):
        """票的實作與案例都 commit 在分支上,主線還停在舊值 —— 落地**前**的真實形狀。

        關鍵是 repo 的工作樹裡**沒有**那個案例檔:只有真的去 git 裡把 candidate 那棵樹
        拿出來,才跑得到它。
        """
        path = self.worktree(branch)
        self.write("src/value.txt", "2\n", where=path)
        self.write(os.path.join("verify", "nav", "__init__.py"), "", where=path)
        self.write(os.path.join("verify", "nav", "test_ticket_1.py"), case, where=path)
        self.git("add", "-A", cwd=path)
        self.git("commit", "-q", "-m", "值改成 2,附案例", cwd=path)
        self.make_ticket(1, verify={"files": ["verify/nav/test_ticket_1.py"],
                                    "tags": ["example"], "run": "", "notes": ""})
        return path, self.git("rev-parse", branch).strip()

    def assert_measured(self, sha):
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertTrue(base["ok"], base["why"])
        self.assertEqual(base["baseline"]["red"],
                         ["verify.nav.test_ticket_1.NavSize.test_value"])
        self.assertEqual(base["candidate_run"]["red"], [])
        self.assertEqual(base["candidate_sha"], sha)
        self.assertEqual(base["base_sha"], self.load_ticket("1")["base_sha"])
        return base

    def test_candidate_may_be_a_branch_name(self):
        """🩸 #20:`--candidate t20` 舊版去找 `$PWD/t20`,印「candidate 裡找不到這幾個
        案例檔」—— 而檔就在 t20 上,只是沒有人去 git 裡拿。

        **變異**:把 `resolve_tree` 裡 `rev-parse --verify` 那一段拔掉 → 這一條紅。
        """
        _, sha = self.a_branch()
        done = self.tool("check", "1", "--candidate", "t1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.assert_measured(sha)["candidate"], "t1")

    def test_candidate_may_be_a_sha(self):
        """落地後補量走的是 `--ref <base_sha> --candidate <merge sha>`,兩格都是 sha。"""
        _, sha = self.a_branch()
        done = self.tool("check", "1", "--candidate", sha)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_measured(sha)

    def test_candidate_may_be_a_worktree_path(self):
        """路徑這一種是舊的叫法,**不准被新的解法吃掉**(同名目錄優先於同名分支)。"""
        path, sha = self.a_branch()
        done = self.tool("check", "1", "--candidate", path)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_measured(sha)

    def test_the_case_file_is_overlaid_onto_a_ref_tree_that_lacks_it(self):
        """案例是這張票才加的,ref 那棵樹上本來就沒有它 —— 不疊上去,乾淨主線那一趟
        跑到的是**零個案例**,而零個案例與「都過了」長得一樣。"""
        _, _ = self.a_branch()
        out = os.path.join(self.home, "vc")
        done = self.tool("check", "1", "--candidate", "t1", "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.exists(os.path.join(
            out, "base", "verify", "nav", "test_ticket_1.py")), "沒疊上去")
        self.assertEqual(self.load_ticket("1")["verify"]["baseline"]["baseline"]["cases"], 1)

    def test_after_landing_the_ref_defaults_to_the_ticket_base_sha(self):
        """🩸 #19:票落地之後,對**主線**量基準永遠是「一條都沒紅」—— 實作已經在主線
        上了。那一句說的是「這一趟量錯了地方」,不是「這條案例是假的」。

        **變異**:把預設的 ref 改回 `ticketlib.main_branch()` → 這一條紅。
        """
        self.make_ticket(1, verify={"files": ["verify/nav/test_ticket_1.py"],
                                    "tags": ["example"], "run": "", "notes": ""})
        opened_at = self.load_ticket("1")["base_sha"]
        self.write("src/value.txt", "2\n")                 # 落地:主線的頭往前走
        self.write(os.path.join("verify", "nav", "test_ticket_1.py"), CASE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "#1 落地")
        self.assertNotEqual(self.git("rev-parse", "main").strip(), opened_at)
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertTrue(base["ok"], base["why"])
        self.assertEqual(base["base_sha"], opened_at, "量的不是開票那一版")

    # --------------------------------------------------- 量不到 ≠ 驗紅沒過

    def test_a_baseline_that_cannot_be_measured_does_not_overwrite_the_ticket(self):
        """🩸「這一趟沒量到」與「這條案例驗不到東西」在票上長得一樣,而 `ticket.py
        close` 只看得到 `ok: false` —— #19 就是這樣卡死的:一句量錯地方的結論蓋掉了
        票上已經有的判決,而且再也沒有人分得出來。

        **變異**:把失敗路徑改回蓋寫票(`unmeasured` 換成走 record 那一段)→ 這一條紅。
        """
        self.a_ticket()
        row = self.load_ticket("1")
        row["verify"]["baseline"] = {"ok": True, "why": "",
                                     "at": "2026-09-22T00:00:00+08:00"}
        row["state_version"] = 7
        self.write(os.path.join("tickets", "1.json"),
                   json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        done = self.tool("check", "1", "--candidate", "沒有這個分支")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("量不到基準,票沒有動", done.stderr)
        self.assertIn("下一步", done.stderr, "守衛的產出要是一個可以敲的動作")
        after = self.load_ticket("1")
        self.assertTrue(after["verify"]["baseline"]["ok"], "量不到卻把票上的判決蓋掉了")
        self.assertEqual(after["state_version"], 7, "量不到的那一趟不該動票")

    def test_a_ref_that_already_contains_the_candidate_is_not_a_verdict(self):
        """🩸 #19:票落地之後拿 `--ref main` 去量,主線上**已經有**那份實作 ——
        「一條都沒紅」說的是這一趟量錯了地方,不是這條案例是假的。舊版把它當判決
        蓋進票,`close` 從此擋著那張票,而票面上再也看不出差別。

        **變異**:把 `merge-base --is-ancestor` 那一段拔掉 → 這一條紅。
        """
        self.a_branch()
        landed = self.worktree("landed")
        self.git("merge", "-q", "--no-ff", "-m", "#1 落地", "t1", cwd=landed)
        done = self.tool("check", "1", "--ref", "landed", "--candidate", "t1")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("量不到基準,票沒有動", done.stderr)
        self.assertIn("本來就有這份實作", done.stderr)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    def test_case_files_missing_from_the_candidate_is_not_a_verdict_either(self):
        """#20 印的就是這一句。它說的是「這一趟拿錯樹」,不是「驗紅沒過」。"""
        self.a_branch()
        done = self.tool("check", "1", "--candidate", "main")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("找不到這幾個案例檔", done.stderr)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    # -------------------------------------------------------------- extract

    def test_extract_writes_a_diff_of_only_the_verify_files(self):
        """驗證者的 `work/` 裡已經有實作者的 patch —— 手工挑檔遲早挑錯一個,
        而差分基準本來沒有寫死在任何地方(外部審查)。"""
        self.a_ticket()
        self.write("src/value.txt", "2\n")
        done = self.tool("extract", "1", "--out", os.path.join(self.repo, "patch-verify.diff"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        text = self.read("patch-verify.diff")
        self.assertIn("--- base/verify/nav/test_ticket_1.py", text)
        self.assertIn("+++ work/verify/nav/test_ticket_1.py", text)
        self.assertNotIn("+++ work/src/", text, "只准含驗證檔,實作者那一半不進來")
        self.assertNotIn(self.repo, text, "檔頭不准是絕對路徑(D-012)")

    # ------------------------------------------------------------ 標籤登記

    def test_a_fragment_counts_as_registered_before_it_is_merged(self):
        """一票一個片段檔就不會撞;**合併那一步不准變成新的排隊點**。"""
        self.write(os.path.join("verify", "TAGS.d", "7.md"), "- `nav-size` — 導覽列尺寸(#7)\n")
        self.write(os.path.join("verify", "nav", "test_ticket_7.py"),
                   "import unittest\nTAGS = ['nav-size']\n\n\n"
                   "class T(unittest.TestCase):\n    def test_x(self):\n        pass\n")
        done = self.run_py("scripts/verify.py", "--list")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("nav-size", done.stdout)

    def test_tags_merge_folds_the_fragments_in_and_sorts_them(self):
        self.write(os.path.join("verify", "TAGS.d", "7.md"), "- `nav-size` — 導覽列尺寸(#7)\n")
        self.write(os.path.join("verify", "TAGS.d", "9.md"), "- `ledger-colour` — 帳本配色(#9)\n")
        done = self.tool("tags-merge")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        text = self.read(os.path.join("verify", "TAGS.md"))
        self.assertLess(text.index("`example`"), text.index("`ledger-colour`"))
        self.assertIn("`nav-size`", text)

    def test_the_same_tag_with_two_different_meanings_is_loud(self):
        """同名不同說明 = 兩張票對同一個標籤的理解不一樣。靜靜取其中一份,那個分歧
        會在半年後以「這個 tag 到底在守什麼」的形式回來。"""
        self.write(os.path.join("verify", "TAGS.d", "7.md"), "- `example` — 別的意思\n")
        done = self.tool("tags-merge")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("撞名", done.stderr)


if __name__ == "__main__":
    unittest.main()
